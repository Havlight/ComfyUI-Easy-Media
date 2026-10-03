"""Internal native H3 graph boundaries: prepare, sample result, view and publish."""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

import folder_paths
import torch
from comfy_api.latest import io

from ..utils.h3_native import (
    new_native_metadata, prepare_native_canvas, slice_native_context,
    stamp_native_result, trim_native_media, validate_native_latent,
)
from ..utils.h3_native_artifacts import (
    commit_native_generation, file_checksum, load_native_latent, media_version_id, native_child_path,
    read_native_manifest, refresh_native_dependencies,
)
from ..utils.h3_native_sources import encode_native_seed, read_delivered_seed
from ..utils.h3_native_timing import NativePlanError, NativeTaskPlan, compile_native_plan
from ..utils.h3_native_preflight import (
    generation_snapshot, native_stage_layout, preflight_native_sources, run_source, validate_parent_range,
)
from ..utils.h3_project import compact_h3_task_segments, safe_h3_project_name, save_h3_audio
from ..utils.h3_previous_frame import save_tail_image
from .minimax import _h3_project_source_path, _notify_multitrack_project_refresh

TYPE_TRACKS_INFO = io.Custom(io_type="TRACKS_INFO")


def _project_directory(project_name: str) -> Path:
    return Path(folder_paths.get_output_directory()).resolve() / "easy_media" / "projects" / safe_h3_project_name(project_name)


def _native_model_adapter(model: Any) -> str:
    inner = getattr(model, "model", None)
    config = getattr(inner, "model_config", None)
    latent_format = getattr(inner, "latent_format", None)
    if type(config).__name__ != "MiniMaxH3" or type(latent_format).__name__ != "MiniMaxH3AV":
        raise NativePlanError("MODEL_ADAPTER", "Native H3 requires the verified ComfyUI MiniMaxH3 / MiniMaxH3AV adapter.")
    return "comfy-minimax-h3-av-v1"


