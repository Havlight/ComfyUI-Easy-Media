# H3 native workflow consolidation

Implementation baseline: `9f5b8b7` (local main, 2026-10-03).

## Accepted behavior

- Load legacy H3 timelines into the same native geometry used by new edits. No upgrade button or routine confirmation dialog. Media tracks retain their timing; locked tracks cannot be moved by normalization. One edit, including ripple changes, is one undo operation.
- Shot raw duration is 17k+5; continuation delivers 17n frames after a 39-frame context. Output trims context exactly once. Combine edits remain frame-based.
- Project owns `allow_vae_fallback`, default false. Migrate explicit editor policies without silently resetting them. External ingress and output decoding remain allowed. Corruption, stale versions and invalid locks cannot be bypassed by fallback.
- Before the first sampler, validate every selected task and its dependencies, including sources that earlier tasks in this run will produce. Pin existing source versions. Keep runtime checks for changed files and actual tensor results.
- Offer Shot, Context and Drift. Retain historical Masked artifacts, but require an explicit supported method before regenerating a retired task. Keep shared mask utilities.
- Preserve historical results; explain invalidated results and dependencies. Normalize old data at the boundary rather than retaining two H3 generation engines.

## Contracts

1. Timing: integer half-open global frame ranges; raw context windows and delivered views are separate. Audio uses cumulative sample boundaries.
2. Sources: immutable version identity, stage, shape, clocks, integrity and provenance. High-resolution tensors are not a substitute for missing low-stage history.
3. Dependencies: distinguish existing pinned versions from outputs planned earlier in this run. A changed parent invalidates dependent cached results, not their saved files.
4. Methods: Context uses native guides plus a short anchor. Drift copies context and installs schedule-dependent video masks. Ordinary Dual refinement and SelfLift have distinct stage behavior.

## Commit sequence

1. Record baseline and contracts.
2. Unify timeline normalization, automatic load conversion and nonblocking editing.
3. Move fallback to Project and add full-run preflight with version snapshots.
4. Retire Masked and the legacy Project execution branches; retain source adapters.
5. Expose dependency/rerender state and complete integration regressions.
6. Validate actual generation, update user documentation and commit release assets separately.

## Validation baseline and acceptance

Existing manual entry points: `tests/manual/h3_native_api_smoke.py`, `h3_native_gpu_matrix.py`, `h3_native_gpu_smoke.py`.
Local reference projects (not checked into Git): Context `native-validation-aed7fb83e042`, SelfLift Drift `native-validation-3eb11e573df8`, Dual Masked `native-validation-5c9f52ceaead`, audio lock `native-validation-2102df3681d3`.

The Masked woman-reaching-for-a-branch example is an unresolved motion-repetition case, **not** a passing perceptual baseline. Removing the method does not count as fixing it. Existing small SelfLift samples also exhibit visible artifacts. Structural success and quality acceptance must be recorded separately.

Check sequential edits/undo, automatic legacy conversion, model-format switching, fallback migration, preflight failure before sampling, pinned source integrity, planned parents, mode/stage switches, locks, previous-frame references, and uint8 sampling previews. Run real Context/Drift generation in Single/Dual/SelfLift; inspect joins and sound as well as frame counts. Never infer quality success solely from a completed queue.


## Completion record — 2026-10-03

Implemented in `67cfba0` (contracts), `0da1f51` (editing), `2cd9085` (Project policy/preflight), and `818a2c9` (one engine, retired method, dependency status). Documentation/manual validation and release bundles are separate commits following these changes.

### UI and compatibility

- Editor layout and existing controls remain. Legacy H3 task durations align on load; generation edits, split/markers and prompt overrides share native geometry. No upgrade button or timing confirmation dialog. Ripple changes are one undo transaction, with a short inline notice.
- Project owns `allow_vae_fallback`, off by default. Existing explicit Editor preferences migrate once; a saved Project value wins. The boolean is appended after existing schema inputs to preserve older widget positions.
- Shot / Context / Drift are the only offered choices. A historical Masked task retains its identity and a disabled label until the user chooses a supported method. Saved media remain readable.
- Saved-result status identifies edited sources and dependent tasks without modifying the manifest. It excludes media-cache indexes and visual labels from content identity. Status queries reuse Editor normalization without opening media. Slot upstream changes, runtime prompt overrides and MODEL settings still require backend validation.
- Both save modes keep immutable generations. Public legacy artifact readers remain available, while Project's legacy generation branches and implicit VAE context rebuild are removed. A first-pass-only preview is not a resume checkpoint; disabling it regenerates both passes.

### Automated checks

- Windows ComfyUI Python: **978 passed, 1 skipped** across the Python suite. Covers shared node helpers and other formats as well as native graph/preflight/artifacts/locks, previous frame, previews, prompt overrides and generation history.
- Frontend: **608 passed / 54 files**; TypeScript strict check and production build passed. A pre-existing MediaSelector IntersectionObserver timing flake occurred during one full run; the final full run passed.
- Deleted tests described the removed legacy Project engine (22-frame context rebuild, destructive override, legacy second-pass resume). Native contract, stage/lock and integration coverage replaces those assertions; standalone legacy artifact-reader coverage remains.
- Linux FFmpeg path limitations were avoided by running the full backend suite in the actual Windows Comfy environment. Test dependencies were isolated under validation output, without changing Comfy's installed packages.

