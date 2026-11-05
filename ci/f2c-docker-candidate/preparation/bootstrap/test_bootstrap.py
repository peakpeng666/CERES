"""Pure shell-syntax/static/AWK fixture tests. Never invoke the bootstrap.

No Docker, APT, dpkg, gpgv, package install/download, native build or external
write is performed. Python here is the authoring/test interpreter, not assumed
present in the future Ubuntu base.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
HASH = "a" * 64


def awk(name, text, **variables):
    command = ["awk"]
    for key, value in variables.items():
        command.extend(["-v", f"{key}={value}"])
    command.extend(["-f", str(ROOT / name)])
    return subprocess.run(command, input=text, text=True, capture_output=True)


def release():
    return f"""-----BEGIN PGP SIGNED MESSAGE-----
Hash: SHA512

Origin: Ubuntu
Suite: jammy
Codename: jammy
Architectures: amd64 arm64
Components: main restricted universe multiverse
Date: Wed, 21 Oct 2026 00:00:00 UTC
SHA256:
 {HASH} 12 main/binary-amd64/Packages.xz
 {HASH} 13 universe/binary-amd64/Packages.xz
-----BEGIN PGP SIGNATURE-----
fixture-only-not-a-signature
-----END PGP SIGNATURE-----
"""


def package():
    return f"""Package: python3
Version: 3.10.6-1~22.04.1
Architecture: amd64
Depends: python3-minimal (= 3.10.6-1~22.04.1)
Description: fixture only
 continuation
Filename: pool/main/p/python3-defaults/python3_3.10.6-1~22.04.1_amd64.deb
Size: 1234
SHA256: {HASH}

