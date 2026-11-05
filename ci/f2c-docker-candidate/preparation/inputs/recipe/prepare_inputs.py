#!/usr/bin/env python3
"""Future image-only trusted materializer. No application hooks or native build."""
import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import platform
import stat
import re
import sys
import sysconfig

SOURCE_REQUIRED = {
    'official-source-inputs.json', 'build-requirements.lock', 'recipe/build-wheel-inputs.json',
    'recipe/prepare_inputs.py', 'recipe/live_inputs.py', 'recipe/runtime_prepare.py',
    'recipe/guardlib.py', 'recipe/verify_prepared_inputs.py', 'source-provenance.json',
}

# Failure-only finite projection. No exception string or external value is emitted.
DIAGNOSTIC_STAGE = 'inputs_prepare'
DIAGNOSTIC_SUBSTAGES = frozenset(['entry', 'source_anchor', 'guard_load', 'root_identity', 'fresh_state', 'helper_load', 'python_prefix', 'bootstrap_verify', 'application_verify', 'validation_absent', 'package_lock', 'signed_metadata_bytes', 'package_archive_bytes', 'abi_evidence', 'source_manifest', 'source_materialize', 'wheel_manifest', 'wheel_materialize', 'interface_materialize', 'command_measure', 'command_archive_bytes', 'runtime_provider_prepare', 'provider_archive_bytes', 'provider_evidence_write', 'host_snapshot', 'input_snapshot', 'lock_assembly', 'prepared_verifier_load', 'prepared_verify', 'lock_write', 'artifact_index_write', 'artifact_index_verify', 'anchor_write'])
DIAGNOSTIC_MESSAGES = {'Actual host is not Jammy': 'ACTUAL_HOST_IS_NOT_JAMMY', 'Aliased bootstrap executable path': 'ALIASED_BOOTSTRAP_EXECUTABLE_PATH', 'Application source must be a real directory': 'APPLICATION_SOURCE_MUST_BE_A_REAL_DIRECTORY', 'Archive has multiple top-level roots': 'ARCHIVE_HAS_MULTIPLE_TOP_LEVEL_ROOTS', 'Archive root is not a directory': 'ARCHIVE_ROOT_IS_NOT_A_DIRECTORY', 'Artifact index anchor mismatch': 'ARTIFACT_INDEX_ANCHOR_MISMATCH', 'Bootstrap receipt changed': 'BOOTSTRAP_RECEIPT_CHANGED', 'Build output already exists; use a fresh container': 'BUILD_OUTPUT_ALREADY_EXISTS_USE_A_FRESH_CONTAINER', 'Build wheel does not match reviewed requirements': 'BUILD_WHEEL_DOES_NOT_MATCH_REVIEWED_REQUIREMENTS', 'Build wheel filename must be a basename': 'BUILD_WHEEL_FILENAME_MUST_BE_A_BASENAME', 'Build wheel reference set changed': 'BUILD_WHEEL_REFERENCE_SET_CHANGED', 'CMake interface is outside its exact-tree lock': 'CMAKE_INTERFACE_IS_OUTSIDE_ITS_EXACT_TREE_LOCK', 'Changed F2C source identity': 'CHANGED_F2C_SOURCE_IDENTITY', 'Closure exclusions forbidden': 'CLOSURE_EXCLUSIONS_FORBIDDEN', 'Compiler stage requires exactly the selected Lab declaration and lock files': 'COMPILER_STAGE_REQUIRES_EXACTLY_THE_SELECTED_LAB_DECLARATION_AND_LOCK_FILES', 'Compiler-only preparation required': 'COMPILER_ONLY_PREPARATION_REQUIRED', 'Compiler-only preparation requires validation-data absent': 'COMPILER_ONLY_PREPARATION_REQUIRES_VALIDATION_DATA_ABSENT', 'Conflicting official package providers': 'CONFLICTING_OFFICIAL_PACKAGE_PROVIDERS', 'Declare snapshot reproducibility scope': 'DECLARE_SNAPSHOT_REPRODUCIBILITY_SCOPE', 'Docker image input preparation incomplete': 'DOCKER_IMAGE_INPUT_PREPARATION_INCOMPLETE', 'Downloaded source hash/size mismatch': 'DOWNLOADED_SOURCE_HASH_SIZE_MISMATCH', 'Duplicate ZIP member': 'DUPLICATE_ZIP_MEMBER', 'Duplicate archive member': 'DUPLICATE_ARCHIVE_MEMBER', 'Duplicate source-index key': 'DUPLICATE_SOURCE_INDEX_KEY', 'Duplicate wheel members': 'DUPLICATE_WHEEL_MEMBERS', 'Empty/unprepared tree lock': 'EMPTY_UNPREPARED_TREE_LOCK', 'Existing download differs': 'EXISTING_DOWNLOAD_DIFFERS', 'Expected relative path': 'EXPECTED_RELATIVE_PATH', 'Extracted provider wheel differs': 'EXTRACTED_PROVIDER_WHEEL_DIFFERS', 'Extraction destination already exists': 'EXTRACTION_DESTINATION_ALREADY_EXISTS', 'Failed to read official package payload': 'FAILED_TO_READ_OFFICIAL_PACKAGE_PAYLOAD', 'Foreign package closure': 'FOREIGN_PACKAGE_CLOSURE', 'GLIBC floor exceeds Jammy': 'GLIBC_FLOOR_EXCEEDS_JAMMY', 'Git state must not enter application inputs': 'GIT_STATE_MUST_NOT_ENTER_APPLICATION_INPUTS', 'Group/world writable tree entry': 'GROUP_WORLD_WRITABLE_TREE_ENTRY', 'Hard-linked input is not immutable in isolation': 'HARD_LINKED_INPUT_IS_NOT_IMMUTABLE_IN_ISOLATION', 'Helper anchor mismatch': 'HELPER_ANCHOR_MISMATCH', 'Host alias policy must be explicit and host-only': 'HOST_ALIAS_POLICY_MUST_BE_EXPLICIT_AND_HOST_ONLY', 'Incomplete bootstrap tool receipt': 'INCOMPLETE_BOOTSTRAP_TOOL_RECEIPT', 'Incomplete host command lock': 'INCOMPLETE_HOST_COMMAND_LOCK', 'Incomplete index': 'INCOMPLETE_INDEX', 'Incomplete package closure': 'INCOMPLETE_PACKAGE_CLOSURE', 'Incomplete upstream source closure': 'INCOMPLETE_UPSTREAM_SOURCE_CLOSURE', 'Input materialization must occur at final /inputs': 'INPUT_MATERIALIZATION_MUST_OCCUR_AT_FINAL_INPUTS', 'Input must be candidate-local': 'INPUT_MUST_BE_CANDIDATE_LOCAL', 'Input preparation cannot build native sources': 'INPUT_PREPARATION_CANNOT_BUILD_NATIVE_SOURCES', 'Installed command/provider differs from official package bytes': 'INSTALLED_COMMAND_PROVIDER_DIFFERS_FROM_OFFICIAL_PACKAGE_BYTES', 'Interface must be candidate-local': 'INTERFACE_MUST_BE_CANDIDATE_LOCAL', 'Interpreter patch version differs': 'INTERPRETER_PATCH_VERSION_DIFFERS', 'Invalid SHA256': 'INVALID_SHA256', 'Invalid external anchor': 'INVALID_EXTERNAL_ANCHOR', 'Invalid path reference': 'INVALID_PATH_REFERENCE', 'Invalid reviewed source index': 'INVALID_REVIEWED_SOURCE_INDEX', 'Invalid source review digest': 'INVALID_SOURCE_REVIEW_DIGEST', 'Legacy same-runner preparation prohibited': 'LEGACY_SAME_RUNNER_PREPARATION_PROHIBITED', 'Materialize as final nonroot workload owner': 'MATERIALIZE_AS_FINAL_NONROOT_WORKLOAD_OWNER', 'Measured image environment required': 'MEASURED_IMAGE_ENVIRONMENT_REQUIRED', 'Measurement path is not absolute': 'MEASUREMENT_PATH_IS_NOT_ABSOLUTE', 'Mislabelled host scope': 'MISLABELLED_HOST_SCOPE', 'Missing actual Jammy system zlib alternative': 'MISSING_ACTUAL_JAMMY_SYSTEM_ZLIB_ALTERNATIVE', 'Missing authenticated package provenance': 'MISSING_AUTHENTICATED_PACKAGE_PROVENANCE', 'Missing complete build wheelhouse': 'MISSING_COMPLETE_BUILD_WHEELHOUSE', 'Missing conservative disk floor': 'MISSING_CONSERVATIVE_DISK_FLOOR', 'Missing indexed helper/lock': 'MISSING_INDEXED_HELPER_LOCK', 'Missing link-time wheel zlib selection': 'MISSING_LINK_TIME_WHEEL_ZLIB_SELECTION', 'Missing loader/configuration closure files': 'MISSING_LOADER_CONFIGURATION_CLOSURE_FILES', 'Missing measured repository identity': 'MISSING_MEASURED_REPOSITORY_IDENTITY', 'Missing measured system CMake modules': 'MISSING_MEASURED_SYSTEM_CMAKE_MODULES', 'Missing measurement input': 'MISSING_MEASUREMENT_INPUT', 'Missing native system/provider closure': 'MISSING_NATIVE_SYSTEM_PROVIDER_CLOSURE', 'Missing official package byte provenance for selected command/provider': 'MISSING_OFFICIAL_PACKAGE_BYTE_PROVENANCE_FOR_SELECTED_COMMAND_PROVIDER', 'Missing review evidence': 'MISSING_REVIEW_EVIDENCE', 'Missing reviewed preparer/source input': 'MISSING_REVIEWED_PREPARER_SOURCE_INPUT', 'Missing source provenance': 'MISSING_SOURCE_PROVENANCE', 'Missing versioned dependency': 'MISSING_VERSIONED_DEPENDENCY', 'Missing/duplicate closure scope': 'MISSING_DUPLICATE_CLOSURE_SCOPE', 'Multiple/foreign OR-Tools provider': 'MULTIPLE_FOREIGN_OR_TOOLS_PROVIDER', 'Must verify actual OS release file': 'MUST_VERIFY_ACTUAL_OS_RELEASE_FILE', 'NUL in path': 'NUL_IN_PATH', 'No selected archive input files': 'NO_SELECTED_ARCHIVE_INPUT_FILES', 'Non-wheel OR-Tools provider': 'NON_WHEEL_OR_TOOLS_PROVIDER', 'Nonregular source member': 'NONREGULAR_SOURCE_MEMBER', 'Not an Ubuntu Jammy x64 lock': 'NOT_AN_UBUNTU_JAMMY_X64_LOCK', 'OR-Tools compile options changed': 'OR_TOOLS_COMPILE_OPTIONS_CHANGED', 'OR-Tools interface definitions changed': 'OR_TOOLS_INTERFACE_DEFINITIONS_CHANGED', 'Only regular compiler source inputs are allowed': 'ONLY_REGULAR_COMPILER_SOURCE_INPUTS_ARE_ALLOWED', 'Only regular indexed compiler inputs are allowed': 'ONLY_REGULAR_INDEXED_COMPILER_INPUTS_ARE_ALLOWED', 'Output must be /out': 'OUTPUT_MUST_BE_OUT', 'Oversized archive member': 'OVERSIZED_ARCHIVE_MEMBER', 'Package authentication/closure incomplete': 'PACKAGE_AUTHENTICATION_CLOSURE_INCOMPLETE', 'Package installation not verified': 'PACKAGE_INSTALLATION_NOT_VERIFIED', 'Package lock is transaction-only, not complete relevant dependency closure': 'PACKAGE_LOCK_IS_TRANSACTION_ONLY_NOT_COMPLETE_RELEVANT_DEPENDENCY_CLOSURE', 'Package lock must be candidate-local': 'PACKAGE_LOCK_MUST_BE_CANDIDATE_LOCAL', 'Package preparation did not verify installation': 'PACKAGE_PREPARATION_DID_NOT_VERIFY_INSTALLATION', 'Package provenance covers only transaction delta': 'PACKAGE_PROVENANCE_COVERS_ONLY_TRANSACTION_DELTA', 'Preparation cannot establish native ABI': 'PREPARATION_CANNOT_ESTABLISH_NATIVE_ABI', 'Preparation requires actual Ubuntu22.04': 'PREPARATION_REQUIRES_ACTUAL_UBUNTU22_04', 'Preparation requires exactly CPython 3.12.13': 'PREPARATION_REQUIRES_EXACTLY_CPYTHON_3_12_13', 'Prepared inputs must remain at /inputs': 'PREPARED_INPUTS_MUST_REMAIN_AT_INPUTS', 'Protobuf propagated DLL definitions changed': 'PROTOBUF_PROPAGATED_DLL_DEFINITIONS_CHANGED', 'Provider extraction is not exact-tree locked': 'PROVIDER_EXTRACTION_IS_NOT_EXACT_TREE_LOCKED', 'Provider extraction must be candidate-local': 'PROVIDER_EXTRACTION_MUST_BE_CANDIDATE_LOCAL', 'Provider wheel layout changed': 'PROVIDER_WHEEL_LAYOUT_CHANGED', 'Python must be image-resident /opt/urp-python/bin/python3.12': 'PYTHON_MUST_BE_IMAGE_RESIDENT_OPT_URP_PYTHON_BIN_PYTHON3_12', 'Python must remain inside image': 'PYTHON_MUST_REMAIN_INSIDE_IMAGE', 'Python prefix must be canonical and absolute': 'PYTHON_PREFIX_MUST_BE_CANONICAL_AND_ABSOLUTE', 'Require exactly CPython 3.12.13': 'REQUIRE_EXACTLY_CPYTHON_3_12_13', 'Reviewed source index anchor mismatch': 'REVIEWED_SOURCE_INDEX_ANCHOR_MISMATCH', 'Reviewed wheel-owned zlib provider missing': 'REVIEWED_WHEEL_OWNED_ZLIB_PROVIDER_MISSING', 'Root contains unsupported CMake/linker path delimiters': 'ROOT_CONTAINS_UNSUPPORTED_CMAKE_LINKER_PATH_DELIMITERS', 'Root is group/world writable': 'ROOT_IS_GROUP_WORLD_WRITABLE', 'Root must be caller-owned directory': 'ROOT_MUST_BE_CALLER_OWNED_DIRECTORY', 'Root must be canonical/absolute': 'ROOT_MUST_BE_CANONICAL_ABSOLUTE', 'Selected Python differs from reviewed executable': 'SELECTED_PYTHON_DIFFERS_FROM_REVIEWED_EXECUTABLE', 'Source archive filename must be a basename': 'SOURCE_ARCHIVE_FILENAME_MUST_BE_A_BASENAME', 'Source download exceeds pinned size': 'SOURCE_DOWNLOAD_EXCEEDS_PINNED_SIZE', 'Source index symlink refused': 'SOURCE_INDEX_SYMLINK_REFUSED', 'Special application source entry': 'SPECIAL_APPLICATION_SOURCE_ENTRY', 'Special file in input tree': 'SPECIAL_FILE_IN_INPUT_TREE', 'Special symlink referent': 'SPECIAL_SYMLINK_REFERENT', 'Symlink escapes allowed root': 'SYMLINK_ESCAPES_ALLOWED_ROOT', 'Symlink root/ancestor': 'SYMLINK_ROOT_ANCESTOR', 'System library must not be bundled': 'SYSTEM_LIBRARY_MUST_NOT_BE_BUNDLED', 'System zlib aliases the wheel provider': 'SYSTEM_ZLIB_ALIASES_THE_WHEEL_PROVIDER', 'Tree root has symlink ancestry or escapes allowed root': 'TREE_ROOT_HAS_SYMLINK_ANCESTRY_OR_ESCAPES_ALLOWED_ROOT', 'Tree root must be a real directory': 'TREE_ROOT_MUST_BE_A_REAL_DIRECTORY', 'Tree scopes missing': 'TREE_SCOPES_MISSING', 'Unapproved source download URL': 'UNAPPROVED_SOURCE_DOWNLOAD_URL', 'Unbounded ELF dependency closure': 'UNBOUNDED_ELF_DEPENDENCY_CLOSURE', 'Unbounded archive extraction': 'UNBOUNDED_ARCHIVE_EXTRACTION', 'Unbounded source download': 'UNBOUNDED_SOURCE_DOWNLOAD', 'Unbounded wheel extraction': 'UNBOUNDED_WHEEL_EXTRACTION', 'Unexpected CPython ABI': 'UNEXPECTED_CPYTHON_ABI', 'Unexpected OR-Tools C++ feature contract': 'UNEXPECTED_OR_TOOLS_C_FEATURE_CONTRACT', 'Unexpected application code in compiler preparation': 'UNEXPECTED_APPLICATION_CODE_IN_COMPILER_PREPARATION', 'Unexpected bootstrap receipt fields': 'UNEXPECTED_BOOTSTRAP_RECEIPT_FIELDS', 'Unexpected image Python prefix': 'UNEXPECTED_IMAGE_PYTHON_PREFIX', 'Unexpected package architecture': 'UNEXPECTED_PACKAGE_ARCHITECTURE', 'Unexpected preparation interpreter/platform': 'UNEXPECTED_PREPARATION_INTERPRETER_PLATFORM', 'Unexpected source reference set': 'UNEXPECTED_SOURCE_REFERENCE_SET', 'Unexpected validation inputs in compiler-only preparation': 'UNEXPECTED_VALIDATION_INPUTS_IN_COMPILER_ONLY_PREPARATION', 'Unhashed build requirements': 'UNHASHED_BUILD_REQUIREMENTS', 'Unknown path base': 'UNKNOWN_PATH_BASE', 'Unresolved or foreign package pin': 'UNRESOLVED_OR_FOREIGN_PACKAGE_PIN', 'Unreviewed build settings': 'UNREVIEWED_BUILD_SETTINGS', 'Unreviewed package-root provider substitution': 'UNREVIEWED_PACKAGE_ROOT_PROVIDER_SUBSTITUTION', 'Unsafe NEEDED name': 'UNSAFE_NEEDED_NAME', 'Unsafe relative path': 'UNSAFE_RELATIVE_PATH', 'Unsafe source-index path': 'UNSAFE_SOURCE_INDEX_PATH', 'Verifier running with wrong Python': 'VERIFIER_RUNNING_WITH_WRONG_PYTHON', 'Wheel zlib differs from selected provider': 'WHEEL_ZLIB_DIFFERS_FROM_SELECTED_PROVIDER', 'Writable bootstrap executable': 'WRITABLE_BOOTSTRAP_EXECUTABLE', 'Writable/symlink source input': 'WRITABLE_SYMLINK_SOURCE_INPUT', 'Wrong Docker input/control/output layout': 'WRONG_DOCKER_INPUT_CONTROL_OUTPUT_LAYOUT', 'Wrong Docker isolation protocol': 'WRONG_DOCKER_ISOLATION_PROTOCOL', 'Wrong Jammy native SONAME contract': 'WRONG_JAMMY_NATIVE_SONAME_CONTRACT', 'Wrong OR-Tools runtime wheel': 'WRONG_OR_TOOLS_RUNTIME_WHEEL', 'Wrong Ubuntu22.04 interface archive': 'WRONG_UBUNTU22_04_INTERFACE_ARCHIVE', 'Wrong application source anchor': 'WRONG_APPLICATION_SOURCE_ANCHOR', 'Wrong application source revision': 'WRONG_APPLICATION_SOURCE_REVISION', 'Wrong bootstrap tool version': 'WRONG_BOOTSTRAP_TOOL_VERSION', 'Wrong closure base': 'WRONG_CLOSURE_BASE', 'Wrong host architecture/OS': 'WRONG_HOST_ARCHITECTURE_OS', 'Wrong selected application provenance': 'WRONG_SELECTED_APPLICATION_PROVENANCE', 'Wrong selected application scope': 'WRONG_SELECTED_APPLICATION_SCOPE', 'ZIP alias/special file refused': 'ZIP_ALIAS_SPECIAL_FILE_REFUSED', 'Zlib alternative exceeds Jammy GLIBC floor': 'ZLIB_ALTERNATIVE_EXCEEDS_JAMMY_GLIBC_FLOOR', 'Zlib alternative has wrong provenance scope': 'ZLIB_ALTERNATIVE_HAS_WRONG_PROVENANCE_SCOPE'}
DIAGNOSTIC_PREFIXES = {'Bootstrap executable bytes changed: ': 'BOOTSTRAP_EXECUTABLE_BYTES_CHANGED',
 'Changed file alias: ': 'CHANGED_FILE_ALIAS',
 'Changed file resolution: ': 'CHANGED_FILE_RESOLUTION',
 'Changed locked file: ': 'CHANGED_LOCKED_FILE',
 'Duplicate JSON key: ': 'DUPLICATE_JSON_KEY',
 'ELF symbol-version requirement unsatisfied: ': 'ELF_SYMBOL_VERSION_REQUIREMENT_UNSATISFIED',
 'Group/world writable indexed path: ': 'GROUP_WORLD_WRITABLE_INDEXED_PATH',
 'Indexed artifact mismatch: ': 'INDEXED_ARTIFACT_MISMATCH',
 'Installed package version differs: ': 'INSTALLED_PACKAGE_VERSION_DIFFERS',
 'Missing independent review: ': 'MISSING_INDEPENDENT_REVIEW',
 'Missing locked file: ': 'MISSING_LOCKED_FILE',
 'Missing prepared section: ': 'MISSING_PREPARED_SECTION',
 'Missing regular file: ': 'MISSING_REGULAR_FILE',
 'Missing reviewed CMake target: ': 'MISSING_REVIEWED_CMAKE_TARGET',
 'Prepared state already exists: ': 'PREPARED_STATE_ALREADY_EXISTS',
 'Reviewed source input changed: ': 'REVIEWED_SOURCE_INPUT_CHANGED',
 'Symlink in indexed path: ': 'SYMLINK_IN_INDEXED_PATH',
 'Tree membership/content changed: ': 'TREE_MEMBERSHIP_CONTENT_CHANGED',
 'Unexpected SONAME alias: ': 'UNEXPECTED_SONAME_ALIAS',
 'Unexpected zlib alternative SONAME: ': 'UNEXPECTED_ZLIB_ALTERNATIVE_SONAME',
 'Unresolved ELF dependency: ': 'UNRESOLVED_ELF_DEPENDENCY',
 'Writable locked file: ': 'WRITABLE_LOCKED_FILE',
 'Wrong bootstrap tool path/provenance: ': 'WRONG_BOOTSTRAP_TOOL_PATH_PROVENANCE',
 'Wrong measured command: ': 'WRONG_MEASURED_COMMAND',
 'Zlib alternative dependency versions are unsatisfied: ': 'ZLIB_ALTERNATIVE_DEPENDENCY_VERSIONS_ARE_UNSATISFIED',
 'Zlib alternative expands unmeasured dependency closure: ': 'ZLIB_ALTERNATIVE_EXPANDS_UNMEASURED_DEPENDENCY_CLOSURE',
 'Zlib alternative lacks required symbol versions: ': 'ZLIB_ALTERNATIVE_LACKS_REQUIRED_SYMBOL_VERSIONS'}
