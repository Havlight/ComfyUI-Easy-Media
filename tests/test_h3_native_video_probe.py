"""Lock preflight checks decoded counts without allocating an RGB frame batch."""
from __future__ import annotations

import importlib
import io
import json
import sys
import types
import weakref
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest
import torch

from test_h3_native_artifacts import timing

lock = importlib.import_module('native_artifact_unit.utils.h3_native_lock')


class FileVideo:
    def __init__(self, source, start=0, duration=0):
        self.source, self.start, self.duration = source, start, duration

    def get_stream_source(self):
        if isinstance(self.source, io.BytesIO):
            self.source.seek(0)
        return self.source

    def get_active_trim_window(self):
        return self.start, self.duration

    def get_components(self):
        pytest.fail('file preflight must not materialize RGB images or audio')


@pytest.fixture(autouse=True)
def file_adapter(monkeypatch):
    latest = types.ModuleType('comfy_api.latest')
    latest.InputImpl = types.SimpleNamespace(VideoFromFile=FileVideo)
    monkeypatch.setitem(sys.modules, latest.__name__, latest)


def write_video(path, frames=22, fps=24):
    with av.open(str(path), 'w') as container:
        stream = container.add_stream('libx264', rate=fps)
        stream.width, stream.height, stream.pix_fmt = 32, 24, 'yuv420p'
        for index in range(frames):
            frame = av.VideoFrame.from_ndarray(np.full((24, 32, 3), index % 256, dtype=np.uint8), format='rgb24')
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return str(path)


@pytest.mark.parametrize('buffered', [False, True])
@pytest.mark.parametrize('start,duration,expected', [(0, 0, 22), (5 / 24, 5 / 24, 5), (17 / 24, 0, 5)])
def test_file_and_buffer_count_actual_frames_and_respect_trim(tmp_path, buffered, start, duration, expected):
    path = tmp_path / 'source.mp4'
    source = write_video(path)
    if buffered:
        source = io.BytesIO(path.read_bytes())
        source.seek(7)
    video = FileVideo(source, start, duration)
    lock.validate_native_locked_video(video, expected)
    # The same source remains usable for later generation or another probe.
    lock.validate_native_locked_video(video, expected)


@pytest.mark.parametrize('frames,fps', [(21, 24), (23, 24), (22, 25), (22, Fraction(24000, 1001))])
def test_bad_frame_count_or_rate_fails_before_sampling(tmp_path, frames, fps):
    video = FileVideo(write_video(tmp_path / 'source.mp4', frames, fps))
    with pytest.raises(timing.NativePlanError, match='LOCK_VIDEO_RANGE'):
        lock.validate_native_locked_video(video, 22)


@pytest.mark.parametrize('missing', [False, True])
def test_unreadable_source_is_an_explicit_preflight_error(tmp_path, missing):
    path = tmp_path / 'source.mp4'
    if not missing:
        path.write_bytes(b'not a video')
    with pytest.raises(timing.NativePlanError, match='LOCK_VIDEO_DECODE'):
        lock.validate_native_locked_video(FileVideo(str(path)), 22)


@pytest.mark.parametrize('frames,fps,valid', [(22, 24, True), (21, 24, False), (22, 30, False)])
def test_in_memory_adapter_preserves_component_contract_without_serializing(frames, fps, valid):
    class MemoryVideo:
        def get_components(self):
            return types.SimpleNamespace(images=types.SimpleNamespace(shape=(frames, 24, 32, 3)), frame_rate=fps)

        def get_stream_source(self):
            pytest.fail('preflight must not serialize a non-file video')

    if valid:
        lock.validate_native_locked_video(MemoryVideo(), 22)
    else:
        with pytest.raises(timing.NativePlanError, match='LOCK_VIDEO_RANGE'):
            lock.validate_native_locked_video(MemoryVideo(), 22)


