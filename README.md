# File-open instructions benchmark

This project compares retired CPU instructions used to open a small text file in
LVCE Editor, Visual Studio Code, and Geany on Linux ARM64. The latest complete report is
published on [GitHub Pages](https://levivilet.github.io/file-open-instructions-benchmark/).

Each result opens a temporary `hello.txt` whose bytes are exactly `hello world`.
The editor starts with a fresh HOME and XDG profile without the file. After a
normal main window appears and settles for ten seconds, the controller focuses
the editor and opens the file through its normal open-file UI: Ctrl+O in VS Code
and Geany, or File > Open File in LVCE. It enters the absolute path with Ctrl+L, using
10ms per character and a fixed 250ms chooser path-resolution pause, then
submits the chooser’s Open action. Editor work during input pacing is included.
The measured window begins immediately before the open command and ends after
a fixed one-second response window following that click. The editor process tree is
counted in a dedicated cgroup; the controller, X server, window manager and
external keyboard driver stay outside it. Startup, fixture preparation,
readiness, settling, focus, clipboard/title verification and cleanup are
excluded. Activity by the editor during the response window is included.

After cutoff, the benchmark requires VS Code’s and Geany’s window titles
to identify `hello.txt` and LVCE’s selected tab to identify the exact fixture path
(read through CDP only after counting stops, since LVCE keeps a static native window
title). It copies the selected document to verify its contents exactly match
`hello world`. Each editor has three fresh-profile trials. The report shows the
median and publishes raw `perf` output, trial boundaries, protocol, editor
versions, CPU/host details and run identity. Unsupported, zero or multiplexed
counters, failed opens, timeouts, invalid content and missing matrix entries
fail the report rather than creating partial comparisons.

GitHub Actions checks the per-CPU cgroup accounting on Linux ARM64 and measures
each pinned editor artifact on the same runner class. Pull requests build and
validate the report from the complete editor matrix. Merges to `main` publish
the validated report and downloadable raw evidence to GitHub Pages.

To run the report tests locally:

```sh
python3 -m unittest discover -s tests
```

The full hardware workflow requires an ARM64 Linux host with access to retired
instruction counters, `perf`, X11, and the listed editor dependencies.

The separate **Profile LVCE open** job samples retired instructions in the same
editor cgroup around three normal opens. Its `profile-lvce` artifact contains
`perf.data`, native symbol/thread reports, process command lines and acknowledged
sampling/counter boundaries. Startup is excluded; sampling encloses the counter
window. Sampling perturbs execution, so these diagnostic results never feed the
published report. JavaScript JIT frames may be unresolved; use the process/native
attribution to select a focused JavaScript profile next.
