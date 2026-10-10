from __future__ import annotations

import gc
import importlib
import sys
import types
import uuid
import weakref
from pathlib import Path

import pytest
import torch


@pytest.fixture
def adapter(monkeypatch):
    namespace = types.ModuleType("lora_models_unit")
    namespace.__path__ = [str(Path(__file__).resolve().parents[1] / "utils")]
    monkeypatch.setitem(sys.modules, namespace.__name__, namespace)
    for name in ("h3_lora_models", "h3_segment_loras"):
        monkeypatch.delitem(sys.modules, f"lora_models_unit.{name}", raising=False)
    return importlib.import_module("lora_models_unit.h3_lora_models")


def test_byte_lru_oversized_working_set_and_release(adapter):
    cache = adapter.WeightCache(10)
    class Weights:
        pass
    a, b = Weights(), Weights()
    ref = weakref.ref(a)
    assert cache.get("a", lambda: a, lambda _: 6) is a
    assert cache.get("a", lambda: pytest.fail("must reuse"), lambda _: 6) is a
    cache.get("large", Weights, lambda _: 50)
    assert list(cache.items) == ["a"] and cache.bytes == 6
    cache.get("b", lambda: b, lambda _: 5)
    del a
    gc.collect()
    assert ref() is None and cache.bytes == 5
    cache.clear()
    assert not cache.items and cache.bytes == 0


def test_file_identity_alias_replacement_and_read_only_cached_lookup(adapter, monkeypatch, tmp_path):
    path = tmp_path / "a.safetensors"
    path.write_bytes(b"old")
    monkeypatch.setitem(sys.modules, "folder_paths", types.SimpleNamespace(get_full_path=lambda *unused: str(path)))
    assert adapter.file_identity("a", cached_only=True) is None
    first = adapter.file_identity("a")
    assert adapter.file_identity("alias")["sha256"] == first["sha256"]
    assert adapter.file_identity("a", cached_only=True)["sha256"] == first["sha256"]
    path.write_bytes(b"new content")
    assert adapter.file_identity("a", cached_only=True) is None
    with pytest.raises(ValueError, match="changed after preflight"):
        adapter.check_pinned_file(first)
    assert adapter.file_identity("a")["sha256"] != first["sha256"]


class Model:
    def __init__(self, patches=None):
        self.patches_uuid = uuid.uuid4()
        self.patches = list(patches or ["global"])

    def clone(self):
        return Model(self.patches)

    def add_patches(self, patches, strength):
        self.patches.extend((key, value, strength) for key, value in patches.items())
        return list(patches)


def test_model_isolation_empty_identity_and_weak_descriptor_ownership(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "check_pinned_file", lambda _: None)
    monkeypatch.setattr(adapter, "load_weights", lambda identity: identity)
    monkeypatch.setattr(adapter, "validated_patches", lambda model, identity, label: {label: identity["sha256"]})
    base = Model()
    files = {"a": {"sha256": "a"}, "b": {"sha256": "b"}}
    rules = [{"lora": "a", "sha256": "a", "strength": .6}]
    assert adapter.prepare_lora_model(base, [], files, "first") is base
    first = adapter.prepare_lora_model(base, rules, files, "first")
    assert adapter.prepare_lora_model(base, rules, files, "first") is first
    second = adapter.prepare_lora_model(base, rules, files, "second")
    assert first is not second and base.patches == ["global"]
    assert first.patches == second.patches == ["global", ("a", "a", .6)]
    old_ref = weakref.ref(first)
    del first
    gc.collect()
    assert old_ref() is None
    assert adapter.lora_cache_stats()["live_model_descriptors"] == 1
    base.patches_uuid = uuid.uuid4()
    assert adapter.prepare_lora_model(base, rules, files, "second") is not second


@pytest.fixture
def core_stubs(adapter, monkeypatch):
    patch = types.SimpleNamespace(name="lora", loaded_keys={"up", "down", "alpha"},
        weights=(torch.ones(4, 2), torch.ones(2, 3), 2., None, None, None))
    loaded = {"linear.weight": patch}
    comfy = types.ModuleType("comfy")
    comfy.lora = types.ModuleType("comfy.lora")
    comfy.lora.model_lora_keys_unet = lambda model, mapping: {}
    comfy.lora.load_lora = lambda *args, **kwargs: loaded
    comfy.lora_convert = types.ModuleType("comfy.lora_convert")
    comfy.lora_convert.convert_lora = lambda weights: weights
    for module in (comfy, comfy.lora, comfy.lora_convert):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    model = types.SimpleNamespace(model=types.SimpleNamespace(state_dict=lambda: {"linear.weight": torch.zeros(4, 3)}))
    return model, loaded, {"up": patch.weights[0], "down": patch.weights[1], "alpha": torch.tensor(2.)}


def test_core_mapping_requires_complete_compatible_model_weights(adapter, core_stubs):
    model, loaded, weights = core_stubs
    assert adapter.validated_patches(model, weights, "a") == loaded
    with pytest.raises(ValueError, match="CLIP and partial loads"):
        adapter.validated_patches(model, weights | {"text_encoder.extra": torch.ones(2)}, "a")
    loaded["linear.weight"].weights = (torch.ones(5, 2), torch.ones(2, 3), None, None, None, None)
    with pytest.raises(ValueError, match="shape mismatch"):
        adapter.validated_patches(model, weights, "a")
    loaded.clear()
    with pytest.raises(ValueError, match="no matching MODEL"):
        adapter.validated_patches(model, weights, "a")


@pytest.mark.parametrize("slot,value,match", [(2, float("nan"), "nonfinite"),
    (3, torch.ones(1), "not yet verified"), (4, torch.ones(1), "not yet verified"),
    (5, [4, 3], "not yet verified")])
def test_unsupported_adapters_fail_before_sampling(adapter, core_stubs, slot, value, match):
    model, loaded, weights = core_stubs
    values = list(loaded["linear.weight"].weights)
    values[slot] = value
    loaded["linear.weight"].weights = values
    with pytest.raises(ValueError, match=match):
        adapter.validated_patches(model, weights, "a")