DIAGNOSTIC_CLASSES = frozenset(('RuntimeError', 'CalledProcessError', 'FileNotFoundError', 'PermissionError', 'FileExistsError', 'OSError', 'ValueError', 'TypeError', 'KeyError', 'JSONDecodeError', 'UnicodeDecodeError', 'ImportError', 'ModuleNotFoundError', 'AttributeError', 'TimeoutError', 'OtherError'))
_DIAGNOSTIC_SUBSTAGE = 'entry'

def diagnostic_phase(value):
    global _DIAGNOSTIC_SUBSTAGE
    _DIAGNOSTIC_SUBSTAGE = value if value in DIAGNOSTIC_SUBSTAGES else 'entry'

def diagnostic_detail(error):
    code = 'UNKNOWN_FAILURE'
    if type(error) is RuntimeError and len(error.args) == 1 and type(error.args[0]) is str:
        code = DIAGNOSTIC_MESSAGES.get(error.args[0], code)
        if code == 'UNKNOWN_FAILURE':
            for prefix in sorted(DIAGNOSTIC_PREFIXES, key=len, reverse=True):
                if error.args[0].startswith(prefix):
                    code = DIAGNOSTIC_PREFIXES[prefix]
                    break
    name = type(error).__name__
    return {'schema': 1, 'stage': DIAGNOSTIC_STAGE,
            'substage': _DIAGNOSTIC_SUBSTAGE if _DIAGNOSTIC_SUBSTAGE in DIAGNOSTIC_SUBSTAGES else 'entry',
            'code': code, 'exception_class': name if name in DIAGNOSTIC_CLASSES else 'OtherError'}

