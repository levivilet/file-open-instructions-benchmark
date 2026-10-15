#!/usr/bin/env bash
set -euo pipefail
openbox > /tmp/file-open-openbox.log 2>&1 &
desktop_pid=$!
trap 'kill "$desktop_pid" 2>/dev/null || true' EXIT
ready=0
for attempt in {1..100}; do
  if xprop -root _NET_SUPPORTING_WM_CHECK | grep -q 'window id'; then ready=1; break; fi
  sleep .05
done
test "$ready" -eq 1
sudo --preserve-env=DISPLAY,XAUTHORITY,BENCHMARK_RUN_URL,BENCHMARK_COMMIT python3 scripts/benchmark.py --editor "$1" --perf "$2" --repeats 3
