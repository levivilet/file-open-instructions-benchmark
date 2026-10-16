# File-open instructions benchmark

This project compares retired CPU instructions used to open a small text file in
LVCE Editor, Visual Studio Code, Geany, minimal Electron and Zed on Linux ARM64. The latest complete report is
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

The earlier comparison used official v0.123.11 as the recent baseline. The v0.123.12
candidate also contains intervening main-area, Git, extension-management,
TypeScript watcher, title-bar and color-theme changes. Its whole-editor result
therefore compares complete releases and cannot isolate savings caused by the
ASCII shortcut. Earlier v0.122.9/v0.123.8 results are historical context.

The minimal Electron entry uses the **same Electron 44.8.0 runtime bytes** as
LVCE v0.123.17: the checksum-verified LVCE Debian package is extracted separately
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

The report also retains the complete alternating-order official LVCE release
comparison at `raw/paired-lvce.json`: both pinned artifact identities, every raw
counter/content check, and a deterministic bootstrap interval for the mean paired
savings. Report generation rejects mismatched artifacts, protocols, ordering,
invalid document/counter evidence, summaries that disagree with raw trials, or a
comparison from a different workflow run. Deployment waits for this comparison.
A release comparison includes all changes between its tags; the separate
`paired-icon` diagnostic artifact changes only the icon lookup function in the
baseline package and is never presented as an official release.

Zed uses its pinned official ARM64 native binary under X11 with Mesa software
Vulkan (`ZED_ALLOW_EMULATED_GPU=1`). Finish Setup (Ctrl+Enter) runs before counting.
A private D-Bus session and GTK file-chooser portal use a separate temporary
HOME/XDG profile. They stay outside the editor cgroup, as do the X server and
input driver; their instructions are excluded. The measured editor still
performs its normal Ctrl+O request and handles the chooser response. After
cutoff, Ctrl+K P copies the exact document path, then select-all/copy verifies
the exact contents. No fixture is passed on the launch command line.

The preceding v0.123.13/v0.123.14 official-release comparison shipped LVCE #16025: view discovery
avoids fetching the full extension inventory when every returned view already
supplies CSS. Worker architecture, virtual DOM and file-cache freshness checks
remain unchanged. The merged editor matrix is protocol v5; do not pool earlier
protocols or separate runs.

**Controlled IPC comparison** independently extracts two official v0.123.13
packages, applies only the exact recorded renderer-worker function replacement
to one, and measures forty alternating-order pairs without probes or tracing.
It is frozen in `config/ipc-baseline.json`; the older icon diagnostic remains
frozen separately in `config/icon-baseline.json` (v0.123.12). The report validates
every IPC trial, its run/commit identity and summary, publishes all raw data at
`raw/paired-ipc.json`, and links the diagnostic process/worker message analysis.
The controlled patched bundle is diagnostic; the historical .13/.14 comparison
validated that shipped change. Report generation/deployment require both paired
comparisons and the entire five-editor matrix. No samples or failed trials are
silently discarded.

The separate **Controlled preference comparison** job tests microtask coalescing
of the editor worker's legacy global preference reads against two copies of
checksum-pinned official v0.123.15. All forty alternating pairs are retained and
validated using the ordinary uninstrumented protocol. This is an exploratory
bundle patch retained in the diagnostic results, not a shipped optimization. Application-scoped
reads are untouched. **Native-only LVCE profile** samples the same official
baseline with no Chromium/V8 trace, avoiding the V8 profiling threads present in
the original diagnostic job. Sampling results remain diagnostic only. All
existing matrix, comparison, report and deployment gates remain required.

**Controlled chooser comparison** separately quantifies forcing hidden files
visible versus the native chooser default, using forty pairs on the same
v0.123.15 archive. It is diagnostic only: changing the default visibility is a
user-visible tradeoff, so this experiment alone does not authorize shipping it.

**Controlled renderer read comparison** measures skipping the renderer worker's
redundant whole-file read for functional editors with an already-known filename
language. The editor worker still reads and validates the document normally;
unknown-language first-line detection and legacy rendering keep their existing
reads. Forty independent alternating pairs use the same pinned v0.123.15
archive. This candidate is diagnostic until correctness and release acceptance.

**Controlled first-tab comparison** tests suppressing scroll-into-view only for
the first tab added to an empty group. The isolated main-area-worker patch keeps
the virtual DOM and all subsequent tab visibility handling unchanged. Forty
pairs quantify the avoided layout cost before any source or behavior change is
shipped. Earlier candidate experiments remain separate, including inconclusive
results.

**Controlled worker minification comparison** tests the V8 parsing cost identified
by native instruction samples. Both variants use the same verified official
v0.123.15 archive; one transforms checksum-guarded packaged worker bundles with
esbuild 0.28.2, preserving function names and module exports. The artifact records
every input/output hash and size. It retains forty alternating uninstrumented
pairs with ordinary counting boundaries; no tracing or runtime/security flags
are changed. This is a diagnostic candidate, not a shipped release comparison.

**Controlled Electron runtime comparison** isolates a runtime update: both
variants use identical official LVCE v0.123.15 application files, while one uses
the checksum-pinned official Electron 44.8.0 ARM64 runtime. It verifies the
runtime version and unchanged application tree hash, records the replacement
identity, and retains forty ordinary uninstrumented pairs. Original application
configuration bytes (including its historical runtime label) remain unchanged;
the experimental runtime metadata records the actual runtime. These results
are diagnostic and never presented as an official LVCE release comparison.


The current official comparison uses v0.123.14/v0.123.17, and the minimal
Electron reference uses the exact v0.123.17 runtime. That release contains worker
bundle minification (LVCE #16032) and Electron 44.8 integration (#16036), plus
intervening changes from other tasks. Whole-release differences cannot be
attributed entirely to these two optimizations. The same-release worker,
runtime and combined experiments retain their separate frozen v0.123.15 inputs.

The report states whether the official candidate's forty-pair median is below
1,300M instructions. It also publishes sixteen controlled diagnostic comparisons
with every raw pair, exact transformation, artifact identity and interval.
Report generation validates each experiment with its original guards and
requires its current workflow run and commit. Missing, invalid or stale data
fails the report. These rows remain separate from official package results;
isolated savings are never summed and separate repetitions are never pooled.

Additional retained experiments cover the alternative name-preserving minifier,
lazy editor commands, fonts, initial rendering, plaintext tokenization, V8 cache
policy, local Problems summaries, buffered JavaScript responses and redundant
diff RPC removal. Inconclusive experiments remain visible. The native and V8
profiling jobs stay outside the uninstrumented report inputs. The mapped native
profile retains private V8 symbol maps and native samples for explicit address
resolution when perf's automatic symbol lookup is unavailable.
