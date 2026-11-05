"""Pure compiler-only Docker contract checks; no native or image execution."""
import copy
import importlib.util
import pathlib
import types
import unittest
from unittest import mock

RECIPE = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('docker_input_guardlib', RECIPE / 'guardlib.py')
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)


class DockerLayoutTests(unittest.TestCase):
    def fixture(self):
        return {'application_commit': g.APPLICATION_COMMIT, 'layout': dict(g.LAYOUT), 'network': dict(g.NETWORK),
                'preparation': {'same_live_runner_required': False, 'image_environment_required': True,
                                'scope': 'compiler-build-only', 'validation_inputs_prepared': False,
                                'structural_abi_proven': False, 'native_build_executed': False},
                'host': {'python': {'prefix': str(g.PYTHON_PREFIX)}}}

    def test_accepts_compiler_only_split_mount_layout(self): g.require_docker_layout(self.fixture())

    def test_validation_scope_is_not_allowed(self):
        for scope, prepared in [('compiler-build-with-validation-inputs', True), ('compiler-build-only', 'false')]:
            value = self.fixture(); value['preparation'].update(scope=scope, validation_inputs_prepared=prepared)
            with self.assertRaises(RuntimeError): g.require_docker_layout(value)

    def test_old_same_runner_network_record_is_refused(self):
        value = self.fixture(); value['network'] = {'kernel_boot_id': 'boot', 'netns': 'net:[1]'}
        with self.assertRaisesRegex(RuntimeError, 'isolation protocol'): g.require_docker_layout(value)

    def test_writable_or_relocated_input_is_refused(self):
        for key, alternative in [('inputs_readonly', False), ('control_readonly', False), ('inputs', '/opt/urp/prepared'), ('build', '/inputs/output')]:
            value = self.fixture(); value['layout'][key] = alternative
            with self.subTest(key=key), self.assertRaises(RuntimeError): g.require_docker_layout(value)

    def test_wrong_application_or_python_is_refused(self):
        value = self.fixture(); value['application_commit'] = '0' * 40
        with self.assertRaises(RuntimeError): g.require_docker_layout(value)
        value = self.fixture(); value['host']['python']['prefix'] = '/host/python'
        with self.assertRaises(RuntimeError): g.require_docker_layout(value)

    def test_preparation_cannot_claim_native_acceptance(self):
        for key in ('native_build_executed', 'structural_abi_proven', 'same_live_runner_required'):
            value = self.fixture(); value['preparation'][key] = True
            with self.assertRaises(RuntimeError): g.require_docker_layout(value)

    def test_only_compiler_scopes_and_commands_required(self):
        self.assertFalse({'recipe/build_offline.sh', 'recipe/build_guard.py', 'recipe/run_build.py'} & g.REQUIRED_INDEX)
        self.assertFalse({'unshare', 'node', 'npm'} & g.REQUIRED_COMMANDS)
        self.assertNotIn('node-prefix', g.REQUIRED_HOST_SCOPES)
        self.assertNotIn('validation-data', g.REQUIRED_INPUT_SCOPES)
        self.assertEqual(g.APPLICATION_FILES, {'application/src/lab/pyproject.toml', 'application/src/lab/uv.lock'})

    def test_wrong_output_rejected_before_filesystem_touch(self):
        with mock.patch.object(g, 'root_guard') as root_guard, self.assertRaises(RuntimeError): g.fresh_outputs(pathlib.Path('/inputs'))
        root_guard.assert_not_called()


class BootstrapReceiptTests(unittest.TestCase):
    def fixture(self):
        versions = {'python': '3.12.13', 'uv': '0.12.23'}
        return {'schema': 1, 'tools': {name: {'version': versions[name], 'path': path, 'sha256': 'a' * 64,
                'provenance': copy.deepcopy(g.BOOTSTRAP_PROVENANCE[name])} for name, path in g.BOOTSTRAP_PATHS.items()}}

    def validate(self, value, measured='a' * 64):
        with mock.patch.object(pathlib.Path, 'is_symlink', return_value=False), \
             mock.patch.object(pathlib.Path, 'is_file', return_value=True), \
             mock.patch.object(pathlib.Path, 'stat', return_value=types.SimpleNamespace(st_mode=0o755)), \
             mock.patch.object(g, 'sha256', return_value=measured): return g.verify_bootstrap(value)

    def test_exact_python_uv_receipt_measures_without_execution(self):
        self.assertEqual(set(g.BOOTSTRAP_PATHS), {'python', 'uv'})
        self.assertEqual(self.validate(self.fixture()), self.fixture())

    def test_wrong_bytes_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'bytes changed'): self.validate(self.fixture(), 'b' * 64)

    def test_wrong_versions_refused(self):
        for name, version in [('uv', '0.12.24'), ('python', '3.12.12')]:
            value = self.fixture(); value['tools'][name]['version'] = version
            with self.assertRaises(RuntimeError): self.validate(value)

    def test_mutable_action_or_python_archive_refused(self):
        value = self.fixture(); value['tools']['uv']['provenance']['action'] = 'owner/action@main'
        with self.assertRaises(RuntimeError): self.validate(value)
        value = self.fixture(); value['tools']['python']['provenance']['archive_sha256'] = '0' * 64
        with self.assertRaises(RuntimeError): self.validate(value)

    def test_extra_node_missing_tool_or_host_path_refused(self):
        value = self.fixture(); value['tools']['node'] = {}
        with self.assertRaises(RuntimeError): self.validate(value)
        value = self.fixture(); value['tools'].pop('uv')
        with self.assertRaises(RuntimeError): self.validate(value)
        value = self.fixture(); value['tools']['uv']['path'] = '/usr/bin/uv'
        with self.assertRaises(RuntimeError): self.validate(value)

    def test_no_ubuntu_deb_claim_for_bootstrap_tools(self):
        for row in self.fixture()['tools'].values(): self.assertNotIn('signed-ubuntu', row['provenance']['kind'])


if __name__ == '__main__': unittest.main()
