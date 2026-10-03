from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("h3_native_timing_unit", ROOT / "utils/h3_native_timing.py")
timing = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = timing
spec.loader.exec_module(timing)
FIXTURES = json.loads((ROOT / "tests/fixtures/h3_native_timing.json").read_text())


@pytest.mark.parametrize("case", FIXTURES["durations"])
def test_shared_duration_cases(case):
    assert timing.snap_duration(case["frames"], case["continuation"]) == case["expected"]


@pytest.mark.parametrize("case", FIXTURES["splits"])
def test_shared_split_cases(case):
    assert timing.native_split_frame(case["start"], case["end"], case["requested"], case["continuation"]) == case["expected"]


def test_audio_clock_tracks_delivered_seams_without_accumulating_rounding():
    rng = random.Random(513)
    parent = None
    end = 0
    sample_total = 0
    for index in range(1000):
        start = end
        duration = timing.snap_duration(rng.randint(50, 360), index > 0)
        end += duration
        context = 39 if index else 0
        plan = timing.NativeTaskPlan(str(index), start, end, "context" if index else "shot",
                                     str(index - 1) if index else None, context, start - context, duration + context)
        clock = timing.audio_clock(plan, parent)
        assert abs(clock["audio_origin_units"] - plan.raw_start_frame * 5) <= 1
        assert clock["audio_origin_units"] + clock["audio_ticks"] * 3 >= end * 5
        if parent:
            assert clock["source_audio_end"] - clock["source_audio_start"] == 65
            left, right = timing.source_video_slice(plan, parent)
            assert left % 5 == 0 and right - left == 12
        sample_total += timing.sample_at_frame(end, 44100) - timing.sample_at_frame(start, 44100)
        assert sample_total == timing.sample_at_frame(end, 44100)
        parent = {**plan.as_dict(), **clock}


def _info(*segments):
    return {"format": "MiniMax", "frame_rate": 24,
            "h3_native": {"version": 1, "allow_vae_fallback": False},
            "tracks": [{"type": "task", "segments": list(segments)}]}


def test_fallback_does_not_disable_native_geometry_validation():
    info = _info({"id": "a", "start_frame": 0, "end_frame": 240, "content": {}})
    for fallback in (False, True):
        info["h3_native"]["allow_vae_fallback"] = fallback
        with pytest.raises(timing.NativePlanError, match="DURATION_GRID"):
            timing.compile_native_plan(info)


def test_parent_identity_and_raw_vs_delivered_ranges():
    info = _info({"id": "a", "start_frame": 0, "end_frame": 243, "content": {}},
                 {"id": "b", "start_frame": 243, "end_frame": 481,
                  "content": {"continuity_mode": "context"}})
    second = timing.compile_native_plan(info)[1]
    assert (second.raw_start_frame, second.raw_frames, second.context_frames) == (204, 277, 39)
    assert second.parent_segment_id == "a"
    info["tracks"][0]["segments"].pop(0)
    with pytest.raises(timing.NativePlanError, match="PARENT"):
        timing.compile_native_plan(info)


def test_short_split_and_invalid_audio_source_are_rejected():
    with pytest.raises(timing.NativePlanError, match="SPLIT_SHORT"):
        timing.native_split_frame(0, 39, 20, False)
    plan = timing.NativeTaskPlan("b", 243, 481, "context", "a", 39, 204, 277)
    with pytest.raises(timing.NativePlanError, match="AUDIO_RANGE"):
        timing.audio_clock(plan, {"audio_origin_units": 0, "audio_ticks": 400})


def test_legacy_has_no_native_guarantee():
    assert timing.native_policy({}) is None
    assert timing.compile_native_plan({"tracks": []}) == []


def test_native_markers_expand_legal_stable_tasks():
    info = {'h3_native': {'version': 1, 'allow_vae_fallback': False}, 'frame_rate': 24,
            'tracks': [{'type': 'task', 'segments': [{'id': 'a', 'start_frame': 0, 'end_frame': 243, 'content': {}}]}],
            'task_markers': [{'id': 'cut', 'frame': 124}]}
    plans = timing.compile_native_plan(info)
    assert [(p.segment_id, p.raw_frames) for p in plans] == [('a', 124), ('a:marker:cut', 158)]
    info['task_markers'][0]['frame'] = 120
    with pytest.raises(ValueError, match='MARKER_GRID'):
        timing.compile_native_plan(info)


def test_load_conversion_aligns_generation_only_and_preserves_legacy_policy():
    data = _info({"id": "a", "start_frame": 0, "end_frame": 240, "content": {}},
                 {"id": "b", "start_frame": 240, "end_frame": 480, "content": {"continuity_mode": "context"}})
    data["h3_native"]["allow_vae_fallback"] = True
    media = {"type": "video", "locked": True, "segments": [{"start_frame": 0, "end_frame": 480}]}
    data["tracks"].append(media)
    converted = timing.normalize_native_timeline(data)
    assert converted["h3_native"] == {"version": 2, "allow_vae_fallback": True}
    assert [(p.start_frame, p.end_frame) for p in timing.compile_native_plan(converted)] == [(0, 243), (243, 481)]
    assert converted["tracks"][1]["segments"] == media["segments"]
    assert data["tracks"][0]["segments"][0]["end_frame"] == 240
    assert timing.normalize_native_timeline(converted) == converted


def test_load_conversion_does_not_bypass_track_locks():
    data = _info({"id": "a", "start_frame": 0, "end_frame": 240, "content": {}})
    data["tracks"][0]["locked"] = True
    with pytest.raises(timing.NativePlanError, match="TRACK_LOCKED"):
        timing.normalize_native_timeline(data)


def test_load_conversion_aligns_markers_using_the_same_split_contract():
    data = _info({"id": "a", "start_frame": 0, "end_frame": 240, "content": {}})
    data["task_markers"] = [{"id": "cut", "frame": 120}]
    converted = timing.normalize_native_timeline(data)
    assert converted["task_markers"][0]["frame"] == 124
    assert len(timing.compile_native_plan(converted)) == 2
