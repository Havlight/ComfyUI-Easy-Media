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
