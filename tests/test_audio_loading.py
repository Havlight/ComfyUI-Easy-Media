"""Audio files reach the timeline and references even when torchaudio cannot decode them."""

import importlib.util
import sys
import types
from fractions import Fraction
from pathlib import Path

import pytest

av = pytest.importorskip("av")
np = pytest.importorskip("numpy")

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def audio(monkeypatch):
    monkeypatch.setitem(sys.modules, "folder_paths", types.SimpleNamespace(
        get_output_directory=lambda: "", get_annotated_filepath=lambda name: name))
    spec = importlib.util.spec_from_file_location("easy_media_audio_for_loading", ROOT / "utils/audio.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_m4a(path: Path, seconds: float, rate: int = 44100) -> None:
    """An AAC stereo tone in an MP4 container, like a phone recording."""
    with av.open(str(path), "w", format="mp4") as container:
        stream = container.add_stream("aac", rate=rate, layout="stereo")
        samples = np.sin(np.linspace(0, 440 * 2 * np.pi * seconds, int(rate * seconds))).astype(np.float32) * 0.5
        for start in range(0, len(samples), 1024):
            chunk = np.stack([samples[start:start + 1024]] * 2)
            frame = av.AudioFrame.from_ndarray(chunk, format="fltp", layout="stereo")
            frame.sample_rate, frame.pts, frame.time_base = rate, start, Fraction(1, rate)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)


def test_m4a_loads_without_torchaudio(audio, monkeypatch, tmp_path):
    path = tmp_path / "麻雀叫聲.m4a"
    write_m4a(path, 1.0)

    def no_torchaudio(*_args):
        raise AssertionError("torchaudio should not be needed for AAC")

    monkeypatch.setattr(audio, "_torchaudio_load", no_torchaudio)
    waveform = audio.load_audio_waveform("local", None, str(path), None, 24000)
    assert waveform is not None and waveform.shape[:2] == (1, 2)
    # Resampled to the requested rate; AAC adds encoder padding of a few frames.
    assert abs(waveform.shape[-1] - 24000) < 2048
    assert float(waveform.abs().max()) > 0.1
