"""Pure CA filename regression tests; never invoke bootstrap or package code.

Fixtures contain a synthetic public-PEM-shaped payload, not host CA trust.
Only local GNU tar listing/stdout reads and AWK parsing are performed. No
download, Docker, installation, native compilation or system extraction occurs.
"""
from pathlib import Path, PurePosixPath
import io
import json
import re
import shlex
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
PREFIX = "./usr/share/ca-certificates/mozilla/"
EXCEPTION = "NetLock_Arany_=Class_Gold=_Főtanúsítvány.crt"
ORDINARY = "Example_Root-CA.crt"
PAYLOAD = b"-----BEGIN CERTIFICATE-----\nYQ==\n-----END CERTIFICATE-----\n"
ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C", "TZ": "UTC"}


def parse(text):
    return subprocess.run(
        ["awk", "-f", str(ROOT / "parse_ca_members.awk")],
        input=text, capture_output=True, env=ENV, check=False,
    )


def record(name, *, kind="-", size=59):
    return (
        f"{kind}rw-r--r-- 0/0 {size} 1970-01-01 00:00:00 {PREFIX}{name}\n"
    ).encode("utf-8")


def write_fixture(path):
    with tarfile.open(path, "w", format=tarfile.GNU_FORMAT) as archive:
        directory = tarfile.TarInfo(PREFIX)
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o755
        archive.addfile(directory)
        for name in (ORDINARY, EXCEPTION):
            info = tarfile.TarInfo(PREFIX + name)
            info.size = len(PAYLOAD)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(PAYLOAD))


def list_fixture(path, *, literal):
    command = ["tar", "--list", "--verbose", "--numeric-owner", "--full-time"]
    if literal:
        command.append("--quoting-style=literal")
    return subprocess.run(
        [*command, "--file", str(path)],
        capture_output=True, env=ENV, check=False,
    )


class CertificateFilenameCompatibility(unittest.TestCase):
    def test_exact_exception_and_ordinary_filename_are_accepted(self):
        self.assertIsNone(re.fullmatch(r"[A-Za-z0-9_+().,-]+\.crt", EXCEPTION))
        result = parse(record(ORDINARY) + record(EXCEPTION))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.decode("utf-8").splitlines(),
            [PREFIX + ORDINARY, PREFIX + EXCEPTION],
        )

    def test_default_c_locale_tar_escaping_reproduces_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.tar"
            write_fixture(path)
            listed = list_fixture(path, literal=False)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn(b"F\\305\\221tan\\303\\272s", listed.stdout)
            self.assertEqual(parse(listed.stdout).returncode, 47)

    def test_literal_listing_and_stdout_extraction_preserve_exact_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.tar"
            write_fixture(path)
            listed = list_fixture(path, literal=True)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn(EXCEPTION.encode("utf-8"), listed.stdout)
            selected = parse(listed.stdout)
            self.assertEqual(selected.returncode, 0, selected.stderr)
            members = selected.stdout.decode("utf-8").splitlines()
            self.assertEqual(members, [PREFIX + ORDINARY, PREFIX + EXCEPTION])
            for member in members:
                extracted = subprocess.run(
                    ["tar", "--extract", "--to-stdout", "--file", str(path), "--", member],
                    capture_output=True, env=ENV, check=False,
                )
                self.assertEqual(extracted.returncode, 0, extracted.stderr)
                self.assertEqual(extracted.stdout, PAYLOAD)
                verified = subprocess.run(
                    ["awk", "-f", str(ROOT / "verify_ca_pem.awk")],
                    input=extracted.stdout, capture_output=True, env=ENV, check=False,
                )
                self.assertEqual(verified.returncode, 0, verified.stderr)
            # GNU tar is never permitted to extract a fixture to the filesystem.
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_exception_is_not_a_general_unicode_or_equals_allowlist(self):
        invalid = [
            EXCEPTION.replace("Class_Gold", "Class_Silver"),
            EXCEPTION.replace("Fő", "Fo"),
            EXCEPTION + ".crt",
            "Other=Root.crt",
            "Other_ő_Root.crt",
            "NetLock_Arany_=Class_Gold=_F\\305\\221tan\\303\\272s\\303\\255tv\\303\\241ny.crt",
        ]
        for name in invalid:
            with self.subTest(name=name):
                self.assertEqual(parse(record(name)).returncode, 47)

    def test_path_control_link_and_duplicate_guards_are_retained(self):
        invalid = [
            record("../" + EXCEPTION),
            record("nested/" + EXCEPTION),
            record(EXCEPTION.replace("NetLock", "Net Lock")),
            record(EXCEPTION.replace("NetLock", "Net\tLock")),
            record(EXCEPTION.replace("NetLock", "Net\x1bLock")),
            record(EXCEPTION, kind="l"),
            record(EXCEPTION, kind="h"),
            record(EXCEPTION) + record(EXCEPTION),
            record(EXCEPTION)[:-1] + b" -> /etc/passwd\n",
        ]
        for text in invalid:
            with self.subTest(text=text):
                self.assertEqual(parse(text).returncode, 47)

    def test_member_size_and_count_limits_are_retained(self):
        for size in (0, -1, 65537):
            with self.subTest(size=size):
                self.assertEqual(parse(record(EXCEPTION, size=size)).returncode, 47)
        self.assertEqual(parse(b"").returncode, 47)
        excessive = b"".join(record(f"Root_{index}.crt") for index in range(513))
        self.assertEqual(parse(excessive).returncode, 47)


class BootstrapSeedCopyContract(unittest.TestCase):
    def test_compiler_and_standalone_dockerfiles_copy_all_seeds_to_script_seed(self):
        script = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")
        seed_match = re.search(r"^SEED=(/[^\s]+)$", script, re.MULTILINE)
        self.assertIsNotNone(seed_match)
        seed = seed_match.group(1).rstrip("/")
        lock = json.loads((ROOT / "ca-inputs.lock.json").read_text())
        names = {row["name"] for row in lock["inputs"]}
        self.assertEqual(names, {
            "jammy-security.InRelease",
            "jammy-security.main.amd64.Packages.xz",
            "ca-certificates.deb",
        })
        for dockerfile in (ROOT / "Dockerfile.bootstrap", ROOT.parent / "Dockerfile"):
            with self.subTest(dockerfile=dockerfile.name):
                logical = dockerfile.read_text().replace("\\\n", " ")
                deliveries = {}
                for line in logical.splitlines():
                    if not line.strip().startswith("COPY "):
                        continue
                    tokens = shlex.split(line)
                    destination = tokens[-1].rstrip("/")
                    for source in tokens[1:-1]:
                        name = PurePosixPath(source).name
                        if name in names:
                            self.assertNotIn(name, deliveries)
                            deliveries[name] = destination
                self.assertEqual(deliveries, {name: seed for name in names})

    def test_bootstrap_listing_explicitly_uses_literal_quoting(self):
        script = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")
        commands = [line for line in script.splitlines() if "/usr/bin/tar --list " in line]
        self.assertEqual(len(commands), 1)
        tokens = shlex.split(commands[0])
        self.assertIn("--quoting-style=literal", tokens)
        self.assertIn("--numeric-owner", tokens)
        self.assertIn("--full-time", tokens)


if __name__ == "__main__":
    unittest.main()
