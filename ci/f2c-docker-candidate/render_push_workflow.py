"""Render, but never publish or launch, the reviewed compiler push wrapper.

The caller supplies independently reviewed candidate/source-index pins. The
wrapper is a separate file, outside the candidate tree, because putting a file
containing its own candidate commit or source-index digest in that index would
create a circular dependency. This renderer itself belongs in the source index.

Only the event envelope, job guard and three input occurrences change. The
trusted bootstrap and compiler-stage shell body remain byte-for-byte identical
to the reviewed manual template. No YAML serializer rewrites that body.
"""

import argparse
from pathlib import Path
import re


REPOSITORY = "peakpeng666/URP"
APPLICATION_COMMIT = "3acf840d1a0c16ab1cfcca15744e4ea6609ddb35"
CANDIDATE_INPUT = "${{ inputs.candidate_revision }}"
SOURCE_INDEX_INPUT = "${{ inputs.source_index_sha256 }}"
TEMPLATE_HEADER = """# REVIEW CANDIDATE ONLY. Not installed, published, dispatched or executed.
# Place reviewed candidate at ci/f2c-docker-candidate in the separately pinned revision.
name: Reviewed F2C Docker compiler stage
on:
  workflow_dispatch:
    inputs:
      candidate_revision:
        description: Independently reviewed full Git commit containing candidate
        required: true
      source_index_sha256:
        description: Independently reviewed candidate source-index.json SHA256
        required: true
permissions:
  contents: read
jobs:
  compiler-build-only:
"""
JOB_PREFIX = """    runs-on: ubuntu-24.04
    timeout-minutes: 90
    steps:
"""


def _fullmatch(pattern, value, label):
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise ValueError("invalid " + label)


def validate_parameters(candidate_revision, source_index_sha256, branch, workflow_path):
    """Accept literal pins and a deliberately narrow, non-pattern ref/path set."""
    _fullmatch(r"[0-9a-f]{40}", candidate_revision, "reviewed candidate Git SHA")
    _fullmatch(r"[0-9a-f]{64}", source_index_sha256, "reviewed source-index SHA256")
    if candidate_revision == "0" * 40:
        raise ValueError("null candidate Git SHA is not a revision")
    _fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,159}", branch, "repair branch")
    parts = branch.split("/")
    if (branch.startswith("refs/") or ".." in branch or
            any(not part or part.startswith((".", "-")) or
                part.endswith((".", ".lock")) for part in parts)):
        raise ValueError("invalid repair branch")
    _fullmatch(r"\.github/workflows/[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.ya?ml",
               workflow_path, "workflow path")
    if ".." in workflow_path:
        raise ValueError("invalid workflow path")


def render(template, *, candidate_revision, source_index_sha256, branch, workflow_path):
    """Return a push-only fixed-pin workflow; do not execute or write anything."""
    validate_parameters(candidate_revision, source_index_sha256, branch, workflow_path)
    if not isinstance(template, str) or "\r" in template or "\x00" in template:
        raise ValueError("template must be LF-only text")
    if not template.startswith(TEMPLATE_HEADER):
        raise ValueError("manual template envelope changed; review renderer before use")
    body = template[len(TEMPLATE_HEADER):]
    if not body.startswith(JOB_PREFIX) or not body.endswith("\n"):
        raise ValueError("manual template job envelope changed")
    # Do not silently carry through another top-level key, job or job-level key.
    if any(line and not line.startswith("      ")
           for line in body[len(JOB_PREFIX):].splitlines()):
        raise ValueError("unexpected template job structure")
    expressions = re.findall(r"\$\{\{.*?\}\}", body, flags=re.DOTALL)
    if expressions.count(CANDIDATE_INPUT) != 2 or expressions.count(SOURCE_INDEX_INPUT) != 1:
        raise ValueError("template fixed-pin replacement contract changed")
    if len(expressions) != 3 or body.count("${{") != 3:
        raise ValueError("unreviewed expression in manual template")
    required_pins = (
        "          ref: " + CANDIDATE_INPUT + "\n",
        "          REVIEWED_REVISION: " + CANDIDATE_INPUT + "\n",
        "          REVIEWED_SOURCE_INDEX: " + SOURCE_INDEX_INPUT + "\n",
        "          ref: " + APPLICATION_COMMIT + "\n",
    )
    if any(body.count(line) != 1 for line in required_pins):
        raise ValueError("template checkout or reviewed environment pin changed")
    # Quote even all-numeric full hashes so YAML never coerces a pin to a number.
    body = body.replace(CANDIDATE_INPUT, "'" + candidate_revision + "'")
    body = body.replace(SOURCE_INDEX_INPUT, "'" + source_index_sha256 + "'")
    workflow_ref = REPOSITORY + "/" + workflow_path + "@refs/heads/" + branch
    return (
        "# GENERATED FIXED-PIN WRAPPER. Rendering does not publish or launch it.\n"
        "# Keep this generated file outside the candidate source index.\n"
        "name: Reviewed F2C Docker compiler stage (fixed push)\n"
        "on:\n"
        "  push:\n"
        "    branches:\n"
        "      - '" + branch + "'\n"
        "    paths:\n"
        "      - '" + workflow_path + "'\n"
        "permissions:\n"
        "  contents: read\n"
        "concurrency:\n"
        "  group: 'urp-f2c-compiler:" + branch + ":" + workflow_path + "'\n"
        "  cancel-in-progress: false\n"
        "jobs:\n"
        "  compiler-build-only:\n"
        "    if: >-\n"
        "      ${{ github.event_name == 'push' &&\n"
        "          github.repository == '" + REPOSITORY + "' &&\n"
        "          github.ref == 'refs/heads/" + branch + "' &&\n"
        "          github.workflow_ref == '" + workflow_ref + "' }}\n"
        + body
    )


def write_wrapper(text, output, *, candidate_root):
    """Create a separate local file, never overwrite or enter the frozen tree."""
    output = Path(output)
    root = Path(candidate_root).resolve()
    if any(path == root or root in path.parents
           for path in (output.absolute(), output.resolve())):
        raise ValueError("generated wrapper must be outside the candidate source tree")
    # Exclusive creation also rejects an existing output file or symlink.
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-revision", required=True)
    parser.add_argument("--source-index-sha256", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--workflow-path", required=True)
    parser.add_argument("--output", type=Path, required=True,
                        help="new local output file outside this candidate tree")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent
    template = (root / "workflow/compiler-build.yml.example").read_bytes().decode("utf-8")
    result = render(template, candidate_revision=args.candidate_revision,
                    source_index_sha256=args.source_index_sha256,
                    branch=args.branch, workflow_path=args.workflow_path)
    write_wrapper(result, args.output, candidate_root=root)


if __name__ == "__main__":
    main()
