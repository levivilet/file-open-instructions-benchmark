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
A fixed 100ms pause after Ctrl+L lets the GTK location entry realize before typing;
this pause is inside the measured interval for every editor. An observed failed
Geany trial lost the leading slash while GTK warned about an unrealized widget.
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

The diagnostic job also records a Chromium trace with V8 CPU profiles, including
dedicated workers, in `chromium-trace.json`. This trace is collected only in the
separate diagnostic run; the ordinary instruction comparison remains untraced.

**Paired LVCE comparison** measures forty uninstrumented pairs against the frozen
`config/lvce-baseline.json` release. Both binaries are checksum-verified and run
on the same ARM64 host, alternating which version goes first. Each open retains
the normal protocol, fresh profile and exact path/content checks. The separate
`paired-lvce` artifact preserves every counter and a deterministic bootstrap
interval for mean paired savings. Inspect raw results before claiming a gain;
an interval spanning zero is inconclusive.

The initial ASCII grapheme comparison used v0.123.9/v0.123.10, whose source
difference was only text-measurement-worker 1.25.1 → 1.25.2. Ten pairs were
inconclusive (mean savings −6.65M instructions, bootstrap 95% interval −29.45M to
+6.95M); a fixed forty-pair follow-up was also inconclusive (−2.76M, interval
−10.93M to +2.49M). Every trial was retained, including the initial low baseline.

Profiles then identified another ICU segmentation call in editor-worker's
horizontal-range fallback. Its printable ASCII/tab shortcut ships in
editor-worker 19.74.5. Unicode and CR/LF retain the existing fallback; the worker
architecture and virtual DOM remain unchanged.

The final comparison uses official v0.123.11 as the recent baseline. The v0.123.12
candidate also contains intervening main-area, Git, extension-management,
TypeScript watcher, title-bar and color-theme changes. Its whole-editor result
therefore compares complete releases and cannot isolate savings caused by the
ASCII shortcut. Earlier v0.122.9/v0.123.8 results are historical context.

The minimal Electron entry uses the **same Electron 44.7.0 runtime bytes** as
LVCE v0.123.12: the checksum-verified LVCE Debian package is extracted separately
and only `resources/app` is replaced with the small app in `electron/`. Installation
checks LVCE's embedded runtime version first. No npm installation or second Electron
release is involved. The app reads the selected UTF-8 file asynchronously and displays
it in a focused textarea, retaining the absolute path for post-cutoff CDP verification.
Select-all/copy must return the same exact fixture contents as every editor.

Minimal Electron uses its native File menu's Ctrl+O accelerator and the same native
Electron file chooser. LVCE uses its existing custom File > Open File interaction,
which includes the documented 150ms menu pause. Both retain the same path pacing
and one-second response window. This is an application-overhead reference, not an
isolated measurement of workers: the minimal app has no editor features, worker
architecture, virtual DOM, syntax handling, tabs or extensions. Differences in menus
and rendering remain included. The lockfile's Electron entry intentionally points
to the LVCE archive; its version labels both the runtime and source package.
