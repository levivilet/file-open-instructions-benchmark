#!/usr/bin/env bash
# A private bus and portal profile, entirely outside the measured editor cgroup.
set -euo pipefail
portal_root=$(mktemp -d -t file-open-portal-XXXXXX)
trap 'rm -rf "$portal_root"' EXIT
export HOME="$portal_root/home"
export XDG_CONFIG_HOME="$portal_root/config" XDG_DATA_HOME="$portal_root/data"
export XDG_CACHE_HOME="$portal_root/cache" XDG_STATE_HOME="$portal_root/state"
export XDG_RUNTIME_DIR="$portal_root/runtime" XDG_CURRENT_DESKTOP=Benchmark
mkdir -p "$HOME" "$XDG_CONFIG_HOME/xdg-desktop-portal" "$XDG_DATA_HOME" "$XDG_CACHE_HOME" "$XDG_STATE_HOME" "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
printf '[preferred]\ndefault=gtk\n' > "$XDG_CONFIG_HOME/xdg-desktop-portal/portals.conf"
export ZED_PORTAL_SESSION=1
# Do not activate host services (notably a keyring password prompt) on this bus.
# The only services needed for the chooser are started explicitly by run.sh.
cat > "$portal_root/bus.conf" <<'BUS'
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir=/tmp</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*"/>
    <allow receive_sender="*"/>
    <allow own="*"/>
  </policy>
</busconfig>
BUS
dbus-run-session --config-file="$portal_root/bus.conf" -- bash scripts/run.sh "$@"
