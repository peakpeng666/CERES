"""Release guard tests. All external commands are fakes; no images are published."""
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40
BENCH_DIGEST = "sha256:" + "1" * 64
SIM_DIGEST = "sha256:" + "2" * 64


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "deploy").mkdir()
        (self.root / "bin").mkdir()
        shutil.copyfile(ROOT / "deploy/publish-image.sh", self.root / "deploy/publish-image.sh")
        self.log = self.root / "calls.jsonl"
        self.env = {
            **os.environ,
            "PATH": str(self.root / "bin") + os.pathsep + os.environ["PATH"],
            "CALL_LOG": str(self.log),
            "GITHUB_SHA": SHA,
            "GITHUB_REPOSITORY_OWNER": "PeakPeng666",
            "GITHUB_REF": "refs/heads/main",
            "BUILD_EXIT": "0", "SMOKE_EXIT": "0", "PUSH_DIGEST": BENCH_DIGEST,
            "GITHUB_OUTPUT": str(self.root / "outputs"),
        }
        self.command("git", "#!/bin/sh\nprintf '%s\\n' \"$GITHUB_SHA\"\n")
        self.command("docker", """#!/usr/bin/env python3
import json, os, sys
with open(os.environ['CALL_LOG'], 'a') as out:
    out.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'push':
    if os.environ.get('PUSH_EXIT'): sys.exit(int(os.environ['PUSH_EXIT']))
    print(os.environ.get('PUSH_LOG', 'tag: digest: ' + os.environ['PUSH_DIGEST'] + ' size: 1234'))
sys.exit(int(os.environ['BUILD_EXIT']) if sys.argv[1] == 'build' else 0)
""")
        (self.root / "deploy/test-image.sh").write_text(
            "#!/bin/sh\nprintf '[\"smoke\"]\\n' >> \"$CALL_LOG\"\nexit \"$SMOKE_EXIT\"\n"
        )

    def tearDown(self):
        self.temp.cleanup()

    def command(self, name, contents):
        target = self.root / "bin" / name
        target.write_text(contents)
        target.chmod(0o755)

    def run_publish(self, **env):
        result = subprocess.run(
            ["bash", "deploy/publish-image.sh", "bench", "deploy/Dockerfile"],
            cwd=self.root, env={**self.env, **env}, capture_output=True, text=True
        )
        events = [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []
        return result, events

    def test_main_checks_before_any_push_and_passes_full_revision(self):
        result, events = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([e[0] for e in events], ["build", "smoke", "push"])
        self.assertIn("URP_REVISION=" + SHA, events[0])
        self.assertIn("org.opencontainers.image.revision=" + SHA, events[0])
        self.assertEqual(events[2:], [
            ["push", "ghcr.io/peakpeng666/urp-bench:" + SHA],
        ])

    def test_smoke_failure_never_pushes(self):
        result, events = self.run_publish(SMOKE_EXIT="9")
        self.assertEqual(result.returncode, 9)
        self.assertEqual([e[0] for e in events], ["build", "smoke"])

    def test_build_failure_never_smokes_or_pushes(self):
        result, events = self.run_publish(BUILD_EXIT="7")
        self.assertEqual(result.returncode, 7)
        self.assertEqual([e[0] for e in events], ["build"])

    def test_other_branch_dispatch_does_not_replace_main(self):
        result, events = self.run_publish(GITHUB_REF="refs/heads/feature/test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([e for e in events if e[0] == "push"], [
            ["push", "ghcr.io/peakpeng666/urp-bench:" + SHA]
        ])

    def test_release_build_only_publishes_sha_before_joint_promotion(self):
        result, events = self.run_publish(GITHUB_REF="refs/tags/v2.3.4")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([e[1].rsplit(":", 1)[1] for e in events if e[0] == "push"],
                         [SHA])

    def test_malformed_revision_fails_before_build(self):
        result, events = self.run_publish(GITHUB_SHA="shortsha")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events, [])

    def test_checkout_mismatch_fails_before_build(self):
        self.command("git", "#!/bin/sh\nprintf '%s\\n' " + "b" * 40 + "\n")
        result, events = self.run_publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events, [])


    def test_push_digest_is_exported_from_exact_push_response(self):
        result, events = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'outputs').read_text(), 'digest=' + BENCH_DIGEST + '\n')
        self.assertFalse(any(e[:2] == ['image', 'inspect'] for e in events))

    def test_missing_or_ambiguous_push_digest_does_not_export(self):
        for log in ('no digest returned', 'digest: sha256:' + '1' * 64 + '\ndigest: sha256:' + '2' * 64):
            with self.subTest(log=log):
                result, events = self.run_publish(PUSH_LOG=log)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / 'outputs').exists())

    def test_failed_push_cannot_export_acceptance_identity(self):
        result, events = self.run_publish(PUSH_EXIT='8')
        self.assertEqual(result.returncode, 8)
        self.assertFalse((self.root / 'outputs').exists())


class HealthCheckTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("image_healthcheck", ROOT / "deploy/image-healthcheck.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def response(self, data):
        return patch.object(self.module.urllib.request, "urlopen", return_value=io.BytesIO(data))

    def test_ok_json_passes(self):
        with self.response(b'{"status":"ok"}'):
            self.module.check("http://127.0.0.1/test")

    def test_default_checks_both_gateway_and_archive(self):
        with patch.object(self.module.urllib.request, 'urlopen',
                          side_effect=[io.BytesIO(b'{"status":"ok"}'), io.BytesIO(b'{"status":"ok"}')]) as opened:
            self.module.check()
        self.assertEqual(len(opened.call_args_list), 2)
        self.assertTrue(opened.call_args_list[0].args[0].endswith('/lab/api/health'))
        self.assertTrue(opened.call_args_list[1].args[0].endswith('/api/health'))

    def test_explicit_lab_only_mode_does_not_require_archive(self):
        with patch.object(self.module.urllib.request, 'urlopen',
                          return_value=io.BytesIO(b'{"status":"ok","archive_required":false}')) as opened:
            self.module.check()
        self.assertEqual(opened.call_count, 1)

    def test_malformed_mode_never_disables_archive_check(self):
        for value in ('"false"', '0', 'null'):
            with self.subTest(value=value), self.response(
                    ('{"status":"ok","archive_required":' + value + '}').encode()), \
                    self.assertRaisesRegex(RuntimeError, 'must be a boolean'):
                self.module.check()

    def test_green_lab_does_not_mask_failed_archive(self):
        with patch.object(self.module.urllib.request, 'urlopen',
                          side_effect=[io.BytesIO(b'{"status":"ok"}'), io.BytesIO(b'{"status":"failed"}')]), \
                self.assertRaises(RuntimeError):
            self.module.check()

    def test_http_200_but_failed_status_is_unhealthy(self):
        with self.response(b'{"status":"failed"}'), self.assertRaises(RuntimeError):
            self.module.check("http://127.0.0.1/test")

    def test_non_json_is_unhealthy(self):
        with self.response(b'<html>not the gateway</html>'), self.assertRaises(ValueError):
            self.module.check("http://127.0.0.1/test")

    def test_wrong_json_shape_is_unhealthy(self):
        with self.response(b'["ok"]'), self.assertRaises(RuntimeError):
            self.module.check("http://127.0.0.1/test")


class ImageSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'bin').mkdir()
        self.output = self.root / 'evidence'
        self.env = {**os.environ, 'PATH': str(self.root / 'bin') + os.pathsep + os.environ['PATH'],
                    'GITHUB_SHA': SHA, 'MOCK_HEALTH': 'running healthy', 'COHORT_EXIT': '0',
                    'MOCK_STATE': str(self.root / 'docker-state'),
                    'REQUEST_LOG': str(self.root / 'curl-requests'),
                    'PORT_BEFORE': '31410', 'PORT_AFTER': '31410', 'RESTART_CURL_EXIT': '0',
                    'RESTART_ARCHIVE_STATUS': 'ok', 'RESTART_INDEX_EMPTY': '0'}
        self.command('docker', '''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
restarted = pathlib.Path(os.environ['MOCK_STATE']).exists()
if args[:2] == ['image', 'inspect']:
    print(os.environ.get('LABEL_REVISION', os.environ['GITHUB_SHA']) if '--format' in args else '[]')
elif args[0] == 'run': print('container-id')
elif args[0] == 'port':
    port = os.environ['PORT_AFTER' if restarted else 'PORT_BEFORE']
    if port: print('127.0.0.1:' + port)
elif args[0] == 'restart':
    pathlib.Path(os.environ['MOCK_STATE']).write_text('restarted')
    print('container-id')
elif args[0] == 'inspect': print(os.environ['MOCK_HEALTH'] if '--format' in args else '[]')
elif args[0] == 'cp':
    target = pathlib.Path(args[-1]); target.mkdir(exist_ok=True)
    (target / 'summary.json').write_text('{"passed":true}')
elif args[0] == 'exec':
    if 'cat' in args: print(os.environ.get('FILE_REVISION', os.environ['GITHUB_SHA']))
    if '/opt/urp/deploy/check-cohort.py' in args: sys.exit(int(os.environ['COHORT_EXIT']))
''')
        self.command('curl', '''#!/usr/bin/env python3
import os, pathlib, sys, urllib.parse
args = sys.argv[1:]
restarted = pathlib.Path(os.environ['MOCK_STATE']).exists()
url = args[-1]
with open(os.environ['REQUEST_LOG'], 'a') as out:
    out.write(('restart ' if restarted else 'start ') + url + '\\n')
expected_port = os.environ['PORT_AFTER' if restarted else 'PORT_BEFORE']
if str(urllib.parse.urlsplit(url).port) != expected_port:
    print('Mock connection refused: stale published port', file=sys.stderr)
    sys.exit(7)
if restarted and os.environ['RESTART_CURL_EXIT'] != '0':
    sys.exit(int(os.environ['RESTART_CURL_EXIT']))
if restarted and urllib.parse.urlsplit(url).path == '/api/health':
    print('{"status":"' + os.environ['RESTART_ARCHIVE_STATUS'] + '"}')
    sys.exit(0)
if restarted and urllib.parse.urlsplit(url).path == '/' and os.environ['RESTART_INDEX_EMPTY'] == '1':
    sys.exit(0)
if '-X' in args:
    pathlib.Path(args[args.index('-o') + 1]).write_text('{"code":"shutdown_forbidden"}')
    print('403', end='')
elif args[-1].endswith('/api/health'): print('{"status":"ok"}')
else: print('<html>URP</html>')
''')

    def command(self, name, contents):
        path = self.root / 'bin' / name
        path.write_text(contents)
        path.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def run_smoke(self, **env):
        return subprocess.run(['bash', str(ROOT / 'deploy/test-image.sh'), 'test:sha', SHA,
                               'bench', str(self.output)], env={**self.env, **env},
                              capture_output=True, text=True)

    def test_passing_smoke_retains_exit_status(self):
        result = self.run_smoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.output / 'exit-code.txt').read_text().strip(), '0')
        self.assertTrue((self.output / 'health-after-restart.json').is_file())
        self.assertTrue((self.output / 'summary.txt').is_file())

    def test_revision_mismatch_fails(self):
        result = self.run_smoke(LABEL_REVISION='b' * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.output / 'summary.txt').exists())

    def test_restart_uses_new_published_port_and_preserves_both_mappings(self):
        result = self.run_smoke(PORT_BEFORE='32768', PORT_AFTER='32769')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.output / 'ports-start.txt').read_text().strip(), '127.0.0.1:32768')
        self.assertEqual((self.output / 'ports-restart.txt').read_text().strip(), '127.0.0.1:32769')
        self.assertEqual((self.output / 'endpoint-start.txt').read_text().strip(), 'http://127.0.0.1:32768')
        self.assertEqual((self.output / 'endpoint-restart.txt').read_text().strip(), 'http://127.0.0.1:32769')
        requests = (self.root / 'curl-requests').read_text().splitlines()
        self.assertIn('start http://127.0.0.1:32768/lab/api/health', requests)
        self.assertEqual([line for line in requests if line.startswith('restart ')],
                         ['restart http://127.0.0.1:32769/lab/api/health',
                          'restart http://127.0.0.1:32769/api/health',
                          'restart http://127.0.0.1:32769/'])
        self.assertTrue((self.output / 'summary.txt').is_file())
        self.assertEqual(json.loads((self.output / 'archive-health-after-restart.json').read_text())['status'], 'ok')
        self.assertTrue((self.output / 'index-after-restart.html').read_text())

    def test_restart_without_mapping_fails_instead_of_reusing_cached_port(self):
        result = self.run_smoke(PORT_AFTER='')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.output / 'summary.txt').exists())
        self.assertFalse((self.output / 'endpoint-restart.txt').exists())
        self.assertIn('Cannot determine published port', result.stderr)

    def test_restart_endpoint_failure_is_not_ignored(self):
        result = self.run_smoke(PORT_AFTER='32769', RESTART_CURL_EXIT='7')
        self.assertEqual(result.returncode, 7)
        self.assertEqual((self.output / 'exit-code.txt').read_text().strip(), '7')
        self.assertFalse((self.output / 'summary.txt').exists())

    def test_restart_gateway_health_does_not_hide_unhealthy_archive(self):
        result = self.run_smoke(PORT_AFTER='32769', RESTART_ARCHIVE_STATUS='failed')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads((self.output / 'health-after-restart.json').read_text())['status'], 'ok')
        self.assertEqual(json.loads((self.output / 'archive-health-after-restart.json').read_text())['status'], 'failed')
        self.assertFalse((self.output / 'summary.txt').exists())

    def test_restart_empty_homepage_is_not_accepted(self):
        result = self.run_smoke(PORT_AFTER='32769', RESTART_INDEX_EMPTY='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.output / 'index-after-restart.html').stat().st_size, 0)
        self.assertFalse((self.output / 'summary.txt').exists())

    def test_crashed_container_fails_without_success_summary(self):
        result = self.run_smoke(MOCK_HEALTH='exited unhealthy')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.output / 'summary.txt').exists())

    def test_cohort_failure_is_retained(self):
        result = self.run_smoke(COHORT_EXIT='12')
        self.assertEqual(result.returncode, 12)
        self.assertEqual((self.output / 'exit-code.txt').read_text().strip(), '12')
        self.assertFalse((self.output / 'summary.txt').exists())

    def test_existing_evidence_directory_is_not_overwritten(self):
        self.output.mkdir()
        (self.output / 'preserve').write_text('old evidence')
        result = self.run_smoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.output / 'preserve').read_text(), 'old evidence')
        self.assertFalse((self.output / 'exit-code.txt').exists())