class EasyH3NativePreflight(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(node_id="easy h3NativePreflight", display_name="H3 Run Preflight",
            category="EasyUse/H3/dev", is_dev_only=True, not_idempotent=True,
            inputs=[TYPE_TRACKS_INFO.Input("tracks_info"), io.String.Input("project_name"),
                    io.String.Input("config_json"), io.Model.Input("model"), io.Model.Input("second_model", optional=True),
                    io.Sigmas.Input("sigmas", optional=True), io.Sigmas.Input("second_sigmas", optional=True),
                    io.Sigmas.Input("context_second_sigmas", optional=True), io.Vae.Input("vae"), io.Vae.Input("audio_vae"),
                    io.AnyType.Input("project_static", optional=True)],
            outputs=[io.AnyType.Output("run_state")])

    @classmethod
    def execute(cls, tracks_info: dict[str, Any], project_name: str, config_json: str,
                model: Any, vae: Any, audio_vae: Any, second_model: Any = None,
                sigmas: torch.Tensor | None = None, second_sigmas: torch.Tensor | None = None,
                context_second_sigmas: torch.Tensor | None = None, project_static: Any = None) -> io.NodeOutput:
        from ..utils.h3_project import h3_task_entries, h3_task_is_passthrough, h3_task_type, h3_generation_mode
        from ..utils.h3_previous_frame import previous_frame_position
        from .project import EasyH3ProjectStaticPrepare
        from .basic import MultiTrackTaskOutput

        config = json.loads(config_json)
        recipe = config['recipe']
        selected = config['selected']
        plans = compile_native_plan(tracks_info)
        entries = h3_task_entries(tracks_info)
        contents = [entry.get('task', {}).get('content', {}) for entry in entries]
        passthrough = {i for i in selected if recipe['sampling_mode'] == 'passthrough' or h3_task_is_passthrough(entries[i])}
        previous_frames = {i for i in selected if i not in passthrough and previous_frame_position(contents[i]) is not None}
        _native_model_adapter(model)
        if second_model is not None:
            _native_model_adapter(second_model)
        if vae is None or audio_vae is None:
            raise NativePlanError('VAE_COMPONENT', 'The first loader must provide video and audio VAEs.')
        if int(getattr(audio_vae, 'audio_sample_rate', 32000)) % 40:
            raise NativePlanError('LOCK_ADAPTER', 'The audio VAE must use an integer number of samples per latent tick.')
        if recipe['sampling_mode'] == 'selflift' and not (0.25 <= recipe['lowres_scale'] <= 1 and 0 < recipe['transition_ratio'] < 1):
            raise NativePlanError('SELFLIFT_CONFIG', 'SelfLift needs lowres_scale between 0.25 and 1, and transition_ratio between 0 and 1.')
        if any(i not in passthrough for i in selected):
            for name, schedule, required in (
                ('first', sigmas, True),
                ('second', second_sigmas, config['run_second_pass']),
                ('context second', context_second_sigmas, config['has_context_second_pass']),
            ):
                if not required and schedule is None:
                    continue
                if (not isinstance(schedule, torch.Tensor) or schedule.ndim != 1 or schedule.numel() < 2
                        or not torch.isfinite(schedule).all() or torch.any(schedule < 0)
                        or torch.any(schedule[1:] > schedule[:-1]) or not schedule[0] > schedule[-1]):
                    raise NativePlanError('SCHEDULE', f'The {name} schedule must be finite, nonnegative and descending.')
            if config['run_second_pass'] and recipe['upscale_model'] != 'None':
                if not folder_paths.get_full_path('latent_upscale_models', recipe['upscale_model']):
                    raise NativePlanError('UPSCALER_MISSING', 'The selected latent upscaler is unavailable.')
        directory = _project_directory(project_name)
        state = preflight_native_sources(directory, read_native_manifest(directory), plans, selected,
                                         recipe, previous_frames, contents, passthrough)
        # Resolve every task's references before the first sampler. Reuse the
        # static preparation cache when each task executes later.
        errors = []
        if project_static is not None:
            for index in selected:
                try:
                    plan = plans[index]
                    prepared = EasyH3ProjectStaticPrepare.execute(project_static=project_static,
                        task_index=index, task_start_frame=plan.start_frame,
                        task_duration_frames=plan.end_frame-plan.start_frame, fps=24,
                        generation_mode='reference' if index in passthrough else h3_generation_mode(h3_task_type(entries[index], tracks_info)))
                    MultiTrackTaskOutput.execute(tracks_info=prepared.result[1], task_index=index, prompt_format='default')
                except (OSError, KeyError, TypeError, ValueError, RuntimeError) as error:
                    errors.append(f'Task {index + 1}: {error}')
        if errors:
            raise NativePlanError('PREFLIGHT_MEDIA', '\n' + '\n'.join(errors))
        return io.NodeOutput(state)


class EasyH3NativePrepare(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(node_id="easy h3NativePrepare", display_name="H3 Native Prepare",
                         category="EasyUse/H3/dev", is_dev_only=True, not_idempotent=True,
                         inputs=[io.Latent.Input("latent"), io.Model.Input("model"),
                                 io.Model.Input("second_model", optional=True), io.String.Input("project_name"),
                                 io.String.Input("plan_json"), io.String.Input("recipe_json"),
                                 io.Sigmas.Input("sigmas", optional=True), io.Sigmas.Input("second_sigmas", optional=True),
                                 io.Vae.Input("vae", optional=True), io.Vae.Input("audio_vae", optional=True),
                                 io.String.Input("previous_frame_source", optional=True),
                                 io.AnyType.Input("previous", optional=True), io.AnyType.Input("run_state", optional=True)],
                         outputs=[io.Latent.Output("latent"), io.Latent.Output("high_context"),
                                  io.Latent.Output("low_context"), io.AnyType.Output("native_state")])

    @classmethod
    def execute(cls, latent: dict[str, Any], model: Any, project_name: str, plan_json: str,
                recipe_json: str, second_model: Any = None, previous: Any = None,
                sigmas: torch.Tensor | None = None, second_sigmas: torch.Tensor | None = None,
                vae: Any = None, audio_vae: Any = None, previous_frame_source: str | None = None, run_state: dict[str, Any] | None = None) -> io.NodeOutput:
        del previous
        adapter = _native_model_adapter(model)
        if second_model is not None:
            _native_model_adapter(second_model)
        plan = NativeTaskPlan(**json.loads(plan_json))
        recipe = {**json.loads(recipe_json), "adapter": adapter}
        recipe["model_patch_revision"] = str(getattr(model, "patches_uuid", "unavailable"))
        recipe["second_model_patch_revision"] = str(getattr(second_model, "patches_uuid", "unavailable"))
        for key, schedule in (("sigmas", sigmas), ("second_sigmas", second_sigmas)):
            if schedule is not None:
                if schedule.ndim != 1 or schedule.numel() < 2 or not torch.isfinite(schedule).all():
                    raise NativePlanError("SCHEDULE", "Sampling schedules must be finite one-dimensional sigma sequences.")
                recipe[key] = schedule.detach().cpu().tolist()
        mode = recipe["sampling_mode"]
        layout = native_stage_layout(recipe)
        high_stage, high_size = layout["high"]
        low_stage = layout.get("low", (None, None))[0]
        high_source = low_source = None
        high_parent = low_parent = None
        if plan.parent_segment_id:
            directory = _project_directory(project_name)
            manifest = read_native_manifest(directory)
            refresh_native_dependencies(manifest)
            source = (run_source(run_state, plan.parent_segment_id) if run_state is not None
                      else generation_snapshot(manifest, plan.parent_segment_id, recipe.get("parent_index", 0)))
            generation_id, generation = source["generation"], source["record"]
            descriptors = generation.get("native", {})
            seed_window = None
            imported_parent = False

            def load_stage(label: str, stage: str, size: tuple[int, int]) -> dict[str, Any]:
                nonlocal seed_window, imported_parent
                if label in descriptors:
                    saved = load_native_latent(directory, descriptors[label])
                    if recipe.get("parent_plan"):
                        validate_parent_range(validate_native_latent(saved), NativeTaskPlan(**recipe["parent_plan"]))
                    if label == "high":
                        imported_parent = validate_native_latent(saved)["source_kind"] == "imported_seed"
                    try:
                        sliced = slice_native_context(saved, plan, stage, size)
                        if label == "low" and high_source is not None and sliced["h3_native_slice"]["audio_origin_units"] != high_source["h3_native_slice"]["audio_origin_units"]:
                            raise NativePlanError("STAGE_CLOCK", "The saved low stage uses a different audio clock from the selected high source.")
                        return saved
                    except NativePlanError as error:
                        if error.code not in {"STAGE_MISMATCH", "SIZE_MISMATCH", "STAGE_CLOCK"}:
                            raise
                        if not imported_parent and not recipe.get("allow_vae_fallback"):
                            raise
                kind = "imported_seed" if imported_parent else "rebuilt_generated"
                if kind == "rebuilt_generated" and not recipe.get("allow_vae_fallback"):
                    raise NativePlanError("VAE_FALLBACK_REQUIRED", f"The predecessor has no {label} native source. Regenerate it or allow VAE fallback.", plan.segment_id)
                if vae is None or audio_vae is None or "parent_plan" not in recipe:
                    raise NativePlanError("SOURCE_ADAPTER", "A delivered source rebuild needs the declared predecessor plan and first loader's VAEs.")
                if not generation.get("video") or generation.get("sampling_pass") == "first":
                    raise NativePlanError("SOURCE_ADAPTER", "This saved version has no completed delivered video to rebuild; regenerate it.")
                source_plan = NativeTaskPlan(**recipe["parent_plan"])
                path = native_child_path(directory, generation["video"])
                if source.get("video_sha256") and file_checksum(path) != source["video_sha256"]:
                    raise NativePlanError("SOURCE_CHANGED", "The delivered source changed after preflight.", plan.segment_id)
                if seed_window is None:
                    seed_window = read_delivered_seed(path, source_plan.end_frame - source_plan.start_frame)
                seed_recipe = {**recipe, "source_file": path.name, "source_sha256": file_checksum(path), "source_generation": generation_id}
                return encode_native_seed(*seed_window, vae, audio_vae, source_plan,
                    "imported_seed" if imported_parent else stage, seed_recipe,
                    size[1] * 16, size[0] * 16, kind, bool(recipe.get("allow_vae_fallback")),
                    dependency_id=media_version_id(plan.parent_segment_id, generation_id, generation),
                    native_audio_context=high_source if label == "low" else None)

            full_high = load_stage("high", high_stage, high_size)
            high_parent = validate_native_latent(full_high)
            imported_parent = high_parent["source_kind"] == "imported_seed"
            expected_parent = recipe.get("parent_plan", {})
            if (high_parent["end_frame"] != plan.start_frame
                    or (expected_parent and any(high_parent[key] != expected_parent[key] for key in ("start_frame", "end_frame")))):
                raise NativePlanError("PARENT_RANGE", "The predecessor's delivered range changed; regenerate it before continuing.", plan.segment_id)
            high_source = slice_native_context(full_high, plan, high_stage, high_size)
            if low_stage:
                low_size = layout["low"][1]
                full_low = load_stage("low", low_stage, low_size)
                low_parent = validate_native_latent(full_low)
                low_source = slice_native_context(full_low, plan, low_stage, low_size)
            else:
                low_source = high_source
        reasons = tuple(recipe.get("fallback_reasons", []))
        high_meta = new_native_metadata(plan, high_stage, recipe, high_parent, reasons)
        low_meta = new_native_metadata(plan, low_stage, recipe, low_parent, reasons) if low_stage else None
        if low_meta is not None and low_parent is not None:
            if low_meta["audio_origin_units"] != high_meta["audio_origin_units"] or low_meta["audio_ticks"] != high_meta["audio_ticks"]:
                raise NativePlanError("STAGE_CLOCK", "The two stages use different audio clocks; regenerate their shared predecessor.")
            high_meta["reference_artifact_ids"] = [low_parent["artifact_id"]]
            for item in low_parent.get("fallback_history", []):
                if item not in high_meta["fallback_history"]:
                    high_meta["fallback_history"].append(item)
        state = {"high": high_meta, "low": low_meta}
        if previous_frame_source:
            identity = json.loads(previous_frame_source)
            manifest = read_native_manifest(_project_directory(project_name))
            segment = manifest.get("segments", {}).get(str(identity["segment_index"]), {})
            record = segment.get("generations", {}).get(str(identity["generation"]), {})
            if record.get("video") != identity["video"]:
                raise NativePlanError("REFERENCE_VERSION", "The previous-frame version changed during preparation.")
            dependency = media_version_id(segment.get("segment_id", recipe["previous_segment_id"]), identity["generation"], record)
            for metadata in (high_meta, low_meta):
                if metadata is not None:
                    metadata.setdefault("reference_artifact_ids", []).append(dependency)
                    metadata["previous_frame_source"] = identity
                    for ancestor in record.get("fallback_history", []):
                        if ancestor not in metadata["fallback_history"]:
                            metadata["fallback_history"].append(ancestor)
        return io.NodeOutput(prepare_native_canvas(latent, high_meta), high_source, low_source, state)


class EasyH3NativeMasked(io.ComfyNode):
    """Hard native video prefix and an eight-tick half-cosine audio release."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(node_id="easy h3NativeMasked", display_name="H3 Native Masked Context", category="EasyUse/H3/dev",
                         is_dev_only=True, inputs=[io.Latent.Input("latent"), io.Latent.Input("context_latent"),
                                                 io.Boolean.Input("refine", default=False)],
                         outputs=[io.Latent.Output("latent")])

    @classmethod
    def execute(cls, latent: dict[str, Any], context_latent: dict[str, Any], refine: bool = False) -> io.NodeOutput:
        from ..modules.motion_context.drift_control_av import prepare_context_swap_latent

        if not context_latent.get("h3_native_source"):
            raise NativePlanError("MASKED_SOURCE", "Masked context requires a verified native source adapter.")
        prepared, _, _ = prepare_context_swap_latent(latent, context_latent, 39,
                                                     continue_audio=not refine, freeze_audio=refine)
        return io.NodeOutput(prepared)


class EasyH3NativeDriftModel(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(node_id="easy h3NativeDriftModel", display_name="H3 Native Drift Stage", category="EasyUse/H3/dev",
                         is_dev_only=True, inputs=[io.Model.Input("model"), io.Latent.Input("latent"), io.Sigmas.Input("sigmas")],
                         outputs=[io.Model.Output("model")])

    @classmethod
    def execute(cls, model: Any, latent: dict[str, Any], sigmas: torch.Tensor) -> io.NodeOutput:
        from ..modules.motion_context.drift_control_av import install_drift_control_av_model

        return io.NodeOutput(install_drift_control_av_model(model, latent, sigmas, prefix_steps=12))


class EasyH3NativeLockedVideoInfo(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(node_id="easy h3NativeLockedVideoInfo", display_name="H3 Native Locked Video Window",
                         category="EasyUse/H3/dev", is_dev_only=True,
                         inputs=[TYPE_TRACKS_INFO.Input("tracks_info"), io.String.Input("plan_json"), io.String.Input("track_id")],
                         outputs=[TYPE_TRACKS_INFO.Output("tracks_info")])

    @classmethod
    def execute(cls, tracks_info: dict[str, Any], plan_json: str, track_id: str) -> io.NodeOutput:
        plan = NativeTaskPlan(**json.loads(plan_json))
        source = next((track for track in tracks_info.get("tracks", []) if track.get("id") == track_id), None)
        if source is None:
            raise NativePlanError("LOCK_VIDEO_SOURCE", "The locked video track was removed before execution.")
        # Reuse TaskOutput's external media resolver with an explicit raw window.
        # A terminal sentinel bounds its existing next-task crop logic.
        info = {**tracks_info, "tracks": [{"type": "task", "segments": [
            {"id": "native-reference-window", "start_frame": plan.raw_start_frame, "end_frame": plan.end_frame, "content": {}},
            {"id": "native-reference-end", "start_frame": plan.end_frame, "end_frame": plan.end_frame + 1, "content": {}},
        ]}, source], "task_markers": []}
        for key in ("h3_native", "_preloaded_media", "_easy_media_runtime_cache"):
            info.pop(key, None)
        return io.NodeOutput(info)


class EasyH3NativeAudioLock(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(node_id="easy h3NativeAudioLock", display_name="H3 Native Audio Lock", category="EasyUse/H3/dev",
                         is_dev_only=True, inputs=[io.Latent.Input("latent"), io.AnyType.Input("native_state"),
                             io.Audio.Input("audio"), io.Vae.Input("audio_vae"), io.String.Input("intervals_json")],
                         outputs=[io.Latent.Output("latent"), io.Audio.Output("delivered_locked_audio")])

    @classmethod
    def execute(cls, latent: dict[str, Any], native_state: dict[str, Any], audio: dict[str, Any] | None,
                audio_vae: Any, intervals_json: str) -> io.NodeOutput:
        from ..utils.h3_native_lock import lock_native_audio, native_locked_audio_view

        return io.NodeOutput(lock_native_audio(latent, native_state["high"], audio, audio_vae, json.loads(intervals_json)),
                             native_locked_audio_view(native_state["high"], audio))


class EasyH3NativeSeed(io.ComfyNode):
    """Import a bounded suffix once; subsequent stages retain its provenance."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(node_id="easy h3NativeSeed", display_name="H3 Native Seed", category="EasyUse/H3/dev",
                         is_dev_only=True, inputs=[io.String.Input("video_path"), io.String.Input("plan_json"),
                             io.String.Input("recipe_json"), io.Vae.Input("vae"), io.Vae.Input("audio_vae")],
                         outputs=[io.Latent.Output("high"), io.Latent.Output("low"), io.Audio.Output("seed_audio")])

    @classmethod
    def execute(cls, video_path: str, plan_json: str, recipe_json: str, vae: Any, audio_vae: Any) -> io.NodeOutput:
        plan = NativeTaskPlan(**json.loads(plan_json))
        recipe = json.loads(recipe_json)
        path = _h3_project_source_path(video_path, Path(folder_paths.get_output_directory()).resolve())
        images, audio = read_delivered_seed(path, plan.end_frame - plan.start_frame)
        recipe = {**recipe, "source_sha256": file_checksum(path)}
        kind = "rebuilt_generated" if recipe.get("fallback_reasons") else "imported_seed"
        high = encode_native_seed(images, audio, vae, audio_vae, plan, "imported_seed", recipe,
                                  recipe["target_width"], recipe["target_height"], kind,
                                  bool(recipe.get("allow_vae_fallback")))
        low = None
        mode = recipe.get("context_sampling_mode")
        if mode in {"dual", "selflift"}:
            if mode == "dual":
                width, height = recipe["first_width"], recipe["first_height"]
            else:
                width, height = (max(2, round(recipe[key] / 16 * recipe["lowres_scale"] / 2) * 2) * 16
                                 for key in ("target_width", "target_height"))
            low = encode_native_seed(images, audio, vae, audio_vae, plan, "imported_seed", recipe,
                                     width, height, kind, bool(recipe.get("allow_vae_fallback")))
        return io.NodeOutput(high, low, audio)


class EasyH3NativeResult(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(node_id="easy h3NativeResult", display_name="H3 Native Result", category="EasyUse/H3/dev",
                         is_dev_only=True, inputs=[io.Latent.Input("latent"), io.AnyType.Input("native_state"),
                                                   io.Latent.Input("low_latent", optional=True)],
                         outputs=[io.Latent.Output("raw_high"), io.Latent.Output("raw_low"), io.AnyType.Output("native_state")])

    @classmethod
    def execute(cls, latent: dict[str, Any], native_state: dict[str, Any], low_latent: dict[str, Any] | None = None) -> io.NodeOutput:
        high = stamp_native_result(latent, native_state["high"])
        low = stamp_native_result(low_latent, native_state["low"]) if low_latent is not None and native_state["low"] else None
        return io.NodeOutput(high, low, {"high": high["h3_native"], "low": low["h3_native"] if low else None})


class EasyH3NativeMediaView(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(node_id="easy h3NativeMediaView", display_name="H3 Native Media View", category="EasyUse/H3/dev",
                         is_dev_only=True, inputs=[io.AnyType.Input("native_state"), io.Audio.Input("audio"),
                                                   io.Image.Input("images", optional=True)],
                         outputs=[io.Image.Output("images"), io.Audio.Output("audio")])

    @classmethod
    def execute(cls, native_state: dict[str, Any], audio: dict[str, Any], images: torch.Tensor | None = None) -> io.NodeOutput:
        images, audio = trim_native_media(native_state["high"], images, audio)
        return io.NodeOutput(images, audio)


class EasyH3NativeArtifact(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(node_id="easy h3NativeArtifact", display_name="H3 Native Artifact", category="EasyUse/H3/dev",
                         is_dev_only=True, is_output_node=True, not_idempotent=True,
                         inputs=[io.String.Input("project_name"), io.Int.Input("segment_index", min=0),
                                 TYPE_TRACKS_INFO.Input("tracks_info"), io.Latent.Input("latent"),
                                 io.Latent.Input("low_latent", optional=True), io.Audio.Input("raw_audio"),
                                 io.Audio.Input("audio", optional=True), io.Audio.Input("locked_audio", optional=True), io.String.Input("video_path", default="", optional=True),
                                 io.Image.Input("last_frame", optional=True), io.AnyType.Input("previous", optional=True),
                                 io.AnyType.Input("run_state", optional=True)],
                         outputs=[io.String.Output("project_name")])

    @classmethod
    def execute(cls, project_name: str, segment_index: int, tracks_info: dict[str, Any], latent: dict[str, Any],
                raw_audio: dict[str, Any], low_latent: dict[str, Any] | None = None,
                audio: dict[str, Any] | None = None, video_path: str = "", last_frame: torch.Tensor | None = None,
                previous: Any = None, locked_audio: dict[str, Any] | None = None,
                run_state: dict[str, Any] | None = None) -> io.NodeOutput:
        del previous
        name = safe_h3_project_name(project_name)
        plans = compile_native_plan(tracks_info)
        meta = validate_native_latent(latent)
        if plans[segment_index].segment_id != meta["segment_id"]:
            raise NativePlanError("SEGMENT_ID", "The render plan changed before saving; the prior version is intact.")
        directory = _project_directory(name)
        directory.mkdir(parents=True, exist_ok=True)
        latents = {"high": latent, **({"low": low_latent} if low_latent is not None else {})}
        compact = compact_h3_task_segments(tracks_info)
        fields = {"project_name": name, "width": tracks_info["width"], "height": tracks_info["height"],
                  "fps": 24, "h3_native": tracks_info["h3_native"],
                  "task_segments": [{**compact[index], **plan.as_dict()} for index, plan in enumerate(plans)]}
        recipe = meta["recipe"]
        record = {"continuity_mode": meta["continuity_mode"], "seed": recipe.get("seed", 0),
                  "sampling_pass": "first" if recipe.get("first_pass_only") else "second" if recipe["sampling_mode"] == "dual" else "single",
                  "audio_locked": locked_audio is not None,
                  "raw_audio_sample_rate": raw_audio["sample_rate"], "native_recipe": recipe,
                  **({"previous_frame_source": meta["previous_frame_source"]} if meta.get("previous_frame_source") else {}),
                  "fallback_history": meta["fallback_history"]}
        with tempfile.TemporaryDirectory(prefix=".native-media-", dir=directory) as work:
            media: dict[str, Path] = {}
            if video_path:
                media["video"] = _h3_project_source_path(video_path, Path(folder_paths.get_output_directory()).resolve())
            elif audio is not None:
                media["audio"] = Path(work) / "delivered.wav"
                save_h3_audio(audio, media["audio"], subtype="FLOAT")
            else:
                raise NativePlanError("OUTPUT_MISSING", "No delivered media was produced; the prior version is intact.")
            if locked_audio is not None:
                media["locked_audio"] = Path(work) / "locked.wav"
                save_h3_audio(locked_audio, media["locked_audio"], subtype="FLOAT")
            media["raw_audio"] = Path(work) / "raw.wav"
            save_h3_audio(raw_audio, media["raw_audio"], subtype="FLOAT")
            if last_frame is not None:
                media["last_frame"] = Path(work) / "last.png"
                save_tail_image(last_frame, media["last_frame"])
            saved = commit_native_generation(directory, segment_index, fields, record, latents, media)
            if run_state is not None:
                published = read_native_manifest(directory)
                segment = next(item for item in published['segments'].values() if item.get('segment_id') == meta['segment_id'])
                version = next(key for key, item in segment['generations'].items()
                               if item.get('native', {}).get('high', {}).get('metadata', {}).get('artifact_id') == meta['artifact_id'])
                run_state['produced'][meta['segment_id']] = {'segment_index': segment_index, 'generation': version, 'record': saved}
            staged_video = media.get("video")
            if (staged_video is not None and staged_video.name.startswith(".staging_video_")
                    and staged_video.parent.resolve() == directory.resolve()):
                try:
                    staged_video.unlink()
                except OSError as error:
                    logging.warning("Native generation saved; could not remove temporary video %s: %s", staged_video.name, error)
        _notify_multitrack_project_refresh(name, "after_save", segment_index)
        return io.NodeOutput(name)
