"""Opt-in real-model GPU structural smoke test, not a perceptual quality score.

Run with the ComfyUI Python environment. It never edits model files, and uses
zero text embeddings to isolate the AV canvas/continuation contract.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
import types
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Path to a compatible H3 diffusion model")
    parser.add_argument("--report", required=True, help="Path for the JSON result")
    parser.add_argument("--video-vae", help="Optionally verify actual video decode and external seed encode")
    parser.add_argument("--audio-vae", help="Required together with --video-vae")
    options = parser.parse_args()
    if bool(options.video_vae) != bool(options.audio_vae):
        parser.error("--video-vae and --audio-vae must be provided together")
    model_path, report_path = Path(options.model).resolve(), Path(options.report).resolve()
    sys.argv = [sys.argv[0]]  # Comfy CLI must not parse this test's arguments.
    root = Path(__file__).resolve().parents[2]
    comfy_root = root.parent.parent
    sys.path.insert(0, str(comfy_root))
    for name, path in (("native_gpu_smoke", root), ("native_gpu_smoke.utils", root / "utils"),
                       ("native_gpu_smoke.modules", root / "modules")):
        package = types.ModuleType(name)
        package.__path__ = [str(path)]
        sys.modules[name] = package

    import torch
    import comfy.sd
    import comfy.samplers
    from comfy_extras.nodes_custom_sampler import BasicGuider, RandomNoise, SamplerCustomAdvanced

    native = importlib.import_module("native_gpu_smoke.utils.h3_native")
    timing = importlib.import_module("native_gpu_smoke.utils.h3_native_timing")
    core = importlib.import_module("native_gpu_smoke.modules.motion_context.core")
    if not torch.cuda.is_available():
        raise RuntimeError("This opt-in test requires a CUDA device")
    start = time.monotonic()
    print("Loading the real H3 diffusion model for the native AV structural check", flush=True)
    model = comfy.sd.load_diffusion_model(str(model_path), model_options={"dtype": torch.bfloat16})
    text_dimension = model.model.diffusion_model.condition_proj.in_features
    conditioning = [[torch.zeros(1, 8, text_dimension), {}]]
    sigmas = torch.tensor([1.0, 0.5, 0.0], dtype=torch.float32)
    sampler = comfy.samplers.sampler_object("euler")
    encoder_calls = []

    class NoEncoder:
        def encode(self, *args, **kwargs):
            encoder_calls.append(True)
            raise AssertionError("Native context attempted VAE encoding")

    parent = None
    reports = []
    for index, plan in enumerate([
        timing.NativeTaskPlan("a", 0, 56, "shot", None, 0, 0, 56),
        timing.NativeTaskPlan("b", 56, 73, "context", "a", 39, 17, 56),
    ]):
        parent_meta = parent["h3_native"] if parent else None
        metadata = native.new_native_metadata(plan, "single_final", {"structural_smoke": True}, parent_meta)
        video = torch.zeros(1, 24, timing.video_steps(plan.raw_frames), 16, 16)
        audio = torch.zeros(1, 32, 2, round(plan.raw_frames * 5 / 3))
        latent = native.prepare_native_canvas({"samples": native._official_nested_tensor((video, audio))}, metadata)
        positive = conditioning
        if parent:
            context = native.slice_native_context(parent, plan, "single_final", (16, 16))
            positive, trim, latent = core.apply_reencoded_anchor_motion_context(
                conditioning=conditioning, vae=NoEncoder(), latent=latent,
                context_latent=context, context_length="39", anchor_length="5")
            assert trim == 39
        guider = BasicGuider.execute(model, positive)[0]
        noise = RandomNoise.execute(63000 + index)[0]
        sampled = SamplerCustomAdvanced.execute(noise, guider, sampler, sigmas, latent)[1]
        parent = native.stamp_native_result(sampled, metadata)
        streams = native._streams_from_latent(parent)
        assert all(torch.isfinite(stream).all().item() for stream in streams)
        reports.append({"segment": plan.segment_id, "raw_frames": plan.raw_frames,
                        "audio_ticks": metadata["audio_ticks"], "shapes": [list(t.shape) for t in streams]})
        print(f"Native segment {plan.segment_id} completed with {metadata['audio_ticks']} audio ticks", flush=True)
    result = {"passed": True, "scope": "real-model structural AV test; zero text; not a quality evaluation",
              "device": torch.cuda.get_device_name(0), "torch": torch.__version__,
              "seconds": time.monotonic() - start, "encoder_calls": len(encoder_calls), "segments": reports,
              "peak_cuda_bytes": torch.cuda.max_memory_allocated()}
    if options.video_vae:
        def load_vae(path: str):
            state, metadata = comfy.utils.load_torch_file(str(Path(path).resolve()), return_metadata=True)
            return comfy.sd.VAE(sd=state, metadata=metadata)
        print("Checking actual output decoders and the external seed encoder boundary", flush=True)
        video_vae, audio_vae = load_vae(options.video_vae), load_vae(options.audio_vae)
        raw_images = video_vae.decode(streams[0])
        if raw_images.ndim == 5:  # Match ComfyUI's VAEDecode node IMAGE adapter.
            raw_images = raw_images.reshape(-1, *raw_images.shape[-3:])
        raw_audio = {"waveform": audio_vae.decode(streams[1]).movedim(-1, 1), "sample_rate": audio_vae.audio_sample_rate}
        images, audio = native.trim_native_media(parent["h3_native"], raw_images, raw_audio)
        assert images.shape[0] == 17
        assert audio["waveform"].shape[-1] == timing.sample_at_frame(73, 32000) - timing.sample_at_frame(56, 32000)
        sources = importlib.import_module("native_gpu_smoke.utils.h3_native_sources")
        seed = sources.encode_native_seed(torch.zeros(39, 256, 256, 3),
            {"waveform": torch.zeros(1, 2, 52000), "sample_rate": 32000}, video_vae, audio_vae,
            timing.NativeTaskPlan("external", 0, 56, "shot", None, 0, 0, 56),
            "imported_seed", {}, 256, 256, "imported_seed", False)
        result["vae"] = {"raw_video_frames": len(raw_images), "delivered_video_frames": len(images),
                         "delivered_audio_samples": audio["waveform"].shape[-1],
                         "seed_shapes": [list(t.shape) for t in native._streams_from_latent(seed)]}
        result["seconds"] = time.monotonic() - start
        result["peak_cuda_bytes"] = torch.cuda.max_memory_allocated()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
