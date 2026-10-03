"""Opt-in H3 native A/B and stage validation with real prompt, models and VAEs.

Outputs are local validation artifacts. Small test renders do not establish a
preferred default or imply full-resolution perceptual quality certification.
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
    for name in ('model', 'clip', 'lora', 'video-vae', 'audio-vae', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--modes', default='single,selflift,dual')
    parser.add_argument('--methods', default='context,context_drift')
    parser.add_argument('--upscaler', default='None')
    args = parser.parse_args()
    paths = {name: Path(getattr(args, name.replace('-', '_'))).resolve() for name in ('model', 'clip', 'lora', 'video-vae', 'audio-vae', 'output')}
    paths['output'].mkdir(parents=True, exist_ok=True)
    sys.argv = [sys.argv[0]]
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root.parent.parent))
    for name, path in (('native_matrix', root), ('native_matrix.utils', root / 'utils'), ('native_matrix.modules', root / 'modules')):
        package = types.ModuleType(name)
        package.__path__ = [str(path)]
        sys.modules[name] = package
    import torch
    import comfy.sd
    import comfy.samplers
    import comfy.utils
    import comfy.model_management
    import folder_paths
    from comfy_extras.nodes_custom_sampler import BasicGuider, RandomNoise, SamplerCustomAdvanced
    from PIL import Image, ImageDraw
    import soundfile as sf

    native = importlib.import_module('native_matrix.utils.h3_native')
    timing = importlib.import_module('native_matrix.utils.h3_native_timing')
    core = importlib.import_module('native_matrix.modules.motion_context.core')
    drift = importlib.import_module('native_matrix.modules.motion_context.drift_control_av')
    selflift = importlib.import_module('native_matrix.modules.selflift.sampling')
    upscale = importlib.import_module('native_matrix.modules.selflift.h3_latent_upscale')
    folder_paths.add_model_folder_path('latent_upscale_models', str(root.parent.parent / 'models/latent_upscale_models'))
    cache = paths['output'] / f"conditioning-{paths['clip'].stem}.pt"
    prompt = 'A continuous cinematic medium shot of a woman wearing a blue jacket, slowly turning her head and raising her right hand in a sunny garden. The camera gently moves to the right. Leaves rustle softly. Natural daylight, stable face, consistent clothing, smooth realistic motion.'
    if cache.exists():
        positive = torch.load(cache, weights_only=True)
    else:
        print('Encoding real prompt', flush=True)
        clip = comfy.sd.load_clip(ckpt_paths=[str(paths['clip'])], clip_type=comfy.sd.CLIPType.MINIMAX)
        positive = clip.encode_from_tokens_scheduled(clip.tokenize(prompt))
        torch.save(positive, cache)
        del clip
        comfy.model_management.unload_all_models()
    print('Loading diffusion model and two distinct LoRA patch sets', flush=True)
    base = comfy.sd.load_diffusion_model(str(paths['model']), model_options={'dtype': torch.bfloat16})
    lora = comfy.utils.load_torch_file(str(paths['lora']), safe_load=True)
    model = comfy.sd.load_lora_for_models(base, None, lora, 1.0, 0)[0]
    second = comfy.sd.load_lora_for_models(base, None, lora, 0.8, 0)[0]
    del base, lora
    assert model.patches_uuid != second.patches_uuid
    sigmas = torch.tensor([1, .995, .9825, .9607, .9234, .8553, .7207, .4249, .2125, 0], dtype=torch.float32)
    refine = torch.tensor([.42, .18, 0], dtype=torch.float32)
    sampler = comfy.samplers.sampler_object('euler')
    class NoEncoder:
        def encode(self, *unused, **kwargs):
            raise AssertionError('Generated media attempted VAE re-encoding')
    report = {'scope': 'small real-prompt stage and method comparison; default remains Context',
              'prompt': prompt, 'model': paths['model'].name, 'clip': paths['clip'].name,
              'lora': paths['lora'].name, 'second_lora_strength': .8, 'sigmas': sigmas.tolist(),
              'second_sigmas': refine.tolist(), 'upscaler': args.upscaler, 'runs': []}
    outputs = []

    def sample(stage_model, conditioning, latent, schedule, seed):
        return SamplerCustomAdvanced.execute(RandomNoise.execute(seed)[0], BasicGuider.execute(stage_model, conditioning)[0], sampler, schedule, latent)[1]

    def lift(video, size, **options):
        if args.upscaler == 'None':
            return torch.nn.functional.interpolate(video, size=(video.shape[2], *size), mode='nearest')
        return upscale.learned_latent_lift(video, size, args.upscaler, force_unload=True, **options)

    for mode in args.modes.split(','):
        parents = None
        for method in ['shot', *args.methods.split(',')]:
            began = time.monotonic()
            is_child = method != 'shot'
            plan = timing.NativeTaskPlan('b' if is_child else 'a', 124 if is_child else 0,
                                       209 if is_child else 124, method, 'a' if is_child else None,
                                       39 if is_child else 0, 85 if is_child else 0, 124)
            high_stage = {'single': 'single_final', 'dual': 'dual_high_final', 'selflift': 'selflift_high_final'}[mode]
            low_stage = {'dual': 'dual_low_prediction', 'selflift': 'selflift_low_prediction'}.get(mode)
            high_meta = native.new_native_metadata(plan, high_stage, {'gpu_matrix': mode}, parents[0]['h3_native'] if is_child else None)
            low_meta = native.new_native_metadata(plan, low_stage, {'gpu_matrix': mode}, parents[1]['h3_native'] if is_child else None) if low_stage else None
            height, width = (10, 16) if mode == 'dual' else (20, 32)
            latent = native.prepare_native_canvas({'samples': native._official_nested_tensor((torch.zeros(1, 24, 37, height, width), torch.zeros(1, 32, 2, 207)))}, high_meta)
            conditioning, stage_model, high_model = positive, model, second
            context = low_context = None
            if is_child:
                context = native.slice_native_context(parents[0], plan, high_stage, (20, 32))
                low_context = native.slice_native_context(parents[1], plan, low_stage, (10, 16)) if low_stage else context
                first_source = low_context if mode == 'dual' else context
                if method == 'context':
                    conditioning, _, latent = core.apply_reencoded_anchor_motion_context(positive, NoEncoder(), latent, first_source, '39', '5')
                elif method == 'context_drift':
                    stage_model, latent, _ = drift.apply_context_swap_drift_control(model, latent, first_source, sigmas, '39')
                    if mode == 'selflift':
                        high_model = drift.install_drift_control_av_model(second, latent, sigmas, 12)
                else:
                    latent, _, _ = drift.prepare_context_swap_latent(latent, first_source, 39, continue_audio=True, freeze_audio=False)
            if mode == 'selflift':
                high, low = selflift.progressive_sample_h3(stage_model, conditioning, NoEncoder(), latent, sigmas, 721, .6, .5,
                            highres_model=high_model, low_context_latent=low_context, rho=0,
                            latent_lifter=None if args.upscaler == 'None' else lift)
            else:
                high, low = sample(stage_model, conditioning, latent, sigmas, 721), None
                if mode == 'dual':
                    low = high
                    video, audio = native._streams_from_latent(high)
                    high = {**high, 'samples': native._official_nested_tensor((lift(video, (20, 32)), audio))}
                    # Resize masks together with the first-pass video canvas.
                    vm, am = core._noise_mask_streams(high)
                    if vm is not None:
                        high['noise_mask'] = native._official_nested_tensor((torch.nn.functional.interpolate(vm, size=(37, 20, 32), mode='nearest'), am))
                    if is_child:
                        if method == 'context_masked':
                            high, _, _ = drift.prepare_context_swap_latent(high, context, 39, continue_audio=False, freeze_audio=True)
                        else:
                            high, _ = core.apply_hires_anchor_continuity(high, context, '39')
                    high = sample(second, positive, high, refine, 722)
            high = native.stamp_native_result(high, high_meta)
            low = native.stamp_native_result(low, low_meta) if low is not None else None
            for tensor in native._streams_from_latent(high):
                assert torch.isfinite(tensor).all()
            if method == 'shot':
                parents = (high, low)
            outputs.append((mode, method, high))
            report['runs'].append({'mode': mode, 'method': method, 'seconds': round(time.monotonic() - began, 2), 'encoder_calls': 0})
            print(json.dumps(report['runs'][-1]), flush=True)
            (paths['output'] / 'matrix.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    comfy.model_management.unload_all_models()
    print('Decoding comparison outputs', flush=True)
    def vae(path):
        state, metadata = comfy.utils.load_torch_file(str(path), return_metadata=True)
        return comfy.sd.VAE(sd=state, metadata=metadata)
    video_vae, audio_vae = vae(paths['video-vae']), vae(paths['audio-vae'])
    rows = []
    for mode, method, high in outputs:
        video, audio = native._streams_from_latent(high)
        images = video_vae.decode(video).reshape(-1, 320, 512, 3).cpu()
        audio = {'waveform': audio_vae.decode(audio).movedim(-1, 1).cpu(), 'sample_rate': 32000}
        images, delivered = native.trim_native_media(high['h3_native'], images, audio)
        sf.write(paths['output'] / f'{mode}-{method}.wav', delivered['waveform'][0].T.numpy(), 32000)
        selected = [0, min(4, len(images)-1), len(images)//2, len(images)-1]
        row = Image.new('RGB', (1024, 190), 'black')
        for index, frame in enumerate(selected):
            picture = Image.fromarray(images[frame].clamp(0, 1).mul(255).byte().numpy()).resize((256, 160))
            row.paste(picture, (index * 256, 24))
        ImageDraw.Draw(row).text((5, 4), f'{mode} / {method}', fill='white')
        rows.append(row)
    contact = Image.new('RGB', (1024, 190 * len(rows)))
    for index, row in enumerate(rows):
        contact.paste(row, (0, index * 190))
    contact.save(paths['output'] / 'comparison.png')
    report['passed'] = True
    (paths['output'] / 'matrix.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('Matrix completed', flush=True)


if __name__ == '__main__':
    import torch

    with torch.inference_mode():
        main()
