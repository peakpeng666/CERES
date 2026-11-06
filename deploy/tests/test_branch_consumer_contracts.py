"""Manual-only historical workflow contracts. No dispatch, network or native execution.

The frozen helper renderers remain historical evidence. This test prevents their
push-only envelopes from silently replacing the reviewed main/manual wrappers.
"""
from pathlib import Path
import ast
import hashlib
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
BODY_HASHES = {'f2c-candidate.yml': ['8f2218263fa031ed686c33c86e831135425306b300d1f86992f45b1ea9addffb'],
 'f2c-capability.yml': ['ad46ea2614f4b27c9788e74459937a5959cf5472894ce0daa6a6f253c802a8dd'],
 'f2c-docker-compiler.yml': ['235a8fdc55e7178f51e6239d409930e284ada3ecf80b09ca457d6e42cf2b66d4'],
 'native-acceptance.yml': ['9c7949b548adabf14b5d074f5d0cb9c79956938af6b2093f7c8844ae7f56f640',
                           '5025382e5aee802bd8c986157ee756599d17eafb07ecba0063860ea51ea60f71'],
 'warehouse-diagnostic.yml': ['c64f0b0f91ec8905e71be458af97775c97d7429d265e282398214e2277b4a5ae']}


# Independent reviewer froze these complete wrapper RAW bytes before this fix.
# Never refresh these anchors merely to make a changed workflow pass. A full
# workflow change, including comments/newlines, requires independent review.
# The final raw-byte check covers every byte: comments, prefix, duplicate keys,
# unmatched suffixes and reusable jobs without runs-on cannot escape it.
WORKFLOW_SHA256 = {'f2c-candidate.yml': '4e7e5e2d6dbb7a68e07e210928ac5e34229eb9e1855468ac526a12857ab86886',
 'f2c-capability.yml': 'd9ad41fb54d0a6fafba39cf2bed77f3edf1466e59e56a5216669d200d04c807d',
 'f2c-docker-compiler.yml': '8bce7dc5400b148869757e7a9892e5f34c55bb99fbec8ad2aa081ecab39fa934',
 'native-acceptance.yml': '6e10045a6c0a51d840ae023edc0427bb801f6edeb99c26ce3e08f9781a02e9bd',
 'warehouse-diagnostic.yml': '3a954a01a69fdb16cb77634199164c17c2f0f6684f28a579c83e0e1a202fc775'}
WORKFLOW_SHAPES = {'f2c-candidate.yml': {'top_level_keys': ['name', 'on', 'permissions', 'concurrency', 'jobs'],
                       'jobs': {'measured-candidate': ['if', 'runs-on', 'timeout-minutes', 'env', 'steps']},
                       'concurrency': '  group: f2c-jammy-candidate-${{ github.ref }}\n'
                                      '  cancel-in-progress: false\n'},
 'f2c-capability.yml': {'top_level_keys': ['name', 'on', 'permissions', 'concurrency', 'jobs'],
                        'jobs': {'capability-only': ['if', 'runs-on', 'timeout-minutes', 'env', 'steps']},
                        'concurrency': '  group: f2c-jammy-capability-${{ github.ref }}\n'
                                       '  cancel-in-progress: false\n'},
 'f2c-docker-compiler.yml': {'top_level_keys': ['name', 'on', 'permissions', 'concurrency', 'jobs'],
                             'jobs': {'compiler-build-only': ['if', 'runs-on', 'timeout-minutes', 'steps']},
                             'concurrency': '  group: '
                                            "'urp-f2c-compiler:main:.github/workflows/f2c-docker-compiler.yml'\n"
                                            '  cancel-in-progress: false\n'},
 'native-acceptance.yml': {'top_level_keys': ['name', 'on', 'permissions', 'concurrency', 'jobs'],
                           'jobs': {'maze': ['if', 'runs-on', 'timeout-minutes', 'env', 'steps'],
                                    'warehouse': ['needs',
                                                  'if',
                                                  'runs-on',
                                                  'timeout-minutes',
                                                  'env',
                                                  'steps']},
                           'concurrency': '  group: isolated-native-acceptance\n'
                                          '  cancel-in-progress: false\n'},
 'warehouse-diagnostic.yml': {'top_level_keys': ['name', 'on', 'permissions', 'concurrency', 'jobs'],
                              'jobs': {'warehouse_diagnostic': ['if',
                                                                'runs-on',
                                                                'timeout-minutes',
                                                                'env',
                                                                'steps']},
                              'concurrency': '  group: isolated-warehouse-diagnostic\n'
                                             '  cancel-in-progress: false\n'}}