"""


def status(version="1", state="install ok installed", arch="amd64"):
    return f"Package: example\nStatus: {state}\nArchitecture: {arch}\nVersion: {version}\n\n"


class SyntaxAndPins(unittest.TestCase):
    def test_shell_syntax_only(self):
        result = subprocess.run(["sh", "-n", str(ROOT / "bootstrap.sh")], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_base_digest_is_fixed(self):
        dockerfile = (ROOT / "Dockerfile.bootstrap").read_text()
        self.assertIn("FROM --platform=linux/amd64 docker.io/library/ubuntu@sha256:98510ac07bb4545e389be502a8918b385c018bfe4ceeb5ac7e0375c782268b04 AS bootstrap", dockerfile)
        self.assertNotIn("ARG ", dockerfile)

    def test_release_pins_match_reviewed_predecessor(self):
        import json
        # The authoring pass independently matched these retained reviewed pins.
        # This portable fixture does not depend on any host/private source path.
        values = json.loads((ROOT / "reviewed-input-anchors.json").read_text())
        script = (ROOT / "bootstrap.sh").read_text()
        for value in values["release_hashes"].values():
            self.assertIn(value, script)
        for value in values["release_sizes"].values():
            self.assertIn(str(value), script)
        self.assertIn(values["signer"], script)
        self.assertIn(values["snapshot"], script)

    def test_security_guards_and_order(self):
        script = (ROOT / "bootstrap.sh").read_text()
        for token in [
            'Acquire::Check-Date "true";', 'Acquire::Check-Valid-Until "true";',
            'Acquire::https::Verify-Peer "true";', 'Acquire::https::Verify-Host "true";',
            'Acquire::http::AllowRedirect "false";', 'APT::Get::AllowUnauthenticated "false";',
            'Acquire::AllowInsecureRepositories "false";',
            '/usr/bin/dpkg --compare-versions "$version" gt "$old"',
            '--assume-yes --no-download --no-install-recommends --no-remove install',
            '"$architecture $old install"', '"full_closure_verified":false',
        ]:
            self.assertIn(token, script)
        install = script.index('run install /usr/bin/apt-get')
        self.assertLess(script.index('phase=signed_metadata'), install)
        self.assertLess(script.index('phase=archive_authentication'), install)
        self.assertLess(script.index('run apt-update'), install)
        self.assertLess(script.index('phase=public_ca_seed'), script.index('phase=signed_metadata'))
        self.assertIn('ORIGIN=https://snapshot.ubuntu.com/', script)
        self.assertIn('Acquire::https::CaInfo "/opt/urp-bootstrap-evidence/bootstrap-ca.pem";', script)
        self.assertLess(install, script.index('/usr/bin/python3 -I -B "$HELPERS/check_python_prerequisites.py"'))
        for forbidden in ["--allow-unauthenticated", "--allow-downgrades", "trusted=yes", "curl ", "wget ", "sudo ", "docker "]:
            self.assertNotIn(forbidden, script)

    def test_seed_lock_and_script_agree(self):
        import json
        lock = json.loads((ROOT / "ca-inputs.lock.json").read_text())
        script = (ROOT / "bootstrap.sh").read_text()
        self.assertEqual(lock["package"]["suite"], "jammy-security")
        self.assertEqual(lock["package"]["version"], "20260816ubuntu0.22.04.1")
        for row in lock["inputs"]:
            self.assertIn(row["sha256"], script)
            self.assertIn(str(row["size"]), script)
            self.assertTrue(row["url"].startswith("https://snapshot.ubuntu.com/ubuntu/20261021T000000Z/"))


class CertificateFixtures(unittest.TestCase):
    def test_regular_certificates_only(self):
        text = "drwxr-xr-x 0/0 0 2026-10-20 00:00:00 ./usr/share/ca-certificates/mozilla/\n-rw-r--r-- 0/0 1234 2026-10-20 00:00:00 ./usr/share/ca-certificates/mozilla/Example_Root-CA.crt\n-rwxr-xr-x 0/0 1234 2026-10-20 00:00:00 ./usr/sbin/update-ca-certificates\n"
        result = awk("parse_ca_members.awk", text)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "./usr/share/ca-certificates/mozilla/Example_Root-CA.crt\n")

    def test_certificate_links_traversal_duplicates_rejected(self):
        line = "-rw-r--r-- 0/0 1234 2026-10-20 00:00:00 ./usr/share/ca-certificates/mozilla/Example.crt\n"
        for text in [line.replace("-rw", "lrw", 1), line.replace("Example.crt", "../Example.crt"), line + line, line.replace("Example.crt", "Example.crt -> /etc/passwd"), line.replace("1234", "70000"), ""]:
            with self.subTest(text=text):
                self.assertNotEqual(awk("parse_ca_members.awk", text).returncode, 0)

    def test_public_pem_only(self):
        valid = "-----BEGIN CERTIFICATE-----\nYQ==\n-----END CERTIFICATE-----\n"
        self.assertEqual(awk("verify_ca_pem.awk", valid).returncode, 0)
        for text in [valid.replace("CERTIFICATE", "PRIVATE KEY"), valid + valid, valid.replace("YQ==", "$(id)"), valid.replace("-----END CERTIFICATE-----", ""), ""]:
            self.assertNotEqual(awk("verify_ca_pem.awk", text).returncode, 0)

    def test_controller_is_syntax_only_and_not_executed(self):
        import ast
        ast.parse((ROOT / "materialize_ca_inputs.py").read_text())
        self.assertNotIn("subprocess", (ROOT / "materialize_ca_inputs.py").read_text())


class ReleaseFixtures(unittest.TestCase):
    def test_expected_signed_payload_shape(self):
        result = awk("parse_release.awk", release(), suite="jammy")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 2)

    def test_identity_and_index_mutations_fail(self):
        mutations = [
            ("Origin: Ubuntu", "Origin: Debian"),
            ("Suite: jammy", "Suite: noble"),
            ("Codename: jammy", "Codename: noble"),
            ("Architectures: amd64 arm64", "Architectures: arm64"),
            ("Components: main restricted universe multiverse", "Components: main"),
            ("Origin: Ubuntu", "Origin: Ubuntu\nOrigin: Ubuntu"),
            ("universe/binary-amd64/Packages.xz", "universe/binary-arm64/Packages.xz"),
            (HASH + " 12", "bad 12"),
            ("-----BEGIN PGP SIGNATURE-----", "not a marker"),
        ]
        for before, after in mutations:
            with self.subTest(after=after):
                self.assertNotEqual(awk("parse_release.awk", release().replace(before, after), suite="jammy").returncode, 0)

    def test_duplicate_index_fails(self):
        text = release().replace("SHA256:\n", f"SHA256:\n {HASH} 12 main/binary-amd64/Packages.xz\n")
        self.assertNotEqual(awk("parse_release.awk", text, suite="jammy").returncode, 0)


class PackageFixtures(unittest.TestCase):
    def test_signed_record(self):
        result = awk("parse_packages.awk", package(), suite="jammy", component="main")
        self.assertEqual(result.returncode, 0, result.stderr)
        fields = result.stdout.strip().split("\t")
        self.assertEqual(len(fields), 8)
        self.assertEqual(fields[:3], ["python3", "3.10.6-1~22.04.1", "amd64"])

    def test_unsafe_package_metadata(self):
        changes = [
            ("Architecture: amd64", "Architecture: arm64"),
            ("Package: python3", "Package: python3\nPackage: python3"),
            ("pool/main/p/", "pool/main/../"),
            ("pool/main/p/", "pool/main//p/"),
            ("pool/main/p/", "https://elsewhere/"),
            ("Size: 1234", "Size: 0"),
            (HASH, "a" * 63),
            ("Version: 3.10.6-1~22.04.1", "Version: $(id)"),
        ]
        for before, after in changes:
            with self.subTest(after=after):
                self.assertNotEqual(awk("parse_packages.awk", package().replace(before, after)).returncode, 0)


class SimulationFixtures(unittest.TestCase):
    def test_install_and_upgrade(self):
        text = "Reading package lists...\nInst python3 (3.10.6-1~22.04.1 Ubuntu:22.04/jammy [amd64])\nInst ca-certificates [1] (2 Ubuntu:22.04/jammy [all])\nConf python3 (3.10.6-1~22.04.1 Ubuntu:22.04/jammy [amd64])\n"
        result = awk("parse_simulation.awk", text)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "python3\t3.10.6-1~22.04.1\t-\nca-certificates\t2\t1\n")

    def test_unsafe_simulations(self):
        for text in [
            "Remv example [1]\n", "Purg example [1]\n",
            "Inst\n",
            "Inst example:arm64 (1 Ubuntu [arm64])\n",
            "Inst example (1 Ubuntu [amd64])\nInst example (1 Ubuntu [amd64])\n",
            "Inst ../example (1 Ubuntu [amd64])\n", "Inst example (1 [amd64]) trailing\n",
            "Inst example [1 (2 Ubuntu [amd64])\n",
        ]:
            with self.subTest(text=text):
                self.assertNotEqual(awk("parse_simulation.awk", text).returncode, 0)


class UriFixtures(unittest.TestCase):
    def test_cache_name_only(self):
        text = "'file:/opt/urp-bootstrap-evidence/repo/pool/main/e/example/example_1_amd64.deb' example_1_amd64.deb 123 MD5Sum:ignored\n"
        result = awk("parse_uris.awk", text)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "pool/main/e/example/example_1_amd64.deb\texample_1_amd64.deb\t123\n")

    def test_url_decode_bounded(self):
        text = "'file:///opt/urp-bootstrap-evidence/repo/pool/main/e/example/example_1%2b2_amd64.deb' example_1%2b2_amd64.deb 123 SHA256:ignored\n"
        result = awk("parse_uris.awk", text)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("example_1+2_amd64.deb", result.stdout)

    def test_uri_path_and_collision_rejection(self):
        text = "'file:/opt/urp-bootstrap-evidence/repo/pool/main/e/example/example_1_amd64.deb' example_1_amd64.deb 123 MD5Sum:ignored\n"
        changes = [text + text, text.replace("file:", "https:"), text.replace("/e/example/", "/../example/"), text.replace("example_1_amd64.deb 123", "../example_1_amd64.deb 123"), text.replace("/e/example/", "/%2e%2e/example/")]
        for item in changes:
            with self.subTest(item=item):
                self.assertNotEqual(awk("parse_uris.awk", item).returncode, 0)


class InventoryFixtures(unittest.TestCase):
    def test_installed_and_held(self):
        self.assertEqual(awk("parse_status.awk", status()).stdout, "example\tamd64\t1\tinstall\n")
        self.assertEqual(awk("parse_status.awk", status(state="hold ok installed")).stdout, "example\tamd64\t1\thold\n")

    def test_partial_and_foreign_and_duplicate_fail(self):
        for text in [status(state="install ok unpacked"), status(arch="arm64"), status() + status()]:
            self.assertNotEqual(awk("parse_status.awk", text).returncode, 0)

    def delta(self, base, plan, post):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for name, text in [("base", base), ("plan", plan), ("post", post)]:
                path = Path(directory) / name
                path.write_text(text)
                paths.append(str(path))
            return subprocess.run(["awk", "-f", str(ROOT / "verify_delta.awk"), *paths], capture_output=True)

    def test_exact_delta(self):
        base = "example\tamd64\t1\tinstall\n"
        plan = f"example\tamd64\t2\t1\tupgrade\tpool/main/e/example.deb\t123\t{HASH}\tjammy\tmain\n"
        self.assertEqual(self.delta(base, plan, "example\tamd64\t2\tinstall\n").returncode, 0)
        for post in ["", base, "example\tamd64\t2\thold\n", "example\tamd64\t2\tinstall\nforeign\tall\t1\tinstall\n"]:
            self.assertNotEqual(self.delta(base, plan, post).returncode, 0)
        self.assertNotEqual(self.delta(base.replace("install", "hold"), plan, "example\tamd64\t2\tinstall\n").returncode, 0)

    def test_install_and_unchanged_package_delta(self):
        base = "basepkg\tamd64\t1\tinstall\n"
        plan = f"example\tall\t2\t-\tinstall\tpool/main/e/example.deb\t123\t{HASH}\tjammy\tmain\n"
        self.assertEqual(self.delta(base, plan, base + "example\tall\t2\tinstall\n").returncode, 0)
        self.assertEqual(self.delta(base, "", base).returncode, 0)
        self.assertNotEqual(self.delta(base, plan, "example\tall\t2\tinstall\n").returncode, 0)


if __name__ == "__main__":
    unittest.main()
