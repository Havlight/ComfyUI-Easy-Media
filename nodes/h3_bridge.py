from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import torch
from comfy_api.latest import io
from comfy_execution.graph_utils import GraphBuilder

from ..utils.h3_bridge import (
    BRIDGE_CONTEXT, BRIDGE_LENGTH, BRIDGE_TOTAL, bridge_sources, bridge_tracks_info,
    load_bridge_anchor,
)
from ..utils.h3_project import parse_tracks_info
from ..utils.h3_previous_frame import (
    previous_frame_position, project_generation, video_identity, write_project_manifest,
)


class EasyH3BridgeMask(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="easy h3BridgeMask", display_name="H3 Bridge Mask", category="EasyUse/H3/dev", is_dev_only=True,
            inputs=[io.Latent.Input("latent"), io.Vae.Input("vae"),
                    io.String.Input("project_name"), io.String.Input("source")],
            outputs=[io.Latent.Output("latent")],
        )

    @classmethod
    def execute(cls, latent: dict, vae: Any, project_name: str, source: str) -> io.NodeOutput:
        import comfy.nested_tensor
        video, audio = latent["samples"].unbind()
        metadata = json.loads(source)
        anchors = []
        for side in ("left", "right"):
            frames = load_bridge_anchor(project_name, metadata, side)
            anchors.append(vae.encode(frames))
            del frames
        start, end = anchors
        if start.shape != end.shape or start.shape[2] != 12 or video.shape[2] != 32:
            raise ValueError("Restart Bridge requires 39-frame anchors and a 107-frame H3 latent")
        if start.shape[:2] + start.shape[3:] != video.shape[:2] + video.shape[3:]:
            raise ValueError("Restart Bridge endpoints must match the final project resolution")
        result = video.clone()
        result[:, :, :12] = start.to(result)
        result[:, :, -12:] = end.to(result)
        mask = torch.ones_like(video[:, :1], dtype=torch.float32)
        mask[:, :, :12] = 0
        mask[:, :, -12:] = 0
        existing = latent.get("noise_mask")
        audio_mask = existing.unbind()[1] if existing is not None else torch.ones_like(audio[:, :1])
        return io.NodeOutput({**latent, "samples": comfy.nested_tensor.NestedTensor((result, audio)),
                              "noise_mask": comfy.nested_tensor.NestedTensor((mask, audio_mask))})


