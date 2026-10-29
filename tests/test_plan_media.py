from __future__ import annotations

import importlib
import io
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    # Fake parent packages keep the plugin and utils __init__ startup out of unit tests.
    for package, path in {"plan_media_test": ROOT, "plan_media_test.utils": ROOT / "utils"}.items():
        if package not in sys.modules:
            module = types.ModuleType(package)
            module.__path__ = [str(path)]
            sys.modules[package] = module
    return importlib.import_module(f"plan_media_test.{name}")


@pytest.fixture
def plan_media(tmp_path, monkeypatch):
    module = _load("utils.plan_media")
    for name in ("input", "output"):
        (tmp_path / name).mkdir()
    # Other tests may leave a stub folder_paths without these functions.
    monkeypatch.setattr(module.folder_paths, "get_input_directory", lambda: str(tmp_path / "input"), raising=False)
    monkeypatch.setattr(module.folder_paths, "get_output_directory", lambda: str(tmp_path / "output"), raising=False)
    return module


def test_file_references_are_copied_once_by_content(plan_media, tmp_path):
    source = tmp_path / "shots" / "alice.png"
    source.parent.mkdir()
    source.write_bytes(b"png-bytes")

    first = plan_media.resolve_plan_media({"media_type": "image", "file": str(source)})
    again = plan_media.resolve_plan_media({"media_type": "image", "file": str(source)})

    assert first == again
    assert first["source_type"] == "input" and first["file_name"] == "alice.png"
    # One folder per content hash keeps the original name for the Editor.
    assert first["file_path"].startswith("easy_media/imports/") and first["file_path"].endswith("/alice.png")
    assert (tmp_path / "input" / first["file_path"]).read_bytes() == b"png-bytes"
    assert len([path for path in (tmp_path / "input/easy_media/imports").iterdir()]) == 1
    renamed = tmp_path / "shots" / "copy.png"
    renamed.write_bytes(b"png-bytes")
    assert plan_media.resolve_plan_media({"media_type": "image", "file": str(renamed)})["file_path"] == first["file_path"]


def test_folder_and_local_references_are_checked_in_place(plan_media, tmp_path):
    (tmp_path / "input/refs").mkdir()
    (tmp_path / "input/refs/line.wav").write_bytes(b"wav")
    (tmp_path / "output/clip.mp4").write_bytes(b"mp4")

    assert plan_media.resolve_plan_media({"media_type": "audio", "input": "refs/line.wav"}) == {
        "source_type": "input", "file_path": "refs/line.wav", "file_name": "line.wav"}
    assert plan_media.resolve_plan_media({"media_type": "video", "output": "clip.mp4"})["source_type"] == "output"
    local = plan_media.resolve_plan_media({"media_type": "audio", "local": str(tmp_path / "input/refs/line.wav")})
    assert local["source_type"] == "local" and local["local_path"].endswith("line.wav")
    assert not (tmp_path / "input/easy_media").exists()


@pytest.mark.parametrize("item,message", [
    ({"media_type": "image", "file": "shots/alice.png"}, "not an absolute path"),
    ({"media_type": "image", "file": "MISSING"}, "was not found"),
    ({"media_type": "video", "input": "refs/line.wav"}, "not a supported video file"),
    ({"media_type": "audio", "input": "../escape.wav"}, "outside the folder"),
    ({"media_type": "audio", "input": "a.wav", "output": "b.wav"}, "exactly one"),
    ({"media_type": "text", "file": "x"}, "media_type"),
])
def test_unusable_references_explain_why(plan_media, tmp_path, item, message):
    (tmp_path / "input/refs").mkdir()
    (tmp_path / "input/refs/line.wav").write_bytes(b"wav")
    (tmp_path / "escape.wav").write_bytes(b"wav")
    if item.get("file") == "MISSING":
        item = {**item, "file": str(tmp_path / "nowhere.png")}
    with pytest.raises(plan_media.PlanMediaError, match=message):
        plan_media.resolve_plan_media(item)


def test_timeline_clips_learn_the_length_of_their_file(plan_media, tmp_path, monkeypatch):
    (tmp_path / "input/song.wav").write_bytes(b"wav")
    (tmp_path / "output/clip.mp4").write_bytes(b"mp4")
    probed = []

    def probe(path):
        probed.append(Path(path).name)
        return {"duration": 92.3, "has_audio": False} if path.name == "clip.mp4" else {"duration": 61.0, "has_audio": True}

    monkeypatch.setattr(plan_media, "_probe", probe)

    assert plan_media.resolve_plan_media({"media_type": "audio", "input": "song.wav", "probe": True})["duration"] == 61.0
    assert plan_media.resolve_plan_media({"media_type": "video", "output": "clip.mp4", "probe": True}) == {
        "source_type": "output", "file_path": "clip.mp4", "file_name": "clip.mp4", "duration": 92.3, "has_audio": False}
    assert "duration" not in plan_media.resolve_plan_media({"media_type": "audio", "input": "song.wav"})
    assert probed == ["song.wav", "clip.mp4"]


def test_urls_are_downloaded_and_named_by_content(plan_media, tmp_path, monkeypatch):
    class _Response(io.BytesIO):
        headers = {"Content-Type": "audio/mpeg"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(plan_media.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response(b"mp3"))

    result = plan_media.resolve_plan_media({"media_type": "audio", "file": "https://example.com/voice?id=1"})

    assert result["file_path"].endswith(".mp3") and result["file_name"] == "voice.mp3"
    assert (tmp_path / "input" / result["file_path"]).read_bytes() == b"mp3"
