"""Pure rendering/temporary-file fixtures; no shell, network, Docker or native build."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render_push_workflow import (APPLICATION_COMMIT, CANDIDATE_INPUT, JOB_PREFIX,
                                  SOURCE_INDEX_INPUT, TEMPLATE_HEADER, render,
                                  validate_parameters, write_wrapper)


ROOT = Path(__file__).resolve().parents[1]
PARAMETERS = {
    "candidate_revision": "a" * 40,
    "source_index_sha256": "b" * 64,
    "branch": "dot/repair-f2c-compiler-20261022",
    "workflow_path": ".github/workflows/f2c-compiler-fixed.yml",
}


class PushRendererTests(unittest.TestCase):
    def setUp(self):
        self.template = (ROOT / "workflow/compiler-build.yml.example").read_text()
        self.output = render(self.template, **PARAMETERS)

    def test_only_push_on_exact_branch_and_own_path(self):
        self.assertIn("on:\n  push:\n    branches:\n      - '" + PARAMETERS["branch"] +
                      "'\n    paths:\n      - '" + PARAMETERS["workflow_path"] + "'\n", self.output)
        for forbidden in ("workflow_dispatch:", "workflow_call:", "pull_request:",
                          "pull_request_target:", "schedule:", "inputs."):
            self.assertNotIn(forbidden, self.output)

    def test_fixed_repository_event_ref_and_workflow_identity(self):
        self.assertIn("github.repository == 'peakpeng666/URP'", self.output)
        self.assertIn("github.event_name == 'push'", self.output)
        self.assertIn("github.ref == 'refs/heads/" + PARAMETERS["branch"] + "'", self.output)
        self.assertIn("github.workflow_ref == 'peakpeng666/URP/" + PARAMETERS["workflow_path"] +
                      "@refs/heads/" + PARAMETERS["branch"] + "'", self.output)

    def test_pins_are_literal_and_application_pin_stays_fixed(self):
        self.assertIn("ref: '" + PARAMETERS["candidate_revision"] + "'\n", self.output)
        self.assertIn("REVIEWED_SOURCE_INDEX: '" + PARAMETERS["source_index_sha256"] + "'\n", self.output)
        self.assertIn("ref: " + APPLICATION_COMMIT + "\n", self.output)
        self.assertEqual(self.output.count("${{"), 1)  # The fixed job guard only.

    def test_permission_and_concurrency_are_bounded(self):
        self.assertIn("permissions:\n  contents: read\n", self.output)
        self.assertIn("  cancel-in-progress: false\n", self.output)
        self.assertNotIn("contents: write", self.output)
        self.assertNotIn("cancel-in-progress: true", self.output)
        self.assertEqual(self.output.count("jobs:\n"), 1)

    def test_full_job_body_parity_after_only_pin_substitution(self):
        expected = self.template[len(TEMPLATE_HEADER):]
        expected = expected.replace(CANDIDATE_INPUT, "'" + PARAMETERS["candidate_revision"] + "'")
        expected = expected.replace(SOURCE_INDEX_INPUT, "'" + PARAMETERS["source_index_sha256"] + "'")
        self.assertEqual(self.output[self.output.index(JOB_PREFIX):], expected)

    def test_shell_bootstrap_and_receipt_body_byte_parity(self):
        marker = "        run: |\n"
        self.assertEqual(self.template.split(marker, 1)[1], self.output.split(marker, 1)[1])

    def test_parent_template_body_changes_are_carried_through_unchanged(self):
        modified = self.template.replace("          umask 077\n", "          umask 077\n          # parent reviewed new bootstrap check\n")
        output = render(modified, **PARAMETERS)
        self.assertEqual(modified.split("        run: |\n", 1)[1], output.split("        run: |\n", 1)[1])

    def test_hashes_are_quoted_even_when_only_decimal_digits(self):
        output = render(self.template, **dict(PARAMETERS, candidate_revision="1" * 40,
                                             source_index_sha256="2" * 64))
        self.assertIn("ref: '" + "1" * 40 + "'", output)
        self.assertIn("REVIEWED_SOURCE_INDEX: '" + "2" * 64 + "'", output)

    def test_hash_validation_rejects_non_full_or_expression_values(self):
        for name, length in (("candidate_revision", 40), ("source_index_sha256", 64)):
            for value in (None, True, 12, "", "a" * (length - 1), "a" * (length + 1),
                          "A" * length, "g" * length, "a" * length + "\n", "main",
                          "${{ github.sha }}", "'" + "a" * length):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        validate_parameters(**dict(PARAMETERS, **{name: value}))
        with self.assertRaises(ValueError):
            validate_parameters(**dict(PARAMETERS, candidate_revision="0" * 40))

    def test_branch_validation_rejects_patterns_injections_and_unsafe_refs(self):
        invalid = (None, True, "", "main\n", "refs/heads/repair", "/repair", "repair/",
                   "repair//a", ".repair", "repair/.hidden", "repair/..", "repair/a..b",
                   "repair/-a", "repair/a.lock", "repair/a.", "repair/*", "repair/**",
                   "repair/!x", "repair/[ab]", "repair/a?", "repair/a+b", "repair/@{x}",
                   "repair/a b", "repair/a'b", 'repair/a"b', "repair/a:b", "repair/a\\b",
                   "${{ github.ref }}", "x" * 161)
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_parameters(**dict(PARAMETERS, branch=value))

    def test_workflow_validation_rejects_paths_outside_exact_file(self):
        for value in (None, True, "", "/.github/workflows/x.yml", "./.github/workflows/x.yml",
                      ".github/workflows/../x.yml", ".github/workflows/sub/x.yml",
                      ".github/workflows/*.yml", ".github/workflows/x?.yml",
                      ".github/workflows/x..y.yml", ".github/workflows/.x.yml",
                      ".github/workflows/x.yml\n", ".github/workflows/x.yml'",
                      ".github/workflows/x.json", "ci/workflows/x.yml",
                      ".github/workflows/" + "a" * 121 + ".yml"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_parameters(**dict(PARAMETERS, workflow_path=value))

    def test_safe_branch_and_yaml_extensions(self):
        for branch in ("repair", "repair/f2c-compiler_v4.1", "Dot/repair_2026"):
            for path in (".github/workflows/f2c.yml", ".github/workflows/f2c-build.yaml"):
                validate_parameters(**dict(PARAMETERS, branch=branch, workflow_path=path))

    def test_envelope_drift_new_job_or_new_expression_fails_closed(self):
        for template in (
            self.template.replace("  workflow_dispatch:", "  pull_request:"),
            self.template.replace("    runs-on: ubuntu-24.04", "    runs-on: self-hosted"),
            self.template + "  additional-job:\n    runs-on: ubuntu-24.04\n",
            self.template + "    environment: production\n",
            self.template + "      # ${{ github.sha }}\n",
            self.template + "      # ${{ unfinished\n",
            self.template.replace(CANDIDATE_INPUT, "main", 1),
            self.template.replace("          ref: " + APPLICATION_COMMIT,
                                  "          ref: " + "c" * 40),
            self.template.replace("\n", "\r\n"),
            self.template + "\x00\n",
            self.template.rstrip("\n"),
        ):
            with self.subTest(template=template[-100:]):
                with self.assertRaises(ValueError):
                    render(template, **PARAMETERS)

    def test_renderer_is_deterministic_and_does_not_modify_template(self):
        before = (ROOT / "workflow/compiler-build.yml.example").read_bytes()
        self.assertEqual(render(self.template, **PARAMETERS), self.output)
        self.assertEqual(before, (ROOT / "workflow/compiler-build.yml.example").read_bytes())

    def test_output_is_separate_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            candidate = base / "candidate"
            candidate.mkdir()
            output = base / "wrapper.yml"
            write_wrapper(self.output, output, candidate_root=candidate)
            self.assertEqual(output.read_text(), self.output)
            with self.assertRaises(FileExistsError):
                write_wrapper("replace", output, candidate_root=candidate)
            self.assertEqual(output.read_text(), self.output)
            with self.assertRaises(ValueError):
                write_wrapper(self.output, candidate / "wrapper.yml", candidate_root=candidate)
            self.assertEqual(list(candidate.iterdir()), [])

    def test_output_cannot_enter_candidate_through_a_symlink(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            candidate = base / "candidate"
            candidate.mkdir()
            alias = base / "alias"
            alias.symlink_to(candidate, target_is_directory=True)
            with self.assertRaises(ValueError):
                write_wrapper(self.output, alias / "wrapper.yml", candidate_root=candidate)
            self.assertEqual(list(candidate.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
