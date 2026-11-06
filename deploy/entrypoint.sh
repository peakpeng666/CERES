#!/bin/bash
# Container start. ROS_LOCALHOST_ONLY selects lo, which is not multicast-capable.
# This script does not add a multicast route.
set -euo pipefail
mkdir -p /data/lab /data/archive /data/lab/matplotlib
if [[ "$(id -u)" -eq 0 ]]; then
  mkdir -p /home/urp /opt/urp/setup/2026-10-15/cache/ignition-fuel
  # Only the writable volumes: a desktop's dataset.json can be a read-only bind mount under /data.
  chown -R urp:urp /data/lab /data/archive /home/urp
  export HOME=/home/urp
else
  # A desktop matches its host UID. There may be no passwd entry or writable /home/urp for that user.
  export HOME=/data/lab/home
  mkdir -p "$HOME"
fi
export MPLCONFIGDIR=/data/lab/matplotlib
# Server-mode Gazebo still initializes GLX for sensors. Loopback has no X server.
if [[ -z "${DISPLAY:-}" ]] && command -v Xvfb >/dev/null 2>&1; then
  export DISPLAY=:99
  Xvfb :99 -screen 0 1280x1024x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
fi
cd /opt/urp/src/lab
if [[ "$(id -u)" -eq 0 ]]; then
  exec setpriv --reuid=urp --regid=urp --init-groups -- "$@"
fi
exec "$@"