@pytest.mark.parametrize('case', ['valid', 'short_decode', 'decode_error', 'no_timestamp', 'no_stream'])
def test_probe_keeps_bounded_frame_references_and_closes_on_failure(monkeypatch, case):
    live_frames = weakref.WeakSet()

    class Frame:
        def __init__(self, pts):
            self.pts = pts

        def to_ndarray(self, *args, **kwargs):
            pytest.fail('probe must not convert decoded frames to pixel arrays')

    class Container:
        closed = False
        # A plausible header must not conceal missing/corrupt decoded frames.
        stream = types.SimpleNamespace(average_rate=24, time_base=Fraction(1, 24), frames=243)
        streams = types.SimpleNamespace(video=[] if case == 'no_stream' else [stream])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def decode(self, stream):
            assert stream.thread_count == 1
            for index in range(242 if case == 'short_decode' else 243):
                if case == 'decode_error' and index == 10:
                    raise av.error.InvalidDataError(1094995529, 'corrupt packet')
                frame = Frame(None if case == 'no_timestamp' else index)
                live_frames.add(frame)
                assert len(live_frames) <= 2, 'preflight retained previously decoded frames'
                yield frame

    container = Container()
    monkeypatch.setattr(av, 'open', lambda *args, **kwargs: container)
    if case == 'valid':
        lock.validate_native_locked_video(FileVideo('unused.mp4'), 243)
    else:
        code = {'short_decode': 'RANGE', 'decode_error': 'DECODE', 'no_timestamp': 'DECODE', 'no_stream': 'SOURCE'}[case]
        with pytest.raises(timing.NativePlanError, match=f'LOCK_VIDEO_{code}'):
            lock.validate_native_locked_video(FileVideo('unused.mp4'), 243)
    assert container.closed


@pytest.mark.parametrize('last_short', [False, True])
def test_whole_run_gate_streams_every_locked_window_and_rejects_a_later_bad_one(monkeypatch, tmp_path, last_short):
    from test_h3_native_graph import native_inputs
    from test_minimax_node import _load_minimax_node

    module = _load_minimax_node(monkeypatch)
    latest = sys.modules['comfy_api.latest']
    latest.InputImpl.VideoFromFile = FileVideo
    latest.io.NodeOutput = lambda *values: types.SimpleNamespace(result=values)
    spec = importlib.util.spec_from_file_location('easy_media.nodes.h3_native', Path(__file__).resolve().parents[1] / 'nodes/h3_native.py')
    runtime = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, runtime)
    spec.loader.exec_module(runtime)
    monkeypatch.setattr(runtime, '_project_directory', lambda name: tmp_path / 'project')
    info = native_inputs()['tracks_info'][0]
    info['tracks'].append({'id': 'locked-video', 'type': 'video', 'audio_locked': True,
        'segments': [{'start_frame': 0, 'end_frame': 481, 'content': {'media_type': 'video'}}]})
    prepared = []
    windows = []

    def prepare(**kwargs):
        prepared.append(kwargs['task_index'])
        return types.SimpleNamespace(result=(None, info))

    def output(tracks_info, task_index, prompt_format):
        task = tracks_info['tracks'][0]['segments'][0]
        videos = []
        if task['id'] == 'native-reference-window':
            count = task['end_frame'] - task['start_frame']
            windows.append(count)
            short = int(last_short and len(windows) == 2)
            videos = [FileVideo(write_video(tmp_path / f'window-{len(windows)}.mp4', count - short))]
        return types.SimpleNamespace(result=(None, None, None, None, [], [], videos))

    monkeypatch.setattr(module._project_module.EasyH3ProjectStaticPrepare, 'execute', prepare)
    basic = types.ModuleType('easy_media.nodes.basic')
    basic.MultiTrackTaskOutput = types.SimpleNamespace(execute=output)
    monkeypatch.setitem(sys.modules, basic.__name__, basic)
    model = types.SimpleNamespace(model=types.SimpleNamespace(
        model_config=type('MiniMaxH3', (), {})(), latent_format=type('MiniMaxH3AV', (), {})()))
    config = {'selected': [0, 1], 'run_second_pass': False, 'has_context_second_pass': False,
        'recipe': {'sampling_mode': 'single', 'target_width': 32, 'target_height': 32,
                   'first_width': 32, 'first_height': 32, 'allow_vae_fallback': False}}

    def run():
        return runtime.EasyH3NativePreflight.execute(info, 'probe-test', json.dumps(config), model,
            object(), object(), sigmas=torch.tensor([1.0, 0.0]), project_static={})

    if last_short:
        with pytest.raises(ValueError, match=r'(?s)PREFLIGHT_MEDIA.*Task 2:.*LOCK_VIDEO_RANGE'):
            run()
    else:
        assert run().result[0]['planned_ids'] == ['a', 'b']
    assert prepared == [0, 1]
    assert windows == [243, 277]