def emit_diagnostic(error):
    try:
        print('SAFE_PREPARATION_DIAGNOSTIC:' + json.dumps(diagnostic_detail(error), sort_keys=True, separators=(',', ':')), file=sys.stderr)
    except Exception:
        pass  # Diagnostic failure never replaces the original unsuccessful outcome.


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def source_anchor(root, expected):
    """No local helper import before this independent reviewed source anchor."""
    def need(value, message):
        if not value:
            raise RuntimeError(message)
    def unique(pairs):
        value = {}
        for key, entry in pairs:
            need(key not in value, 'Duplicate source-index key')
            value[key] = entry
        return value
    need(re.fullmatch('[0-9a-f]{64}', expected), 'Invalid source review digest')
    index_path = root / 'source-index.json'
    need(not index_path.is_symlink(), 'Source index symlink refused')
    raw = index_path.read_bytes()
    need(hashlib.sha256(raw).hexdigest() == expected, 'Reviewed source index anchor mismatch')
    index = json.loads(raw, object_pairs_hook=unique)
    need(index.get('schema') == 1 and index.get('status') == 'reviewed-source', 'Invalid reviewed source index')
    need(index.get('application_commit') == '3acf840d1a0c16ab1cfcca15744e4ea6609ddb35', 'Wrong application source anchor')
    need(SOURCE_REQUIRED.issubset(index['files']), 'Missing reviewed preparer/source input')
    for name, entry in index['files'].items():
        need(isinstance(name, str) and not name.startswith('/') and all(x not in ('', '.', '..') for x in name.split('/')), 'Unsafe source-index path')
        path = root
        need(isinstance(entry, dict) and set(entry) == {'sha256'}, 'Only regular compiler source inputs are allowed')
        for component in name.split('/'):
            path /= component
            need(not path.is_symlink() and not path.stat().st_mode & 0o022, 'Writable/symlink source input')
        need(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256'], 'Reviewed source input changed: ' + name)
    return index

def measure_scope(g, root, prefix, name, path, host=False):
    live = sys.modules['live_inputs']
    allowed = root if not host else (prefix if path.is_relative_to(prefix) else pathlib.Path('/'))
    return {'name': name, 'ref': live.path_ref(path, root, prefix), 'host_aliases': host,
            'entries': g.snapshot(path, allowed, host_aliases=host)}

def verify_application(root, index, g):
    """Only current Lab declaration and lock files, without Git or source-code execution."""
    application = root / 'application'
    g.require(application.is_dir() and not application.is_symlink(), 'Application source must be a real directory')
    git = application / '.git'
    g.require(not git.exists() and not git.is_symlink(), 'Git state must not enter application inputs')
    members = set()
    for path in application.rglob('*'):
        mode = path.lstat().st_mode
        if stat.S_ISREG(mode) or stat.S_ISLNK(mode):
            members.add(path.relative_to(root).as_posix())
        else:
            g.require(stat.S_ISDIR(mode), 'Special application source entry')
    expected = {name for name in index['files'] if name.startswith('application/')}
    g.require(members == expected == g.APPLICATION_FILES, 'Compiler stage requires exactly the selected Lab declaration and lock files')
    for name in sorted(members):
        g.verify_index_entry(root, name, index['files'][name])
    return members


def validation_inputs(root, g):
    """This compiler preparation never accepts/provisions validation dependencies."""
    path = root / 'validation-data'
    g.require(not path.exists() and not path.is_symlink(), 'Compiler-only preparation requires validation-data absent')


def prepare(root, python, package_lock, source_digest):
    diagnostic_phase('source_anchor')
    index = source_anchor(root, source_digest)
    diagnostic_phase('guard_load')
    g = load('guardlib', root / 'recipe/guardlib.py')
    diagnostic_phase('root_identity')
    g.root_guard(root)
    g.require(root == pathlib.Path('/inputs'), 'Input materialization must occur at final /inputs')
    g.require(os.geteuid() > 0 and os.getegid() > 0, 'Materialize as final nonroot workload owner')
    g.require(python == g.PYTHON_EXECUTABLE, 'Python must be image-resident /opt/urp-python/bin/python3.12')
    g.require(sys.version_info[:3] == (3, 12, 13), 'Preparation requires exactly CPython 3.12.13')
    g.require(pathlib.Path(sys.executable) == python and sys.platform == 'linux' and platform.machine() == 'x86_64', 'Unexpected preparation interpreter/platform')
    release = dict(line.split('=', 1) for line in pathlib.Path('/etc/os-release').read_text().splitlines() if '=' in line)
    g.require(release.get('ID', '').strip('"') == 'ubuntu' and release.get('VERSION_ID', '').strip('"') == '22.04', 'Preparation requires actual Ubuntu22.04')
    diagnostic_phase('fresh_state')
    for name in ('output', 'artifact-index.json', 'reviewed-inputs.lock.json', 'prepared-anchor.json', 'sources', 'provider', 'interface', 'preparation-evidence'):
        g.require(not (root / name).exists() and not (root / name).is_symlink(), 'Prepared state already exists: ' + name)
    g.require(package_lock.is_relative_to(root) and not package_lock.is_symlink(), 'Package lock must be candidate-local')
    diagnostic_phase('helper_load')
    live = load('live_inputs', root / 'recipe/live_inputs.py')
    runtime = load('runtime_prepare', root / 'recipe/runtime_prepare.py')
    diagnostic_phase('python_prefix')
    prefix = pathlib.Path(sys.base_prefix).resolve()
    g.require(prefix == g.PYTHON_PREFIX, 'Unexpected image Python prefix')
    diagnostic_phase('bootstrap_verify')
    bootstrap_path = g.regular_under(root, 'bootstrap-tools.json')
    bootstrap = g.verify_bootstrap(g.read_json(bootstrap_path))
    # Application code is anchored by the reviewed source assembly, not Git
    # metadata or untrusted code execution inside the prepared image.
    diagnostic_phase('application_verify')
    verify_application(root, index, g)
    diagnostic_phase('validation_absent')
    validation_inputs(root, g)
    diagnostic_phase('package_lock')
    packages = g.read_json(package_lock)
    g.require(packages.get('distribution') == 'ubuntu' and packages.get('release') == '22.04' and packages.get('installed') is True, 'Package preparation did not verify installation')
    g.require(packages.get('signed_metadata_verified') is True and packages.get('resolved'), 'Package authentication/closure incomplete')
    if packages['snapshot']['kind'] == 'ubuntu-snapshot':
        packages['snapshot']['kind'] = 'immutable-signed-snapshot'
    diagnostic_phase('signed_metadata_bytes')
    for row in packages['signed_metadata']:
        g.verify_candidate_file(root, prefix, row)
    diagnostic_phase('package_archive_bytes')
    for row in packages['resolved']:
        g.verify_candidate_file(root, prefix, row['archive'])
    g.require(packages.get('closure_complete') is True, 'Package lock is transaction-only, not complete relevant dependency closure')
    diagnostic_phase('abi_evidence')
    (root / 'preparation-evidence').mkdir(mode=0o700)
    abi_evidence = root / 'preparation-evidence/python-abi.json'
    abi = {'version': platform.python_version(), 'implementation': platform.python_implementation(),
           'SOABI': sysconfig.get_config_var('SOABI'), 'EXT_SUFFIX': sysconfig.get_config_var('EXT_SUFFIX')}
    g.require(abi['implementation'] == 'CPython' and abi['SOABI'] == 'cpython-312-x86_64-linux-gnu'
              and abi['EXT_SUFFIX'] == '.cpython-312-x86_64-linux-gnu.so', 'Unexpected CPython ABI')
    live.write_json_new(abi_evidence, abi)
    diagnostic_phase('source_manifest')
    source_rows = g.read_json(root / 'official-source-inputs.json')['sources']
    expected_names = {'fields2cover', 'steering_functions', 'matplot', 'json', 'ortools-jammy-headers', 'ortools-python-runtime'}
    g.require(len(source_rows) == len(expected_names) and {row['name'] for row in source_rows} == expected_names, 'Unexpected source reference set')
    archives, source_locks = {}, {}
    diagnostic_phase('source_materialize')
    for row in source_rows:
        name = row['name']
        filename = row.get('filename', name + '.tar.gz')
        g.relative_path(filename)
        g.require('/' not in filename, 'Source archive filename must be a basename')
        destination = root / 'inputs/archives' / filename
        live.fetch(row, destination)
        archives[name] = destination
        if name in ('fields2cover', 'steering_functions', 'matplot', 'json'):
            live.tar_data_only(destination, root / 'sources' / name)
            source_locks[name] = {'archive': live.file_row(destination, root, prefix), 'commit': row['commit'], 'source_url': row['url']}
    diagnostic_phase('wheel_manifest')
    build_rows = g.read_json(root / 'recipe/build-wheel-inputs.json')['wheels']
    requirement_text = (root / 'build-requirements.lock').read_text()
    g.require(len(build_rows) == 9, 'Build wheel reference set changed')
    build_wheels = []
    diagnostic_phase('wheel_materialize')
    for row in build_rows:
        g.require(row['sha256'] in requirement_text and row['name'] + '==' + row['version'] in requirement_text, 'Build wheel does not match reviewed requirements')
        g.relative_path(row['filename'])
        g.require('/' not in row['filename'], 'Build wheel filename must be a basename')
        destination = root / 'inputs/wheels' / row['filename']
        live.fetch(row, destination)
        build_wheels.append(live.file_row(destination, root, prefix))
    diagnostic_phase('interface_materialize')
    headers, provider = root / 'interface/upstream', root / 'provider'
    live.tar_data_only(archives['ortools-jammy-headers'], headers, interface_only=True)
    live.wheel_data_only(archives['ortools-python-runtime'], provider)
    diagnostic_phase('command_measure')
    command_paths = {'gcc': '/usr/bin/gcc-11', 'gxx': '/usr/bin/g++-11', 'ld': '/usr/bin/ld',
                     'as': '/usr/bin/as', 'ar': '/usr/bin/ar', 'ranlib': '/usr/bin/ranlib',
                     'strip': '/usr/bin/strip', 'readelf': '/usr/bin/readelf', 'pkg_config': '/usr/bin/pkg-config',
                     'gdal_config': '/usr/bin/gdal-config', 'bash': '/usr/bin/bash', 'env': '/usr/bin/env',
                     'dpkg_deb': '/usr/bin/dpkg-deb'}
    commands = {name: live.file_row(pathlib.Path(path), root, prefix) for name, path in command_paths.items()}
    package_archives = [g.resolve_ref(root, prefix, row['archive']['ref']) for row in packages['resolved']]
    # Prove the image command bytes before invoking readelf on untrusted ELF data.
    # dpkg-deb belongs to the independently authenticated image bootstrap.
    diagnostic_phase('command_archive_bytes')
    live.verify_system_archive_bytes(package_archives, list(map(pathlib.Path, command_paths.values())))
    diagnostic_phase('runtime_provider_prepare')
    shim, system_libraries, runtime_evidence = runtime.prepare_runtime(root, prefix, provider, headers)
    system_paths = [g.resolve_ref(root, prefix, row['ref']) for row in system_libraries]
    diagnostic_phase('provider_archive_bytes')
    byte_proof = live.verify_system_archive_bytes(package_archives, [*map(pathlib.Path, command_paths.values()), *system_paths])
    diagnostic_phase('provider_evidence_write')
    byte_evidence = root / 'preparation-evidence/system-provider-bytes.json'
    live.write_json_new(byte_evidence, {'schema': 1, 'all_selected_commands_and_system_providers_match': True, 'files': byte_proof,
                                         'scope': 'signed-ubuntu-deb-commands-and-system-providers',
                                         'selected_commands': sorted(command_paths),
                                         'bootstrap_tools_excluded': ['python', 'uv']})
    diagnostic_phase('host_snapshot')
    cmake_roots = sorted(pathlib.Path('/usr/share').glob('cmake-*'))
    g.require(cmake_roots, 'Missing measured system CMake modules')
    host_paths = [('uv-prefix', pathlib.Path('/opt/urp-uv')), ('python-prefix', prefix), ('system-headers', pathlib.Path('/usr/include')),
                  ('compiler-support', pathlib.Path('/usr/lib/gcc')), ('system-libraries', pathlib.Path('/usr/lib/x86_64-linux-gnu')),
                  ('system-executables', pathlib.Path('/usr/bin')), ('installed-package-metadata', pathlib.Path('/var/lib/dpkg')),
                  ('system-cmake', cmake_roots[0]), ('system-pkgconfig', pathlib.Path('/usr/share/pkgconfig')),
                  ('eigen-cmake', pathlib.Path('/usr/share/eigen3'))]
    host_paths.extend(('extra-system-cmake-' + str(i), path) for i, path in enumerate(cmake_roots[1:]))
    host_trees = [measure_scope(g, root, prefix, name, path, host=True) for name, path in host_paths]
    configuration = [pathlib.Path(p) for p in ('/etc/ld.so.cache', '/etc/ld.so.conf', '/etc/os-release', '/usr/lib/os-release')]
    configuration.extend(sorted(pathlib.Path('/etc/ld.so.conf.d').glob('*')))
    configuration.extend(path for path in (pathlib.Path('/usr/lib/libarmadillo.so'),) if path.exists())
    host_files = [live.file_row(path, root, prefix) for path in configuration if path.is_file()]
    host_files += system_libraries
    diagnostic_phase('input_snapshot')
    input_paths = [(name, root / 'sources' / name) for name in ('fields2cover', 'steering_functions', 'matplot', 'json')]
    input_paths += [('ortools-interface', root / 'interface'), ('ortools-wheel-extracted', provider),
                    ('build-wheelhouse', root / 'inputs/wheels'), ('input-archives', root / 'inputs/archives'),
                    ('application', root / 'application')]
    evidence = [live.file_row(runtime_evidence, root, prefix), live.file_row(byte_evidence, root, prefix), live.file_row(package_lock, root, prefix), live.file_row(abi_evidence, root, prefix), live.file_row(bootstrap_path, root, prefix)]
    for name in ('base_inventory', 'post_inventory', 'transaction'):
        if packages.get(name):
            g.verify_candidate_file(root, prefix, packages[name])
            evidence.append(packages[name])
    diagnostic_phase('lock_assembly')
    lock = {
        'schema': 1, 'status': 'reviewed-complete',
        'application_commit': g.APPLICATION_COMMIT, 'layout': dict(g.LAYOUT),
        'preparation': {'mode': 'measured-by-externally-reviewed-preparer', 'source_index_sha256': source_digest,
                        'scope': 'compiler-build-only',
                        'validation_inputs_prepared': False,
                        'structural_abi_proven': False, 'same_live_runner_required': False,
                        'image_environment_required': True, 'native_build_executed': False},
        'review': dict.fromkeys(('host_closure_complete', 'input_closure_complete', 'package_provenance_verified',
                                'interface_metadata_reviewed', 'single_wheel_runtime_provider', 'dependency_link_flags_reviewed'), True),
        'host': {'os': {'id': 'ubuntu', 'version': '22.04', 'architecture': 'x86_64'},
                 'python': {'version': '3.12.13', 'prefix': str(prefix), 'executable': live.file_row(python, root, prefix)},
                 'os_release': live.file_row(pathlib.Path('/etc/os-release'), root, prefix),
                 'commands': commands, 'trees': host_trees, 'files': host_files},
        'network': dict(g.NETWORK),
        'packages': packages, 'bootstrap': bootstrap,
        'inputs': {'trees': [measure_scope(g, root, prefix, name, path) for name, path in input_paths],
                   'sources': source_locks, 'application': {'commit': g.APPLICATION_COMMIT, 'ref': {'base': 'candidate', 'path': 'application'}, 'source_index_sha256': source_digest, 'selected_files': sorted(g.APPLICATION_FILES)},
                   'build_requirements': live.file_row(root / 'build-requirements.lock', root, prefix),
                   'build_wheels': build_wheels},
        'interface': {'archive': live.file_row(archives['ortools-jammy-headers'], root, prefix),
                      'cmake_ref': live.path_ref(shim, root, prefix)},
        'runtime': {'provider': 'ortools-python-wheel-only', 'wheel': live.file_row(archives['ortools-python-runtime'], root, prefix),
                    'extracted_ref': live.path_ref(provider, root, prefix),
                    'system_sonames': {'gdal': 'libgdal.so.30', 'tinyxml2': 'libtinyxml2.so.9'},
                    'system_libraries': system_libraries},
        'build': {'source_date_epoch': 1788454736, 'parallel_jobs': 2, 'minimum_free_bytes': 4294967296},
    }
    lock['review']['evidence'] = evidence
    # Validate installed versions, exact source identities, closures and layout
    # before issuing a measured anchor. This preflight does not import natives.
    diagnostic_phase('prepared_verifier_load')
    verifier = load('prepared_input_verifier', root / 'recipe/verify_prepared_inputs.py')
    diagnostic_phase('prepared_verify')
    verifier.verify_prepared(root, str(python), lock, g)
    diagnostic_phase('lock_write')
    live.write_json_new(root / 'reviewed-inputs.lock.json', lock)
    diagnostic_phase('artifact_index_write')
    files = dict(index['files'])
    files['source-index.json'] = {'sha256': source_digest}
    files['reviewed-inputs.lock.json'] = {'sha256': g.sha256(root / 'reviewed-inputs.lock.json')}
    live.write_json_new(root / 'artifact-index.json', {'schema': 1, 'status': 'reviewed-complete', 'files': files})
    measured = g.sha256(root / 'artifact-index.json')
    diagnostic_phase('artifact_index_verify')
    g.verify_index(root, measured)
    diagnostic_phase('anchor_write')
    live.write_json_new(root / 'prepared-anchor.json', {'schema': 1, 'source_index_sha256': source_digest,
                        'artifact_index_sha256': measured, 'same_live_runner_required': False,
                        'protocol': g.PROTOCOL, 'image_environment_required': True,
                        'scope': lock['preparation']['scope'], 'validation_inputs_prepared': False,
                        'native_build_executed': False, 'structural_abi_proven': False})
    return {'preparation_complete': True, 'artifact_index_sha256': measured, 'native_build_executed': False,
            'scope': lock['preparation']['scope'], 'validation_inputs_prepared': False}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=pathlib.Path, required=True)
    p.add_argument('--python', type=pathlib.Path, required=True)
    p.add_argument('--package-lock', type=pathlib.Path, required=True)
    p.add_argument('--expected-source-index-sha256', required=True)
    args = p.parse_args()
    os.umask(0o022)
    print(json.dumps(prepare(args.root, args.python, args.package_lock, args.expected_source_index_sha256)))

if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as error:
        emit_diagnostic(error)
        # Caller redirects this private diagnostic stream. Public workflow emits
        # only its sanitized stage/status summary, never raw host paths or logs.
        print('Preparation refused: ' + str(error), file=sys.stderr)
        raise SystemExit(2)
    except Exception as error:
        emit_diagnostic(error)
        raise