def verify_complete_contract(name, raw):
    """Fail closed on exact reviewed structure AND complete unnormalized bytes.

    This is a narrow contract for these already-parsed reviewed YAML files,
    not a general YAML parser. Unknown YAML spelling cannot bypass the complete
    raw-byte SHA256 gate. No PyYAML/runtime dependency or native work is needed.
    """
    if name not in WORKFLOW_SHA256 or not isinstance(raw, bytes):
        raise ValueError('Unknown workflow or non-byte source')
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError('Workflow must be reviewed UTF-8 bytes') from exc
    shape = WORKFLOW_SHAPES[name]
    keys = re.findall(r'^([A-Za-z_][A-Za-z0-9_-]*):', text, re.M)
    if keys != shape['top_level_keys']:
        raise ValueError('Top-level workflow keys changed or duplicated')
    events = re.search(r'^on:\n(.*?)^permissions:\n', text, re.M | re.S)
    if events is None or events.group(1) != '  workflow_dispatch:\n':
        raise ValueError('Only reviewed no-input manual dispatch is allowed')
    permissions = re.search(r'^permissions:\n(.*?)^concurrency:\n', text, re.M | re.S)
    if permissions is None or permissions.group(1) != '  contents: read\n':
        raise ValueError('Top-level permission scope changed')
    concurrency = re.search(r'^concurrency:\n(.*?)^jobs:\n', text, re.M | re.S)
    if concurrency is None or concurrency.group(1) != shape['concurrency']:
        raise ValueError('Concurrency keys, group or cancellation changed')
    if text.count('\njobs:\n') != 1:
        raise ValueError('Missing or duplicate jobs mapping')
    jobs_text = text.split('\njobs:\n', 1)[1]
    matches = list(re.finditer(r'^  ([A-Za-z_][A-Za-z0-9_-]*):\n', jobs_text, re.M))
    if [m.group(1) for m in matches] != list(shape['jobs']):
        raise ValueError('Complete jobs collection changed or duplicated')
    if not matches or matches[0].start() != 0:
        raise ValueError('Unreviewed text before first job')
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(jobs_text)
        job = jobs_text[match.end():end]
        keys = re.findall(r'^    ([A-Za-z_][A-Za-z0-9_-]*):', job, re.M)
        if keys != shape['jobs'][match.group(1)]:
            raise ValueError('Job-level keys changed or duplicated: ' + match.group(1))
    # No text normalization, reserialization, universal-newline conversion,
    # or stripping. This is the final complete-boundary publication gate.
    if hashlib.sha256(raw).hexdigest() != WORKFLOW_SHA256[name]:
        raise ValueError('Complete reviewed workflow bytes changed: ' + name)


