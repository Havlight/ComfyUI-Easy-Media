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
    options = parser.parse_args()
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
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
