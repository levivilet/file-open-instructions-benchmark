#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == zed && "${ZED_PORTAL_SESSION:-}" != 1 ]]; then
  exec bash scripts/zed-session.sh "$@"
fi
portal_pids=()
cleanup() {
  kill "$desktop_pid" "${portal_pids[@]}" 2>/dev/null || true
  wait || true
}
openbox > /tmp/file-open-openbox.log 2>&1 &
desktop_pid=$!
trap cleanup EXIT
ready=0
for attempt in {1..100}; do
  if xprop -root _NET_SUPPORTING_WM_CHECK | grep -q 'window id'; then ready=1; break; fi
  sleep .05
done
test "$ready" -eq 1
if [[ "$1" == zed ]]; then
  mkdir -p results/diagnostics
  /usr/libexec/xdg-desktop-portal-gtk > results/diagnostics/zed-portal-gtk.log 2>&1 &
  portal_pids+=("$!")
  gdbus wait --session --timeout 15 org.freedesktop.impl.portal.desktop.gtk
  /usr/libexec/xdg-desktop-portal > results/diagnostics/zed-portal.log 2>&1 &
  portal_pids+=("$!")
  gdbus wait --session --timeout 15 org.freedesktop.portal.Desktop
  gdbus introspect --session --dest org.freedesktop.portal.Desktop --object-path /org/freedesktop/portal/desktop | grep -q org.freedesktop.portal.FileChooser
fi
sudo --preserve-env=DBUS_SESSION_BUS_ADDRESS,DISPLAY,XAUTHORITY,BENCHMARK_RUN_URL,BENCHMARK_COMMIT python3 "${3:-scripts/benchmark.py}" --editor "$1" --perf "$2" --repeats "${4:-3}"
