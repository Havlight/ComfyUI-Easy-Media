"""Strict MODEL-only H3 LoRA adapter with bounded CPU ownership.

The core ModelPatcher owns GPU patch/unpatch. This module never merges base
weights, retains context wrappers, or moves a variant onto the GPU.
"""
from __future__ import annotations

import hashlib
import math
import threading
import weakref
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .h3_segment_loras import SegmentLoraError

WEIGHT_CACHE_BYTES = 512 * 1024**2
_LOCK = threading.RLock()
_IDENTITIES: OrderedDict[str, dict[str, Any]] = OrderedDict()
_MODELS: weakref.WeakValueDictionary[tuple[Any, ...], Any] = weakref.WeakValueDictionary()


class WeightCache:
    """Only evictable CPU weights are counted here; active patchers own theirs."""

    def __init__(self, budget: int = WEIGHT_CACHE_BYTES) -> None:
        self.budget = budget
        self.bytes = 0
        self.items: OrderedDict[str, tuple[Any, int]] = OrderedDict()

    def get(self, key: str, loader: Callable[[], Any], measure: Callable[[Any], int]) -> Any:
        if key in self.items:
            self.items.move_to_end(key)
            return self.items[key][0]
        value = loader()
        size = measure(value)
        if size <= self.budget:
            while self.items and self.bytes + size > self.budget:
                _, (_, old_size) = self.items.popitem(last=False)
                self.bytes -= old_size
            self.items[key] = (value, size)
            self.bytes += size
        return value

    def clear(self) -> None:
        self.items.clear()
        self.bytes = 0


_WEIGHTS = WeightCache()


def _stat(path: Path) -> list[int]:
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino]


def file_identity(name: str, *, cached_only: bool = False) -> dict[str, Any] | None:
    """Hash once per preflight; UI may only reuse previously verified identities."""
    import folder_paths

    filename = folder_paths.get_full_path("loras", name)
    if not filename:
        raise SegmentLoraError(f"LoRA file is unavailable: {name}")
    path = Path(filename).resolve()
    try:
        before = _stat(path)
        with _LOCK:
            cached = _IDENTITIES.get(str(path))
            if cached_only:
                return dict(cached) if cached and cached["stat"] == before else None
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(4 * 1024**2), b""):
                digest.update(chunk)
        if before != _stat(path):
            raise SegmentLoraError(f"LoRA changed while being read: {name}")
        result = dict(lora=name, path=str(path), stat=before, sha256=digest.hexdigest())
        with _LOCK:
            _IDENTITIES[str(path)] = result
            _IDENTITIES.move_to_end(str(path))
            while len(_IDENTITIES) > 256:
                _IDENTITIES.popitem(last=False)
        return dict(result)
    except OSError as error:
        raise SegmentLoraError(f"Cannot read LoRA {name}: {error}") from error


def check_pinned_file(identity: Mapping[str, Any]) -> None:
    try:
        if _stat(Path(identity["path"])) != identity["stat"]:
            raise SegmentLoraError(f"LoRA changed after preflight: {identity['lora']}. Queue the run again.")
    except OSError as error:
        raise SegmentLoraError(f"LoRA is no longer readable: {identity['lora']}") from error


def _weight_bytes(weights: Mapping[str, Any]) -> int:
    # Safetensors has distinct storages; count aliases only once for other loaders.
    storages = {}
    for value in weights.values():
        if hasattr(value, "untyped_storage"):
            storage = value.untyped_storage()
            storages[storage.data_ptr()] = storage.nbytes()
    return sum(storages.values())


def load_weights(identity: Mapping[str, Any]) -> dict[str, Any]:
    import comfy.utils
    import torch

    check_pinned_file(identity)

    def load() -> dict[str, Any]:
        try:
            weights = comfy.utils.load_torch_file(identity["path"], safe_load=True)
            if not isinstance(weights, dict) or not weights:
                raise SegmentLoraError("No LoRA tensors found.")
            for key, value in weights.items():
                if not isinstance(value, torch.Tensor) or value.device.type != "cpu" or not torch.isfinite(value).all():
                    raise SegmentLoraError(f"Invalid or nonfinite CPU tensor: {key}")
            check_pinned_file(identity)
            return weights
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
            raise SegmentLoraError(f"Cannot load LoRA {identity['lora']}: {error}") from error

    with _LOCK:
        return _WEIGHTS.get(identity["sha256"], load, _weight_bytes)