def contract_mutations(raw):
    """Independent-review regressions plus additional complete-envelope cases."""
    text = raw.decode('utf-8')
    def before_job_key(key):
        return text.replace('    runs-on:', key + '    runs-on:', 1)
    first_job = re.search(r'^  ([A-Za-z_][A-Za-z0-9_-]*):\n', text.split('jobs:\n', 1)[1], re.M).group(1)
    mutations = {
        'added_unguarded_reusable_job': text + '  extra-reusable:\n    uses: ./.github/workflows/ci.yml\n',
        'job_write_all_permission': before_job_key('    permissions: write-all\n'),
        'job_continue_on_error': before_job_key('    continue-on-error: true\n'),
        'job_debug_environment': before_job_key('    env:\n      ACTIONS_STEP_DEBUG: true\n'),
        'job_defaults': before_job_key('    defaults:\n      run:\n        shell: bash -x {0}\n'),
        'job_strategy': before_job_key('    strategy:\n      matrix:\n        extra: [one, two]\n'),
        'job_container': before_job_key('    container: ubuntu:latest\n'),
        'job_environment': before_job_key('    environment: production\n'),
        'job_reusable_uses': before_job_key('    uses: ./.github/workflows/ci.yml\n'),
        'workflow_debug_environment': text.replace('jobs:\n', 'env:\n  ACTIONS_STEP_DEBUG: true\njobs:\n', 1),
        'workflow_defaults': text.replace('jobs:\n', 'defaults:\n  run:\n    shell: bash -x {0}\njobs:\n', 1),
        'changed_concurrency_group': re.sub(r'^  group: .*$', '  group: unreviewed-shared-group', text, count=1, flags=re.M),
        'concurrency_cancel_true': text.replace('  cancel-in-progress: false', '  cancel-in-progress: true', 1),
        'duplicate_concurrency_group': text.replace('  cancel-in-progress:', '  group: duplicate\n  cancel-in-progress:', 1),
        'duplicate_jobs_key': text + 'jobs: {}\n',
        'duplicate_on_key': text + 'on:\n  push:\n',
        'duplicate_permissions_key': text + 'permissions: write-all\n',
        'duplicate_job_id': text + '  ' + first_job + ':\n    uses: ./.github/workflows/ci.yml\n',
        'manual_dispatch_inputs': text.replace('  workflow_dispatch:\n', '  workflow_dispatch:\n    inputs:\n      unsafe:\n        type: string\n', 1),
        'push_trigger': text.replace('  workflow_dispatch:\n', '  push:\n    branches: [main]\n', 1),
        'repository_guard_removed': text.replace("github.repository == 'peakpeng666/URP'", 'true', 1),
        'job_ref_changed': text.replace("github.ref == 'refs/heads/main'", "github.ref == 'refs/heads/other'", 1),
        'job_event_changed': text.replace("github.event_name == 'workflow_dispatch'", "github.event_name == 'push'", 1),
        'duplicate_job_if': before_job_key('    if: always()\n'),
        'appended_yaml_document': text + '---\nname: extra\non: push\njobs: {}\n',
        'appended_text_even_comment': text + '# Unreviewed appended bytes\n',
        'prepended_utf8_bom': '\ufeff' + text,
        'changed_lf_to_crlf': text.replace('\n', '\r\n'),
        'missing_final_newline': text.rstrip('\n'),
        'timeout_changed': re.sub(r'timeout-minutes: [0-9]+', 'timeout-minutes: 999', text, count=1),
    }
    return {label: value.encode('utf-8') for label, value in mutations.items()}


def blocks(text):
    return re.findall(r'^    runs-on:.*?(?=^  [A-Za-z0-9_-]+:|\Z)', text, re.M | re.S)


def guards(text):
    lines = text.splitlines()
    result = []
    for i, line in enumerate(lines):
        if not line.startswith('    if: '):
            continue
        guard = line[len('    if: '):]
        if guard == '>-':
            parts = []
            for next_line in lines[i + 1:]:
                if not next_line.startswith('      '):
                    break
                parts.append(next_line.strip())
            guard = ' '.join(parts)
        result.append(guard.removeprefix('${{').removesuffix('}}').strip())
    return result


def evaluate_guard(guard, context):
    """Evaluate only this small, fail-closed boolean comparison grammar."""
    source = guard.replace('&&', ' and ').replace('||', ' or ')
    for name in sorted(context, key=len, reverse=True):
        source = source.replace(name, repr(context[name]))
    tree = ast.parse(source, mode='eval')
    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bool)):
            return node.value
        if isinstance(node, ast.BoolOp):
            values = [visit(v) for v in node.values]
            if isinstance(node.op, ast.And): return all(values)
            if isinstance(node.op, ast.Or): return any(values)
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            return visit(node.left) == visit(node.comparators[0])
        raise ValueError('Unexpected guard syntax: ' + type(node).__name__)
    return bool(visit(tree))