class ComposeContractTests(unittest.TestCase):
    def test_host_port_does_not_change_container_listener(self):
        for name in ('docker-compose.yml', 'docker-compose.sim.yml'):
            with self.subTest(compose=name):
                source = (ROOT / 'deploy' / name).read_text()
                self.assertIn('${URP_PORT:-4100}:4100', source)
                self.assertIn('      URP_PORT: "4100"', source)
                self.assertNotIn('URP_PORT: "${URP_PORT', source)


class ChannelPromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'calls.jsonl'
        docker = self.bin / 'docker'
        docker.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
a = sys.argv[1:]
with open(os.environ['CALL_LOG'], 'a') as f:
    f.write(json.dumps(a) + '\\n')
with open(os.environ['CONFIG_LOG'], 'a') as f:
    f.write(os.environ.get('DOCKER_CONFIG', '') + '\\n')
if a[0] == 'pull' and 'urp-sim@' in a[-1] and os.environ.get('FAIL_SIM'):
    sys.exit(7)
if a[:2] == ['image', 'inspect']:
    print(('b' * 40) if 'urp-sim@' in a[-1] and os.environ.get('BAD_SIM') else os.environ['GITHUB_SHA'])
if a[:3] == ['buildx', 'imagetools', 'create']:
    resolved = a[-1].split('@', 1)[1] if '@' in a[-1] else os.environ.get('MUTATED_SHA_TAG_DIGEST', 'unverified-tag')
    with open(os.environ['PROMOTED_DIGEST_LOG'], 'a') as f:
        f.write(resolved + '\\n')
