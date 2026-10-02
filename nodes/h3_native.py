"""Internal native H3 graph boundaries: prepare, sample result, view and publish."""
from __future__ import annotations

import json
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
                                 io.AnyType.Input("previous", optional=True)],
                         outputs=[io.Latent.Output("latent"), io.Latent.Output("high_context"),
                                  io.Latent.Output("low_context"), io.AnyType.Output("native_state")])

    @classmethod
    def execute(cls, latent: dict[str, Any], model: Any, project_name: str, plan_json: str,
                recipe_json: str, second_model: Any = None, previous: Any = None,
                sigmas: torch.Tensor | None = None, second_sigmas: torch.Tensor | None = None,
                vae: Any = None, audio_vae: Any = None) -> io.NodeOutput:
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
        high_stage = {"single": "single_final", "dual": "dual_high_final", "selflift": "selflift_high_final"}[mode]
        low_stage = {"dual": "dual_low_prediction", "selflift": "selflift_low_prediction"}.get(mode)
        if recipe.get("first_pass_only"):
            high_stage = "dual_low_prediction"
        high_source = low_source = None
        high_parent = low_parent = None
        if plan.parent_segment_id:
            directory = _project_directory(project_name)
            manifest = read_native_manifest(directory)
            refresh_native_dependencies(manifest)
            saved_parent = next((s for s in manifest.get("segments", {}).values()
                                 if s.get("segment_id") == plan.parent_segment_id), None)
            if saved_parent is None and not manifest.get("h3_native"):
                saved_parent = manifest.get("segments", {}).get(str(recipe.get("parent_index")))
            if not saved_parent:
                raise NativePlanError("SOURCE_MISSING", "Generate or restore the required predecessor first.", plan.segment_id)
            generation_id = str(saved_parent.get("active_generation"))
            generation = saved_parent.get("generations", {}).get(generation_id)
            if not isinstance(generation, dict) or generation.get("native_stale"):
                raise NativePlanError("STALE_SOURCE", "The predecessor is missing or depends on a replaced version; regenerate it.", plan.segment_id)
            descriptors = generation.get("native", {})
            high_size = (recipe["target_height"] // 16, recipe["target_width"] // 16)
            seed_window = None
            imported_parent = False

            def load_stage(label: str, stage: str, size: tuple[int, int]) -> dict[str, Any]:
                nonlocal seed_window
                if label in descriptors:
                    saved = load_native_latent(directory, descriptors[label])
                    try:
                        slice_native_context(saved, plan, stage, size)
                        return saved
                    except NativePlanError as error:
                        if error.code not in {"STAGE_MISMATCH", "SIZE_MISMATCH"}:
                            raise
                        if not recipe.get("allow_vae_fallback"):
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
            if high_parent["end_frame"] != plan.start_frame:
                raise NativePlanError("PARENT_RANGE", "The predecessor's delivered range changed; regenerate it before continuing.", plan.segment_id)
            high_source = slice_native_context(full_high, plan, high_stage, high_size)
            if low_stage:
                if mode == "selflift":
                    scale = recipe["lowres_scale"]
                    low_size = tuple(max(2, round(size * scale / 2) * 2) for size in high_size)
                else:
                    low_size = (recipe["first_height"] // 16, recipe["first_width"] // 16)
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
        return io.NodeOutput(prepare_native_canvas(latent, high_meta), high_source, low_source, state)


class EasyH3NativeAudioLock(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(node_id="easy h3NativeAudioLock", display_name="H3 Native Audio Lock", category="EasyUse/H3/dev",
                         is_dev_only=True, inputs=[io.Latent.Input("latent"), io.AnyType.Input("native_state"),
                             io.Audio.Input("audio"), io.Vae.Input("audio_vae"), io.String.Input("intervals_json")],
                         outputs=[io.Latent.Output("latent")])

    @classmethod
    def execute(cls, latent: dict[str, Any], native_state: dict[str, Any], audio: dict[str, Any],
                audio_vae: Any, intervals_json: str) -> io.NodeOutput:
        from ..utils.h3_native_lock import lock_native_audio

        return io.NodeOutput(lock_native_audio(latent, native_state["high"], audio, audio_vae, json.loads(intervals_json)))


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
                                 io.Image.Input("last_frame", optional=True), io.AnyType.Input("previous", optional=True)],
                         outputs=[io.String.Output("project_name")])

    @classmethod
    def execute(cls, project_name: str, segment_index: int, tracks_info: dict[str, Any], latent: dict[str, Any],
                raw_audio: dict[str, Any], low_latent: dict[str, Any] | None = None,
                audio: dict[str, Any] | None = None, video_path: str = "", last_frame: torch.Tensor | None = None,
                previous: Any = None, locked_audio: dict[str, Any] | None = None) -> io.NodeOutput:
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
                  "audio_locked": compact[segment_index].get("audio_locked", False),
                  "raw_audio_sample_rate": raw_audio["sample_rate"], "native_recipe": recipe,
                  "fallback_history": meta["fallback_history"]}
        with tempfile.TemporaryDirectory(prefix=".native-media-", dir=directory) as work:
            media: dict[str, Path] = {}
            if video_path:
                media["video"] = _h3_project_source_path(video_path, Path(folder_paths.get_output_directory()).resolve())
            elif audio is not None:
                media["audio"] = Path(work) / "delivered.wav"
                save_h3_audio(audio, media["audio"])
            else:
                raise NativePlanError("OUTPUT_MISSING", "No delivered media was produced; the prior version is intact.")
            if locked_audio is not None:
                media["locked_audio"] = Path(work) / "locked.wav"
                save_h3_audio(locked_audio, media["locked_audio"])
            media["raw_audio"] = Path(work) / "raw.wav"
            save_h3_audio(raw_audio, media["raw_audio"])
            if last_frame is not None:
                media["last_frame"] = Path(work) / "last.png"
                save_tail_image(last_frame, media["last_frame"])
            commit_native_generation(directory, segment_index, fields, record, latents, media)
        _notify_multitrack_project_refresh(name, "after_save", segment_index)
        return io.NodeOutput(name)