### Actual model/API checks

RTX 4090, ComfyUI embedded Python, H3 int8 ConvRot and Turbo LoRA. The first MODEL uses LoRA strength 1.0; a distinct second MODEL uses 0.8. Custom Dual second sigmas are `.72, .5, .3, .14, .06, 0`. Small test dimensions are 320×256; learned Dual upscaling targets 640×512.

| Run | Validation project | Result |
| --- | --- | --- |
| Single Context | `native-validation-b68d1e6beb75` | Three segments completed |
| Single Drift + external audio lock | `native-validation-6eca2d7d5509` | Three segments completed; later resumed from segment 2 |
| Dual Context + learned upscaler | `native-validation-18e09c56136e` | Three segments, distinct second MODEL and custom second sigmas |
| Dual Drift + learned upscaler | `native-validation-e8a2e18f0954` | Three segments, distinct second MODEL and custom second sigmas |
| SelfLift Context, direct lift | `native-validation-5c8c450d919c` | Three segments; structural pass, visual artifacts |
| SelfLift Drift, direct lift | `native-validation-ecc131caa6ce` | Three segments; structural pass, visual artifacts |
| SelfLift Context, learned lift | `native-validation-2827ec900bd4` | Three segments; structural pass, visual artifacts remain |
| Single Context + external video lock | `native-validation-47b73e14b4a0` | Three segments; full raw windows validated before sampling |

Each completed run delivered **90 + 51 + 51 = 192 frames**. The actual SaveVideo export after Video Combine also has 192 frames. Every raw sidecar uses `17k+5`, context is 0 / 39 / 39, and saved fallback history is empty. All inspected audio arrays are finite. Locked audio yields exactly 352800 samples at the existing import/mix rate of 44100 Hz, and matches the resampled external source sample-for-sample (maximum difference 0). This is sample-level validation, not a listening-quality score.

The real resume run left segment 1 at generation 0 and added generations 3/4 for segments 2/3 while retaining their previous versions 1/2. It exercised an existing pinned predecessor followed by a predecessor produced in the same run.

`tests/manual/h3_native_preflight_api.py` verified invalid sigma order, missing low-stage history when switching Single → Dual, and an edited unscheduled parent. All three failed in preflight before a sampler ran; existing manifest bytes were unchanged. Read-only API status reported all tasks saved, then edited/parent_changed/parent_changed after a prompt edit. Audio/video lock projects also returned saved status without false source-edit warnings.

Local reports, videos and contact sheets live in `ComfyUI/output/easy_media/native-validation/`, with report filenames beginning `consolidation-`. Test artifacts and model files are not committed. The validation server used its own port 8191 and database; the user's 8188 server was not restarted.

### Perceptual and validation limits

- Contact sheets cover both seams (frames 89→90 and 140→141), with surrounding motion samples. Single/Dual examples do not show an untrimmed 39-frame prefix or a reset to the start of the reaching action. Some framing/lighting changes remain, especially Dual Drift. These samples do not certify arbitrary-prompt motion continuity.
- All tested low-resolution SelfLift recipes show obvious repeating texture/color artifacts, including learned lift. The SelfLift sampling implementation was not changed in this consolidation, and the prior native baseline already exhibited these artifacts. Record these as **quality failures**, despite completed queues and correct latent lineage; do not recommend these sample settings as a quality preset.
- The historical Masked reaching-action repetition remains a historical unresolved example. Retiring the method does not retroactively fix the video.
- Browser automation could not connect because the tool rejected the WSL working-directory URI before execution. UI behavior has component tests and live API coverage, but no new end-to-end browser interaction pass is claimed.
- Preflight cannot predict OOM, numerical model failures, file replacement during execution, or disk failures. Runtime checks and atomic saves remain required.

### Small follow-up: bounded Lock Video preflight memory — 2026-10-03

- File-backed locked windows now count actual decoded video frames one at a time, respecting ComfyUI's active trim window and 24 fps check. Preflight does not convert them into RGB float arrays, stack a frame batch, or decode their audio. Missing streams, short/long windows, wrong FPS and decode errors fail before sampling. Other VIDEO adapters retain their component-based validation.
- The full-run gate, static preparation cache, UI, sampling, VAE behavior and output remain unchanged. Raw-window crop/resize preparation still runs in preflight and generation; repeated preparation log entries can remain. Sharing those results is a separate optimization.
- Added 22 regressions, including actual PyAV files, BytesIO, trims, a misleading header count, bounded frame references, decoder cleanup, in-memory adapters and a bad second window rejected by the full-run gate. Windows ComfyUI Python suite: **1000 passed, 1 skipped**. Release build passed and produced identical assets.
- Actual ComfyUI `VideoFromFile` parity checks passed for eight path/BytesIO trim cases, including negative starts. A synthetic 1056×800, 243-frame, 24 fps H.264 file was measured in separate Windows processes with the same imports: peak working-set increase above the pre-check baseline was **5,017,993,216 bytes (4.67 GiB)** with `get_components()` versus **6,979,584 bytes (6.66 MiB)** with streaming validation. This measures only the isolated check, not end-to-end generation or GPU memory. Local script, fixture and JSON reports are under `output/easy_media/native-validation/lock-video-probe-*`; no new GPU quality claim is made.
