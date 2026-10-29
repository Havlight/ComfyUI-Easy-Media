from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    # Fake parent packages keep the plugin and utils __init__ startup out of unit tests.
    for package, path in {"review_frames_test": ROOT, "review_frames_test.utils": ROOT / "utils"}.items():
        if package not in sys.modules:
            module = types.ModuleType(package)
            module.__path__ = [str(path)]
            sys.modules[package] = module
    return importlib.import_module(f"review_frames_test.{name}")


@pytest.fixture
def review(tmp_path, monkeypatch):
    module = _load("utils.review_frames")
    for name in ("output", "temp"):
        (tmp_path / name).mkdir()
    # Other tests may leave a stub folder_paths without these functions.
    monkeypatch.setattr(module.folder_paths, "get_output_directory", lambda: str(tmp_path / "output"), raising=False)
    monkeypatch.setattr(module.folder_paths, "get_temp_directory", lambda: str(tmp_path / "temp"), raising=False)
    grabbed = []
    monkeypatch.setattr(module, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(module, "_duration", lambda _source: 8.0)

    def grab(_ffmpeg, _source, seconds):
        grabbed.append(seconds)
        return Image.new("RGB", (640, 360), (int(seconds * 20), 0, 0))

    monkeypatch.setattr(module, "_grab", grab)
    module.grabbed = grabbed
    return module


def test_a_sheet_lays_out_evenly_spaced_frames_once_per_video(review, tmp_path):
    (tmp_path / "output/projects/mv").mkdir(parents=True)
    (tmp_path / "output/projects/mv/s03.mp4").write_bytes(b"mp4")

    result = review.contact_sheet("projects/mv/s03.mp4", 4, 1024)

    assert result["type"] == "temp" and result["subfolder"] == "easy_media_review"
    assert review.grabbed == [1.0, 3.0, 5.0, 7.0]
    with Image.open(tmp_path / "temp/easy_media_review" / result["filename"]) as sheet:
        assert sheet.size == (1024, 576)  # A 2 x 2 grid of 16:9 frames.
    assert review.contact_sheet("projects/mv/s03.mp4", 4, 1024) == result
    assert len(review.grabbed) == 4


@pytest.mark.parametrize("relative,frames,width,message", [
    ("../secret.mp4", 4, 1024, "outside the output folder"),
    ("missing.mp4", 4, 1024, "was not found"),
    ("clip.mp4", 0, 1024, "frames must be"),
    ("clip.mp4", 4, 5000, "width must be"),
])
def test_unusable_requests_explain_why(review, tmp_path, relative, frames, width, message):
    (tmp_path / "output/clip.mp4").write_bytes(b"mp4")
    with pytest.raises(review.ReviewError, match=message):
        review.contact_sheet(relative, frames, width)
