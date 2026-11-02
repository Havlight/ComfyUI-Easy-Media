"""The LoRA notes every project shares, with the installed LoRAs and what LoRA Manager keeps."""

import importlib
import json
import sys
import types
from pathlib import Path

import pytest


def utils_module(name: str):
    """A utils module imported as part of a stand-in package: its relative imports work, and
    utils/__init__ (which pulls in torch and ComfyUI) never runs."""
    package = "easy_media_utils_under_test"
    if package not in sys.modules:
        stub = types.ModuleType(package)
        stub.__path__ = [str(Path(__file__).resolve().parents[1] / "utils")]
        sys.modules[package] = stub
    return importlib.import_module(f"{package}.{name}")


module = utils_module("lora_notes")

NOTES = """# LoRA notes

## mmh3/style.safetensors
- For: painterly look
- Strength: 0.6-0.8, all stages

## mmh3/gone.safetensors
- For: an old test
"""


@pytest.fixture
def notes(tmp_path):
    loras = tmp_path / "loras"
    (loras / "mmh3").mkdir(parents=True)
    (loras / "mmh3/style.safetensors").write_bytes(b"weights")
    # LoRA Manager's file beside it; empty fields and "Unknown" are left out.
    (loras / "mmh3/style.metadata.json").write_text(json.dumps({
        "base_model": "Unknown", "trainedWords": ["oilpaint"], "usage_tips": '{"strength_min": 0.5, "strength_max": 0.9}',
        "notes": "", "tags": [], "civitai": {}}), encoding="utf-8")
    installed = ["mmh3\\style.safetensors", "mmh3\\plain.safetensors", "wan\\other.safetensors"]
    return module.LoraNotes(lambda: tmp_path / "agent", lambda: installed, lambda name: str(loras / name))


def test_notes_name_loras_by_heading_with_their_installed_files_and_lora_manager_fields(notes, tmp_path):
    assert notes.read() == {"path": str(tmp_path / "agent/loras.md"), "text": None, "documented": [], "installed_count": 3}
    assert notes.write(NOTES) == {"path": str(tmp_path / "agent/loras.md")}
    result = notes.read()
    assert result["text"] == NOTES
    assert result["documented"] == [
        {"name": "mmh3/style.safetensors", "installed": True, "lora_manager": {
            "trigger_words": ["oilpaint"], "usage_tips": {"strength_min": 0.5, "strength_max": 0.9}}},
        {"name": "mmh3/gone.safetensors", "installed": False}]
    assert "found" not in result


def test_headings_name_loras_outside_code_and_with_forward_slashes():
    text = "## \n- For: nothing\n\n```\n## mmh3/example.safetensors\n```\n## mmh3\\style.safetensors\n##   wan/a.safetensors  \n"
    assert module.documented_loras(text) == ["mmh3/style.safetensors", "wan/a.safetensors"]


def test_find_lists_installed_loras_with_every_word(notes):
    assert [item["name"] for item in notes.read("MMH3 plain")["found"]] == ["mmh3/plain.safetensors"]
    assert len(notes.read("")["found"]) == 3


def test_notes_must_be_text_of_bounded_size(notes):
    with pytest.raises(ValueError):
        notes.write(None)
    with pytest.raises(ValueError):
        notes.write("x" * (module.MAX_NOTES_BYTES + 1))
