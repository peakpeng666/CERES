"""Clean two-environment launch contracts; frontend is always rebuilt, fail closed."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
STARTUP_FIXTURES = ('setup/run.sh', 'setup/profile.sh', 'src/start.sh')


class StartupContractTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name in STARTUP_FIXTURES:
            dest = self.root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, dest)
        self.events = self.root / 'events'
        self.env = {**os.environ, 'EVENTS': str(self.events), 'BUILD_EXIT': '0'}
        self.env.pop('CERES_PYTHON', None)
        self.executable('.venv/bin/python', '#!/bin/sh\nprintf "root-python %s\\n" "$*" >> "$EVENTS"\n')
        self.executable('src/lab/.venv/bin/python', '#!/bin/sh\necho gateway >> "$EVENTS"\n')
        self.executable('bin/node', '#!/bin/sh\nexit 0\n')
        self.executable('bin/npm', '#!/bin/sh\necho build >> "$EVENTS"\nexit "$BUILD_EXIT"\n')
        self.env['PATH'] = str(self.root / 'bin') + os.pathsep + os.environ['PATH']
        self.write('src/frontend/dist/index.html', 'old dist')

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def executable(self, name, content):
        path = self.write(name, content)
        path.chmod(0o755)
        return path

    def run_command(self, script, *args, cwd=None):
        return subprocess.run(['bash', str(self.root / script), *args], cwd=cwd or self.root,
                              env=self.env, text=True, capture_output=True)

    def test_root_default_research_and_solver_check_no_legacy_venv(self):
        for command in ('research', 'solver-check'):
            result = self.run_command('setup/run.sh', command, '--help')
            self.assertEqual(result.returncode, 0, result.stderr)
        events = self.events.read_text()
        self.assertIn('root-python src/research/scripts/ceres.py --help', events)
        self.assertIn('root-python setup/2026-10-15/deployment/benchmark.py solver', events)
        self.assertFalse((self.root / 'src/research/.venv').exists())

    def test_python_resolver_explicit_relative_and_path_override(self):
        for override in ('./.venv/bin/python', 'node'):
            self.env['CERES_PYTHON'] = override
            result = self.run_command('src/start.sh', 'python-path')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(Path(result.stdout.strip()).is_absolute())
        self.env['CERES_PYTHON'] = '/ceres-nonexistent/python'
        result = self.run_command('src/start.sh', 'python-path')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('/ceres-nonexistent/python', result.stderr)

    def test_all_build_inputs_and_source_deletion_rebuild_even_with_old_mtimes(self):
        for name in ('package-lock.json', 'package.json', 'vite.config.ts', 'tsconfig.json',
                     'tools/optional-public.mjs', 'public/new.txt', 'src/deleted.ts', 'core/new.ts'):
            with self.subTest(name=name):
                path = self.write('src/frontend/' + name, 'changed')
                os.utime(path, (1, 1))
                if name == 'src/deleted.ts':
                    path.unlink()
                self.events.unlink(missing_ok=True)
                result = self.run_command('setup/run.sh', 'lab-code')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.events.read_text().splitlines(), ['build', 'gateway'])
        # Even unchanged inputs rebuild: deliberately simpler than a partial fingerprint.
        self.events.unlink()
        self.assertEqual(self.run_command('setup/run.sh', 'lab-code').returncode, 0)
        self.assertEqual(self.events.read_text().splitlines(), ['build', 'gateway'])

    def test_failed_build_never_serves_existing_dist(self):
        self.env['BUILD_EXIT'] = '17'
        result = self.run_command('setup/run.sh', 'lab-code')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.events.read_text().splitlines(), ['build'])
        self.assertIn('停止启动', result.stderr)


class WorkflowClosureTests(unittest.TestCase):
    def test_engineering_checkout_contains_public_cli_repository_fixtures(self):
        workflow = (ROOT / '.github/workflows/ci.yml').read_text()
        job = workflow.split('  engineering:', 1)[1].split('  deployment-contracts:', 1)[0]
        block = job.split('          sparse-checkout: |\n', 1)[1]
        selected = []
        for line in block.splitlines():
            if not line.startswith('            '):
                break
            selected.append(Path(line.strip()))
        # Cone checkout includes selected directories and files immediately inside
        # their ancestor directories (which is how src/start.sh is obtained).
        fixtures = (*STARTUP_FIXTURES, 'setup/materials-archive.json',
                    'docs/assets/copies.json', 'docs/assets/restoration.json',
                    'docs/assets/drive-restoration-20261101.json')
        for name in fixtures:
            path = Path(name)
            with self.subTest(fixture=name):
                covered = any(directory in path.parents or path.parent in directory.parents
                              for directory in selected)
                self.assertTrue(covered, f'engineering sparse checkout omits {name}')
                self.assertTrue((ROOT / path).is_file(), f'required fixture missing: {name}')

    def test_every_pr_builds_image_without_publication(self):
        workflow = (ROOT / '.github/workflows/deploy.yml').read_text()
        trigger = workflow.split('  pull_request:', 1)[1].split('  workflow_dispatch:', 1)[0]
        self.assertNotIn('paths:', trigger)
        self.assertNotIn('paths-ignore:', trigger)
        self.assertIn('docker build -f deploy/Dockerfile', workflow)
        self.assertNotIn('docker push', workflow)
        self.assertIn('tools/smoke_archive.py', workflow)

    def test_default_ci_checks_dual_service_in_isolated_runtime(self):
        workflow = (ROOT / '.github/workflows/ci.yml').read_text()
        job = workflow.split('  agriculture-contracts:', 1)[1].split('  engineering:', 1)[0]
        self.assertIn('uv sync --locked --extra solvers', job)
        self.assertIn('tools/smoke_dual_service.py', job)
        self.assertNotIn('--no-archive', job)


if __name__ == '__main__':
    unittest.main()
