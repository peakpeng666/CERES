"""Offline installer contracts. Run: python3 -m unittest -v test_install_entrypoints.

Bash functions run with isolated HOME/CERES_HOME and mocks; no Docker, network,
apt, sudo, WSL, or external repositories are changed. PowerShell contracts are
static when pwsh is unavailable, and explicitly do not constitute Windows QA.
"""
from pathlib import Path
import os
import json
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
BASH = ROOT / "install.sh"
PS = ROOT / "install.ps1"
SHA = "1234567890abcdef1234567890abcdef12345678"


class BashInstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="CERES 空格 O'Brien [case] ")
        self.home = Path(self.tmp.name)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("CERES_", "URP_")) and k != "WSL_DISTRO_NAME"}
        self.env.update(HOME=str(self.home), CERES_HOME=str(self.home / "state"))

    def tearDown(self):
        self.tmp.cleanup()

    def run_bash(self, body, env=None, success=True):
        result = subprocess.run(["bash", "-c", 'source "$1"\n' + body, "test", str(BASH)],
                                env=self.env | (env or {}), text=True, capture_output=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def test_bash_syntax(self):
        subprocess.run(["bash", "-n", str(BASH)], check=True)

    def test_default_main_consistent(self):
        r = self.run_bash('configure; printf "%s|%s|%s|%s" "$REF" "$RAW" "$TAG" "$CERES_IMAGE_TAG"')
        self.assertEqual(r.stdout, 'main|https://raw.githubusercontent.com/peakpeng666/URP/main|main|main')

    def test_legacy_environment_and_new_precedence(self):
        r = self.run_bash('configure; printf "%s|%s" "$REF" "$PORT"',
                          {'URP_REF': 'v1', 'URP_PORT': '4300'})
        self.assertEqual(r.stdout, 'v1|4300')
        r = self.run_bash('configure; printf "%s|%s" "$REF" "$PORT"',
                          {'URP_REF': 'v1', 'CERES_REF': 'v2', 'URP_PORT': '4300', 'CERES_PORT': '4400'})
        self.assertEqual(r.stdout, 'v2|4400')

    def test_existing_install_state_is_reused(self):
        self.env.pop('CERES_HOME')
        legacy = self.home / '.local/share/urp'
        legacy.mkdir(parents=True)
        r = self.run_bash('configure; printf "%s" "$STATE"')
        self.assertEqual(r.stdout, str(legacy))
        current = self.home / '.local/share/ceres'
        current.mkdir()
        r = self.run_bash('configure; printf "%s" "$STATE"')
        self.assertEqual(r.stdout, str(current))

    def test_release_commit_and_branch_selection(self):
        for ref, tag in [('v20261019', 'v20261019'), (SHA, SHA), ('redo', ''), ('feature/fix', '')]:
            with self.subTest(ref=ref):
                r = self.run_bash('configure; printf "%s|%s|%s" "$REF" "$RAW" "$TAG"', {'CERES_REF': ref})
                self.assertEqual(r.stdout, f'{ref}|https://raw.githubusercontent.com/peakpeng666/URP/{ref}|{tag}')

    def test_ref_argument_beats_environment(self):
        r = self.run_bash('configure --ref v2 status; printf "%s|%s|%s" "$REF" "$TAG" "$cmd"', {'CERES_REF': 'redo'})
        self.assertEqual(r.stdout, 'v2|v2|status')

    def test_branch_never_falls_back_to_another_image_channel(self):
        r = self.run_bash('configure; require_image_tag', {'CERES_REF': 'redo'}, success=False)
        self.assertIn('不会回落到其他通道', r.stderr)

    def test_explicit_branch_image_exported(self):
        r = self.run_bash('configure; require_image_tag; printf "%s|%s" "$TAG" "$CERES_IMAGE_TAG"',
                          {'CERES_REF': 'redo', 'CERES_IMAGE_TAG': SHA})
        self.assertEqual(r.stdout, f'{SHA}|{SHA}')

    def test_official_raw_infers_ref(self):
        r = self.run_bash('configure; printf "%s|%s" "$REF" "$TAG"',
                          {'CERES_RAW': 'https://raw.githubusercontent.com/peakpeng666/URP/redo'})
        self.assertEqual(r.stdout, 'redo|')

    def test_raw_ref_conflict_rejected(self):
        self.run_bash('configure', {'CERES_RAW': 'https://raw.githubusercontent.com/peakpeng666/URP/redo',
                                   'CERES_REF': 'deploy'}, success=False)

    def test_custom_raw_requires_explicit_ref(self):
        self.run_bash('configure', {'CERES_RAW': 'https://example.invalid/urp'}, success=False)

    def test_invalid_ref_and_tag_rejected(self):
        for ref in ['--upload-pack=evil', '../main', "redo'; touch /tmp/no", 'a//b', 'main\nfoo']:
            with self.subTest(ref=ref):
                self.run_bash('configure', {'CERES_REF': ref}, success=False)
        self.run_bash('configure', {'CERES_IMAGE_TAG': 'foo/bar'}, success=False)

    def test_saved_selection_retains_ref_tag_components_port(self):
        self.run_bash('configure; save_selection', {'CERES_REF': 'v1', 'CERES_COMPONENTS': 'workbench', 'CERES_PORT': '4200'})
        r = self.run_bash('configure; printf "%s|%s|%s|%s" "$REF" "$TAG" "$COMPONENTS" "$PORT"')
        self.assertEqual(r.stdout, 'v1|v1|workbench|4200')
        r = self.run_bash('configure; printf "%s|%s" "$REF" "$TAG"', {'CERES_REF': 'redo'})
        self.assertEqual(r.stdout, 'redo|')

    def test_missing_curl_stops_without_sudo(self):
        r = self.run_bash('''configure
command() { if [[ "$1" == -v && "$2" == curl ]]; then return 1; fi; builtin command "$@"; }
sudo() { echo UNEXPECTED_SUDO; exit 99; }
require_curl
''', success=False)
        self.assertIn('缺少 curl', r.stderr)
        self.assertNotIn('UNEXPECTED_SUDO', r.stdout)

    def test_native_platform_rejects_arm_and_wsl1(self):
        for arch, kernel, wsl in [('aarch64', 'linux', ''), ('x86_64', '4.4.0-Microsoft', 'Ubuntu-22.04')]:
            with self.subTest(arch=arch, kernel=kernel):
                self.run_bash('''ubuntu_2204() { return 0; }
uname() { if [[ "$1" == -m ]]; then echo "$TEST_ARCH"; else echo "$TEST_KERNEL"; fi; }
require_native_platform''', {'TEST_ARCH': arch, 'TEST_KERNEL': kernel, 'WSL_DISTRO_NAME': wsl}, success=False)

    def test_native_platform_accepts_native_and_wsl2(self):
        for kernel, wsl in [('6.8.0-generic', ''), ('5.15.90.1-microsoft-standard-WSL2', 'Ubuntu-22.04')]:
            self.run_bash('''ubuntu_2204() { return 0; }
uname() { if [[ "$1" == -m ]]; then echo x86_64; else echo "$TEST_KERNEL"; fi; }
require_native_platform''', {'TEST_KERNEL': kernel, 'WSL_DISTRO_NAME': wsl})

    def test_update_uses_fetched_head_not_stale_remote_branch(self):
        r = self.run_bash('''configure --ref v1
mkdir -p "$STATE"
git() { printf '%s\\n' "$*" >> "$STATE/git.log"; }
update_src "$STATE/src"
cat "$STATE/git.log"
''')
        self.assertIn('fetch --depth 1 --filter=blob:none origin v1', r.stdout)
        self.assertIn('checkout --detach --quiet FETCH_HEAD', r.stdout)
        self.assertNotIn('origin/deploy', r.stdout)

    def test_failed_fetch_never_checks_out_or_records_success(self):
        self.run_bash('''configure --ref v1
mkdir -p "$STATE"
git() { printf '%s\\n' "$*" >> "$STATE/git.log"; if [[ "$*" == *' fetch '* ]]; then return 17; fi; }
update_src "$STATE/src"
''', success=False)
        log = (self.home / 'state/git.log').read_text()
        self.assertNotIn('checkout', log)
        self.assertFalse((self.home / 'state/source.ref').exists())

    def test_dirty_worktree_is_not_overwritten(self):
        r = self.run_bash('''configure
mkdir -p "$STATE"
git() { if [[ "$*" == *'status --porcelain'* ]]; then echo ' M user.py'; else echo UNEXPECTED_MUTATION; fi; }
update_src "$STATE/src"
''', success=False)
        self.assertNotIn('UNEXPECTED_MUTATION', r.stdout)

    def test_interrupted_compose_download_preserves_previous_files(self):
        deploy = self.home / 'state/deploy'
        deploy.mkdir(parents=True)
        names = ['docker-compose.sim.yml', 'docker-compose.gpu.yml', 'docker-compose.display.yml',
                 'docker-compose.fuel.yml', 'fetch-fuel.sh']
        for name in names:
            (deploy / name).write_text('old:' + name)
        self.run_bash('''configure
curl() { printf partial > "$4"; return 18; }
fetch_compose
''', success=False)
        for name in names:
            self.assertEqual((deploy / name).read_text(), 'old:' + name)
        self.assertEqual(sorted(p.name for p in deploy.iterdir()), sorted(names))

    def test_compose_files_publish_after_complete_download(self):
        self.run_bash('''configure
curl() { printf '%s' "new:${2##*/}" > "$4"; }
fetch_compose
''')
        deploy = self.home / 'state/deploy'
        for path in deploy.iterdir():
            self.assertEqual(path.read_text(), 'new:' + path.name)

    def test_failed_health_preserves_saved_selection_and_cli(self):
        self.run_bash('configure; save_selection', {'CERES_REF': 'v1'})
        self.run_bash('''configure
require_curl() { :; }; require_docker_platform() { :; }; pull_sim() { :; }
resolve_image_source() { :; }; fetch_compose() { :; }; compose_files() { :; }
wait_health() { return 23; }
install_cli() { touch "$STATE/unexpected-cli-update"; }
docker_up
''', {'CERES_REF': 'v2'}, success=False)
        self.assertEqual((self.home / 'state/install.selection').read_text().splitlines()[0], 'v1')
        self.assertFalse((self.home / 'state/unexpected-cli-update').exists())

    def test_image_revision_pins_assets(self):
        r = self.run_bash('''configure
git() { exit 99; }
docker() { echo "$TEST_SHA"; }
resolve_image_source
printf '%s' "$ASSET_RAW"
''', {'TEST_SHA': SHA})
        self.assertTrue(r.stdout.endswith('/' + SHA))

    def test_explicit_commit_rejects_wrong_image(self):
        self.run_bash('''configure
docker() { echo 0000000000000000000000000000000000000000; }
resolve_image_source
''', {'CERES_REF': SHA}, success=False)

    def test_explicit_main_drops_legacy_saved_image_and_raw(self):
        state = self.home / 'state'
        state.mkdir()
        (state / 'install.selection').write_text('deploy\nhttps://raw.githubusercontent.com/peakpeng666/URP/deploy\ndeploy\nfull\n4200\n')
        r = self.run_bash('configure --ref main update; printf "%s|%s|%s|%s" "$REF" "$RAW" "$TAG" "$PORT"')
        self.assertEqual(r.stdout, 'main|https://raw.githubusercontent.com/peakpeng666/URP/main|main|4200')

    def test_legacy_writes_stop_before_any_external_action(self):
        for ref in ('deploy', 'redo'):
            for action in ('up', 'update', 'fuel'):
                with self.subTest(ref=ref, action=action):
                    r = self.run_bash('configure --ref ' + ref + ' ' + action + '; have_docker() { echo unexpected; }; route', success=False)
                    self.assertIn('旧分支安装选择需要迁移', r.stderr)
                    self.assertNotIn('unexpected', r.stdout)
        self.assertFalse((self.home / 'state/install.selection').exists())

    def test_legacy_status_remains_read_only_and_available(self):
        for ref in ('deploy', 'redo'):
            with self.subTest(ref=ref):
                r = self.run_bash('configure --ref ' + ref + ' status; require_curl() { :; }; have_docker() { return 1; }; curl() { echo healthy; }; route')
                self.assertIn('healthy', r.stdout)
        self.assertFalse((self.home / 'state/install.selection').exists())

    def test_sha_image_override_rejects_mismatched_revision(self):
        r = self.run_bash('configure; docker() { echo 0000000000000000000000000000000000000000; }; resolve_image_source',
                          {'CERES_REF': 'main', 'CERES_IMAGE_TAG': SHA}, success=False)
        self.assertIn('镜像提交标签', r.stderr)

    def test_matching_sha_override_keeps_assets_on_same_revision(self):
        r = self.run_bash('configure; docker() { echo "$TEST_SHA"; }; resolve_image_source; printf "%s|%s" "$TAG" "$ASSET_RAW"',
                          {'CERES_REF': 'main', 'CERES_IMAGE_TAG': SHA, 'TEST_SHA': SHA})
        self.assertTrue(r.stdout.endswith(SHA + '|https://raw.githubusercontent.com/peakpeng666/URP/' + SHA))

    def test_single_main_ci_and_image_contract(self):
        for path in ('.github/workflows/images.yml', '.github/workflows/ci.yml'):
            source = (ROOT / path).read_text()
            self.assertIn('branches: [main]', source)
            self.assertNotIn('branches: [deploy]', source)
        for path in ('deploy/docker-compose.yml', 'deploy/docker-compose.sim.yml'):
            self.assertIn('${CERES_IMAGE_TAG:-${URP_IMAGE_TAG:-main}}', (ROOT / path).read_text())
        self.assertIn('refs/heads/main', (ROOT / 'deploy/promote-images.sh').read_text())
        self.assertNotIn('refs/heads/deploy', (ROOT / 'deploy/publish-image.sh').read_text())
        workflow = (ROOT / '.github/workflows/images.yml').read_text()
        promotion = workflow.split('  promote-channels:', 1)[1]
        self.assertIn('always()', promotion)
        self.assertIn('group: ceres-image-channel-promotion', promotion)
        self.assertNotIn('github.workflow', promotion)
        self.assertIn('needs.verify-public-pull.outputs.bench_digest', promotion)
        self.assertIn('needs.verify-public-pull.outputs.sim_digest', promotion)
        self.assertIn('inputs.publish_channels', promotion)
        desktop = (ROOT / '.github/workflows/desktop.yml').read_text()
        self.assertIn("publish_channels: ${{ github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v') }}", desktop)

        for name in ('bench', 'sim', 'verify-public-pull'):
            self.assertIn("needs." + name + ".result == 'success'", promotion)



    def test_first_install_explicit_setup_and_marker(self):
        src = self.home / 'state/src'
        setup = src / 'setup/ubuntu/setup.sh'
        setup.parent.mkdir(parents=True)
        setup.write_text('printf "setup:%s\\n" "$*"\n')
        r = self.run_bash('''configure
require_native_platform() { :; }; bootstrap_native_tools() { :; }; require_curl() { :; }
clone_src() { printf '%s' "$STATE/src"; }
git() { echo "$TEST_SHA"; }
install_cli() { :; }; curl() { return 1; }
exec() { printf 'exec:%s\\n' "$*"; }
native_up
''', {'TEST_SHA': SHA, 'CERES_COMPONENTS': 'full'})
        self.assertIn('setup:--components full', r.stdout)
        self.assertIn('exec:bash ', r.stdout)
        self.assertEqual((self.home / 'state/source.setup').read_text().strip(), f'{SHA} full')

    def test_failed_setup_does_not_record_ready_or_launch(self):
        src = self.home / 'state/src'
        setup = src / 'setup/ubuntu/setup.sh'
        setup.parent.mkdir(parents=True)
        setup.write_text('exit 19\n')
        r = self.run_bash('''configure
require_native_platform() { :; }; bootstrap_native_tools() { :; }; require_curl() { :; }
clone_src() { printf '%s' "$STATE/src"; }; git() { echo "$TEST_SHA"; }
install_cli() { echo UNEXPECTED_INSTALL; }; exec() { echo UNEXPECTED_RUN; }
native_up
''', {'TEST_SHA': SHA}, success=False)
        self.assertFalse((self.home / 'state/source.setup').exists())
        self.assertNotIn('UNEXPECTED_', r.stdout)


class PowerShellStaticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PS.read_text()

    @unittest.skipUnless(shutil.which('pwsh'), 'PowerShell is not installed; Windows behavior remains unverified')
    def test_powershell_parser(self):
        command = r'''$tokens = $null; $errors = $null
[System.Management.Automation.Language.Parser]::ParseFile($env:CERES_INSTALLER_TEST_PATH, [ref]$tokens, [ref]$errors) | Out-Null
if ($errors.Count) { $errors | Out-String | Write-Error; exit 1 }
'''
        for path in [PS, ROOT / 'deploy/windows/start-lab.ps1', ROOT / 'deploy/windows/stop-lab.ps1']:
            subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-Command', command],
                           env=os.environ | {'CERES_INSTALLER_TEST_PATH': str(path)}, check=True)

    @unittest.skipUnless(shutil.which('pwsh'), 'PowerShell is not installed; download failure injection remains unverified')
    def test_powershell_interrupted_download_preserves_previous_compose(self):
        with tempfile.TemporaryDirectory(prefix="CERES 空格 O'Brien [case] ") as temp:
            deploy = Path(temp) / 'deploy'
            deploy.mkdir()
            names = ['docker-compose.sim.yml', 'docker-compose.gpu.yml', 'docker-compose.fuel.yml', 'docker-compose.display.yml']
            for name in names:
                (deploy / name).write_text('old:' + name)
            command = r'''$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:CERES_INSTALLER_TEST_PATH, [ref]$tokens, [ref]$errors)
$fn = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-ComposeFile' }, $true)
. ([scriptblock]::Create($fn.Extent.Text))
$ErrorActionPreference = 'Stop'
$State = $env:CERES_TEST_STATE
$AssetRaw = 'https://example.invalid/fixture'
function Invoke-WebRequest { param([switch]$UseBasicParsing, [string]$Uri, [string]$OutFile)
    [IO.File]::WriteAllText($OutFile, 'partial'); throw 'injected interruption'
}
try { Get-ComposeFile; throw 'expected download error' } catch {
    if ($_.Exception.Message -ne 'injected interruption') { throw }
}
# Catching the expected exception alone leaves $? false, so pwsh -Command
# would return 1. Emit a positive outcome only after the exact error matched.
[Console]::WriteLine('expected interruption caught')
'''
            result = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-Command', command],
                                    env=os.environ | {'CERES_INSTALLER_TEST_PATH': str(PS), 'CERES_TEST_STATE': temp},
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0,
                             f'PowerShell fixture exited {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}')
            self.assertEqual(result.stdout.strip(), 'expected interruption caught')
            for name in names:
                self.assertEqual((deploy / name).read_text(), 'old:' + name)
            self.assertEqual(sorted(p.name for p in deploy.iterdir()), sorted(names))

    def test_selection_paths_are_literal(self):
        for marker in ['Test-Path -LiteralPath $selectionPath', 'Get-Content -Raw -LiteralPath $selectionPath',
                       'Set-Content -Encoding UTF8 -LiteralPath $pending',
                       'Move-Item -Force -LiteralPath $pending -Destination $selectionPath']:
            self.assertIn(marker, self.source)

    @unittest.skipUnless(shutil.which('pwsh'), 'PowerShell is not installed; state path roundtrip remains unverified')
    def test_saved_selection_roundtrips_through_literal_paths(self):
        with tempfile.TemporaryDirectory(prefix="CERES 空格 O'Brien [case] ") as temp:
            output = Path(temp) / 'loaded.json'
            command = r'''$tokens = $null; $errors = $null
$source = [IO.File]::ReadAllText($env:CERES_INSTALLER_TEST_PATH)
$ast = [System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
$fn = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Save-Selection' }, $true)
. ([scriptblock]::Create($fn.Extent.Text))
$ErrorActionPreference = 'Stop'
$State = $env:CERES_TEST_STATE
$selectionPath = Join-Path $State 'install.selection.json'
$Ref = 'v1'; $Raw = 'https://raw.githubusercontent.com/peakpeng666/URP/v1'; $Tag = 'v1'; $Components = 'research'; $Port = '4200'
Save-Selection
foreach ($name in @('CERES_REF', 'CERES_RAW', 'CERES_IMAGE_TAG', 'CERES_COMPONENTS', 'CERES_PORT')) { [Environment]::SetEnvironmentVariable($name, $null, 'Process') }
$env:CERES_HOME = $State
. ([scriptblock]::Create($source.Substring(0, $source.IndexOf('function Save-Selection {'))))
[IO.File]::WriteAllText($env:CERES_TEST_OUTPUT, (@{ref=$Ref; tag=$Tag; components=$Components; port=$Port} | ConvertTo-Json))
'''
            subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-Command', command],
                           env=os.environ | {'CERES_INSTALLER_TEST_PATH': str(PS), 'CERES_TEST_STATE': temp,
                                             'CERES_TEST_OUTPUT': str(output)}, check=True)
            self.assertEqual(json.loads(output.read_text()),
                             {'ref': 'v1', 'tag': 'v1', 'components': 'research', 'port': '4200'})

    def test_explicit_ref_and_export_contract(self):
        self.assertIn('[string]$Ref = $env:CERES_REF', self.source)
        self.assertIn('CERES_REF = $Ref; CERES_RAW = $Raw; CERES_IMAGE_TAG = $Tag', self.source)
        self.assertIn('CERES_COMPONENTS = $Components; CERES_PORT = $Port', self.source)
        self.assertIn('$env:CERES_IMAGE_TAG = $Tag', self.source)
        self.assertIn('$env:CERES_IMAGE_TAG = $oldTag', self.source)

    def test_wsl_version_os_arch_and_bootstrap_guards(self):
        for marker in ['wsl.exe --list --verbose', "$match.Groups[1].Value -ne '2'",
                       'ubuntu|22.04|x86_64', 'command -v curl', 'exit 127', 'set -euo pipefail']:
            self.assertIn(marker, self.source)
        self.assertNotIn('curl -fsSL $Raw/install.sh | bash', self.source)
        self.assertIn('curl -fsSL "$CERES_RAW/install.sh" -o "$script"', self.source)

    def test_fuel_uses_container_paths_not_random_windows_bash(self):
        self.assertNotIn('& bash $script $dest', self.source)
        self.assertIn("$downloadArgs += @($Image, '/installer/fetch-fuel.sh', '/fuel')", self.source)
        self.assertIn('source=$dest,target=/fuel', self.source)

    def test_windows_quotes_each_forwarded_value(self):
        self.assertIn('ConvertTo-BashLiteral $_.Value', self.source)
        self.assertIn("ConvertTo-BashLiteral $Command", self.source)
        self.assertIn('$Value.Replace', self.source)

    def test_health_failure_does_not_report_success(self):
        self.assertIn("else { throw '容器已启动，但健康检查失败。", self.source)

    def test_compose_argument_array_is_bound_explicitly(self):
        self.assertNotIn('Invoke-Compose @(', self.source)
        self.assertIn("Invoke-Compose -ComposeArgs @('up', '-d'", self.source)

    def test_read_only_commands_do_not_download_compose(self):
        action = self.source.split('function Invoke-DockerPath {', 1)[1].split("if ($Action -eq 'up'", 1)[0]
        self.assertNotIn('Get-ComposeFile', action)


