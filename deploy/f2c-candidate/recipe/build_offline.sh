#!/usr/bin/env bash
# Preparation/review only until a separate execution decision is made.
# Invoke this reviewed launcher with a trusted shell and trusted Python.
set +xv
set -euo pipefail
IFS=$' \t\n'
# Drop inherited exports before any external command (including Python).
while IFS= builtin read -r name; do builtin export -n "$name"; done < <(builtin compgen -e)
export PATH=/usr/bin:/bin LANG=C.UTF-8 LC_ALL=C.UTF-8
umask 077

mode=${1:-}; [[ $# -gt 0 ]] && shift
root= python= anchor=
while [[ $# -gt 0 ]]; do
  [[ $# -ge 2 ]] || { builtin printf '%s\n' 'Missing option value; refusing.' >&2; exit 2; }
  case "$1" in
    --root) [[ -z "$root" ]] || exit 2; root=$2;;
    --python) [[ -z "$python" ]] || exit 2; python=$2;;
    --expected-index-sha256) [[ -z "$anchor" ]] || exit 2; anchor=$2;;
    *) builtin printf '%s\n' 'Unknown option; refusing.' >&2; exit 2;;
  esac
  shift 2
done
[[ "$root" == /* && "$python" == /* && "$anchor" =~ ^[0-9a-f]{64}$ ]] || {
  builtin printf '%s\n' 'Require --root ABSOLUTE_ROOT --python TRUSTED_ABSOLUTE_PYTHON --expected-index-sha256 EXTERNALLY_REVIEWED_SHA256.' >&2; exit 2;
}
case "$mode" in
  --check-reviewed-inputs|--execute-reviewed-build|--check-inside-netns|--execute-inside-netns) ;;
  *) builtin printf '%s\n' 'Unknown mode; refusing.' >&2; exit 2;;
esac

# No candidate helper is executed until this inline standard-library bootstrap
# verifies an EXTERNAL anchor. Never derive the expected digest from the bundle.
"$python" -I -B - "$root" "$anchor" <<'BOOTSTRAP'
import hashlib, json, os, pathlib, re, stat, sys
def fail(message):
    raise SystemExit('Refusing: ' + message)
def unique(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            fail('duplicate JSON key')
        result[k] = v
    return result
root = pathlib.Path(sys.argv[1])
if not root.is_absolute() or root != pathlib.Path(os.path.abspath(root)):
    fail('root must be canonical and absolute')
for p in (root, *root.parents):
    if p.is_symlink():
        fail('symlink root/ancestor')
if not root.is_dir() or root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o022:
    fail('root must be caller-owned and not group/world writable')
def read_regular(relative):
    if not isinstance(relative, str) or not relative or relative.startswith('/') or any(x in ('', '.', '..') for x in relative.split('/')):
        fail('unsafe indexed path')
    p = root
    for part in relative.split('/'):
        p = p / part
        if p.is_symlink() or p.stat().st_mode & 0o022:
            fail('symlink or writable indexed path')
    if not stat.S_ISREG(p.stat().st_mode):
        fail('non-regular indexed file')
    return p.read_bytes()
raw = read_regular('artifact-index.json')
if hashlib.sha256(raw).hexdigest() != sys.argv[2]:
    fail('artifact index anchor mismatch')
index = json.loads(raw, object_pairs_hook=unique)
required = {'recipe/build_offline.sh', 'recipe/guardlib.py', 'recipe/build_guard.py',
            'recipe/verify_offline_inputs.py', 'recipe/run_build.py', 'reviewed-inputs.lock.json'}
if index.get('schema') != 1 or index.get('status') != 'reviewed-complete' or not required.issubset(index.get('files', {})):
    fail('incomplete reviewed index')
for name, entry in index['files'].items():
    digest = entry.get('sha256', '')
    if not re.fullmatch(r'[0-9a-f]{64}', digest) or hashlib.sha256(read_regular(name)).hexdigest() != digest:
        fail('indexed artifact mismatch: ' + name)
# Measure trusted launch binaries before the namespace launcher uses them.
# The selected Python is a trust root for this bootstrap; it is measured again
# here and in the complete host closure before any build command runs.
lock = json.loads(read_regular('reviewed-inputs.lock.json'), object_pairs_hook=unique)
if lock.get('status') != 'reviewed-complete':
    fail('Jammy preparation incomplete')
host = lock['host']
bases = {'candidate': root, 'system': pathlib.Path('/'), 'python': pathlib.Path(host['python']['prefix'])}
def ref_path(ref):
    value = ref['path']
    if not isinstance(value, str) or not value or value.startswith('/') or any(x in ('', '.', '..') for x in value.split('/')):
        fail('unsafe launch binary path')
    return bases[ref['base']] / value
for label, entry, expected in [
    ('Python', host['python']['executable'], pathlib.Path(sys.executable)),
    *[(name, host['commands'][name], pathlib.Path('/usr/bin') / name) for name in ('bash', 'env', 'unshare')],
]:
    path = ref_path(entry['ref'])
    if path != expected or path.resolve(strict=True) != ref_path(entry['resolved_ref']):
        fail('launch binary path differs: ' + label)
    if (os.readlink(path) if path.is_symlink() else None) != entry.get('symlink_target') or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
        fail('launch binary hash/alias differs: ' + label)
BOOTSTRAP

case "$mode" in
  --check-reviewed-inputs|--execute-reviewed-build)
    inner=--execute-inside-netns
    [[ "$mode" != --check-reviewed-inputs ]] || inner=--check-inside-netns
    # A failure to create a NEW kernel network namespace is terminal. No online
    # fallback, host firewall mutation, container pull, or environment marker.
    exec /usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 LC_ALL=C.UTF-8 \
      /usr/bin/unshare --user --map-root-user --net \
      /usr/bin/bash --noprofile --norc "$root/recipe/build_offline.sh" "$inner" \
      --root "$root" --python "$python" --expected-index-sha256 "$anchor";;
esac
action=--check
[[ "$mode" != --execute-inside-netns ]] || action=--execute
exec "$python" -I -B "$root/recipe/verify_offline_inputs.py" "$action" \
  --root "$root" --python "$python" --expected-index-sha256 "$anchor"
