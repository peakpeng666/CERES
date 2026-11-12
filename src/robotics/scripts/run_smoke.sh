#!/usr/bin/env bash
# Deliberately no installation, download, credentials, benchmark input, or hardware route.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/evidence/runtime_$(date -u +%Y%m%dT%H%M%SZ)_$$}"
# Evidence is append-only: even an empty existing directory may be another run's.
if [[ -e "$OUT" || -L "$OUT" ]]; then
  printf 'Refusing existing evidence path: %s\n' "$OUT" >&2
  exit 2
fi
mkdir -p -- "$(dirname -- "$OUT")"
mkdir -- "$OUT"
OUT="$(cd "$OUT" && pwd)"
fail() { printf '%s\n' "$1" >&2; printf '{"runtime_executed":false,"success":false,"stage":"preflight","reason":"%s"}\n' "$1" > "$OUT/preflight.json"; exit 2; }
[[ -r /opt/ros/humble/setup.bash ]] || fail 'ROS 2 Humble is not installed; runtime NOT executed'
# ROS setup may inspect unset variables.
set +u
source /opt/ros/humble/setup.bash
set -u
[[ "${ROS_DISTRO:-}" == humble ]] || fail 'Only ROS 2 Humble is supported'
for exe in python3 ros2 ign timeout setsid flock; do command -v "$exe" >/dev/null || fail "Missing required executable: $exe"; done
timeout 10s ign gazebo --versions > "$OUT/gazebo_versions.txt" 2>&1 || fail 'Cannot inspect Gazebo versions within 10 seconds'
python3 - "$OUT/gazebo_versions.txt" <<'PY' || fail 'Exactly Gazebo Fortress major version 6 must be selectable'
from pathlib import Path
import re
import sys
versions = re.findall(r'(?<![\d.])(\d+)\.\d+(?:\.\d+)?', Path(sys.argv[1]).read_text())
sys.exit(0 if set(versions) == {'6'} else 1)
PY
timeout 10s ros2 pkg prefix ceres_route_sim >/dev/null 2>&1 || fail 'Build and source the ceres_route_sim colcon workspace first'
timeout 10s ros2 pkg prefix ros_gz_bridge >/dev/null 2>&1 || fail 'ros_gz_bridge is missing'
SHARE="$(timeout 10s ros2 pkg prefix --share ceres_route_sim)" || fail 'Cannot locate installed simulation data'
timeout 15s ign sdf -k "$SHARE/worlds/synthetic_greenhouse.sdf" > "$OUT/sdf_validation.log" 2>&1 || fail 'Installed SDF failed libsdformat validation'
timeout 10s python3 - "$ROOT" "$SHARE" > "$OUT/source_manifest.json" <<'PY' || fail 'Cannot record runtime source manifest'
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import sys
root, share = map(Path, sys.argv[1:])
spec = importlib.util.find_spec('ceres_route_sim')
if spec is None or not spec.origin:
    raise RuntimeError('Cannot locate installed ceres_route_sim Python sources')
code = Path(spec.origin).resolve().parent
files = set(code.rglob('*.py')) | set((root / 'scripts').glob('*.py')) | set((root / 'scripts').glob('*.sh'))
files |= set(path for path in share.rglob('*') if path.is_file() and path.suffix in {'.py', '.json', '.sdf', '.yaml', '.xml'})
print(json.dumps({'scope': 'runtime code and installed simulation data', 'python': sys.version,
                  'platform': platform.platform(), 'installed_python_directory': str(code),
                  'files': {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(files)}}, indent=2))
PY
# Both DDS and Gazebo transport restricted to this host; dedicated simulator namespace.
export ROS_LOCALHOST_ONLY=1 ROS_DOMAIN_ID=71 IGN_IP=127.0.0.1
export IGN_PARTITION="ceres_synthetic_$(date +%s)_$$"
export CERES_ALLOW_SYNTHETIC_COMMANDS=1
# A unique Gazebo partition alone does not isolate identically named ROS topics.
# Serialize this user's runners sharing the fixed DDS domain and command topic.
exec 9>"${XDG_RUNTIME_DIR:-/tmp}/ceres_synthetic_domain_71_${UID}.lock"
flock -n 9 || fail 'Another synthetic smoke runner is using ROS domain 71'
printf '{"runtime_executed":false,"preflight_passed":true,"status":"ready_for_runtime","stage":"preflight","stack":"Humble/Fortress"}\n' > "$OUT/preflight.json"
LAUNCH_PID=''
cleanup() {
  if [[ -n "$LAUNCH_PID" ]]; then
    kill -INT -- "-$LAUNCH_PID" 2>/dev/null || true
    for _ in {1..50}; do kill -0 "$LAUNCH_PID" 2>/dev/null || break; sleep .1; done
    kill -TERM -- "-$LAUNCH_PID" 2>/dev/null || true
    sleep .5
    kill -KILL -- "-$LAUNCH_PID" 2>/dev/null || true
    wait "$LAUNCH_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
setsid ros2 launch ceres_route_sim synthetic_fortress.launch.py rviz:=false > "$OUT/launch.log" 2>&1 &
LAUNCH_PID=$!
set +e
timeout --signal=INT --kill-after=5s 90s ros2 run ceres_route_sim synthetic_smoke --output-dir "$OUT" --timeout 65 > "$OUT/controller.log" 2>&1
RESULT=$?
set -e
if ! kill -0 "$LAUNCH_PID" 2>/dev/null; then
  RESULT=1
  printf '%s\n' 'Simulator launch exited before result acceptance' >> "$OUT/controller.log"
fi
set +e
python3 "$ROOT/scripts/check_smoke_result.py" "$OUT/smoke_result.json" --exit-code "$RESULT" > "$OUT/runner_result.json"
GATE_RESULT=$?
set -e
printf 'Report: %s/smoke_result.json\n' "$OUT"
if [[ "$RESULT" -ne 0 ]]; then exit "$RESULT"; fi
exit "$GATE_RESULT"
