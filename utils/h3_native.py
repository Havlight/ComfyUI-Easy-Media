"""Native sampler source contracts. Decoders/encoders do not belong here.

Every working slice is a copy. A delivered view never replaces the raw source.
Clock positions are integer 1/120 second units (see h3_native_timing).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from typing import Any

import torch
import torch.nn.functional as F

from ..modules.motion_context.core import (
    _noise_mask_streams, _official_nested_tensor, _streams_from_latent,
)
from .h3_native_timing import (
    NativePlanError, NativeTaskPlan, audio_clock, round_ratio,
    sample_at_frame, source_video_slice, video_steps,
)

NATIVE_FORMAT = "minimax-h3-av-v1"
NATIVE_STAGES = frozenset({"single_final", "dual_low_prediction", "dual_high_final",
                           "selflift_low_prediction", "selflift_high_final", "imported_seed"})


def recipe_fingerprint(recipe: dict[str, Any]) -> str:
    """Reject opaque values instead of treating object addresses as identity."""
    encoded = json.dumps(recipe, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def new_native_metadata(
    plan: NativeTaskPlan, stage: str, recipe: dict[str, Any],
    parent: dict[str, Any] | None = None,
    fallback_reasons: tuple[str, ...] = (), source_kind: str = "native_sampler",
) -> dict[str, Any]:
    if stage not in NATIVE_STAGES:
        raise NativePlanError("STAGE", f"Unsupported native stage: {stage}.", plan.segment_id)
    if source_kind not in {"native_sampler", "imported_seed", "rebuilt_generated"}:
        raise NativePlanError("ORIGIN", f"Unsupported source origin: {source_kind}.", plan.segment_id)
    if plan.parent_segment_id and (not parent or parent.get("segment_id") != plan.parent_segment_id):
        raise NativePlanError("PARENT_ID", "The source belongs to a different task; regenerate the chain.", plan.segment_id)
    inherited = list(parent.get("fallback_history", [])) if parent else []
    history = inherited + [{"segment_id": plan.segment_id, "reason": reason} for reason in fallback_reasons]
    return {"version": 1, "format": NATIVE_FORMAT, **plan.as_dict(), **audio_clock(plan, parent),
            "artifact_id": uuid.uuid4().hex, "parent_artifact_id": parent.get("artifact_id") if parent else None,
            "stage": stage, "source_kind": source_kind, "recipe": deepcopy(recipe),
            "recipe_fingerprint": recipe_fingerprint(recipe), "fallback_history": history,
            "contains_noisy_state": False}


def validate_native_latent(latent: dict[str, Any]) -> dict[str, Any]:
    meta = latent.get("h3_native")
    if not isinstance(meta, dict) or meta.get("version") != 1 or meta.get("format") != NATIVE_FORMAT:
        raise NativePlanError("UNVERIFIED_SOURCE", "This source has no verified native provenance; regenerate it or allow a supported rebuild.")
    if meta.get("stage") not in NATIVE_STAGES or meta.get("contains_noisy_state") is not False:
        raise NativePlanError("STAGE", "A noisy checkpoint cannot be used as a clean context source.")
    if recipe_fingerprint(meta["recipe"]) != meta.get("recipe_fingerprint"):
        raise NativePlanError("RECIPE", "The source recipe fingerprint is invalid.")
    streams = _streams_from_latent(latent)
    if len(streams) != 2:
        raise NativePlanError("STREAMS", "H3 requires exactly one video and one audio stream.")
    video, audio = streams
    if video.ndim != 5 or audio.ndim != 4 or video.shape[:2] != (1, 24) or audio.shape[:3] != (1, 32, 2):
        raise NativePlanError("SHAPE", "Expected H3 video [1,24,T,H,W] and audio [1,32,2,T].")
    if video.shape[2] != video_steps(meta["raw_frames"]) or audio.shape[-1] != meta["audio_ticks"]:
        raise NativePlanError("CLOCK_SHAPE", "Raw tensor lengths do not match the recorded AV clocks.")
    if "streams" in meta and meta["streams"] != describe_streams(streams):
        raise NativePlanError("SHAPE", "Tensor shape or precision differs from the saved source contract.")
    return meta


def describe_streams(streams: list[torch.Tensor]) -> list[dict[str, Any]]:
    return [{"shape": list(stream.shape), "dtype": str(stream.dtype)} for stream in streams]


def prepare_native_canvas(latent: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Allocate the empty audio canvas's last tick before sampling, never stretch encoded audio."""
    video, audio = _streams_from_latent(latent)
    delta = meta["audio_ticks"] - audio.shape[-1]
    if abs(delta) > 1:
        raise NativePlanError("CANVAS_CLOCK", "The empty audio canvas differs by more than one tick.")
    if delta and torch.count_nonzero(audio).item():
        raise NativePlanError("CANVAS_ENCODED", "Only an empty audio canvas may be extended; apply external audio locks afterwards.")
    adjusted = F.pad(audio, (0, delta)) if delta > 0 else audio[..., :meta["audio_ticks"]].clone()
    output = {**latent, "samples": _official_nested_tensor((video, adjusted)), "h3_native": deepcopy(meta)}
    video_mask, audio_mask = _noise_mask_streams(latent)
    if audio_mask is not None and delta:
        adjusted_mask = F.pad(audio_mask, (0, delta), value=1) if delta > 0 else audio_mask[..., :meta["audio_ticks"]].clone()
        output["noise_mask"] = _official_nested_tensor((video_mask, adjusted_mask))
    validate_native_latent(output)
    return output


