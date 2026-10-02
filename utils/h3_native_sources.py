"""Explicit encoder boundary for external seeds and authorized generated rebuilds."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
from typing import Any

import torch
import torch.nn.functional as F

from ..modules.motion_context.core import _official_nested_tensor, _streams_from_latent
from .h3_native import new_native_metadata, stamp_native_result
from .h3_native_timing import CONTEXT_FRAMES, NativePlanError, NativeTaskPlan


def generated_reference_paths(info: dict[str, Any]) -> list[str]:
    """Recognize saved generated project media before reference encoders run.

    Arbitrary user imports have no verifiable sampler lineage and are initial
    external inputs. Known project exports must not be relabelled as imports.
    """
    import folder_paths
    import json

    output = Path(folder_paths.get_output_directory()).resolve()
    candidates: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("muted") is True or value.get("mute") is True:
                return
            for key, item in value.items():
                if key in {"file_path", "path", "filename"} and isinstance(item, str):
                    candidates.add(item.replace("\\", "/"))
                elif isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(info.get("tracks", []))
    found: set[str] = set()
    for value in candidates:
        marker = "easy_media/projects/"
        if marker not in value:
            continue
        relative = value[value.index(marker):].split("?", 1)[0].split(" [", 1)[0]
        path = (output / relative).resolve()
        if not path.is_relative_to(output / "easy_media/projects"):
            continue
        project = (output / relative).parts[len(output.parts) + 2]
        manifest_path = output / marker / project / "project.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise NativePlanError("REFERENCE_PROVENANCE", f"Cannot verify project reference {relative}: {error}") from error
        matched = False
        for segment in [*manifest.get("segments", {}).values(), *manifest.get("detached_segments", {}).values()]:
            for record in segment.get("generations", {}).values():
                if path.name in {record.get("video"), record.get("audio"), record.get("last_frame"), record.get("raw_audio")}:
                    matched = True
                    if record.get("native", {}).get("high", {}).get("metadata", {}).get("source_kind") != "imported_seed":
                        found.add(relative)
        if not matched:  # Includes composite outputs, whose pixels are generated.
            found.add(relative)
    return sorted(found)


def read_delivered_seed(path: Path, delivered_frames: int) -> tuple[torch.Tensor, dict[str, Any]]:
    """Read only the final 39 frames / 1.625 seconds of a verified delivered file."""
    from .h3_previous_frame import read_video_frames, video_frame_count
    from .video import ffprobe_info, get_ffmpeg_path

    if delivered_frames < CONTEXT_FRAMES or video_frame_count(path) != delivered_frames:
        raise NativePlanError("SOURCE_VIEW_CHANGED", "The saved video's length differs from the predecessor's timeline. Regenerate that task before continuing.")
    images = read_video_frames(path, delivered_frames - CONTEXT_FRAMES, CONTEXT_FRAMES)
    info = ffprobe_info(str(path))
    audio = {"waveform": torch.zeros(1, 2, 52000), "sample_rate": 32000}
    if not info.get("has_audio"):
        return images, audio
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise NativePlanError("FFMPEG", "FFmpeg is required to read the delivered audio for this authorized rebuild.")
    try:
        result = subprocess.run([ffmpeg, "-v", "error", "-ss", str((delivered_frames - CONTEXT_FRAMES) / 24),
                                 "-i", str(path), "-vn", "-t", str(CONTEXT_FRAMES / 24), "-f", "f32le",
                                 "-acodec", "pcm_f32le", "-ar", "32000", "-ac", "2", "-"],
                                capture_output=True, check=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise NativePlanError("SEED_DECODE", f"Failed to read delivered audio: {error}") from error
    if result.returncode or len(result.stdout) != 52000 * 2 * 4:
        raise NativePlanError("SEED_AUDIO_SHORT", "The delivered audio does not cover the full context window; restore or regenerate the source.")
    audio["waveform"] = torch.frombuffer(bytearray(result.stdout), dtype=torch.float32).reshape(-1, 2).T.unsqueeze(0).contiguous()
    return images, audio


def encode_native_seed(
    images: torch.Tensor, audio: dict[str, Any], vae: Any, audio_vae: Any,
    source_plan: NativeTaskPlan, stage: str, recipe: dict[str, Any],
    width: int, height: int, source_kind: str, allow_vae_fallback: bool,
    *, dependency_id: str | None = None, native_audio_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Encode exactly 39 delivered frames. Never pad or stretch an encoded latent."""
    if source_kind not in {"imported_seed", "rebuilt_generated"}:
        raise NativePlanError("ENCODER_PURPOSE", "Native sampler results cannot enter the seed encoder.")
    if source_kind == "rebuilt_generated" and not allow_vae_fallback:
        raise NativePlanError("VAE_FALLBACK_REQUIRED", "This source contains generated media. Enable VAE fallback or regenerate its native source.", source_plan.segment_id)
    if images.ndim != 4 or images.shape[0] < CONTEXT_FRAMES:
        raise NativePlanError("SEED_SHORT", "The delivered source needs at least 39 frames for native continuation.", source_plan.segment_id)
    pixels = images[-CONTEXT_FRAMES:, ..., :3]
    if tuple(pixels.shape[1:3]) != (height, width):
        pixels = F.interpolate(pixels.movedim(-1, 1), size=(height, width), mode="bicubic", align_corners=False).movedim(1, -1)
    rate = int(getattr(audio_vae, "audio_sample_rate", 32000))
    waveform = audio["waveform"]
    if audio["sample_rate"] != rate:
        import torchaudio.functional

        waveform = torchaudio.functional.resample(waveform, int(audio["sample_rate"]), rate)
    needed = CONTEXT_FRAMES * rate // 24
    if waveform.ndim != 3 or waveform.shape[-1] < needed:
        raise NativePlanError("SEED_AUDIO_SHORT", "The seed audio does not cover the complete 39-frame window.")
    waveform = waveform[:1, :, -needed:].contiguous()
    video_samples = vae.encode(pixels.contiguous())
    audio_samples = (_streams_from_latent(native_audio_context)[1].clone() if native_audio_context is not None
                     else audio_vae.encode(waveform.movedim(1, -1)))
    if audio_samples.shape[-1] != 65:
        raise NativePlanError("SEED_AUDIO_GRID", "The audio VAE did not produce exactly 65 ticks; its adapter cannot guarantee native alignment.")
    # External imports have no sampler raw window: their recorded native source
    # is the explicitly encoded delivered suffix, separate from the full media.
    plan = replace(source_plan, continuity_mode="shot", parent_segment_id=None, context_frames=0,
                   raw_start_frame=source_plan.end_frame - CONTEXT_FRAMES, raw_frames=CONTEXT_FRAMES, passthrough=True)
    metadata = new_native_metadata(plan, stage, recipe, source_kind=source_kind,
                                   fallback_reasons=("DELIVERED_SOURCE_REBUILD",) if source_kind == "rebuilt_generated" else ())
    if dependency_id is not None:
        metadata["artifact_id"] = dependency_id
    if native_audio_context is not None:
        # Audio resolution is identical across passes. Preserve its existing
        # clock and native samples when only a missing video stage is rebuilt.
        metadata["audio_origin_units"] = native_audio_context["h3_native_slice"]["audio_origin_units"]
        metadata["reference_artifact_ids"] = [native_audio_context["h3_native_source"]["artifact_id"]]
    return stamp_native_result({"samples": _official_nested_tensor((video_samples, audio_samples))}, metadata)