def validated_patches(model: Any, weights: dict[str, Any], label: str) -> dict[str, Any]:
    """Use core key mapping, then reject unsupported/partial or wrong-shape loads.

    Initial verified scope is dense 2D LoRA on H3 linear layers, including alpha.
    Other core adapters must gain explicit shape validation before acceptance.
    """
    import comfy.lora
    import comfy.lora_convert

    try:
        converted = comfy.lora_convert.convert_lora(weights)
        mapping = comfy.lora.model_lora_keys_unet(model.model, {})
        patches = comfy.lora.load_lora(converted, mapping, log_missing=False)
        if not patches:
            raise SegmentLoraError("no matching MODEL patches")
        shapes = model.model.state_dict()
        consumed: set[str] = set()
        for target, adapter in patches.items():
            if not isinstance(target, str) or target not in shapes:
                raise SegmentLoraError(f"unsupported target {target}")
            values = getattr(adapter, "weights", ())
            if getattr(adapter, "name", None) != "lora" or len(values) != 6:
                raise SegmentLoraError(f"unsupported adapter at {target}; use a standard H3 MODEL LoRA")
            up, down, alpha, mid, dora, reshape = values
            if mid is not None or dora is not None or reshape is not None:
                raise SegmentLoraError(f"mid/DoRA/reshape adapters are not yet verified ({target})")
            expected = tuple(shapes[target].shape)
            if (len(expected) != 2 or up.ndim != 2 or down.ndim != 2
                    or up.shape[1] != down.shape[0] or down.shape[0] < 1
                    or (up.shape[0], down.shape[1]) != expected):
                raise SegmentLoraError(f"shape mismatch at {target}: up={tuple(up.shape)}, down={tuple(down.shape)}, model={expected}")
            if alpha is not None and not math.isfinite(alpha):
                raise SegmentLoraError(f"nonfinite alpha at {target}")
            consumed.update(adapter.loaded_keys)
        unused = set(converted) - consumed
        if unused:
            raise SegmentLoraError("unmatched weights (CLIP and partial loads are unsupported): " + ", ".join(sorted(unused)[:5]))
        return patches
    except (ValueError, KeyError, TypeError, AttributeError, RuntimeError) as error:
        raise SegmentLoraError(f"LoRA {label}: {error}") from error


def validate_model_lora(model: Any, identity: Mapping[str, Any]) -> int:
    return len(validated_patches(model, load_weights(identity), identity["lora"]))


def prepare_lora_model(base: Any, rules: Sequence[dict[str, Any]],
                       files: Mapping[str, dict[str, Any]], stage: str) -> Any:
    if not rules:
        return base
    for rule in rules:
        check_pinned_file(files[rule["lora"]])
    key = (id(base), str(base.patches_uuid), stage,
           tuple((rule["sha256"], rule["strength"]) for rule in rules))
    with _LOCK:
        cached = _MODELS.get(key)
        if cached is not None:
            return cached
        result = base.clone()
        for rule in rules:
            identity = files[rule["lora"]]
            patches = validated_patches(base, load_weights(identity), rule["lora"])
            added = result.add_patches(patches, rule["strength"])
            if set(added) != set(patches):
                raise SegmentLoraError(f"LoRA {rule['lora']}: MODEL rejected validated patches.")
        _MODELS[key] = result
        return result


def lora_cache_stats() -> dict[str, int]:
    with _LOCK:
        return dict(cpu_cached_bytes=_WEIGHTS.bytes, cpu_budget_bytes=_WEIGHTS.budget,
                    cpu_cached_files=len(_WEIGHTS.items), live_model_descriptors=len(_MODELS))
