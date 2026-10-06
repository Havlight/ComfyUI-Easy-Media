"""Opt-in real H3 LoRA switching and stage-isolation test; no VAE or model writes.

Identical-input samples test restoration. These zero-text small samples are not
perceptual quality evidence. Run in the installed ComfyUI Python environment.
"""
from __future__ import annotations

import argparse
import gc
import importlib
import json
import sys
import time
import types
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('model', 'base-lora', 'lora-a', 'lora-b', 'report'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--cycles', type=int, default=2)
    parser.add_argument('--managed', action='store_true', help='Exercise the production bounded adapter')
    args = parser.parse_args()
    paths = {key: Path(value).resolve() for key, value in vars(args).items() if key not in ('cycles', 'managed')}
    sys.argv = [sys.argv[0]]
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root.parent.parent))
    for name, path in (('segment_lora_probe', root), ('segment_lora_probe.utils', root / 'utils'),
                       ('segment_lora_probe.modules', root / 'modules')):
        module = types.ModuleType(name)
        module.__path__ = [str(path)]
        sys.modules[name] = module

    import psutil
    import torch
    import comfy.sd
    import comfy.samplers
    import comfy.utils
    from comfy_extras.nodes_custom_sampler import BasicGuider, RandomNoise, SamplerCustomAdvanced

    core = importlib.import_module('segment_lora_probe.modules.motion_context.core')
    lift = importlib.import_module('segment_lora_probe.modules.selflift.sampling')
    artifacts = importlib.import_module('segment_lora_probe.utils.h3_native_artifacts')
    managed = importlib.import_module('segment_lora_probe.utils.h3_lora_models') if args.managed else None
    report: dict[str, Any] = {'passed': False, 'scope': 'identical-input model switching and stages; no quality claim',
        'torch': torch.__version__, 'gpu': torch.cuda.get_device_name(), 'runs': [],
        'files': {key: {'name': value.name, 'bytes': value.stat().st_size, 'sha256': artifacts.file_checksum(value)}
                  for key, value in paths.items() if key != 'report'}}
    paths['report'].parent.mkdir(parents=True, exist_ok=True)

    def save() -> None:
        paths['report'].write_text(json.dumps(report, indent=2), encoding='utf-8')

    def patch(model: Any, path: Path, strength: float) -> Any:
        if managed is not None and path != paths['base_lora']:
            stat = path.stat()
            identity = dict(lora=path.name, path=str(path), sha256=artifacts.file_checksum(path),
                            stat=[stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino])
            managed.validate_model_lora(model, identity)
            return managed.prepare_lora_model(model, [dict(lora=path.name, sha256=identity['sha256'], strength=strength)],
                                               {path.name: identity}, 'first')
        weights = comfy.utils.load_torch_file(str(path), safe_load=True)
        output = comfy.sd.load_lora_for_models(model, None, weights, strength, 0)[0]
        assert output is not model and output.patches_uuid != model.patches_uuid
        assert sum(map(len, output.patches.values())) > sum(map(len, model.patches.values()))
        return output

    def signature(model: Any) -> dict[str, tuple[Any, ...]]:
        return {key: tuple((item[0], id(item[1]), item[2]) for item in values) for key, values in model.patches.items()}

    def copy_result(latent: dict[str, Any]) -> list[Any]:
        parts = [t.detach().to('cpu', copy=True) for t in core._streams_from_latent(latent)]
        assert all(torch.isfinite(t).all() for t in parts)
        return parts

    def compare(left: list[Any], right: list[Any]) -> list[float]:
        deltas = [float((a - b).abs().max()) for a, b in zip(left, right)]
        for a, b in zip(left, right):
            torch.testing.assert_close(a, b, atol=1e-5, rtol=1e-4)
        return deltas

    try:
        print('Loading H3 and global LoRA', flush=True)
        base = comfy.sd.load_diffusion_model(str(paths['model']), model_options={'dtype': torch.bfloat16})
        base = patch(base, paths['base_lora'], 1.0)
        baseline = signature(base)
        a, b = patch(base, paths['lora_a'], .6), patch(base, paths['lora_b'], .6)
        assert signature(base) == baseline
        second_base = patch(base, paths['lora_b'], .2)
        second = patch(second_base, paths['lora_a'], .3)
        assert signature(base) == baseline
        assert all(signature(a)[key][:len(values)] == values for key, values in baseline.items())
        states = {name: signature(model) for name, model in [('base', base), ('a', a), ('b', b), ('second', second)]}
        positive = [[torch.zeros(1, 8, base.model.diffusion_model.condition_proj.in_features), {}]]
        sampler = comfy.samplers.sampler_object('euler')
        sigmas = torch.tensor([.72, 0.0])

        def canvas() -> dict[str, Any]:
            return {'samples': core._official_nested_tensor((torch.zeros(1, 24, 12, 16, 16), torch.zeros(1, 32, 2, 65)))}

        def sample(model: Any, latent: dict[str, Any] | None = None) -> dict[str, Any]:
            return SamplerCustomAdvanced.execute(RandomNoise.execute(731)[0], BasicGuider.execute(model, positive)[0],
                sampler, sigmas, latent if latent is not None else canvas())[1]

        reference: dict[str, list[Any]] = {}
        for cycle in range(args.cycles):
            for name, model in [('base', base), ('a', a), ('b', b), ('a', a), ('base', base)]:
                began = time.monotonic()
                result = copy_result(sample(model))
                deltas = compare(result, reference[name]) if name in reference else None
                reference.setdefault(name, result)
                assert signature(model) == states[name] and signature(base) == baseline
                gc.collect()
                row = {'cycle': cycle, 'model': name, 'seconds': time.monotonic() - began,
                       'restore_max_abs': deltas, 'rss': psutil.Process().memory_info().rss,
                       'cuda_allocated': torch.cuda.memory_allocated(), 'cuda_reserved': torch.cuda.memory_reserved()}
                report['runs'].append(row)
                save()
                print(json.dumps(row), flush=True)
        report['effect_max_abs'] = {name: [float((x-y).abs().max()) for x,y in zip(reference[name], reference['base'])] for name in ('a','b')}
        assert all(any(delta > 1e-5 for delta in values) for values in report['effect_max_abs'].values())

        class NoVae:
            def encode(self, *unused: Any, **kwargs: Any) -> None:
                raise AssertionError('Model-only LoRA test must not encode media')

        stages = []
        for mode in ('dual', 'selflift'):
            for high_name, high_model in [('shared_base', b), ('second_loader', second)]:
                if mode == 'dual':
                    low = sample(a)
                    high = sample(high_model, low)
                else:
                    high, low = lift.progressive_sample_h3(a, positive, NoVae(), canvas(),
                        torch.tensor([1.0, .5, 0.0]), 731, .5, .5, highres_model=high_model, rho=0)
                copy_result(high)
                copy_result(low)
                assert signature(a) == states['a'] and signature(high_model) == states['b' if high_name == 'shared_base' else 'second']
                stages.append({'mode': mode, 'high_model': high_name, 'finite': True})
                print(json.dumps(stages[-1]), flush=True)
        report.update(passed=True, stages=stages, encoder_calls=0,
                      peak_cuda_bytes=torch.cuda.max_memory_allocated(), cpu_weight_cache_budget_bytes=512 * 1024**2)
        if managed is not None:
            report['managed_cache'] = managed.lora_cache_stats()
        save()
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
        save()
        raise


if __name__ == '__main__':
    import torch
    with torch.inference_mode():
        main()