class HistoricalWorkflowConsumerTests(unittest.TestCase):
    def test_complete_reviewed_workflow_bytes_and_structure(self):
        for name in WORKFLOW_SHA256:
            with self.subTest(workflow=name):
                verify_complete_contract(name, (ROOT / '.github/workflows' / name).read_bytes())

    def test_complete_contract_rejects_all_envelope_mutations(self):
        for name in WORKFLOW_SHA256:
            raw = (ROOT / '.github/workflows' / name).read_bytes()
            verify_complete_contract(name, raw)
            mutations = contract_mutations(raw)
            self.assertEqual(len(mutations), 30)
            for label, mutated in mutations.items():
                with self.subTest(workflow=name, mutation=label):
                    self.assertNotEqual(mutated, raw)
                    with self.assertRaises(ValueError):
                        verify_complete_contract(name, mutated)

    def workflows(self):
        for name in BODY_HASHES:
            yield name, (ROOT / '.github/workflows' / name).read_bytes().decode('utf-8')

    def test_only_manual_dispatch_and_read_permission(self):
        for name, text in self.workflows():
            with self.subTest(workflow=name):
                envelope = re.search(r'^on:\n(.*?)^permissions:\n', text, re.M | re.S).group(1)
                self.assertEqual(envelope, '  workflow_dispatch:\n')
                permission = re.search(r'^permissions:\n(.*?)^concurrency:\n', text, re.M | re.S).group(1)
                self.assertEqual(permission, '  contents: read\n')
                self.assertNotIn('cancel-in-progress: true', text)
                self.assertIn('cancel-in-progress: false', text)
                self.assertNotIn('fix/agriculture-compatibility-20261022', text)

    def test_every_job_accepts_only_main_manual_in_own_repository(self):
        for name, text in self.workflows():
            found = guards(text)
            self.assertEqual(len(found), len(BODY_HASHES[name]))
            for guard in found:
                context = {'github.event_name': 'workflow_dispatch',
                           'github.repository': 'peakpeng666/URP',
                           'github.ref': 'refs/heads/main',
                           'github.workflow_ref': 'peakpeng666/URP/.github/workflows/' + name + '@refs/heads/main',
                           'needs.maze.result': 'success'}
                self.assertTrue(evaluate_guard(guard, context))
                for variable, bad in [('github.event_name','push'),('github.event_name','pull_request'),
                                      ('github.event_name','workflow_call'),('github.repository','fork/URP'),
                                      ('github.ref','refs/heads/deploy'),('github.ref','refs/heads/redo'),
                                      ('github.ref','refs/heads/fix/agriculture-compatibility-20261022'),
                                      ('github.ref','refs/tags/main')]:
                    with self.subTest(workflow=name,variable=variable,bad=bad):
                        self.assertFalse(evaluate_guard(guard, context | {variable:bad}))
                if 'github.workflow_ref' in guard:
                    self.assertFalse(evaluate_guard(guard, context | {'github.workflow_ref':'other/workflow'}))
                if 'needs.maze.result' in guard:
                    for result in ['failure','cancelled','skipped']:
                        self.assertFalse(evaluate_guard(guard, context | {'needs.maze.result':result}))
        native = (ROOT / '.github/workflows/native-acceptance.yml').read_text()
        self.assertIn('  warehouse:\n    needs: maze\n', native)

    def test_all_scientific_bodies_pins_and_boundaries_unchanged(self):
        # Includes timeout, env, SHA/index pins, checkout options, every run/step,
        # no-upload/reducer controls, isolation, and failure propagation.
        for name, text in self.workflows():
            actual = [hashlib.sha256(body.encode()).hexdigest() for body in blocks(text)]
            self.assertEqual(actual, BODY_HASHES[name], name + ': body changed; independent review required')

    def test_no_expanded_publish_permissions_or_actions(self):
        for name, text in self.workflows():
            for forbidden in ['contents: write', 'packages: write', 'id-token: write',
                              'pull_request_target:', 'actions/upload-artifact@',
                              'actions/cache@', 'docker push ', 'gh release ', 'git push ',
                              'persist-credentials: true', 'secrets.']:
                with self.subTest(workflow=name,forbidden=forbidden):
                    self.assertNotIn(forbidden, text)


if __name__ == '__main__':
    unittest.main()