class WindowsLauncherTests(unittest.TestCase):
    def test_both_launchers_delegate_without_parallel_execution_logic(self):
        for name, action in [('start-lab.ps1', 'up'), ('stop-lab.ps1', 'down')]:
            with self.subTest(name=name):
                source = (ROOT / 'deploy/windows' / name).read_text()
                self.assertIn(f'& $Installer -Command {action} -Ref $Ref', source)
                self.assertIn('Test-Path -LiteralPath $Installer', source)
                self.assertIn("Join-Path $Root 'install.ps1'", source)
                self.assertIn('[string]$Ref = $env:CERES_REF', source)
                for forbidden in ['wsl.exe', 'docker ', 'run-lab.sh', 'Start-Process', '4100', 'exit 0']:
                    self.assertNotIn(forbidden, source)

    def test_windows_scripts_have_utf8_bom_for_powershell_51(self):
        for path in [PS, ROOT / 'deploy/windows/start-lab.ps1', ROOT / 'deploy/windows/stop-lab.ps1']:
            self.assertTrue(path.read_bytes().startswith(b'\xef\xbb\xbf'), str(path))

    def test_batch_wrappers_quote_script_and_preserve_failure_status(self):
        for name, script in [('启动CERES工作台.bat', 'start-lab.ps1'), ('停止CERES工作台.bat', 'stop-lab.ps1')]:
            source = (ROOT / name).read_text()
            self.assertIn(f'-File "%~dp0deploy\\windows\\{script}"', source)
            self.assertIn('set "ceres_exit=%errorlevel%"', source)
            self.assertIn('exit /b %ceres_exit%', source)
            self.assertLess(source.index('set "ceres_exit='), source.index(' pause'))

    def test_legacy_batch_wrappers_forward_arguments_to_ceres(self):
        for old, new in [('启动实验台.bat', '启动CERES工作台.bat'), ('停止实验台.bat', '停止CERES工作台.bat')]:
            self.assertEqual((ROOT / old).read_text().strip(), f'@call "%~dp0{new}" %*')

    @unittest.skipUnless(shutil.which('pwsh'), 'PowerShell is not installed; launcher execution remains unverified')
    def test_launchers_preserve_unicode_spaces_quotes_environment_and_failures(self):
        with tempfile.TemporaryDirectory(prefix="CERES 空格 O'Brien [case] ") as temp:
            root = Path(temp)
            destination = root / 'deploy/windows'
            destination.mkdir(parents=True)
            for name in ['start-lab.ps1', 'stop-lab.ps1']:
                shutil.copyfile(ROOT / 'deploy/windows' / name, destination / name)
            stub = root / 'install.ps1'
            stub.write_text('''param([string]$Command, [string]$Ref)
if ($env:CERES_TEST_FAIL -eq '1') { throw 'fixture failure' }
$data = @{ command = $Command; ref = $Ref; port = $env:CERES_PORT; components = $env:CERES_COMPONENTS }
[IO.File]::WriteAllText($env:CERES_TEST_OUTPUT, ($data | ConvertTo-Json))
''')
            output = root / 'result.json'
            env = os.environ | {'CERES_PORT': '4200', 'CERES_COMPONENTS': 'research',
                                'CERES_TEST_OUTPUT': str(output), 'CERES_REF': 'v-env'}
            for name, action in [('start-lab.ps1', 'up'), ('stop-lab.ps1', 'down')]:
                for fail in ['0', '1']:
                    result = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-File',
                                             str(destination / name), '-Ref', 'v-explicit'],
                                            env=env | {'CERES_TEST_FAIL': fail}, text=True, capture_output=True)
                    if fail == '1':
                        self.assertNotEqual(result.returncode, 0)
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(json.loads(output.read_text()),
                                         {'command': action, 'ref': 'v-explicit', 'port': '4200', 'components': 'research'})

    @unittest.skipUnless(shutil.which('pwsh'), 'PowerShell is not installed; quoting function execution remains unverified')
    def test_powershell_quote_function_roundtrips_through_bash(self):
        command = r'''$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:CERES_INSTALLER_TEST_PATH, [ref]$tokens, [ref]$errors)
$fn = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'ConvertTo-BashLiteral' }, $true)
. ([scriptblock]::Create($fn.Extent.Text))
[Console]::Write((ConvertTo-BashLiteral $env:CERES_TEST_VALUE))
'''
        # Windows CreateProcess may resolve bare 'bash' to System32's WSL
        # launcher before consulting PATH. CI provides the verified Git Bash
        # executable; other hosts resolve it once and execute its absolute path.
        selected_bash = os.environ.get('CERES_TEST_BASH') or shutil.which('bash')
        self.assertIsNotNone(selected_bash, 'Bash is required for the quoting contract')
        bash = str(Path(selected_bash).resolve(strict=True))
        version = subprocess.run([bash, '--version'], text=True, capture_output=True)
        self.assertEqual(version.returncode, 0,
                         f'Bash preflight: {bash}\nstdout: {version.stdout}\nstderr: {version.stderr}')
        self.assertIn('GNU bash', version.stdout, f'Expected Git/GNU Bash, got {bash}: {version.stdout}')
        for value in ["C:\\用户 空格\\O'Brien\\CERES", "literal'; printf bad; '", 'https://example.invalid/空 格']:
            quoted = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-Command', command],
                                    env=os.environ | {'CERES_INSTALLER_TEST_PATH': str(PS), 'CERES_TEST_VALUE': value},
                                    text=True, capture_output=True)
            self.assertEqual(quoted.returncode, 0,
                             f'PowerShell quote function exited {quoted.returncode}\nstdout: {quoted.stdout}\nstderr: {quoted.stderr}')
            actual = subprocess.run([bash, '-c', 'printf %s ' + quoted.stdout], text=True, capture_output=True)
            self.assertEqual(actual.returncode, 0,
                             f'Bash quote roundtrip: {bash} exited {actual.returncode}\nstdout: {actual.stdout}\nstderr: {actual.stderr}')
            self.assertEqual(actual.stdout, value)


if __name__ == '__main__':
    unittest.main()