def stamp_native_result(latent: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Attach the declared sampler stage after validating the actual result."""
    output = {"samples": latent["samples"], "h3_native": deepcopy(meta)}
    output["h3_native"]["streams"] = describe_streams(_streams_from_latent(output))
    validate_native_latent(output)
    return output


def slice_native_context(
    latent: dict[str, Any], plan: NativeTaskPlan, expected_stage: str,
    expected_size: tuple[int, int] | None = None,
) -> dict[str, Any]:
    meta = validate_native_latent(latent)
    if meta["segment_id"] != plan.parent_segment_id:
        raise NativePlanError("PARENT_ID", "The source task changed; regenerate the required predecessor.", plan.segment_id)
    external_seed = meta["stage"] == "imported_seed" and meta["source_kind"] in {"imported_seed", "rebuilt_generated"}
    final_stages = {"single_final", "dual_high_final", "selflift_high_final"}
    compatible_final = meta["stage"] in final_stages and expected_stage in final_stages
    if meta["stage"] != expected_stage and not external_seed and not compatible_final:
        raise NativePlanError("STAGE_MISMATCH", f"Need {expected_stage}, found {meta['stage']}; regenerate the predecessor in the selected mode.", plan.segment_id)
    video, audio = _streams_from_latent(latent)
    if expected_size is not None and tuple(video.shape[-2:]) != expected_size:
        raise NativePlanError("SIZE_MISMATCH", "The native source resolution changed; regenerate its stage.", plan.segment_id)
    start, end = source_video_slice(plan, meta)
    clock = audio_clock(plan, meta)
    parts = (video[:, :, start:end].detach().to("cpu", copy=True).contiguous(),
             audio[..., clock["source_audio_start"]:clock["source_audio_end"]].detach().to("cpu", copy=True).contiguous())
    return {"samples": _official_nested_tensor(parts), "h3_native_source": deepcopy(meta),
            "h3_native_slice": {"video_start": start, "video_end": end, **clock}}


def trim_native_media(
    meta: dict[str, Any], images: torch.Tensor | None = None,
    audio: dict[str, Any] | None = None,
) -> tuple[torch.Tensor | None, dict[str, Any] | None]:
    """Create the delivered output view without creating a new context source."""
    count = meta["end_frame"] - meta["start_frame"]
    if images is not None:
        prefix = meta["context_frames"]
        if images.shape[0] < prefix + count:
            raise NativePlanError("DECODE_SHORT", "Decoded video does not cover its delivered view.")
        images = images[prefix:prefix + count]
    if audio is not None:
        rate = int(audio["sample_rate"])
        origin = round_ratio(meta["audio_origin_units"] * rate, 120)
        start = sample_at_frame(meta["start_frame"], rate) - origin
        end = sample_at_frame(meta["end_frame"], rate) - origin
        waveform = audio["waveform"]
        if start < 0 or end > waveform.shape[-1]:
            raise NativePlanError("DECODE_SHORT", "Decoded audio does not cover its delivered view.")
        audio = {**audio, "waveform": waveform[..., start:end]}
    return images, audio
