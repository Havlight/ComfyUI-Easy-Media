"""Anchor continuity for H3 tasks: hold, Context Noise, Keystone, Capstone, Late Lock and restore.

Based on roycho87/geiru_context_suite (formerly clown_context_suite, GPL-3.0). Geiru
Context holds the start of the previous shot's tail at the start of a shot, with the
recommended drop_last of 17 frames: of the 39 overlap frames the first 22 are held and
the last 17 are drawn again. Geiru Context Noise (ethanfel's context noise) gives the
held latents after the first part of the current noise, fading to none at the join.
Geiru Keystone locks the anchor clip's first single-frame latent (the keystone) into the
shot's first latent and tacks its last single-frame latent (the capstone) onto the back
of the video for each model call; Geiru Late Lock brings the back latent in from a later
step, so the shot settles its own pose first. The real held latents are put back after
sampling.

H3 video latents come in 17-frame blocks of (1, 4, 4, 4, 4) frames, so every fifth
latent is a single frame: the keystone and capstone are such latents.

A task owns the whole overlap in the composed video, so the previous task's last 39
frames, where the capstone pulls back toward the anchor, are replaced by this task's
first 39: the held 22 copy them and the redrawn 17 continue from there.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import torch

from .core import (
    _audio_tail_from_latent,
    _merge_noise_mask,
    _noise_mask_streams,
    _official_nested_tensor,
    _steps_for_frames,
    _streams_from_latent,
    _video_tail_from_latent,
)

try:
    from ...utils.h3_anchor import context_noise_ramp
except ImportError:
    from utils.h3_anchor import context_noise_ramp

LOGGER = logging.getLogger("easy_media.h3_anchor")

BLOCK_FRAMES = 17
BLOCK_LATENTS = 5
AUDIO_TICKS_PER_FRAME = 5.0 / 3.0
_RESTORE_KEY = "easy_media_h3_anchor_restore"


def _av_parts(latent: dict[str, Any], who: str) -> tuple[torch.Tensor, torch.Tensor]:
    parts = _streams_from_latent(latent)
    if len(parts) < 2:
        raise ValueError(f"{who}: the latent has no audio stream")
    video, audio = parts[:2]
    if video.ndim == 4:
        video = video.unsqueeze(0)
    if audio.ndim == 3:
        audio = audio.unsqueeze(0)
    if video.ndim != 5 or audio.ndim != 4:
        raise ValueError(f"{who}: expected H3 video/audio latent streams")
    return video, audio


def anchor_hold_latent(
    latent: dict[str, Any],
    context_latent: dict[str, Any],
    context_frames: int,
    held_frames: int,
    noise: float = 0.0,
    freeze_audio: bool = False,
) -> tuple[dict[str, Any], int, dict[int, torch.Tensor]]:
    """Geiru Context and Context Noise: hold the start of the previous task's tail in this task.

    The tail covers the ``context_frames`` overlap; its first ``held_frames`` are copied into the
    start of this task and locked, with the audio under them, and the rest of the overlap is drawn
    again. ``noise`` gives the held latents after the first part of the current noise
    (``context_noise_ramp``); those latents are returned to be put back after sampling.
    ``freeze_audio`` keeps the incoming audio whole instead (the second pass, whose audio comes
    from the first). Returns the latent, the number of held video latents and the held latents
    to restore.
    """
    who = "easy h3 anchor hold"
    target_video, target_audio = _av_parts(latent, who)
    blocks, _, covered = _video_tail_from_latent(context_latent, int(context_frames))
    if covered != int(context_frames):
        raise RuntimeError(f"{who}: the context span changed")
    copied = torch.cat(blocks, dim=2)
    held_steps = _steps_for_frames(int(held_frames))
    if held_steps is None or int(held_frames) > int(context_frames):
        raise ValueError(f"{who}: {held_frames} held frames do not end on a latent within the overlap")
    if int(copied.shape[2]) >= int(target_video.shape[2]):
        raise ValueError(f"{who}: the overlap must be shorter than the task")
    if copied.shape[1] != target_video.shape[1] or copied.shape[3:] != target_video.shape[3:]:
        raise ValueError(f"{who}: the context and task video latents differ in shape")
    video = target_video.clone()
    video[:, :, :held_steps] = copied[:, :, :held_steps].to(device=video.device, dtype=video.dtype)
    video_mask = torch.ones_like(video[:, :1], dtype=torch.float32)
    video_mask[:, :, :held_steps] = 0.0
    ramp = context_noise_ramp(held_steps, noise)
    for index, value in ramp:
        video_mask[:, :, index] = value
    originals = {index: copied[:, :, index:index + 1].clone() for index, _ in ramp}

    audio = target_audio
    if freeze_audio:
        audio_mask = torch.zeros_like(audio[:, :1], dtype=torch.float32)
        held_ticks = int(audio.shape[-1])
    else:
        tail, tail_ticks, _ = _audio_tail_from_latent(context_latent, int(context_frames))
        held_ticks = min(round(int(held_frames) * AUDIO_TICKS_PER_FRAME), tail_ticks, int(audio.shape[-1]) - 1)
        if tail.shape[:3] != audio.shape[:3]:
            raise ValueError(f"{who}: the context and task audio latents differ in shape")
        audio = audio.clone()
        audio[..., :held_ticks] = tail[..., :held_ticks].to(device=audio.device, dtype=audio.dtype)
        audio_mask = torch.ones_like(audio[:, :1], dtype=torch.float32)
        audio_mask[..., :held_ticks] = 0.0

    old_video_mask, old_audio_mask = _noise_mask_streams(latent)
    output = latent.copy()
    output["samples"] = _official_nested_tensor((video, audio))
    output["noise_mask"] = _official_nested_tensor((
        _merge_noise_mask(video_mask, old_video_mask, "video"),
        _merge_noise_mask(audio_mask, old_audio_mask, "audio"),
    ))
    LOGGER.info("Anchor hold: video latents 0-%d held of %d, context noise %s, audio %s",
                held_steps - 1, int(copied.shape[2]), ", ".join(f"{index}: {value}" for index, value in ramp) or "off",
                "frozen" if freeze_audio else f"held for {held_ticks} ticks")
    return output, held_steps, originals


def anchor_source_latents(source: dict[str, Any], drawn_from_frame: int) -> torch.Tensor:
    """The keystone and capstone of an anchor task's saved latent, as [B, C, 2, H, W].

    The keystone is the first single-frame latent the task drew itself, from ``drawn_from_frame``
    on (latent 0 of a task that starts a chain, latent 10 of one that holds 22 frames), the
    capstone its last single-frame latent. A passthrough task keeps only the last 39 frames of
    its clip, so both come from those.
    """
    video = _streams_from_latent(source)[0]
    if video.ndim == 4:
        video = video.unsqueeze(0)
    if video.ndim != 5:
        raise ValueError("easy h3 anchor source: expected a 5-D video latent")
    capstone = BLOCK_LATENTS * ((int(video.shape[2]) - 1) // BLOCK_LATENTS)
    keystone = min(BLOCK_LATENTS * math.ceil(int(drawn_from_frame) / BLOCK_FRAMES), capstone)
    picked = torch.cat([video[:, :, keystone:keystone + 1], video[:, :, capstone:capstone + 1]], dim=2)
    return picked.detach().to("cpu", copy=True).contiguous()


def graft_keystone(
    latent: dict[str, Any], anchor: torch.Tensor, front: str | None,
) -> tuple[dict[str, Any], dict[int, torch.Tensor]]:
    """Geiru Keystone's front: lock the keystone (or capstone) into the task's first latent.

    Returns the latent and the first latent it replaced when that latent was held, which
    the restore puts back after sampling.
    """
    who = "easy h3 anchor keystone"
    video, audio = _av_parts(latent, who)
    if anchor.ndim != 5 or anchor.shape[2] != 2:
        raise ValueError(f"{who}: expected the keystone and capstone as [B, C, 2, H, W]")
    if anchor.shape[1] != video.shape[1] or anchor.shape[3:] != video.shape[3:]:
        raise ValueError(f"{who}: the anchor latent {tuple(anchor.shape)} does not match the task {tuple(video.shape)}")
    if front is None:
        return latent, {}
    old_video_mask, old_audio_mask = _noise_mask_streams(latent)
    held = old_video_mask is not None and float(old_video_mask[:, :, 0:1].max()) < 0.5
    original = video[:, :, 0:1].clone()
    video = video.clone()
    pick = 0 if front == "keystone" else 1
    video[:, :, 0:1] = anchor[:, :, pick:pick + 1].to(device=video.device, dtype=video.dtype)
    video_mask = torch.ones_like(video[:, :1], dtype=torch.float32)
    video_mask[:, :, 0:1] = 0.0
    output = latent.copy()
    output["samples"] = _official_nested_tensor((video, audio))
    audio_mask = old_audio_mask if old_audio_mask is not None else torch.ones_like(audio[:, :1], dtype=torch.float32)
    output["noise_mask"] = _official_nested_tensor((_merge_noise_mask(video_mask, old_video_mask, "video"), audio_mask))
    LOGGER.info("Anchor keystone: the %s locked into latent 0", front)
    return output, ({0: original} if held else {})


def _put_back(latent: torch.Tensor, shapes: Any, originals: dict[int, torch.Tensor]) -> tuple[torch.Tensor, list[int]]:
    """A copy of a packed (or video-only) latent with the held latents in their slots again."""
    import comfy.utils

    multi = shapes is not None and len(shapes) > 1
    parts = [part.clone() for part in comfy.utils.unpack_latents(latent, shapes)] if multi else [latent.clone()]
    video = parts[0]
    done = []
    for slot, value in originals.items():
        if (video.ndim == 5 and slot < video.shape[2] and value.shape[1] == video.shape[1]
                and value.shape[3:] == video.shape[3:]):
            video[:, :, slot:slot + 1] = value.to(device=video.device, dtype=video.dtype)
            done.append(slot)
    parts[0] = video
    return (comfy.utils.pack_latents(parts)[0] if multi else parts[0]), done


def _restore_wrapper(originals: dict[int, torch.Tensor]):
    """OUTER_SAMPLE wrapper: put the held latents the keystone and Context Noise changed back
    into the output, and into the denoised latent the step callback sees, which sampler nodes
    return as their denoised output (the one a Project saves)."""
    def wrapper(executor, *args, **kwargs):
        shapes = kwargs.get("latent_shapes", None)
        args = list(args)
        # outer_sample(noise, latent_image, sampler, sigmas, denoise_mask, callback, ...)
        inner = args[5] if len(args) > 5 else kwargs.get("callback")
        if inner is not None:
            def callback(step, x0, x, total_steps):
                try:
                    x0 = _put_back(x0, shapes, originals)[0]
                except Exception as error:  # A preview or capture never stops the render.
                    LOGGER.warning("Anchor restore: could not put the held latent back into a step (%s)", error)
                return inner(step, x0, x, total_steps)
            if len(args) > 5:
                args[5] = callback
            else:
                kwargs["callback"] = callback
        out = executor(*args, **kwargs)
        try:
            out, done = _put_back(out, shapes, originals)
            if done:
                LOGGER.info("Anchor restore: put the held latent(s) %s back into the output", done)
        except Exception as error:  # A finished render is never lost over the restore.
            LOGGER.warning("Anchor restore: could not put the held latent back (%s); output left as sampled", error)
        return out
    return wrapper


def _tack_wrapper(back: torch.Tensor, start: int, previous):
    """model_function_wrapper: from step ``start`` on, tack ``back`` (one locked video latent)
    onto the end of the video for each model call and cut it off the output again."""
    noted = {"shape": False, "streams": False, "tacked": False}

    def wrapper(apply_model, args):
        import comfy.utils

        x, t, c = args["input"], args["timestep"], args["c"]

        def run(xx, cc):
            if previous is None:
                return apply_model(xx, t, **cc)
            return previous(apply_model, {"input": xx, "timestep": t, "c": cc, "cond_or_uncond": args.get("cond_or_uncond")})

        options = c.get("transformer_options", {}) or {}
        sigmas = options.get("sample_sigmas", None)
        step = 0
        if sigmas is not None:
            step = int(torch.argmin((sigmas.float().cpu() - float(t.flatten()[0])).abs()))
        shapes = c.get("latent_shapes", None)
        if shapes is None or len(shapes) < 2:
            if not noted["streams"]:
                LOGGER.warning("Anchor capstone: the model call carries no audio/video latent shapes; not tacked on")
                noted["streams"] = True
            return run(x, c)
        if step < start:
            return run(x, c)
        window = options.get("context_window", None)
        if window is not None and window.index_list[-1] != window.total_frames - 1:
            return run(x, c)  # Context windows: only the window holding the end.
        parts = comfy.utils.unpack_latents(x, shapes)
        video = parts[0]
        if video.ndim != 5 or back.shape[1] != video.shape[1] or back.shape[3:] != video.shape[3:]:
            if not noted["shape"]:
                LOGGER.warning("Anchor capstone: back latent %s does not match the task %s; skipped",
                               tuple(back.shape), tuple(video.shape))
                noted["shape"] = True
            return run(x, c)
        if not noted["tacked"]:
            LOGGER.info("Anchor capstone: tacked on at step %d", step + 1)
            noted["tacked"] = True
        count = video.shape[2]
        tail = back.to(device=video.device, dtype=video.dtype).expand(video.shape[0], -1, -1, -1, -1)
        x2, shapes2 = comfy.utils.pack_latents([torch.cat([video, tail], dim=2)] + list(parts[1:]))
        c2 = dict(c)
        c2["latent_shapes"] = shapes2
        mask = c.get("denoise_mask", None)
        if mask is None:
            mask = torch.ones((video.shape[0], 1, count) + tuple(video.shape[3:]), device=video.device, dtype=torch.float32)
        c2["denoise_mask"] = torch.cat([mask, torch.zeros_like(mask[:, :, :1])], dim=2)  # The tacked latent is locked.
        out = comfy.utils.unpack_latents(run(x2, c2), shapes2)
        out = [out[0][:, :, :count]] + list(out[1:])
        return comfy.utils.pack_latents(out)[0]

    wrapper.easy_media_anchor_back = True
    wrapper.easy_media_previous = previous
    return wrapper


def install_anchor_model(model: Any, back: torch.Tensor | None, start_step: int,
                         originals: dict[int, torch.Tensor]) -> Any:
    """Tack the back latent on from ``start_step`` (Late Lock) and restore the held latents."""
    if back is None and not originals:
        return model
    patched = model.clone()
    if back is not None:
        previous = patched.model_options.get("model_function_wrapper", None)
        if getattr(previous, "easy_media_anchor_back", False):
            previous = previous.easy_media_previous
        patched.set_model_unet_function_wrapper(_tack_wrapper(back, int(start_step), previous))
        LOGGER.info("Anchor capstone: comes in from step %d", int(start_step) + 1)
    if originals:
        from comfy.patcher_extension import WrappersMP

        patched.add_wrapper_with_key(WrappersMP.OUTER_SAMPLE, _RESTORE_KEY, _restore_wrapper(originals))
    return patched