""")
        docker.chmod(0o755)
        git = self.bin / 'git'
        git.write_text("#!/bin/sh\n[ -z \"$GIT_FAIL\" ] || exit 12\nprintf '%s\\trefs/heads/main\\n' \"$REMOTE_HEAD\"\n")
        git.chmod(0o755)
        self.env = {**os.environ, 'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
                    'CALL_LOG': str(self.log), 'CONFIG_LOG': str(self.root / 'configs'),
                    'PROMOTED_DIGEST_LOG': str(self.root / 'promoted-digests'),
                    'GITHUB_SHA': SHA, 'REMOTE_HEAD': SHA, 'GIT_FAIL': '',
                    'GITHUB_REPOSITORY_OWNER': 'PeakPeng666', 'GITHUB_REF': 'refs/heads/main',
                    'BENCH_DIGEST': BENCH_DIGEST, 'SIM_DIGEST': SIM_DIGEST,
                    'GITHUB_OUTPUT': str(self.root / 'outputs'), 'GITHUB_STEP_SUMMARY': str(self.root / 'summary')}

    def tearDown(self):
        self.temp.cleanup()

    def run_script(self, script, **env):
        p = subprocess.run(['bash', str(ROOT / 'deploy' / script)],
                           env={**self.env, **env}, capture_output=True, text=True)
        events = [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []
        return p, events

    def run_promotion(self, **env):
        return self.run_script('promote-images.sh', **env)

    def test_both_digest_sources_checked_before_any_main_alias_changes(self):
        p, events = self.run_promotion()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([x[0] for x in events], ['pull', 'image', 'pull', 'image', 'buildx', 'buildx'])
        updates = [x for x in events if x[0] == 'buildx']
        self.assertEqual([x[-2] for x in updates], ['ghcr.io/peakpeng666/urp-bench:main', 'ghcr.io/peakpeng666/urp-sim:main'])
        self.assertEqual([x[-1] for x in updates], ['ghcr.io/peakpeng666/urp-bench@' + BENCH_DIGEST, 'ghcr.io/peakpeng666/urp-sim@' + SIM_DIGEST])
        self.assertTrue(all('--prefer-index=false' in x for x in updates))

    def test_second_image_failure_leaves_all_aliases_unchanged(self):
        p, events = self.run_promotion(FAIL_SIM='1')
        self.assertEqual(p.returncode, 7)
        self.assertFalse(any(x[0] == 'buildx' for x in events))

    def test_second_revision_mismatch_leaves_all_aliases_unchanged(self):
        p, events = self.run_promotion(BAD_SIM='1')
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse(any(x[0] == 'buildx' for x in events))

    def test_version_promotes_both_without_reordering_latest(self):
        p, events = self.run_promotion(GITHUB_REF='refs/tags/v2.3.4')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([x[-2].rsplit(':', 1)[1] for x in events if x[0] == 'buildx'], ['v2.3.4', 'v2.3.4'])
        self.assertNotIn(':latest', json.dumps(events))

    def test_non_main_branch_has_no_channel_side_effects(self):
        p, events = self.run_promotion(GITHUB_REF='refs/heads/feature/test')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(events, [])

    def test_malformed_sha_stops_before_registry_access(self):
        p, events = self.run_promotion(GITHUB_SHA='abc123')
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(events, [])

    def test_malformed_version_tag_stops_before_registry_access(self):
        p, events = self.run_promotion(GITHUB_REF='refs/tags/vbad/tag')
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(events, [])

    def test_missing_digest_fails_before_registry_access(self):
        p, events = self.run_promotion(SIM_DIGEST='')
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(events, [])

    def test_same_sha_tag_rewrite_cannot_change_verified_digest_source(self):
        # A competing run may reuse the SHA label but cannot redirect @digest.
        changed = 'sha256:' + 'f' * 64
        p, events = self.run_promotion(MUTATED_SHA_TAG_DIGEST=changed)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn(':' + SHA, json.dumps(events))
        self.assertNotIn(changed, json.dumps(events))
        self.assertEqual((self.root / 'promoted-digests').read_text().splitlines(), [BENCH_DIGEST, SIM_DIGEST])
        self.assertEqual([x[-1] for x in events if x[0] == 'buildx'],
                         ['ghcr.io/peakpeng666/urp-bench@' + BENCH_DIGEST, 'ghcr.io/peakpeng666/urp-sim@' + SIM_DIGEST])

    def test_stale_main_rerun_does_not_change_any_alias(self):
        p, events = self.run_promotion(REMOTE_HEAD='c' * 40)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('Skipping stale main', p.stdout)
        self.assertFalse(any(x[0] == 'buildx' for x in events))

    def test_remote_head_lookup_failure_fails_closed(self):
        p, events = self.run_promotion(GIT_FAIL='1')
        self.assertEqual(p.returncode, 12)
        self.assertFalse(any(x[0] == 'buildx' for x in events))


class DigestVerificationTests(unittest.TestCase):
    setUp = ChannelPromotionTests.setUp
    tearDown = ChannelPromotionTests.tearDown
    run_script = ChannelPromotionTests.run_script

    def test_exact_digest_verification_exports_only_after_both_pass(self):
        p, events = self.run_script('verify-published-images.sh')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual((self.root / 'outputs').read_text(), 'bench_digest=' + BENCH_DIGEST + '\nsim_digest=' + SIM_DIGEST + '\n')
        self.assertEqual([x[-1] for x in events if x[0] == 'pull'],
                         ['ghcr.io/peakpeng666/urp-bench@' + BENCH_DIGEST, 'ghcr.io/peakpeng666/urp-sim@' + SIM_DIGEST])

    def test_second_pull_failure_exports_no_verified_digests(self):
        p, events = self.run_script('verify-published-images.sh', FAIL_SIM='1')
        self.assertEqual(p.returncode, 7)
        self.assertFalse((self.root / 'outputs').exists())

    def test_revision_mismatch_exports_no_verified_digests(self):
        p, events = self.run_script('verify-published-images.sh', BAD_SIM='1')
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse((self.root / 'outputs').exists())

    def test_invalid_digest_fails_before_registry_access(self):
        p, events = self.run_script('verify-published-images.sh', BENCH_DIGEST='sha256:nope')
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(events, [])

    def test_verification_never_uses_existing_registry_credentials(self):
        old = str(self.root / 'old-docker-config')
        p, events = self.run_script('verify-published-images.sh', DOCKER_CONFIG=old)
        self.assertEqual(p.returncode, 0, p.stderr)
        configs = (self.root / 'configs').read_text().splitlines()
        self.assertEqual(len(set(configs)), 1)
        self.assertNotEqual(configs[0], old)
        self.assertFalse(Path(configs[0]).exists())


if __name__ == "__main__":
    unittest.main()