class EasyH3BridgeFrames(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(node_id="easy h3BridgeFrames", display_name="H3 Bridge Frames", category="EasyUse/H3/dev",
                         inputs=[io.Image.Input("images")], outputs=[io.Image.Output("images")], is_dev_only=True)

    @classmethod
    def execute(cls, images: torch.Tensor) -> io.NodeOutput:
        if images.shape[0] < BRIDGE_CONTEXT + BRIDGE_LENGTH:
            raise ValueError("H3 bridge decoder returned too few frames")
        return io.NodeOutput(images[BRIDGE_CONTEXT:BRIDGE_CONTEXT + BRIDGE_LENGTH].clone())


class EasyH3BridgeArtifact(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="easy h3BridgeArtifact", display_name="H3 Bridge Artifact", category="EasyUse/H3/dev",
            is_dev_only=True, is_output_node=True, not_idempotent=True,
            inputs=[io.String.Input("project_name"), io.Int.Input("segment_index"), io.String.Input("video_path"),
                    io.String.Input("source")], outputs=[io.String.Output("project_name")],
        )

    @classmethod
    def execute(cls, project_name: str, segment_index: int, video_path: str, source: str) -> io.NodeOutput:
        from .minimax import _h3_project_source_path, _notify_multitrack_project_refresh
        import folder_paths
        metadata = json.loads(source)
        root, manifest, version, record = project_generation(project_name, segment_index)
        for side, index in (("left", segment_index - 1), ("right", segment_index)):
            _, _, selected, candidate = project_generation(project_name, index)
            if video_identity(root, index, selected, candidate) != metadata[side]:
                raise ValueError("Bridge sources changed during sampling; regenerate this boundary")
        filename = f"bridge_{segment_index}_{version}.mp4"
        staged = _h3_project_source_path(video_path, Path(folder_paths.get_output_directory()).resolve())
        try:
            staged.replace(root / filename)
        except OSError as error:
            raise RuntimeError(f"Cannot save replacement bridge {filename}: {error}") from error
        record["bridge_file"] = filename
        record["bridge"] = {**metadata, "file": filename}
        now = time.time()
        record["updated_at"] = now
        manifest["segments"][str(segment_index)]["updated_at"] = now
        manifest["updated_at"] = now
        write_project_manifest(root, manifest)
        _notify_multitrack_project_refresh(project_name, "after_save", segment_index)
        return io.NodeOutput(project_name)


class EasyH3ProjectBridge(io.ComfyNode):
    """Expand one completed boundary using the existing full sampler and saver."""
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="easy h3ProjectBridge", display_name="H3 Project Bridge", category="EasyUse/H3/dev",
            is_dev_only=True, not_idempotent=True, enable_expand=True,
            inputs=[io.String.Input("project_name"), io.Int.Input("segment_index"),
                    io.Custom(io_type="TRACKS_INFO").Input("tracks_info"), io.Model.Input("model"), io.Clip.Input("clip"),
                    io.Vae.Input("vae"), io.Vae.Input("audio_vae"), io.Sampler.Input("sampler"), io.Sigmas.Input("sigmas"),
                    io.Int.Input("seed"), io.Int.Input("width"), io.Int.Input("height"),
                    io.Boolean.Input("enabled_tiling", default=False), io.Int.Input("tile_count", default=2),
                    io.AnyType.Input("previous")], outputs=[io.String.Output("project_name")],
        )

    @classmethod
    def execute(cls, project_name: str, segment_index: int, tracks_info: Any, model: Any, clip: Any,
                vae: Any, audio_vae: Any, sampler: Any, sigmas: Any, seed: int, width: int, height: int,
                enabled_tiling: bool, tile_count: int, previous: Any) -> io.NodeOutput:
        if not callable(getattr(model.model, "_denoise_mask_conds", None)):
            raise RuntimeError("Restart Bridge needs ComfyUI's native H3 latent mask support; update ComfyUI")
        if len(sigmas) < 2 or float(sigmas[0]) < 0.99 or float(sigmas[-1]) != 0:
            raise ValueError("Restart Bridge requires a complete sampling schedule from noise to zero")
        metadata = bridge_sources(project_name, segment_index)
        source = json.dumps(metadata)
        info = bridge_tracks_info(parse_tracks_info(tracks_info), segment_index)
        graph = GraphBuilder()
        task = graph.node("easy multiTrackTaskOutput", tracks_info=info, task_index=0, prompt_format="default")
        images = task.out(4)
        position = previous_frame_position(info["tracks"][0]["segments"][0].get("content", {}))
        if position is not None:
            # Reuse the source recorded by the completed segment, also after Dual resume.
            images = graph.node("easy h3PreviousFrame", images=images, project_name=project_name,
                                segment_index=segment_index, position=position, resume=True).out(0)
        conditioning = graph.node("easy minimaxH3ToVideo", clip=clip, vae=vae, audio_vae=audio_vae,
                                  images=images, audios=task.out(5), videos=task.out(6), prompt=task.out(1),
                                  mode="reference", width=width, height=height, length=BRIDGE_TOTAL,
                                  locked_video_timing_frames=BRIDGE_TOTAL)
        # The original locked audio, when present, remains the timing authority.
        locked = graph.node("easy h3BridgeAudioLock", latent=conditioning.out(1), audio=task.out(8), audio_vae=audio_vae)
        masked = graph.node("easy h3BridgeMask", latent=locked.out(0), vae=vae,
                            project_name=project_name, source=source)
        guider = graph.node("BasicGuider", model=model, conditioning=conditioning.out(0))
        noise = graph.node("RandomNoise", noise_seed=seed)
        sampled = graph.node("easy h3SamplingPreviewSampler", noise=noise.out(0), guider=guider.out(0),
                             sampler=sampler, sigmas=sigmas, latent_image=masked.out(0),
                             enabled_tiling=enabled_tiling, tile_count=tile_count)
        split = graph.node("LTXVSeparateAVLatent", av_latent=sampled.out(1))
        decoded = graph.node("VAEDecode", samples=split.out(0), vae=vae)
        middle = graph.node("easy h3BridgeFrames", images=decoded.out(0))
        saved = graph.node("easy saveVideo", input_mode="images+audio", **{
            "input_mode.images": middle.out(0), "input_mode.fps": 24.0, "output_mode": "hide&save"},
            filename_prefix=f"easy_media/projects/{project_name}/.staging_bridge_{segment_index}")
        artifact = graph.node("easy h3BridgeArtifact", project_name=project_name, segment_index=segment_index,
                              video_path=saved.out(1), source=source)
        return io.NodeOutput(artifact.out(0), expand=graph.finalize())


class EasyH3BridgeAudioLock(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(node_id="easy h3BridgeAudioLock", display_name="H3 Bridge Audio Lock", category="EasyUse/H3/dev",
                         enable_expand=True,
                         inputs=[io.Latent.Input("latent"), io.Vae.Input("audio_vae"), io.Audio.Input("audio", optional=True)],
                         outputs=[io.Latent.Output("latent")], is_dev_only=True)

    @classmethod
    def execute(cls, latent: dict, audio_vae: Any, audio: dict | None = None) -> io.NodeOutput:
        if audio is None:
            return io.NodeOutput(latent)
        graph = GraphBuilder()
        locked = graph.node("easy minimaxH3AudioLock", latent=latent, audio_vae=audio_vae, audio=audio,
                            remix_strength=1.0, short_audio_mode="silence", prepend_frames=0, frame_rate=24.0)
        return io.NodeOutput(locked.out(0), expand=graph.finalize())
