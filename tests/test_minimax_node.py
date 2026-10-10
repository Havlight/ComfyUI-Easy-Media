import importlib.util
from io import BytesIO
import json
import sys
import types
from fractions import Fraction
from pathlib import Path

import pytest
import torch
import torch.nn.functional as F


class _Port:
    def __init__(self, name=None, **kwargs):
        self.name = name
        self.kwargs = kwargs


class _PortType:
    @staticmethod
    def Input(name, **kwargs):
        return _Port(name, **kwargs)

    @staticmethod
    def Output(name=None, **kwargs):
        return _Port(name, **kwargs)


class _DynamicCombo(_PortType):
    @staticmethod
    def Option(name, inputs):
        return name, inputs


class _Autogrow:
    Type = dict

    class TemplatePrefix:
        def __init__(self, input, prefix, min=1, max=10):
            self.input = input
            self.prefix = prefix
            self.min = min
            self.max = max

    @staticmethod
    def Input(name, **kwargs):
        return _Port(name, **kwargs)


class _NodeOutput:
    def __init__(self, *values, expand=None, **kwargs):
        self.values = values
        self.expand = expand
        self.kwargs = kwargs


class _Schema:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _NestedTensor:
    is_nested = True

    def __init__(self, values):
        self.values = tuple(values)
        self.tensors = self.values

    def unbind(self):
        return self.values


def _h3_context_latent(value: int | float = 0, video_steps: int = 7):
    video = torch.full((1, 24, video_steps, 2, 2), float(value))
    pixel_frames = sum((1, 4, 4, 4, 4)[index % 5] for index in range(video_steps))
    audio_steps = round(pixel_frames * 5 / 3)
    audio = torch.full((1, 32, 2, audio_steps), float(value) + 100)
    return {"samples": _NestedTensor((video, audio))}


def _empty_h3_context_latent(video_steps: int = 7):
    video = torch.zeros(1, 24, video_steps, 2, 2)
    pixel_frames = sum((1, 4, 4, 4, 4)[index % 5] for index in range(video_steps))
    audio_steps = round(pixel_frames * 5 / 3)
    audio = torch.zeros(1, 32, 2, audio_steps)
    return {"samples": _NestedTensor((video, audio))}


def _h3_conditioning_cache_dir(tmp_path):
    return tmp_path / "easy_media" / "h3_conditioning_cache"


def _h3_video_anchor_latent(value: int | float = 0):
    return {"samples": torch.full((1, 24, 2, 2, 2), float(value))}


class _ProgressBar:
    instances = []

    def __init__(self, total):
        self.total = total
        self.updates = []
        self.instances.append(self)

    def update_absolute(self, value, total=None, preview=None):
        self.updates.append((value, total))


class _Clip:
    def __init__(self):
        self.tokenize_calls = []

    def tokenize(self, prompt, **kwargs):
        tokens = {"prompt": prompt, "kwargs": kwargs}
        self.tokenize_calls.append(tokens)
        return tokens

    def encode_from_tokens_scheduled(self, tokens):
        return [(torch.tensor([1.0]), {"tokens": tokens})]


class _Vae:
    def __init__(self):
        self.encoded = []

    def encode(self, value):
        self.encoded.append(value)
        temporal = 2 if value.shape[0] > 1 else 1
        return torch.zeros(1, 24, temporal, 2, 2)


class _AudioVae:
    audio_sample_rate = 32000

    def __init__(self):
        self.encoded = []

    def encode(self, value):
        self.encoded.append(value)
        return torch.zeros(1, 32, 2, 4)


class _ImageResizeKJWithNvidia:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "width": ("INT", {"default": 512}),
                "height": ("INT", {"default": 512}),
                "upscale_method": (["nvidia_rtx_vsr", "lanczos"],),
                "keep_proportion": (["stretch", "resize"], {"default": "stretch"}),
                "pad_color": ("STRING", {"default": "0, 0, 0"}),
                "crop_position": (["center"], {"default": "center"}),
                "divisible_by": ("INT", {"default": 2}),
            }
        }


class _MiniMaxLatentUpscaler:
    pass


class _MiniMaxMotionContextTrim:
    pass


class MiniMaxH3:
    unet_config = {"image_model": "minimax_h3"}


class _MiniMaxH3Model:
    def __init__(self):
        self.model = types.SimpleNamespace(model_config=MiniMaxH3())


def _load_minimax_node(monkeypatch):
    io = types.SimpleNamespace(
        Audio=_PortType,
        AnyType=_PortType,
        Autogrow=_Autogrow,
        Boolean=_PortType,
        Clip=_PortType,
        Combo=_PortType,
        ComfyNode=object,
        Conditioning=_PortType,
        ControlAfterGenerate=types.SimpleNamespace(fixed="fixed"),
        DynamicCombo=_DynamicCombo,
        Hidden=types.SimpleNamespace(prompt="PROMPT", unique_id="UNIQUE_ID"),
        Custom=lambda **kwargs: _PortType,
        Float=_PortType,
        Guider=_PortType,
        Image=_PortType,
        Int=_PortType,
        Latent=_PortType,
        Model=_PortType,
        NodeOutput=_NodeOutput,
        Noise=_PortType,
        Sampler=_PortType,
        Schema=_Schema,
        Sigmas=_PortType,
        String=_PortType,
        Vae=_PortType,
        Video=_PortType,
    )
    comfy_api = types.ModuleType("comfy_api")
    comfy_api_latest = types.ModuleType("comfy_api.latest")
    comfy_api_latest.io = io
    comfy_api_latest.InputImpl = types.SimpleNamespace(
        VideoFromFile=lambda path: types.SimpleNamespace(path=path)
    )
    comfy_api_latest.Types = types.SimpleNamespace()
    comfy_api.latest = comfy_api_latest

    core_nodes = types.ModuleType("nodes")
    core_nodes.MAX_RESOLUTION = 16384
    core_nodes.NODE_CLASS_MAPPINGS = {}
    comfy = types.ModuleType("comfy")
    comfy.__path__ = []
    model_management = types.ModuleType("comfy.model_management")
    model_management.intermediate_device = lambda: torch.device("cpu")
    model_management.get_torch_device = lambda: torch.device("cpu")
    nested_tensor = types.ModuleType("comfy.nested_tensor")
    nested_tensor.NestedTensor = _NestedTensor
    comfy_utils = types.ModuleType("comfy.utils")
    comfy_utils.common_upscale = (
        lambda samples, width, height, method, crop: F.interpolate(
            samples, size=(height, width), mode="nearest"
        )
    )
    _ProgressBar.instances.clear()
    comfy_utils.ProgressBar = _ProgressBar
    def save_torch_file(state, path, metadata=None):
        torch.save({"state": state, "metadata": metadata}, path)

    def load_torch_file(
        path,
        safe_load=False,
        device=None,
        return_metadata=False,
    ):
        del safe_load
        stored = torch.load(
            BytesIO(Path(path).read_bytes()),
            map_location=device or torch.device("cpu"),
            weights_only=True,
        )
        if isinstance(stored, dict) and set(stored) == {"state", "metadata"}:
            state = stored["state"]
            metadata = stored["metadata"]
        else:
            state = stored
            metadata = None
        return (state, metadata) if return_metadata else state

    comfy_utils.save_torch_file = save_torch_file
    # The fixture writes torch archives; avoid suffix-based safetensors detection.
    comfy_utils.load_torch_file = load_torch_file
    comfy.model_management = model_management
    comfy.nested_tensor = nested_tensor
    comfy.utils = comfy_utils

    node_helpers = types.ModuleType("node_helpers")

    def conditioning_set_values(conditioning, values):
        return [(tensor, {**metadata, **values}) for tensor, metadata in conditioning]

    node_helpers.conditioning_set_values = conditioning_set_values

    torchaudio = types.ModuleType("torchaudio")
    torchaudio.functional = types.SimpleNamespace(
        resample=lambda waveform, source, target: waveform
    )
    folder_paths = types.ModuleType("folder_paths")
    folder_paths.get_filename_list = lambda category: []
    folder_paths.get_output_directory = lambda: "/tmp"
    folder_paths.get_temp_directory = lambda: "/tmp"
    server = types.ModuleType("server")
    server.PromptServer = types.SimpleNamespace(
        instance=types.SimpleNamespace(send_sync=lambda _event, _payload: None)
    )

    package = types.ModuleType("easy_media")
    package.__path__ = []
    nodes_package = types.ModuleType("easy_media.nodes")
    nodes_package.__path__ = []
    basic_module = types.ModuleType("easy_media.nodes.basic")

    def prepare_multitrack_project_media(info):
        has_locked_audio = any(
            isinstance(track, dict) and track.get("audio_locked") is True
            for track in info.get("tracks", [])
        )
        locked_audio = {"prepared_locked_audio": True} if has_locked_audio else None
        return info, [], [], [], locked_audio

    basic_module.prepare_multitrack_project_media = prepare_multitrack_project_media
    basic_module.crop_multitrack_project_media = (
        lambda shared_audio, shared_video, locked_audio, *_args: (
            shared_audio,
            shared_video,
            locked_audio,
        )
    )
    project_modules_package = types.ModuleType("easy_media.modules")
    project_modules_package.__path__ = []
    motion_context_package = types.ModuleType("easy_media.modules.motion_context")
    motion_context_package.__path__ = []
    utils_package = types.ModuleType("easy_media.utils")
    utils_package.__path__ = [str(Path(__file__).parents[1] / "utils")]
    utils_package.log_node_info = lambda *_args, **_kwargs: None
    log_spec = importlib.util.spec_from_file_location(
        "stage_log_under_test", Path(__file__).parents[1] / "utils" / "log.py"
    )
    log_module = importlib.util.module_from_spec(log_spec)
    log_spec.loader.exec_module(log_module)
    utils_package.log_stage_time = log_module.log_stage_time
    utils_package.instrument_node_timing = log_module.instrument_node_timing
    utils_package.synchronize_execution_device = log_module.synchronize_execution_device
    utils_package.save_audio_to_temp_wav = lambda _audio: None
    utils_package.FFMPEG_RESIZE_METHODS = frozenset()
    for name in (
        "audio_db_to_gain",
        "audio_volume_db",
        "equirectangular_to_perspective",
        "load_audio_waveform",
        "load_image_tensor",
        "multitrack_is_shared_reference",
        "multitrack_is_muted_image",
        "multitrack_media_identity",
        "multitrack_segments_in_window",
        "multitrack_slot_name",
        "resize_image",
        "resize_video_with_ffmpeg",
        "resolve_video_path",
    ):
        setattr(utils_package, name, lambda *_args, **_kwargs: None)
    utils_package.audio_is_muted = (
        lambda settings: isinstance(settings, dict) and settings.get("muted") is True
    )
    models_module = types.ModuleType("easy_media.utils.models")
    models_module.detect_turbo_model = lambda model: types.SimpleNamespace(
        is_turbo=False,
        as_dict=lambda: {
            "status": "unknown",
            "is_turbo": False,
            "source": "fallback",
            "evidence": "test detector",
            "patch_count": 0,
        }
    )
    models_module.detect_turbo_lora_from_prompt = lambda prompt, node_id: None
    models_module.known_project_model_loras = lambda prompt, node_id: {}

    root = Path(__file__).parents[1]
    h3_presets_spec = importlib.util.spec_from_file_location(
        "easy_media.utils.h3_presets", root / "utils" / "h3_presets.py"
    )
    assert h3_presets_spec is not None and h3_presets_spec.loader is not None
    h3_presets_module = importlib.util.module_from_spec(h3_presets_spec)
    h3_presets_spec.loader.exec_module(h3_presets_module)
    monkeypatch.setitem(sys.modules, "comfy_api", comfy_api)
    monkeypatch.setitem(sys.modules, "comfy_api.latest", comfy_api_latest)
    monkeypatch.setitem(sys.modules, "folder_paths", folder_paths)
    monkeypatch.setitem(sys.modules, "easy_media.utils", utils_package)
    audio_gain_module = types.ModuleType("easy_media.utils.audio_gain")
    audio_gain_module.audio_db_to_gain = utils_package.audio_db_to_gain
    audio_gain_module.audio_is_muted = utils_package.audio_is_muted
    audio_gain_module.audio_volume_db = utils_package.audio_volume_db
    panorama_module = types.ModuleType("easy_media.utils.panorama")
    panorama_module.equirectangular_to_perspective = (
        utils_package.equirectangular_to_perspective
    )
    monkeypatch.setitem(
        sys.modules,
        "easy_media.utils.audio_gain",
        audio_gain_module,
    )
    monkeypatch.setitem(
        sys.modules,
        "easy_media.utils.panorama",
        panorama_module,
    )
    multitrack_spec = importlib.util.spec_from_file_location(
        "easy_media.utils.multitrack", root / "utils" / "multitrack.py"
    )
    assert multitrack_spec is not None and multitrack_spec.loader is not None
    multitrack_module = importlib.util.module_from_spec(multitrack_spec)
    monkeypatch.setitem(
        sys.modules,
        "easy_media.utils.multitrack",
        multitrack_module,
    )
    multitrack_spec.loader.exec_module(multitrack_module)
    h3_project_spec = importlib.util.spec_from_file_location(
        "easy_media.utils.h3_project", root / "utils" / "h3_project.py"
    )
    assert h3_project_spec is not None and h3_project_spec.loader is not None
    h3_project_module = importlib.util.module_from_spec(h3_project_spec)
    monkeypatch.setitem(
        sys.modules,
        "easy_media.utils.h3_project",
        h3_project_module,
    )
    h3_project_spec.loader.exec_module(h3_project_module)
    h3_project_module.prepare_multitrack_project_media = (
        lambda info: basic_module.prepare_multitrack_project_media(info)
    )
    h3_project_module.crop_multitrack_project_media = (
        lambda shared_audio, shared_video, locked_audio, *args: (
            basic_module.crop_multitrack_project_media(
                shared_audio,
                shared_video,
                locked_audio,
                *args,
            )
        )
    )
    utils_spec = importlib.util.spec_from_file_location(
        "easy_media.utils.minimax", root / "utils" / "minimax.py"
    )
    assert utils_spec is not None and utils_spec.loader is not None
    utils_module = importlib.util.module_from_spec(utils_spec)
    utils_spec.loader.exec_module(utils_module)
    motion_context_spec = importlib.util.spec_from_file_location(
        "easy_media.modules.motion_context.core",
        root / "modules" / "motion_context" / "core.py",
    )
    assert motion_context_spec is not None and motion_context_spec.loader is not None
    motion_context_module = importlib.util.module_from_spec(motion_context_spec)
    motion_context_spec.loader.exec_module(motion_context_module)
    drift_control_module = types.ModuleType(
        "easy_media.modules.motion_context.drift_control_av"
    )
    drift_control_module.apply_context_swap_drift_control = (
        lambda model, target_latent, context_latent, **kwargs: (
            model,
            target_latent,
            int(kwargs.get("context_length", 22)),
        )
    )

    modules = {
        "comfy_api": comfy_api,
        "comfy_api.latest": comfy_api_latest,
        "nodes": core_nodes,
        "comfy": comfy,
        "comfy.model_management": model_management,
        "comfy.nested_tensor": nested_tensor,
        "comfy.utils": comfy_utils,
        "node_helpers": node_helpers,
        "torchaudio": torchaudio,
        "folder_paths": folder_paths,
        "server": server,
        "easy_media": package,
        "easy_media.nodes": nodes_package,
        "easy_media.nodes.basic": basic_module,
        "easy_media.modules": project_modules_package,
        "easy_media.modules.motion_context": motion_context_package,
        "easy_media.modules.motion_context.core": motion_context_module,
        "easy_media.modules.motion_context.drift_control_av": drift_control_module,
        "easy_media.utils": utils_package,
        "easy_media.utils.multitrack": multitrack_module,
        "easy_media.utils.h3_presets": h3_presets_module,
        "easy_media.utils.h3_project": h3_project_module,
        "easy_media.utils.models": models_module,
        "easy_media.utils.minimax": utils_module,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, "comfy_extras.nodes_minimax_h3", raising=False)
    # Other test modules install a reduced graph_utils stub globally. Reload the
    # real execution package so this module always sees GraphBuilder and
    # ExecutionBlocker regardless of test order.
    monkeypatch.delitem(sys.modules, "comfy_execution.graph_utils", raising=False)
    monkeypatch.delitem(sys.modules, "comfy_execution", raising=False)

    path = root / "nodes" / "minimax.py"
    try:
        spec = importlib.util.spec_from_file_location("easy_media.nodes.minimax", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        project_spec = importlib.util.spec_from_file_location(
            "easy_media.nodes.project",
            root / "nodes" / "project.py",
        )
        if project_spec is None or project_spec.loader is None:
            return None
        project_module = importlib.util.module_from_spec(project_spec)
        sys.modules[project_spec.name] = project_module
        project_spec.loader.exec_module(project_module)
        monkeypatch.setattr(project_module, "install_project_memory_cleanup", lambda: None)
        module.EasyMultiTrackProject = project_module.EasyMultiTrackProject
        module.EasyMultiTrackProjectVideoCombine = (
            project_module.EasyMultiTrackProjectVideoCombine
        )
        module._project_module = project_module
        return module
    except (FileNotFoundError, ImportError):
        raise


def _image_values(*values):
    return torch.tensor(values, dtype=torch.float32).reshape(len(values), 1, 1, 1)


def _base_inputs(**overrides):
    inputs = {
        "clip": [_Clip()],
        "vae": [_Vae()],
        "audio_vae": [],
        "images": [],
        "videos": [],
        "audios": [],
        "prompt": ["prompt"],
        "mode": ["multi_frames"],
        "width": [32],
        "height": [32],
        "length": [5],
        "ref_image_size": ["match"],
    }
    inputs.update(overrides)
    return inputs


def _h3_project_inputs(**overrides):
    inputs = {
        "allow_vae_fallback": [True],
        "model_loader": [{
            "model": _MiniMaxH3Model(),
            "clip": object(),
            "vae": object(),
            "audio_vae": object(),
        }],
        "tracks_info": [{
            "width": 1344,
            "height": 768,
            "frame_rate": 24,
            "format": "MiniMax",
            "tracks": [{
                "type": "task",
                "segments": [{
                    "start_frame": 0,
                    "end_frame": 120,
                    "content": {
                        "task_mode": "default",
                        "continuity_mode": "shot",
                        "images": [],
                        "user_prompt": "a cinematic scene",
                    },
                }],
            }],
        }],
    }
    inputs.update(overrides)
    return inputs


def _h3_sampling_mode(mode, **children):
    return [{"sampling_mode": [mode], **children}]


def _graph_node(output, class_type):
    return next(
        node for node in output.expand.values() if node["class_type"] == class_type
    )


def test_module_loads_without_native_minimax_nodes(monkeypatch):
    module = _load_minimax_node(monkeypatch)

    assert module is not None


def test_h3_conditioning_cache_round_trips_and_skips_lazy_inputs(
    monkeypatch,
    tmp_path,
):
    module = _load_minimax_node(monkeypatch)
    schema_inputs = {
        item.name: item
        for item in module.EasyH3ConditioningCache.define_schema().inputs
    }
    assert schema_inputs["conditioning"].kwargs["lazy"] is True
    assert schema_inputs["latent"].kwargs["lazy"] is True
    monkeypatch.setattr(
        module.folder_paths,
        "get_temp_directory",
        lambda: str(tmp_path),
    )
    cache_logs = []
    monkeypatch.setattr(
        module,
        "log_node_info",
        lambda name, message: cache_logs.append((name, message)),
    )
    clip = _Clip()
    vae = _Vae()
    audio_vae = _AudioVae()
    model = object()
    conditioning = [(
        torch.arange(6, dtype=torch.float32).reshape(1, 2, 3),
        {
            "pooled_output": torch.ones(1, 3),
            "minimax_token_tags": torch.tensor([0, 1], dtype=torch.int64),
            "minimax_refs": [{
                "kind": "video_audio",
                "latent_t": 2,
                "latent": torch.full((1, 24, 2, 2, 2), 3.0),
                "audio_latent": torch.full((1, 32, 2, 4), 4.0),
            }],
        },
    )]
    latent = _empty_h3_context_latent(video_steps=2)
    first_status = {
        "_easy_media_cache_status": {
            "project_media": "首次加载",
            "segment_media": "首次加载",
            "task_output": "首次加载",
        }
    }
    assert module.EasyH3ConditioningCache.check_lazy_status(
        project_name="demo",
        segment_index=0,
        tracks_info=first_status,
        task_output_ready="prompt",
        model=model,
        clip=clip,
        vae=vae,
        audio_vae=audio_vae,
    ) == ["conditioning", "latent"]

    first = module.EasyH3ConditioningCache.execute(
        project_name="demo",
        segment_index=0,
        tracks_info=first_status,
        task_output_ready="prompt",
        model=model,
        clip=clip,
        vae=vae,
        audio_vae=audio_vae,
        conditioning=conditioning,
        latent=latent,
    )

    cache_path = _h3_conditioning_cache_dir(tmp_path) / "conditioning_0.safetensors"
    assert cache_path.is_file()
    assert first.values == (conditioning, latent)
    stored = torch.load(
        BytesIO(cache_path.read_bytes()),
        map_location=torch.device("cpu"),
        weights_only=True,
    )
    assert not any(name.startswith("latent.") for name in stored["state"])
    cache_metadata = json.loads(
        stored["metadata"]["easy_media_h3_conditioning_cache"]
    )
    assert cache_metadata["schema_version"] == "3"
    assert cache_metadata["scope_token"]
    assert "文件(" in cache_logs[-1][1]
    assert "=主条件(" in cache_logs[-1][1]
    assert "+参考视频(" in cache_logs[-1][1]
    assert "+参考音频(" in cache_logs[-1][1]
    assert "initial latent" not in cache_logs[-1][1]

    hit_status = {
        "_easy_media_cache_status": {
            "project_media": "命中恢复缓存",
            "segment_media": "命中恢复缓存",
            "task_output": "命中恢复缓存",
        }
    }
    original_load = module.load_h3_conditioning_cache
    load_calls = []

    def tracked_load(*args, **kwargs):
        load_calls.append(args)
        return original_load(*args, **kwargs)

    monkeypatch.setattr(module, "load_h3_conditioning_cache", tracked_load)
    required = module.EasyH3ConditioningCache.check_lazy_status(
        project_name="demo",
        segment_index=0,
        tracks_info=hit_status,
        task_output_ready="prompt",
        model=model,
        clip=clip,
        vae=vae,
        audio_vae=audio_vae,
    )
    assert required == []

    restored = module.EasyH3ConditioningCache.execute(
        project_name="demo",
        segment_index=0,
        tracks_info=hit_status,
        task_output_ready="prompt",
        model=model,
        clip=clip,
        vae=vae,
        audio_vae=audio_vae,
    )
    assert len(load_calls) == 1
    restored_conditioning, restored_latent = restored.values
    assert torch.equal(restored_conditioning[0][0], conditioning[0][0])
    assert torch.equal(
        restored_conditioning[0][1]["minimax_refs"][0]["audio_latent"],
        conditioning[0][1]["minimax_refs"][0]["audio_latent"],
    )
    assert restored_latent["samples"].is_nested is True
    assert all(
        torch.equal(actual, expected)
        for actual, expected in zip(
            restored_latent["samples"].unbind(),
            latent["samples"].unbind(),
        )
    )


def test_h3_conditioning_cache_reencodes_when_model_changes(
    monkeypatch,
    tmp_path,
):
    module = _load_minimax_node(monkeypatch)
    monkeypatch.setattr(
        module.folder_paths,
        "get_temp_directory",
        lambda: str(tmp_path),
    )
    clip = _Clip()
    vae = _Vae()
    audio_vae = _AudioVae()
    model = object()
    hit_status = {
        "_easy_media_cache_status": {
            "project_media": "命中恢复缓存",
            "segment_media": "命中恢复缓存",
            "task_output": "命中恢复缓存",
        }
    }
    module.EasyH3ConditioningCache.execute(
        project_name="demo",
        segment_index=1,
        tracks_info=hit_status,
        task_output_ready="prompt",
        model=model,
        clip=clip,
        vae=vae,
        audio_vae=audio_vae,
        conditioning=[(torch.ones(1), {})],
        latent=_empty_h3_context_latent(),
    )

    replacement_model = object()
    required = module.EasyH3ConditioningCache.check_lazy_status(
        project_name="demo",
        segment_index=1,
        tracks_info=hit_status,
        task_output_ready="prompt",
        model=replacement_model,
        clip=clip,
        vae=vae,
        audio_vae=audio_vae,
    )

    assert required == ["conditioning", "latent"]
    module.EasyH3ConditioningCache.execute(
        project_name="demo",
        segment_index=1,
        tracks_info=hit_status,
        task_output_ready="prompt",
        model=replacement_model,
        clip=clip,
        vae=vae,
        audio_vae=audio_vae,
        conditioning=[(torch.full((1,), 2.0), {})],
        latent=_empty_h3_context_latent(),
    )
    cache_dir = _h3_conditioning_cache_dir(tmp_path)
    assert [path.name for path in cache_dir.glob("conditioning_1*.safetensors")] == [
        "conditioning_1.safetensors"
    ]
    assert module.EasyH3ConditioningCache.check_lazy_status(
        project_name="demo",
        segment_index=1,
        tracks_info=hit_status,
        task_output_ready="prompt",
        model=replacement_model,
        clip=clip,
        vae=vae,
        audio_vae=audio_vae,
    ) == []


def test_h3_conditioning_cache_refuses_nonzero_initial_latent(
    monkeypatch,
    tmp_path,
):
    module = _load_minimax_node(monkeypatch)
    cache_path = tmp_path / "conditioning_0.safetensors"

    with pytest.raises(ValueError, match="must be zero-filled"):
        module.save_h3_conditioning_cache(
            [(torch.ones(1), {})],
            _h3_context_latent(1),
            cache_path,
            "encoder",
            "scope",
        )

    assert not cache_path.exists()


def test_h3_conditioning_cache_invalidates_temp_pool_once_per_queue(
    monkeypatch,
    tmp_path,
):
    module = _load_minimax_node(monkeypatch)
    monkeypatch.setattr(
        module.folder_paths,
        "get_temp_directory",
        lambda: str(tmp_path),
    )
    clip = _Clip()
    vae = _Vae()
    audio_vae = _AudioVae()
    model = object()
    miss_status = {
        "_easy_media_cache_status": {
            "project_media": "首次加载",
            "segment_media": "首次加载",
            "task_output": "首次加载",
        }
    }

    for segment_index in (0, 1):
        module.EasyH3ConditioningCache.execute(
            project_name="demo",
            segment_index=segment_index,
            tracks_info=miss_status,
            task_output_ready="prompt",
            model=model,
            clip=clip,
            vae=vae,
            audio_vae=audio_vae,
            conditioning=[(torch.tensor([segment_index]), {})],
            latent=_empty_h3_context_latent(),
        )

    assert sorted(
        path.name for path in _h3_conditioning_cache_dir(tmp_path).glob("*.safetensors")
    ) == ["conditioning_0.safetensors", "conditioning_1.safetensors"]


def test_h3_conditioning_cache_switching_project_resets_temp_pool(
    monkeypatch,
    tmp_path,
):
    module = _load_minimax_node(monkeypatch)
    monkeypatch.setattr(
        module.folder_paths,
        "get_temp_directory",
        lambda: str(tmp_path),
    )
    clip = _Clip()
    vae = _Vae()
    audio_vae = _AudioVae()
    model = object()
    hit_status = {
        "_easy_media_cache_status": {
            "project_media": "命中恢复缓存",
            "segment_media": "命中恢复缓存",
            "task_output": "命中恢复缓存",
        }
    }

    for project_name, segment_index in (("first", 0), ("second", 1)):
        module.EasyH3ConditioningCache.execute(
            project_name=project_name,
            segment_index=segment_index,
            tracks_info=hit_status,
            task_output_ready="prompt",
            model=model,
            clip=clip,
            vae=vae,
            audio_vae=audio_vae,
            conditioning=[(torch.tensor([segment_index]), {})],
            latent=_empty_h3_context_latent(),
        )

    assert [
        path.name for path in _h3_conditioning_cache_dir(tmp_path).glob("*.safetensors")
    ] == ["conditioning_1.safetensors"]


def test_h3_conditioning_cache_keeps_only_five_recent_segments(
    monkeypatch,
    tmp_path,
):
    module = _load_minimax_node(monkeypatch)
    monkeypatch.setattr(
        module.folder_paths,
        "get_temp_directory",
        lambda: str(tmp_path),
    )
    clip = _Clip()
    vae = _Vae()
    audio_vae = _AudioVae()
    model = object()
    hit_status = {
        "_easy_media_cache_status": {
            "project_media": "命中恢复缓存",
            "segment_media": "命中恢复缓存",
            "task_output": "命中恢复缓存",
        }
    }

    for segment_index in range(6):
        module.EasyH3ConditioningCache.execute(
            project_name="demo",
            segment_index=segment_index,
            tracks_info=hit_status,
            task_output_ready="prompt",
            model=model,
            clip=clip,
            vae=vae,
            audio_vae=audio_vae,
            conditioning=[(torch.tensor([segment_index]), {})],
            latent=_empty_h3_context_latent(),
        )

    cache_files = sorted(
        path.name for path in _h3_conditioning_cache_dir(tmp_path).glob("*.safetensors")
    )
    assert len(cache_files) == 5
    assert "conditioning_0.safetensors" not in cache_files


def test_h3_conditioning_cache_unavailable_pool_falls_back_to_encoding(
    monkeypatch,
    tmp_path,
):
    module = _load_minimax_node(monkeypatch)
    monkeypatch.setattr(
        module.folder_paths,
        "get_temp_directory",
        lambda: str(tmp_path),
    )
    monkeypatch.setattr(
        module,
        "prepare_h3_conditioning_cache_pool",
        lambda *_args, **_kwargs: None,
    )
    clip = _Clip()
    vae = _Vae()
    audio_vae = _AudioVae()
    model = object()
    hit_status = {
        "_easy_media_cache_status": {
            "project_media": "命中恢复缓存",
            "segment_media": "命中恢复缓存",
            "task_output": "命中恢复缓存",
        }
    }

    required = module.EasyH3ConditioningCache.check_lazy_status(
        project_name="demo",
        segment_index=0,
        tracks_info=hit_status,
        task_output_ready="prompt",
        model=model,
        clip=clip,
        vae=vae,
        audio_vae=audio_vae,
    )

    assert required == ["conditioning", "latent"]
    conditioning = [(torch.ones(1), {})]
    latent = _empty_h3_context_latent()
    result = module.EasyH3ConditioningCache.execute(
        project_name="demo",
        segment_index=0,
        tracks_info=hit_status,
        task_output_ready="prompt",
        model=model,
        clip=clip,
        vae=vae,
        audio_vae=audio_vae,
        conditioning=conditioning,
        latent=latent,
    )
    assert result.values == (conditioning, latent)
    assert not _h3_conditioning_cache_dir(tmp_path).exists()


def test_h3_conditioning_cache_scope_rejects_stale_file_when_clear_fails(
    monkeypatch,
    tmp_path,
):
    module = _load_minimax_node(monkeypatch)
    monkeypatch.setattr(
        module.folder_paths,
        "get_temp_directory",
        lambda: str(tmp_path),
    )
    clip = _Clip()
    vae = _Vae()
    audio_vae = _AudioVae()
    model = object()
    hit_status = {
        "_easy_media_cache_status": {
            "project_media": "命中恢复缓存",
            "segment_media": "命中恢复缓存",
            "task_output": "命中恢复缓存",
        }
    }
    module.EasyH3ConditioningCache.execute(
        project_name="first",
        segment_index=0,
        tracks_info=hit_status,
        task_output_ready="prompt",
        model=model,
        clip=clip,
        vae=vae,
        audio_vae=audio_vae,
        conditioning=[(torch.ones(1), {})],
        latent=_empty_h3_context_latent(),
    )
    cache_path = _h3_conditioning_cache_dir(tmp_path) / "conditioning_0.safetensors"
    assert cache_path.is_file()
    cache_module = sys.modules["easy_media.utils.h3_conditioning_cache"]
    monkeypatch.setattr(
        cache_module,
        "_clear_h3_conditioning_cache_files",
        lambda _cache_dir: None,
    )

    required = module.EasyH3ConditioningCache.check_lazy_status(
        project_name="second",
        segment_index=0,
        tracks_info=hit_status,
        task_output_ready="prompt",
        model=model,
        clip=clip,
        vae=vae,
        audio_vae=audio_vae,
    )

    assert cache_path.is_file()
    assert required == ["conditioning", "latent"]


def test_h3_conditioning_cache_corrupt_body_falls_back_in_same_queue(
    monkeypatch,
    tmp_path,
):
    module = _load_minimax_node(monkeypatch)
    monkeypatch.setattr(
        module.folder_paths,
        "get_temp_directory",
        lambda: str(tmp_path),
    )
    clip = _Clip()
    vae = _Vae()
    audio_vae = _AudioVae()
    model = object()
    hit_status = {
        "_easy_media_cache_status": {
            "project_media": "命中恢复缓存",
            "segment_media": "命中恢复缓存",
            "task_output": "命中恢复缓存",
        }
    }
    module.EasyH3ConditioningCache.execute(
        project_name="demo",
        segment_index=0,
        tracks_info=hit_status,
        task_output_ready="prompt",
        model=model,
        clip=clip,
        vae=vae,
        audio_vae=audio_vae,
        conditioning=[(torch.ones(1), {})],
        latent=_empty_h3_context_latent(),
    )
    cache_path = _h3_conditioning_cache_dir(tmp_path) / "conditioning_0.safetensors"
    monkeypatch.setattr(
        module,
        "load_h3_conditioning_cache",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("corrupt body")),
    )

    required = module.EasyH3ConditioningCache.check_lazy_status(
        project_name="demo",
        segment_index=0,
        tracks_info=hit_status,
        task_output_ready="prompt",
        model=model,
        clip=clip,
        vae=vae,
        audio_vae=audio_vae,
    )

    assert required == ["conditioning", "latent"]
    assert not cache_path.exists()


def test_fallback_conditioning_nodes_use_native_node_ids(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    assert (
        module.MiniMaxH3ImageToVideoFallback.define_schema().node_id
        == "MiniMaxH3ImageToVideo"
    )
    assert (
        module.MiniMaxH3ReferenceToVideoFallback.define_schema().node_id
        == "MiniMaxH3ReferenceToVideo"
    )


def test_missing_native_conditioning_nodes_are_selected_for_registration(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    assert module.get_minimax_h3_fallback_nodes() == [
        module.MiniMaxH3ImageToVideoFallback,
        module.MiniMaxH3ReferenceToVideoFallback,
    ]


def test_reference_mode_routes_to_native_node_when_available(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    module.comfy_nodes.NODE_CLASS_MAPPINGS.update(
        {
            "MiniMaxH3ImageToVideo": object,
            "MiniMaxH3ReferenceToVideo": object,
        }
    )

    output = module.EasyMiniMaxH3ToVideo.execute(
        **_base_inputs(mode=["reference"], images=[_image_values(1)])
    )

    assert module.get_minimax_h3_fallback_nodes() == []
    assert _graph_node(output, module.REFERENCE_BRIDGE_NODE_ID)


def test_missing_native_reference_registers_fallback_and_routes_through_bridge(
    monkeypatch,
):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    module.comfy_nodes.NODE_CLASS_MAPPINGS["MiniMaxH3ImageToVideo"] = object

    output = module.EasyMiniMaxH3ToVideo.execute(
        **_base_inputs(mode=["reference"], images=[_image_values(1)])
    )

    assert module.get_minimax_h3_fallback_nodes() == [
        module.MiniMaxH3ReferenceToVideoFallback,
    ]
    assert _graph_node(output, module.REFERENCE_BRIDGE_NODE_ID)


def test_schema_exposes_list_media_inputs_without_image_position(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    schema = module.EasyMiniMaxH3ToVideo.define_schema()
    inputs = {port.name: port for port in schema.inputs}

    assert schema.node_id == "easy minimaxH3ToVideo"
    assert schema.is_input_list is True
    assert schema.enable_expand is True
    assert list(inputs) == [
        "clip",
        "vae",
        "audio_vae",
        "images",
        "audios",
        "videos",
        "prompt",
        "mode",
        "width",
        "height",
        "length",
        "native_locked_video",
        "locked_video_timing_frames",
        "ref_image_size",
    ]
    assert inputs["audio_vae"].kwargs["optional"] is True
    assert inputs["mode"].kwargs["options"] == [
        "reference",
        "multi_frames",
        "last_frame",
    ]
    assert [output.name for output in schema.outputs] == ["positive", "latent"]


def test_multitrack_h3_project_schema_exposes_pipeline_configuration(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    schema = module.EasyMultiTrackProject.define_schema()
    inputs = {port.name: port for port in schema.inputs}

    assert schema.node_id == "easy multitrackProject"
    assert schema.is_input_list is True
    assert schema.enable_expand is True
    assert not getattr(schema, "is_output_node", False)
    assert schema.not_idempotent is True
    assert schema.hidden == ["PROMPT", "UNIQUE_ID"]
    assert list(inputs) == [
        "tracks_info",
        "model_loader",
        "model_loader_2nd",
        "sampler",
        "sampler_2nd",
        "sigmas",
        "sigmas_2nd",
        "project_name",
        "project_save",
        "segment_start_number",
        "segment_count",
        "seed",
        "sampling_plan",
        "sampling_mode",
        "1st_pass_only",
        "disable_2nd_noise",
        "upscale_by",
        "upscale_model",
        "enabled_tiling",
        "allow_vae_fallback",
        "segment_loras",
    ]
    assert inputs["segment_loras"].kwargs["optional"] is True
    for name in (
        "sampler",
        "sigmas",
    ):
        assert inputs[name].kwargs["optional"] is True
        assert inputs[name].kwargs["raw_link"] is True
        assert inputs[name].kwargs["lazy"] is True
    assert inputs["model_loader"].kwargs["raw_link"] is True
    assert inputs["model_loader"].kwargs["lazy"] is True
    assert inputs["project_name"].kwargs["default"] == ""
    assert inputs["project_save"].kwargs["options"] == ["new", "override"]
    assert inputs["project_save"].kwargs["default"] == "override"
    sampling_plan_options = inputs["sampling_plan"].kwargs["options"]
    assert sampling_plan_options == [
        "custom",
        "ultra_light",
        "light",
        "medium",
        "high",
    ]
    assert json.loads(json.dumps(sampling_plan_options)) == sampling_plan_options
    assert inputs["sampling_plan"].kwargs["default"] == "light"
    sampling_mode_options = inputs["sampling_mode"].kwargs["options"]
    assert [name for name, _ in sampling_mode_options] == [
        "single",
        "dual",
        "selflift",
        "passthrough",
    ]
    assert sampling_mode_options[0][1] == []
    assert sampling_mode_options[1][1] == []
    assert sampling_mode_options[3][1] == []
    selflift_inputs = {port.name: port for port in sampling_mode_options[2][1]}
    assert list(selflift_inputs) == [
        "transition_ratio",
        "lowres_scale",
    ]
    assert selflift_inputs["transition_ratio"].kwargs["default"] == 0.6
    assert selflift_inputs["lowres_scale"].kwargs["default"] == 0.6
    for name in ("sampler_2nd", "sigmas_2nd", "model_loader_2nd"):
        assert inputs[name].kwargs["optional"] is True
        assert inputs[name].kwargs["raw_link"] is True
        assert inputs[name].kwargs["lazy"] is True
    assert inputs["1st_pass_only"].kwargs["default"] is False
    assert inputs["disable_2nd_noise"].kwargs["default"] is False
    assert inputs["upscale_by"].kwargs["default"] == 1.250
    assert inputs["upscale_by"].kwargs["step"] == 0.001
    assert inputs["upscale_by"].kwargs["round"] == 0.001
    assert inputs["upscale_by"].kwargs["extra_dict"] == {"precision": 3}
    assert inputs["segment_start_number"].kwargs["default"] == 1
    assert inputs["segment_start_number"].kwargs["min"] == 1
    assert inputs["segment_count"].kwargs["default"] == -1
    assert [output.name for output in schema.outputs] == [
        "PROJECT_NAME",
        "LOCKED_AUDIO",
    ]


def test_h3_project_passthrough_shot_keeps_outgoing_context(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    result = module.EasyMultiTrackProject.execute(
        **_h3_project_inputs(sampling_mode=_h3_sampling_mode("passthrough"))
    )
    nodes = list(result.expand.values())
    types = {node["class_type"] for node in nodes}
    assert "easy h3PassthroughVideo" in types
    assert "easy minimaxH3ToVideo" not in types
    assert "easy h3ConditioningCache" not in types
    assert "SamplerCustomAdvanced" not in types
    assert "easy h3SegmentSamplingStart" not in types
    passthrough = _graph_node(result, "easy h3PassthroughVideo")
    assert passthrough["inputs"]["frame_count"] == 124
    assert passthrough["inputs"]["fps"] == 24.0
    assert "easy saveVideo" not in types
    artifact = _graph_node(result, "easy h3NativeArtifact")
    assert "latent" in artifact["inputs"]
    seed = _graph_node(result, "easy h3NativeSeed")
    assert json.loads(seed["inputs"]["plan_json"])["continuity_mode"] == "shot"


def test_h3_project_task_passthrough_can_mix_with_sampling(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    inputs = _h3_project_inputs()
    task_segments = inputs["tracks_info"][0]["tracks"][0]["segments"]
    task_segments[0]["content"]["task_mode"] = "passthrough"
    task_segments.extend([
        {
            "start_frame": 120,
            "end_frame": 240,
            "content": {
                "task_mode": "default",
                "continuity_mode": "context",
                "images": [],
                "user_prompt": "continue after source",
            },
        },
        {
            "start_frame": 240,
            "end_frame": 360,
            "content": {
                "task_mode": "passthrough",
                "continuity_mode": "context_swap",
                "images": [],
                "user_prompt": "ignored",
            },
        },
        {
            "start_frame": 360,
            "end_frame": 480,
            "content": {
                "task_mode": "default",
                "continuity_mode": "context",
                "images": [],
                "user_prompt": "continue after second source",
            },
        },
    ])
    result = module.EasyMultiTrackProject.execute(**inputs)
    nodes = list(result.expand.values())
    assert sum(node["class_type"] == "easy h3PassthroughVideo" for node in nodes) == 2
    assert sum(node["class_type"] == "easy minimaxH3ToVideo" for node in nodes) == 2
    assert sum(node["class_type"] == "easy h3NativeArtifact" for node in nodes) == 4
    assert not any(node["class_type"] == "easy h3ProjectContextLatentLoad" for node in nodes)
    artifacts = [node for node in nodes if node["class_type"] == "easy h3NativeArtifact"]
    assert [node["inputs"]["tracks_info"]["tracks"][0]["segments"][i]["content"]["continuity_mode"] for i, node in enumerate(artifacts)] == ["shot", "context", "shot", "context"]


def test_h3_project_single_task_passthrough_skips_prior_context_and_sampler(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    inputs = _h3_project_inputs(segment_start_number=[2], segment_count=[1])
    task_segments = inputs["tracks_info"][0]["tracks"][0]["segments"]
    task_segments.append({
        "start_frame": 120,
        "end_frame": 240,
        "content": {
            "task_mode": "passthrough",
            "continuity_mode": "context",
            "images": [],
            "user_prompt": "ignored",
        },
    })
    monkeypatch.setattr(module._project_module, "has_h3_context_latent", lambda *args, **kwargs: False, raising=False)
    result = module.EasyMultiTrackProject.execute(**inputs)
    types = {node["class_type"] for node in result.expand.values()}
    assert "easy h3PassthroughVideo" in types
    assert "easy h3ProjectContextLatentLoad" not in types
    assert "SamplerCustomAdvanced" not in types
    assert "easy minimaxH3ToVideo" not in types


@pytest.mark.parametrize("continuity_mode", ["shot", "context", "context_swap"])
def test_h3_project_passthrough_ignores_continuity_mode_and_encodes_tail(
    monkeypatch, continuity_mode,
):
    module = _load_minimax_node(monkeypatch)
    inputs = _h3_project_inputs(sampling_mode=_h3_sampling_mode("passthrough"))
    inputs["tracks_info"][0]["tracks"][0]["segments"][0]["content"]["continuity_mode"] = continuity_mode
    result = module.EasyMultiTrackProject.execute(**inputs)
    passthrough = _graph_node(result, "easy h3PassthroughVideo")
    assert "keep_context" not in passthrough["inputs"]
    artifact = _graph_node(result, "easy h3NativeArtifact")
    seed = result.expand[artifact["inputs"]["latent"][0]]
    assert seed["class_type"] == "easy h3NativeSeed"
    assert json.loads(seed["inputs"]["plan_json"])["continuity_mode"] == "shot"




def test_h3_project_static_prepare_exposes_media_cache_boundary(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    static_schema = module._project_module.EasyH3ProjectStaticPrepare.define_schema()

    assert static_schema.node_id == "easy h3ProjectStaticPrepare"
    assert static_schema.enable_expand is True


def test_multitrack_h3_project_keeps_model_and_media_as_prepare_links(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    inputs = _h3_project_inputs(model_loader=[["loader", 0]])
    module.EasyMultiTrackProject.hidden = types.SimpleNamespace(
        prompt={
            "project-node": {
                "inputs": {
                    "tracks_info": ["multitrack-info", 0],
                    "model_loader": ["loader", 0],
                },
            },
        },
        unique_id="project-node",
    )

    result = module.EasyMultiTrackProject.execute(**inputs)
    media_id, media_node = next(
        (node_id, node)
        for node_id, node in result.expand.items()
        if node["class_type"] == "easy h3ProjectStaticPrepare"
        and node_id.endswith("project_media_prepare")
    )
    model_id, model_node = next(
        (node_id, node)
        for node_id, node in result.expand.items()
        if node["class_type"] == "easy h3ProjectStaticPrepare"
        and node_id.endswith("project_model_prepare")
    )
    segment_id, segment_node = next(
        (node_id, node)
        for node_id, node in result.expand.items()
        if node["class_type"] == "easy h3ProjectStaticPrepare"
        and node_id.endswith("segment_static_prepare_0")
    )
    task_id, task = next(
        (node_id, node)
        for node_id, node in result.expand.items()
        if node["class_type"] == "easy multiTrackTaskOutput"
    )
    conditioning = _graph_node(result, "easy minimaxH3ToVideo")

    assert model_node["inputs"]["model_loader"] == ["loader", 0]
    assert "tracks_info" not in model_node["inputs"]
    assert media_node["inputs"]["tracks_info"]["h3_native"]["version"] == 2
    assert "model_loader" not in media_node["inputs"]
    assert segment_node["inputs"]["project_static"] == [media_id, 0]
    assert task["inputs"]["tracks_info"] == [segment_id, 1]
    assert conditioning["inputs"]["clip"] == [model_id, 4]
    assert conditioning["inputs"]["images"] == [task_id, 4]
    assert "seed" not in model_node["inputs"]
    assert "seed" not in media_node["inputs"]
    assert "seed" not in segment_node["inputs"]
    assert "seed" not in task["inputs"]
    assert "seed" not in conditioning["inputs"]


def test_linked_selflift_keeps_segment_order_after_cached_media(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    inputs = _h3_project_inputs(
        model_loader=[["loader", 0]],
        sampling_mode=_h3_sampling_mode("selflift"),
    )
    first = inputs["tracks_info"][0]["tracks"][0]["segments"][0]
    inputs["tracks_info"][0]["tracks"][0]["segments"].append({
        **first,
        "start_frame": 120,
        "end_frame": 240,
        "content": {**first["content"]},
    })

    result = module.EasyMultiTrackProject.execute(**inputs)
    artifact_id = next(
        node_id
        for node_id, node in result.expand.items()
        if node["class_type"] == "easy h3NativeArtifact"
        and node["inputs"]["segment_index"] == 0
    )
    second_selflift = next(
        node
        for node_id, node in result.expand.items()
        if node["class_type"] == "easy minimaxH3SelfLiftSampler"
        and node_id.endswith("selflift_sample_1")
    )
    second_segment_prepare = next(
        node
        for node_id, node in result.expand.items()
        if node["class_type"] == "easy h3ProjectStaticPrepare"
        and node_id.endswith("segment_static_prepare_1")
    )

    assert second_selflift["inputs"]["previous"] == [artifact_id, 0]
    assert second_segment_prepare["inputs"]["previous"] == [artifact_id, 0]


def test_h3_project_static_prepare_materializes_once_then_crops(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    project_module = module._project_module
    loader = {
        "model": _MiniMaxH3Model(),
        "clip": "clip",
        "vae": "vae",
        "audio_vae": "audio-vae",
    }
    prepared = []
    shared_image = _image_values(1)
    shared_audio = {"waveform": torch.ones(1, 1, 1), "sample_rate": 1}
    shared_video = object()
    locked_audio = {"waveform": torch.ones(1, 1, 1), "sample_rate": 1}

    def prepare(info):
        prepared.append(info)
        return info, [shared_image], [shared_audio], [shared_video], locked_audio

    monkeypatch.setattr(project_module, "prepare_multitrack_project_media", prepare)
    monkeypatch.setattr(
        project_module,
        "crop_multitrack_project_media",
        lambda audio, video, locked, *_args: (audio, video, locked),
    )
    tracks_info = _h3_project_inputs()["tracks_info"]
    global_result = project_module.EasyH3ProjectStaticPrepare.execute(
        model_loader=loader,
        tracks_info=tracks_info,
        sampling_plan="custom",
        sampler="sampler",
        sigmas="sigmas",
    )
    segment_result = project_module.EasyH3ProjectStaticPrepare.execute(
        project_static=global_result.values[0],
        task_start_frame=0,
        task_duration_frames=120,
        generation_mode="reference",
    )
    restored_global = project_module.EasyH3ProjectStaticPrepare.execute(
        tracks_info=tracks_info,
    )
    restored_segment = project_module.EasyH3ProjectStaticPrepare.execute(
        project_static=restored_global.values[0],
        task_start_frame=0,
        task_duration_frames=120,
        generation_mode="reference",
    )

    def assert_no_container_cycle(value, ancestors=None):
        if not isinstance(value, (dict, list, tuple)):
            return
        ancestors = set() if ancestors is None else ancestors
        assert id(value) not in ancestors
        nested_ancestors = ancestors | {id(value)}
        children = value.values() if isinstance(value, dict) else value
        for child in children:
            assert_no_container_cycle(child, nested_ancestors)

    assert len(prepared) == 1
    assert global_result.values[2:7] == (
        loader["model"],
        loader["model"],
        "clip",
        "vae",
        "audio-vae",
    )
    assert segment_result.values[1]["_preloaded_media"] == {
        "images": [shared_image],
        "audio": [shared_audio],
        "video": [shared_video],
    }
    assert segment_result.values[8] is locked_audio
    assert restored_global.values[0]["shared_images"] is global_result.values[0]["shared_images"]
    assert restored_global.values[0]["shared_audio"] is global_result.values[0]["shared_audio"]
    assert restored_global.values[0]["shared_video"] is global_result.values[0]["shared_video"]
    assert restored_segment.values[1] is segment_result.values[1]
    assert restored_segment.values[8] is locked_audio
    assert_no_container_cycle(tracks_info)


def test_multitrack_h3_project_loads_segment_media_from_tracks_info(monkeypatch):
    module = _load_minimax_node(monkeypatch)

    result = module.EasyMultiTrackProject.execute(**_h3_project_inputs())
    task_node = next(
        node
        for node in result.expand.values()
        if node["class_type"] == "easy multiTrackTaskOutput"
    )

    assert set(task_node["inputs"]) == {
        "tracks_info",
        "task_index",
        "prompt_format",
        "previous",
    }


@pytest.mark.parametrize(
    "sampling_mode,first_only",
    [
        ("single", False),
        ("dual", False),
        ("dual", True),
        ("selflift", False),
    ],
)
def test_project_memory_boundaries_follow_artifact_saves(monkeypatch, sampling_mode, first_only):
    module = _load_minimax_node(monkeypatch)
    installed = []
    monkeypatch.setattr(module._project_module, "install_project_memory_cleanup", lambda: installed.append(True))
    result = module.EasyMultiTrackProject.execute(**_h3_project_inputs(
        sampling_mode=_h3_sampling_mode(sampling_mode, **{"1st_pass_only": [first_only]}),
        upscale_by=[1.0],
    ))
    assert installed == [True]
    tagged = [node for node in result.expand.values() if "easy_media_segment" in node.get("_meta", {})]
    assert tagged
    assert all(node["_meta"]["easy_media_segment"] == 0 for node in tagged)
    boundaries = [node for node in tagged if node["_meta"].get("easy_media_segment_saved")]
    assert len(boundaries) == 1
    assert boundaries[0]["class_type"] == "easy h3NativeArtifact"
    assert "video_path" in boundaries[0]["inputs"]
    assert "latent" in boundaries[0]["inputs"]
    assert all(
        "easy_media_segment" not in node.get("_meta", {})
        for node in result.expand.values()
        if node["class_type"] in {
            "KSamplerSelect",
            "ManualSigmas",
            "easy h3ProjectStaticPrepare",
            "easy multiTrackTaskOutput",
        }
    )
    conditioning = _graph_node(result, "easy minimaxH3ToVideo")
    assert conditioning["_meta"]["easy_media_segment"] == 0


def test_multitrack_h3_project_prepends_shared_media_before_h3_conditioning(
    monkeypatch,
):
    module = _load_minimax_node(monkeypatch)
    shared_image = _image_values(1)
    shared_audio = {"waveform": torch.ones(1, 1, 1), "sample_rate": 1}
    shared_video = object()
    inputs = _h3_project_inputs()
    inputs["tracks_info"][0]["tracks"][0]["segments"][0]["content"][
        "task_mode"
    ] = "ref"
    monkeypatch.setattr(
        module._project_module,
        "prepare_multitrack_project_media",
        lambda info: (
            info,
            [shared_image],
            [shared_audio],
            [shared_video],
            None,
        ),
    )
    monkeypatch.setattr(
        module._project_module,
        "crop_multitrack_project_media",
        lambda shared_audios, shared_videos, locked_audio, *_args: (
            shared_audios,
            shared_videos,
            locked_audio,
        ),
    )

    result = module.EasyMultiTrackProject.execute(**inputs)
    conditioning = _graph_node(result, "easy minimaxH3ToVideo")
    task_node_id, task_node = next(
        (node_id, node)
        for node_id, node in result.expand.items()
        if node["class_type"] == "easy multiTrackTaskOutput"
    )
    preloaded_media = task_node["inputs"]["tracks_info"]["_preloaded_media"]

    assert preloaded_media["images"][0] is shared_image
    assert preloaded_media["audio"][0] is shared_audio
    assert preloaded_media["video"][0] is shared_video
    assert conditioning["inputs"]["images"] == [task_node_id, 4]
    assert conditioning["inputs"]["audios"] == [task_node_id, 5]
    assert conditioning["inputs"]["videos"] == [task_node_id, 6]
    assert not any(name.startswith("shared_") for name in conditioning["inputs"])
    assert not any(
        node["class_type"] == "easy h3ProjectMediaPreprocessor"
        for node in result.expand.values()
    )


def test_multitrack_h3_minus_one_defers_each_task_media_until_its_loop(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    inputs = _h3_project_inputs(segment_count=[-1])
    inputs["tracks_info"][0]["tracks"][0]["segments"].append({
        "start_frame": 120,
        "end_frame": 240,
        "content": {"user_prompt": "second", "task_mode": "default"},
    })

    result = module.EasyMultiTrackProject.execute(**inputs)
    task_nodes = sorted(
        (
            (node_id, node)
            for node_id, node in result.expand.items()
            if node["class_type"] == "easy multiTrackTaskOutput"
        ),
        key=lambda item: item[1]["inputs"]["task_index"],
    )
    first_artifact_id = next(
        node_id
        for node_id, node in result.expand.items()
        if node["class_type"] == "easy h3NativeArtifact"
        and node["inputs"]["segment_index"] == 0
    )
    assert result.expand[task_nodes[0][1]["inputs"]["previous"][0]]["class_type"] == "easy h3NativePreflight"
    assert task_nodes[1][1]["inputs"]["previous"] == [first_artifact_id, 0]


def test_multitrack_h3_project_outputs_locked_audio_used_by_generation(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    inputs = _h3_project_inputs()
    inputs["tracks_info"][0]["tracks"].append({
        "type": "audio",
        "audio_locked": True,
        "segments": [{
            "start_frame": 0,
            "end_frame": 120,
            "content": {"media_type": "audio"},
        }],
    })

    result = module.EasyMultiTrackProject.execute(**inputs)
    audio_lock = _graph_node(result, "easy h3NativeAudioLock")
    saved_video = _graph_node(result, "easy saveVideo")

    assert result.values[1] == {"prepared_locked_audio": True}
    assert audio_lock["inputs"]["audio"] == {"prepared_locked_audio": True}
    artifact = _graph_node(result, "easy h3NativeArtifact")
    assert "locked_audio" in artifact["inputs"]
    selector = _graph_node(result, "easy h3LockedAudioSelect")
    assert selector["inputs"]["locked_audio"][0].endswith("native_audio_lock_0")
    assert saved_video["inputs"]["input_mode.audio"][0].endswith(
        "locked_audio_select_0"
    )
    assert not any(
        node["class_type"] == "easy multiTrackTaskOutput"
        and node["inputs"]["task_index"] == -1
        for node in result.expand.values()
    )


def test_multitrack_h3_project_outputs_none_without_locked_audio(monkeypatch):
    module = _load_minimax_node(monkeypatch)

    result = module.EasyMultiTrackProject.execute(**_h3_project_inputs())

    assert result.values[1] is None
    assert not any(
        node["class_type"] == "easy h3LockedAudioDurationAlign"
        for node in result.expand.values()
    )
    saved_video = _graph_node(result, "easy saveVideo")
    audio_link = saved_video["inputs"]["input_mode.audio"]
    assert result.expand[audio_link[0]]["class_type"] == "easy h3NativeMediaView"


def test_muted_locked_video_preserves_timing_without_audio_lock(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    inputs = _h3_project_inputs()
    info = inputs["tracks_info"][0]
    info["tracks"].append({
        "type": "video",
        "audio_locked": True,
        "muted": True,
        "segments": [{
            "start_frame": 0,
            "end_frame": 120,
            "content": {"media_type": "video"},
        }],
    })

    info['tracks'][0]['segments'][0]['content']['task_mode'] = 'ref'
    info['tracks'][1]['segments'][0]['end_frame'] = 124
    result = module.EasyMultiTrackProject.execute(**inputs)

    assert not any(
        node["class_type"] == "easy h3NativeAudioLock"
        for node in result.expand.values()
    )
    raw = _graph_node(result, 'easy h3NativeLockedVideoInfo')
    assert json.loads(raw['inputs']['plan_json'])['raw_frames'] == 124
    saved_video = _graph_node(result, 'easy saveVideo')
    audio_link = saved_video['inputs']['input_mode.audio']
    assert result.expand[audio_link[0]]['class_type'] == 'easy h3NativeMediaView'


def test_locked_audio_select_falls_back_when_video_has_no_audio(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    generated = {"waveform": torch.ones(1, 1, 4), "sample_rate": 24}

    missing = module.EasyH3LockedAudioSelect.execute(generated, None)
    malformed = module.EasyH3LockedAudioSelect.execute(generated, {})

    assert missing.values[0] is generated
    assert malformed.values[0] is generated


def test_multitrack_h3_project_does_not_log_execution_events_while_expanding(
    monkeypatch,
):
    module = _load_minimax_node(monkeypatch)
    messages = []
    monkeypatch.setattr(
        module._project_module,
        "log_node_info",
        lambda node_name, message: messages.append((node_name, message)),
    )

    module.EasyMultiTrackProject.execute(**_h3_project_inputs())

    assert messages == [
        ("MultiTrack Project", "Found 1 segments; processing 1"),
    ]


def test_h3_segment_execution_markers_log_the_current_segment(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    messages = []
    notifications = []
    monkeypatch.setattr(
        module,
        "log_node_info",
        lambda node_name, message: messages.append((node_name, message)),
    )
    server = types.ModuleType("server")
    server.PromptServer = types.SimpleNamespace(
        instance=types.SimpleNamespace(
            send_sync=lambda event, payload: notifications.append((event, payload))
        )
    )
    monkeypatch.setitem(sys.modules, "server", server)

    latent = {"samples": torch.zeros(1)}
    sampling_output = module.EasyH3SegmentSamplingStart.execute(
        object(), object(), object(), object(), latent, "demo", 3, "first"
    )
    save_end_output = module.EasyH3SegmentSaveEnd.execute("video.mp4", "demo", 3)

    assert sampling_output.values[-1] is latent
    assert save_end_output.values == ("video.mp4",)
    assert notifications == [
        (
            "easy_multitrack_project_refresh",
            {
                "project_name": "demo",
                "phase": "before",
                "segment_index": 3,
                "sampling_pass": "first",
            },
        ),
    ]
    assert len(messages) == 1
    assert messages[0][0] == "MultiTrack Project"
    assert messages[0][1].startswith("Sampling segment 3 (first):")
    assert "sampler_name=None" in messages[0][1]


def test_h3_context_media_trim_removes_head_and_grid_tail_together(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    images = torch.arange(40, dtype=torch.float32).reshape(40, 1, 1, 1)
    audio = {
        "waveform": torch.arange(80, dtype=torch.float32).reshape(1, 1, 80),
        "sample_rate": 48,
    }

    result = module.EasyH3ContextMediaTrim.execute(
        images,
        audio,
        trim_frames=5,
        output_frames=22,
        fps=24.0,
    )
    output_images, output_audio = result.values

    assert output_images[:, 0, 0, 0].tolist() == list(range(5, 27))
    assert output_audio["waveform"].flatten().tolist() == list(range(10, 54))
    assert output_audio["sample_rate"] == 48


def test_h3_context_media_trim_can_leave_locked_audio_short_for_alignment(
    monkeypatch,
):
    module = _load_minimax_node(monkeypatch)
    images = torch.zeros(5, 1, 1, 3)
    audio = {
        "waveform": torch.arange(8, dtype=torch.float32).reshape(1, 1, 8),
        "sample_rate": 2,
    }

    result = module.EasyH3ContextMediaTrim.execute(
        images,
        audio,
        trim_frames=0,
        output_frames=5,
        pad_audio=False,
        fps=1.0,
    )

    assert result.values[0].shape[0] == 5
    assert result.values[1]["waveform"].shape[-1] == 8


@pytest.mark.parametrize(
    ("total_frames", "expected_start", "expected_padding"),
    [
        (22, 0, 0),
        (120, 102, 4),
        (124, 102, 0),
        (153, 136, 5),
        (168, 153, 7),
        (240, 221, 3),
    ],
)
def test_h3_context_media_trim_preserves_vae_chunk_phase_for_encoding(
    monkeypatch,
    total_frames,
    expected_start,
    expected_padding,
):
    module = _load_minimax_node(monkeypatch)
    images = torch.arange(total_frames, dtype=torch.float32).reshape(
        total_frames, 1, 1, 1
    )
    audio = {
        "waveform": torch.zeros(1, 1, total_frames * 2),
        "sample_rate": 48,
    }

    result = module.EasyH3ContextMediaTrim.execute(
        images,
        audio,
        trim_frames=0,
        output_frames=22,
        phase_align_video_encode=True,
    )

    output_images, output_audio = result.values
    expected = list(range(expected_start, total_frames))
    expected.extend([total_frames - 1] * expected_padding)
    assert output_images[:, 0, 0, 0].tolist() == expected
    assert len(output_images) == 22
    assert output_images[-1].item() == images[-1].item()
    assert output_audio is audio


def test_h3_context_media_trim_rejects_empty_phase_aligned_video(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    audio = {"waveform": torch.zeros(1, 1, 2), "sample_rate": 48}

    with pytest.raises(ValueError, match="at least one frame"):
        module.EasyH3ContextMediaTrim.execute(
            torch.empty(0, 1, 1, 1),
            audio,
            output_frames=22,
            phase_align_video_encode=True,
        )


def test_h3_context_trim_embeds_phase_aligned_five_frame_anchor(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    images = torch.arange(9 * 2 * 2 * 3, dtype=torch.float32).reshape(9, 2, 2, 3)
    vae = _Vae()
    trimmed = {"samples": "trimmed"}
    monkeypatch.setattr(
        module,
        "trim_motion_context_latent",
        lambda _latent, context_length: trimmed.copy(),
    )

    output = module.EasyH3MotionContextLatentTrim.execute(
        {"samples": "full"},
        "22",
        anchor_images=images,
        vae=vae,
    ).values[0]

    assert len(vae.encoded) == 1
    expected = images[-1:].expand(5, *images.shape[1:])
    assert torch.equal(vae.encoded[0], expected)
    assert output["samples"] == "trimmed"
    assert output["anchor_samples"].shape[2] == 2


def test_h3_locked_audio_duration_align_matches_decoded_video_without_padding(
    monkeypatch,
):
    module = _load_minimax_node(monkeypatch)
    images = torch.zeros(209, 1, 1, 3)
    waveform = torch.linspace(-1.0, 1.0, 417_600).reshape(1, 1, -1)

    result = module.EasyH3LockedAudioDurationAlign.execute(
        images,
        {"waveform": waveform, "sample_rate": 48_000},
        fps=24.0,
    )
    aligned = result.values[0]

    assert aligned["sample_rate"] == 48_000
    assert aligned["waveform"].shape == (1, 1, 418_000)
    assert aligned["waveform"][..., 0].item() == pytest.approx(-1.0, abs=1e-4)
    assert aligned["waveform"][..., -1].item() == pytest.approx(1.0, abs=1e-4)


def test_h3_locked_audio_duration_align_rejects_more_than_one_audio_latent_hop(
    monkeypatch,
):
    module = _load_minimax_node(monkeypatch)
    images = torch.zeros(209, 1, 1, 3)
    waveform = torch.zeros(1, 1, 416_000)

    with pytest.raises(ValueError, match="mismatch is too large"):
        module.EasyH3LockedAudioDurationAlign.execute(
            images,
            {"waveform": waveform, "sample_rate": 48_000},
            fps=24.0,
        )


def test_multitrack_h3_project_waits_for_previous_artifact_before_sampling(
    monkeypatch,
):
    module = _load_minimax_node(monkeypatch)
    inputs = _h3_project_inputs()
    first_segment = inputs["tracks_info"][0]["tracks"][0]["segments"][0]
    inputs["tracks_info"][0]["tracks"][0]["segments"].append({
        **first_segment,
        "start_frame": 121,
        "end_frame": 241,
        "content": {**first_segment["content"]},
    })

    result = module.EasyMultiTrackProject.execute(**inputs)
    sampling_starts = sorted(
        (
            node
            for node in result.expand.values()
            if node["class_type"] == "easy h3SegmentSamplingStart"
        ),
        key=lambda node: node["inputs"]["segment_index"],
    )
    artifacts = sorted(
        (
            node
            for node in result.expand.values()
            if node["class_type"] == "easy h3NativeArtifact"
        ),
        key=lambda node: node["inputs"]["segment_index"],
    )

    assert result.expand[sampling_starts[0]["inputs"]["previous"][0]]["class_type"] == "easy h3NativePreflight"
    assert sampling_starts[1]["inputs"]["previous"] == artifacts[1]["inputs"][
        "previous"
    ]


def test_h3_project_artifact_schema(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    artifact_schema = module.EasyH3ProjectArtifact.define_schema()
    artifact_inputs = {port.name: port for port in artifact_schema.inputs}
    assert list(artifact_inputs) == [
        "project_name",
        "project_save",
        "segment_index",
        "context_latent",
        "context_latent_low",
        "last_frame",
        "previous_frame_source",
        "video_path",
        "tracks_info",
        "continuity_mode",
        "sampling_pass",
        "seed",
        "previous",
        "audio",
    ]
    assert artifact_inputs["context_latent_low"].kwargs["optional"] is True
    assert artifact_inputs["seed"].kwargs["optional"] is True
    assert artifact_inputs["project_save"].kwargs["options"] == [
        "new",
        "override",
    ]
    assert artifact_inputs["project_save"].kwargs["default"] == "new"


def test_multitrack_h3_project_render_schema_uses_project_data_widget(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    schema = module.EasyMultiTrackProjectVideoCombine.define_schema()
    inputs = {port.name: port for port in schema.inputs}

    assert schema.node_id == "easy multitrackProjectVideoCombine"
    assert not getattr(schema, "is_output_node", False)
    assert inputs["project_name"].kwargs["force_input"] is True
    assert "project_data" in inputs
    assert schema.hidden == ["UNIQUE_ID"]
    assert [output.name for output in schema.outputs] == [
        "VIDEO",
        "FILENAME_PREFIX",
    ]


def test_multitrack_h3_project_render_returns_video_and_filename_prefix(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    compose_calls = []
    notifications = []
    monkeypatch.setattr(
        module._project_module,
        "compose_h3_project_video",
        lambda project_name, data: compose_calls.append((project_name, data))
        or Path("/temp/demo.mp4"),
    )
    module.EasyMultiTrackProjectVideoCombine.hidden = types.SimpleNamespace(
        unique_id="17"
    )
    server = types.ModuleType("server")
    server.PromptServer = types.SimpleNamespace(
        instance=types.SimpleNamespace(
            send_sync=lambda event, payload: notifications.append((event, payload))
        )
    )
    monkeypatch.setitem(sys.modules, "server", server)

    output = module.EasyMultiTrackProjectVideoCombine.execute(
        "demo",
        json.dumps({"project_name": "demo", "clips": []}),
    )

    assert output.values[0].path.replace("\\", "/") == "/temp/demo.mp4"
    assert output.values[1] == "easy_media/projects/demo/out/demo"
    assert compose_calls == [("demo", {"project_name": "demo", "clips": []})]
    assert notifications == [(
        "easy-media.project.selected",
        {"node_id": "17", "project_name": "demo"},
    )]


def test_project_video_combine_blocks_both_outputs_when_auto_combine_is_disabled(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    compose = monkeypatch.setattr(
        module._project_module,
        "compose_h3_project_video",
        lambda *_args: pytest.fail("disabled auto combine must not compose video"),
    )
    del compose

    output = module.EasyMultiTrackProjectVideoCombine.execute(
        "demo",
        {"project_name": "demo", "clips": [], "auto_combine": False},
    )

    assert type(output.values[0]).__name__ == "ExecutionBlocker"
    assert output.values[1] is output.values[0]


def test_easy_h3_hard_and_hires_context_schemas_and_wrappers(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert not hasattr(module, "EasyMiniMaxH3MotionContext")
    hard_schema = module.EasyMiniMaxH3MotionContextHard.define_schema()
    hard_inputs = {port.name: port for port in hard_schema.inputs}
    assert hard_schema.node_id == "easy MiniMaxH3MotionContextHard"
    assert hard_inputs["context_length"].kwargs["default"] == "22"
    assert "audio_context_length" not in hard_inputs
    assert [output.name for output in hard_schema.outputs] == [
        "conditioning",
        "trim_frames",
        "latent",
    ]
    motion_context_calls = []
    monkeypatch.setattr(
        module,
        "apply_reencoded_anchor_motion_context",
        lambda **kwargs: motion_context_calls.append(kwargs)
        or ("conditioned", 22, "hard-latent"),
    )
    hard_output = module.EasyMiniMaxH3MotionContextHard.execute(
        "conditioning",
        "vae",
        {"samples": "target"},
        {"samples": "context"},
    )
    assert hard_output.values == ("conditioned", 22, "hard-latent")
    assert motion_context_calls == [
        {
            "conditioning": "conditioning",
            "vae": "vae",
            "latent": {"samples": "target"},
            "context_length": "22",
            "context_latent": {"samples": "context"},
            "anchor_length": "5",
        }
    ]

    hires_schema = module.EasyMiniMaxH3HiResContinuity.define_schema()
    assert hires_schema.node_id == "easy MiniMaxH3HiResContinuity"
    monkeypatch.setattr(
        module,
        "apply_hires_anchor_continuity",
        lambda **_kwargs: ("hires-latent", 22),
    )
    hires_output = module.EasyMiniMaxH3HiResContinuity.execute(
        {"samples": "current"},
        {"samples": "previous"},
    )
    assert hires_output.values == ("hires-latent", 22)

    trim_schema = module.EasyH3MotionContextLatentTrim.define_schema()
    assert trim_schema.node_id == "easy h3MotionContextLatentTrim"
    monkeypatch.setattr(
        module,
        "trim_motion_context_latent",
        lambda latent, context_length: {
            "source": latent,
            "context_length": context_length,
        },
    )
    trim_output = module.EasyH3MotionContextLatentTrim.execute(
        {"samples": "full"},
        "22",
    )
    assert trim_output.values == (
        {"source": {"samples": "full"}, "context_length": "22"},
    )


def test_easy_h3_hard_and_hires_context_have_chinese_localization():
    node_defs = json.loads(
        (Path(__file__).parents[1] / "locales" / "zh" / "nodeDefs.json").read_text()
    )
    hard_translation = node_defs["easy MiniMaxH3MotionContextHard"]
    assert set(hard_translation["inputs"]) == {
        "conditioning",
        "vae",
        "latent",
        "context_latent",
        "context_length",
        "video_transition_steps",
        "audio_transition_steps",
        "video_anchor_only",
    }
    assert "easy MiniMaxH3HiResContinuity" in node_defs
    assert "easy MiniMaxH3MotionContextAnchor" not in node_defs
    assert "easy MiniMaxH3HiResAnchorContinuity" not in node_defs


def test_multitrack_h3_project_has_matching_chinese_localization():
    node_defs = json.loads(
        (Path(__file__).parents[1] / "locales" / "zh" / "nodeDefs.json").read_text()
    )
    translation = node_defs["easy multitrackProject"]

    assert translation["display_name"] == "多轨项目"
    assert set(translation["inputs"]) == {
        "model_loader",
        "tracks_info",
        "sampler",
        "sigmas",
        "project_name",
        "project_save",
        "sampling_plan",
        "sampling_mode",
        "transition_ratio",
        "lowres_scale",
        "enabled_tiling",
        "tile_count",
        "model_loader_2nd",
        "1st_pass_only",
        "disable_2nd_noise",
        "upscale_by",
        "upscale_model",
        "segment_start_number",
        "segment_count",
        "seed",
        "sampler_2nd",
        "sigmas_2nd",
    }
    assert translation["outputs"] == {
        "0": {"name": "项目名称"},
        "1": {"name": "锁定音频"},
    }


def test_multitrack_h3_project_expands_single_task_sampling_pipeline(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    log_messages = []
    monkeypatch.setattr(
        module._project_module,
        "log_node_info",
        lambda node_name, message=None: log_messages.append((node_name, message)),
    )

    result = module.EasyMultiTrackProject.execute(
        **_h3_project_inputs(
            project_name="demo",
            sampling_mode=_h3_sampling_mode("single"),
        )
    )

    nodes_by_type = {node["class_type"]: node for node in result.expand.values()}
    assert result.values[0][1] == 0
    assert nodes_by_type["KSamplerSelect"]["inputs"]["sampler_name"] == "er_sde"
    assert "ManualSigmas" in nodes_by_type
    conditioning = nodes_by_type["easy minimaxH3ToVideo"]["inputs"]
    assert conditioning["mode"] == "multi_frames"
    assert conditioning["ref_image_size"] == "match"
    assert (conditioning["width"], conditioning["height"]) == (1344, 768)
    assert "SamplerCustomAdvanced" in nodes_by_type
    assert "easy h3SegmentSamplingStart" in nodes_by_type
    assert (
        nodes_by_type["easy h3SegmentSamplingStart"]["inputs"]["project_name"]
        == "demo"
    )
    assert "easy h3SegmentSaveEnd" not in nodes_by_type
    assert "VAEDecode" in nodes_by_type and "VAEDecodeAudio" in nodes_by_type
    assert "VAEEncode" not in nodes_by_type and "VAEEncodeAudio" not in nodes_by_type
    assert "easy h3NativeArtifact" in nodes_by_type
    save_inputs = nodes_by_type['easy saveVideo']['inputs']
    assert save_inputs['input_mode'] == 'images+audio'
    assert save_inputs['input_mode.fps'] == 24.0
    assert result.expand[save_inputs['input_mode.images'][0]]['class_type'] == 'easy h3NativeMediaView'
    assert result.expand[nodes_by_type['easy h3NativeArtifact']['inputs']['latent'][0]]['class_type'] == 'easy h3NativeResult'


def test_multitrack_project_patches_sampler_when_loader_has_preview_vae(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    module.comfy_nodes.NODE_CLASS_MAPPINGS["ImageResizeKJv2"] = _ImageResizeKJWithNvidia
    module.EasyMultiTrackProject.hidden = types.SimpleNamespace(unique_id="42")
    inputs = _h3_project_inputs(sampling_mode=_h3_sampling_mode("dual"))
    preview_vae = object()
    inputs["model_loader"][0]["preview_vae"] = preview_vae

    result = module.EasyMultiTrackProject.execute(**inputs)
    preview_samplers = [
        node
        for node in result.expand.values()
        if node["class_type"] == "easy h3SamplingPreviewSampler"
    ]

    assert len(preview_samplers) == 2
    assert not any(
        node["class_type"] == "SamplerCustomAdvanced"
        for node in result.expand.values()
    )
    assert {node["inputs"]["sampling_pass"] for node in preview_samplers} == {
        "first",
        "second",
    }
    assert all(node["inputs"]["preview_vae"] is preview_vae for node in preview_samplers)
    assert all(node["inputs"]["preview_node_id"] == "42" for node in preview_samplers)
    assert all(node["inputs"]["preview_fps"] == 24.0 for node in preview_samplers)


def test_h3_sampling_preview_sampler_exposes_optional_tiling(monkeypatch):
    module = _load_minimax_node(monkeypatch)

    schema = module._project_module.EasyH3SamplingPreviewSampler.define_schema()
    inputs = {port.name: port for port in schema.inputs}

    assert inputs["enabled_tiling"].kwargs["default"] is False
    assert inputs["enabled_tiling"].kwargs["optional"] is True
    assert inputs["tile_count"].kwargs["default"] == 2
    assert inputs["tile_count"].kwargs["min"] == 2
    assert inputs["tile_count"].kwargs["max"] == 8
    assert inputs["preview_vae"].kwargs["optional"] is True


def test_multitrack_project_tiles_inside_second_pass_sampler(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    module.comfy_nodes.NODE_CLASS_MAPPINGS["ImageResizeKJv2"] = _ImageResizeKJWithNvidia
    inputs = _h3_project_inputs(
        sampling_mode=_h3_sampling_mode("dual"),
        enabled_tiling=[{"enabled_tiling": ["true"], "tile_count": [4]}],
    )

    result = module.EasyMultiTrackProject.execute(**inputs)
    nodes = list(result.expand.values())
    tiled_sampler = next(
        node
        for node in nodes
        if node["class_type"] == "easy h3SamplingPreviewSampler"
    )

    assert tiled_sampler["inputs"]["enabled_tiling"] is True
    assert tiled_sampler["inputs"]["tile_count"] == 4
    assert not any(node["class_type"] == "easy h3TiledModel" for node in nodes)


def test_multitrack_project_adds_preview_inputs_to_selflift_sampler(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    module.EasyMultiTrackProject.hidden = types.SimpleNamespace(unique_id="84")
    inputs = _h3_project_inputs(sampling_mode=_h3_sampling_mode("selflift"))
    preview_vae = object()
    inputs["model_loader"][0]["preview_vae"] = preview_vae

    result = module.EasyMultiTrackProject.execute(**inputs)
    sampler = _graph_node(result, "easy minimaxH3SelfLiftSampler")

    assert sampler["inputs"]["preview_vae"] is preview_vae
    assert sampler["inputs"]["preview_node_id"] == "84"
    assert sampler["inputs"]["sampling_pass"] == "selflift"


def test_multitrack_h3_project_reads_ref_image_size_from_each_segment(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    inputs = _h3_project_inputs(sampling_mode=_h3_sampling_mode("single"))
    inputs["tracks_info"][0]["tracks"][0]["segments"][0]["content"].update({
        "task_mode": "ref",
        "ref_image_size": "max",
    })

    result = module.EasyMultiTrackProject.execute(**inputs)
    conditioning = _graph_node(result, "easy minimaxH3ToVideo")["inputs"]

    assert conditioning["mode"] == "reference"
    assert conditioning["ref_image_size"] == "max"


def test_multitrack_h3_project_logs_steps_and_reports_progress(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    log_messages = []
    monkeypatch.setattr(
        module._project_module,
        "log_node_info",
        lambda node_name, message=None: log_messages.append((node_name, message)),
    )

    module.EasyMultiTrackProject.execute(
        **_h3_project_inputs(sampling_mode=_h3_sampling_mode("single"))
    )

    assert log_messages == [
        ("MultiTrack Project", "Found 1 segments; processing 1"),
    ]

    progress = _ProgressBar.instances[-1]
    assert progress.total == 100
    assert progress.updates[0] == (0, 100)
    assert progress.updates[-1] == (100, 100)
    assert all(
        current[0] <= following[0]
        for current, following in zip(progress.updates, progress.updates[1:])
    )


def test_multitrack_h3_project_locks_task_audio_before_sampling(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    inputs = _h3_project_inputs(sampling_mode=_h3_sampling_mode("single"))
    task = inputs["tracks_info"][0]["tracks"][0]["segments"][0]
    inputs["tracks_info"][0]["tracks"].append({
        "id": "locked-audio-track",
        "type": "audio",
        "audio_locked": True,
        "segments": [{
            "id": "locked-audio",
            "start_frame": task["start_frame"],
            "end_frame": task["end_frame"],
            "content": {"media_type": "audio"},
        }],
    })

    result = module.EasyMultiTrackProject.execute(**inputs)

    nodes = result.expand
    lock_id, audio_lock = next(
        (node_id, node)
        for node_id, node in nodes.items()
        if node["class_type"] == "easy h3NativeAudioLock"
    )
    conditioning_cache_id = next(
        node_id
        for node_id, node in nodes.items()
        if node["class_type"] == "easy h3ConditioningCache"
    )
    sampling_start = next(
        node
        for node in nodes.values()
        if node["class_type"] == "easy h3SegmentSamplingStart"
    )

    assert nodes[audio_lock['inputs']['latent'][0]]['class_type'] == 'easy h3NativePrepare'
    assert audio_lock['inputs']['audio'] == {'prepared_locked_audio': True}
    assert sampling_start['inputs']['latent_image'] == [lock_id, 0]



def test_multitrack_h3_fast_dual_non_turbo_uses_preset_sigmas_and_pixel_upscale(
    monkeypatch,
):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    module.comfy_nodes.NODE_CLASS_MAPPINGS["ImageResizeKJv2"] = (
        _ImageResizeKJWithNvidia
    )

    result = module.EasyMultiTrackProject.execute(
        **_h3_project_inputs(
            sampling_mode=_h3_sampling_mode("dual", disable_2nd_noise=[True]),
            sampling_plan=["light"],
        )
    )

    nodes = list(result.expand.values())
    conditioning = next(
        node for node in nodes if node["class_type"] == "easy minimaxH3ToVideo"
    )
    resize = next(node for node in nodes if node["class_type"] == "ImageResizeKJv2")
    separate = next(
        node for node in nodes if node["class_type"] == "LTXVSeparateAVLatent"
    )
    encode = next(node for node in nodes if node["class_type"] == "VAEEncode")
    concat = next(
        node for node in nodes if node["class_type"] == "LTXVConcatAVLatent"
    )
    samples = [
        node for node in nodes if node["class_type"] == "SamplerCustomAdvanced"
    ]

    assert not any(node["class_type"] == "SplitSigmas" for node in nodes)
    sigma_schedules = [
        node["inputs"]["sigmas"]
        for node in nodes
        if node["class_type"] == "ManualSigmas"
    ]
    assert len(sigma_schedules) == 2
    assert sigma_schedules[0].startswith("1.0000, 0.9901")
    assert sigma_schedules[1].startswith("0.6316, 0.4877")
    assert (conditioning["inputs"]["width"], conditioning["inputs"]["height"]) == (
        1344,
        768,
    )
    assert resize["inputs"]["upscale_method"] == "nvidia_rtx_vsr"
    assert (resize["inputs"]["width"], resize["inputs"]["height"]) == (1664, 960)
    separate_id = next(
        node_id for node_id, node in result.expand.items() if node is separate
    )
    encode_id = next(
        node_id for node_id, node in result.expand.items() if node is encode
    )
    assert concat["inputs"]["video_latent"] == [encode_id, 0]
    assert concat["inputs"]["audio_latent"] == [separate_id, 1]
    assert len(samples) == 2
    sampler_names = [
        node["inputs"]["sampler_name"]
        for node in nodes
        if node["class_type"] == "KSamplerSelect"
    ]
    assert sampler_names == ["euler", "sa_solver"]
    assert any(node["class_type"] == "DisableNoise" for node in nodes)


def test_multitrack_h3_selflift_uses_target_size_and_one_progressive_sample(
    monkeypatch,
):
    module = _load_minimax_node(monkeypatch)

    result = module.EasyMultiTrackProject.execute(
        **_h3_project_inputs(
            sampling_mode=_h3_sampling_mode("selflift"),
            sampling_plan=["medium"],
            upscale_by=[1.25],
            upscale_model=["None"],
        )
    )

    nodes = list(result.expand.values())
    selflift = next(
        node
        for node in nodes
        if node["class_type"] == "easy minimaxH3SelfLiftSampler"
    )
    artifact = next(
        node
        for node in nodes
        if node["class_type"] == "easy h3NativeArtifact"
    )
    conditioning = next(
        node for node in nodes if node["class_type"] == "easy minimaxH3ToVideo"
    )
    sigma_nodes = [node for node in nodes if node["class_type"] == "ManualSigmas"]

    assert len(sigma_nodes) == 1
    assert sigma_nodes[0]["inputs"]["sigmas"].startswith("1.0000, 0.9956")
    assert not any(node["class_type"] == "KSamplerSelect" for node in nodes)
    assert not any(node["class_type"] == "SamplerCustomAdvanced" for node in nodes)
    assert not any(node["class_type"] == "VAEEncode" for node in nodes)
    assert not any(node["class_type"] == "ImageResizeKJv2" for node in nodes)
    assert (conditioning["inputs"]["width"], conditioning["inputs"]["height"]) == (
        1344,
        768,
    )
    assert selflift["inputs"]["transition_ratio"] == 0.6
    assert selflift["inputs"]["lowres_scale"] == 0.6
    assert selflift["inputs"]["rho"] == 0.0
    assert selflift["inputs"]["w_min"] == 0.25
    assert selflift["inputs"]["w_max"] == 0.7
    assert selflift["inputs"]["enabled_tiling"] is False
    assert "project_name" not in selflift["inputs"]
    assert "segment_index" not in selflift["inputs"]
    assert "upscale_by" not in selflift["inputs"]
    assert selflift["inputs"]["upscaler_model"] == "None"
    assert selflift["inputs"]["highres_model"] is selflift["inputs"]["model"]
    assert json.loads(_graph_node(result, "easy h3NativePrepare")["inputs"]["recipe_json"])["sampling_mode"] == "selflift"
    assert artifact["inputs"]["tracks_info"]["width"] == 1344
    assert artifact["inputs"]["tracks_info"]["height"] == 768


def test_multitrack_h3_selflift_reads_nested_upscale_model(monkeypatch):
    module = _load_minimax_node(monkeypatch)

    result = module.EasyMultiTrackProject.execute(
        **_h3_project_inputs(
            sampling_mode=_h3_sampling_mode(
                "selflift",
                upscale_by=[2.5],
                upscale_model=["h3_upscale.safetensors"],
            ),
        )
    )

    selflift = next(
        node
        for node in result.expand.values()
        if node["class_type"] == "easy minimaxH3SelfLiftSampler"
    )
    assert selflift["inputs"]["lowres_scale"] == 0.6
    assert "upscale_by" not in selflift["inputs"]
    assert selflift["inputs"]["upscaler_model"] == "h3_upscale.safetensors"


def test_multitrack_h3_selflift_passes_dynamic_sampling_values(monkeypatch):
    module = _load_minimax_node(monkeypatch)

    result = module.EasyMultiTrackProject.execute(
        **_h3_project_inputs(
            sampling_mode=_h3_sampling_mode(
                "selflift",
                transition_ratio=[0.75],
                lowres_scale=[0.5],
            ),
            enabled_tiling=[
                {"enabled_tiling": ["true"], "tile_count": [4]}
            ],
        )
    )

    selflift = next(
        node
        for node in result.expand.values()
        if node["class_type"] == "easy minimaxH3SelfLiftSampler"
    )
    assert selflift["inputs"]["transition_ratio"] == 0.75
    assert selflift["inputs"]["lowres_scale"] == 0.5
    assert selflift["inputs"]["enabled_tiling"] is True
    assert selflift["inputs"]["tile_count"] == 4


@pytest.mark.parametrize("continuity_mode", ["context", "context_swap"])
def test_multitrack_h3_selflift_supports_context_and_locked_audio(
    monkeypatch,
    continuity_mode,
):
    module = _load_minimax_node(monkeypatch)
    inputs = _h3_project_inputs(sampling_mode=_h3_sampling_mode("selflift"))
    info = inputs["tracks_info"][0]
    info["tracks"][0]["segments"].append(
        {
            "start_frame": 120,
            "end_frame": 240,
            "content": {
                "task_mode": "default",
                "continuity_mode": continuity_mode,
                "images": [],
                "user_prompt": "continue seamlessly",
            },
        }
    )
    info["tracks"].append(
        {
            "id": "locked-audio-track",
            "type": "audio",
            "audio_locked": True,
            "segments": [
                {
                    "id": "locked-audio",
                    "start_frame": 0,
                    "end_frame": 240,
                    "content": {"media_type": "audio"},
                }
            ],
        }
    )

    result = module.EasyMultiTrackProject.execute(**inputs)

    selflift_nodes = [
        (node_id, node)
        for node_id, node in result.expand.items()
        if node["class_type"] == "easy minimaxH3SelfLiftSampler"
    ]
    assert len(selflift_nodes) == 2
    context_selflift = selflift_nodes[1][1]
    context_type = 'easy MiniMaxH3MotionContextHard' if continuity_mode == 'context' else 'easy MiniMaxH3ContextSwap'
    lock = result.expand[context_selflift['inputs']['latent_image'][0]]
    assert lock['class_type'] == 'easy h3NativeAudioLock'
    assert result.expand[lock['inputs']['latent'][0]]['class_type'] == context_type
    low_context = result.expand[context_selflift['inputs']['low_context_latent'][0]]
    assert low_context['class_type'] == 'easy h3NativePrepare'
    assert context_selflift['inputs']['rho'] == 0.0


def test_selflift_sampler_schema_forces_euler_by_not_exposing_a_sampler(monkeypatch):
    module = _load_minimax_node(monkeypatch)

    schema = module.EasyMiniMaxH3SelfLiftSampler.define_schema()
    inputs = {port.name: port for port in schema.inputs}

    assert schema.node_id == "easy minimaxH3SelfLiftSampler"
    assert schema.is_dev_only is True
    assert "sampler" not in inputs
    assert list(inputs)[-2:] == ["previous", "highres_model"]
    assert inputs["highres_model"].kwargs["optional"] is True
    assert inputs["transition_ratio"].kwargs["default"] == 0.6
    assert inputs["lowres_scale"].kwargs["default"] == 0.6
    assert inputs["rho"].kwargs["default"] == pytest.approx(0.1)
    assert inputs["w_max"].kwargs["default"] == pytest.approx(0.7)
    assert inputs["w_min"].kwargs["default"] == pytest.approx(0.25)
    assert inputs["enabled_tiling"].kwargs["default"] is False
    assert inputs["enabled_tiling"].kwargs["optional"] is True
    assert inputs["highres_tiling"].kwargs["default"] is False
    assert inputs["highres_tiling"].kwargs["optional"] is True
    assert inputs["highres_tiling"].kwargs["advanced"] is True
    assert inputs["low_context_latent"].kwargs["optional"] is True
    assert "project_name" not in inputs
    assert inputs["segment_index"].kwargs["optional"] is True
    assert len(schema.outputs) == 2
    assert "upscale_by" not in inputs


def test_bundled_h3_latent_upscaler_schema_exposes_three_resize_modes(monkeypatch):
    module = _load_minimax_node(monkeypatch)

    schema = module.EasyMiniMaxH3LatentUpscaler.define_schema()
    inputs = {port.name: port for port in schema.inputs}
    modes = inputs["mode"].kwargs["options"]

    assert schema.node_id == "easy minimaxH3LatentUpscaler"
    assert list(inputs) == [
        "latent",
        "model_name",
        "mode",
        "align",
        "enable_temporal_chunking",
        "force_unload",
    ]
    assert [name for name, _ in modes] == [
        "scale by multiplier",
        "target dimensions",
        "megapixels",
    ]
    assert inputs["align"].kwargs["default"] == 32
    assert inputs["enable_temporal_chunking"].kwargs["default"] is True
    assert inputs["force_unload"].kwargs["default"] is True
    node_defs = json.loads(
        (Path(__file__).parents[1] / "locales" / "zh" / "nodeDefs.json").read_text()
    )
    assert set(node_defs["easy minimaxH3LatentUpscaler"]["inputs"]) == {
        "latent",
        "model_name",
        "mode",
        "scale",
        "width",
        "height",
        "megapixels",
        "align",
        "enable_temporal_chunking",
        "force_unload",
    }


def test_bundled_h3_latent_upscaler_preserves_audio_and_resizes_mask(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    calls = []
    upscaler_module = types.ModuleType(
        "easy_media.modules.selflift.h3_latent_upscale"
    )

    def learned_latent_lift(video, target_size, model_name, **kwargs):
        calls.append((tuple(video.shape), target_size, model_name, kwargs))
        return torch.full(
            (*video.shape[:-2], *target_size),
            7.0,
            dtype=video.dtype,
        )

    upscaler_module.learned_latent_lift = learned_latent_lift
    monkeypatch.setitem(
        sys.modules,
        "easy_media.modules.selflift.h3_latent_upscale",
        upscaler_module,
    )
    video = torch.zeros(1, 24, 3, 4, 6)
    audio = torch.randn(1, 32, 2, 9)
    video_mask = torch.ones(1, 1, 3, 4, 6)
    audio_mask = torch.zeros(1, 1, 2, 9)
    latent = {
        "samples": _NestedTensor((video, audio)),
        "noise_mask": _NestedTensor((video_mask, audio_mask)),
        "batch_index": [0],
    }

    output = module.EasyMiniMaxH3LatentUpscaler.execute(
        latent=latent,
        model_name="h3_upscale.safetensors",
        mode={"mode": "target dimensions", "width": 192, "height": 128},
        align=32,
        enable_temporal_chunking=False,
        force_unload=False,
    ).values[0]

    upscaled_video, output_audio = output["samples"].unbind()
    upscaled_mask, output_audio_mask = output["noise_mask"].unbind()
    assert calls == [
        (
            (1, 24, 3, 4, 6),
            (8, 12),
            "h3_upscale.safetensors",
            {"enable_temporal_chunking": False, "force_unload": False},
        )
    ]
    assert upscaled_video.shape == (1, 24, 3, 8, 12)
    assert torch.equal(output_audio, audio)
    assert upscaled_mask.shape == (1, 1, 3, 8, 12)
    assert torch.equal(output_audio_mask, audio_mask)
    assert output["batch_index"] == [0]


def test_selflift_sampler_uses_lowres_scale(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    captured = {}
    sampled = {"samples": object()}
    low_context = {"samples": object()}
    sampling_module = types.ModuleType("easy_media.modules.selflift.sampling")

    def progressive_sample_h3(**kwargs):
        captured.update(kwargs)
        return sampled, low_context

    sampling_module.progressive_sample_h3 = progressive_sample_h3
    monkeypatch.setitem(
        sys.modules,
        "easy_media.modules.selflift.sampling",
        sampling_module,
    )

    result = module.EasyMiniMaxH3SelfLiftSampler.execute(
        model=object(),
        positive=[],
        vae=object(),
        latent_image={"samples": torch.zeros(1, 24, 1, 60, 104)},
        sigmas=torch.tensor([1.0, 0.0]),
        seed=42,
        transition_ratio=0.6,
        lowres_scale=0.6,
    )

    assert captured["lowres_scale"] == pytest.approx(0.6)
    assert captured["rho"] == pytest.approx(0.1)
    assert captured["w_min"] == 0.25
    assert captured["w_max"] == 0.7
    assert captured["highres_tiling"] is False
    assert captured["low_context_latent"] is None
    assert result.values == (sampled, low_context)


def test_selflift_sampler_forwards_high_resolution_model(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    captured = {}
    sampling_module = types.ModuleType("easy_media.modules.selflift.sampling")

    def progressive_sample_h3(**kwargs):
        captured.update(kwargs)
        return {"samples": object()}, {"samples": object()}

    sampling_module.progressive_sample_h3 = progressive_sample_h3
    monkeypatch.setitem(sys.modules, "easy_media.modules.selflift.sampling", sampling_module)
    first_model = object()
    second_model = object()

    module.EasyMiniMaxH3SelfLiftSampler.execute(
        model=first_model,
        highres_model=second_model,
        positive=[],
        vae=object(),
        latent_image={"samples": torch.zeros(1, 24, 1, 60, 104)},
        sigmas=torch.tensor([1.0, 0.0]),
        seed=42,
    )

    assert captured["model"] is first_model
    assert captured["highres_model"] is second_model


def test_selflift_sampler_forwards_artifact_correction_controls(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    captured = {}
    sampling_module = types.ModuleType("easy_media.modules.selflift.sampling")

    def progressive_sample_h3(**kwargs):
        captured.update(kwargs)
        return {"samples": object()}, {"samples": object()}

    sampling_module.progressive_sample_h3 = progressive_sample_h3
    monkeypatch.setitem(
        sys.modules,
        "easy_media.modules.selflift.sampling",
        sampling_module,
    )

    module.EasyMiniMaxH3SelfLiftSampler.execute(
        model=object(),
        positive=[],
        vae=object(),
        latent_image={"samples": torch.zeros(1, 24, 1, 60, 104)},
        sigmas=torch.tensor([1.0, 0.0]),
        seed=42,
        rho=0.2,
        w_max=0.8,
        w_min=0.3,
    )

    assert captured["rho"] == pytest.approx(0.2)
    assert captured["w_max"] == pytest.approx(0.8)
    assert captured["w_min"] == pytest.approx(0.3)


def test_selflift_sampler_uses_saved_low_context_for_masked_video(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    captured = {}
    sampled = {"samples": object()}
    low_result = {"samples": object()}
    sampling_module = types.ModuleType("easy_media.modules.selflift.sampling")

    def progressive_sample_h3(**kwargs):
        captured.update(kwargs)
        return sampled, low_result

    sampling_module.progressive_sample_h3 = progressive_sample_h3
    monkeypatch.setitem(
        sys.modules,
        "easy_media.modules.selflift.sampling",
        sampling_module,
    )
    video = torch.zeros(1, 24, 7, 4, 6)
    audio = torch.zeros(1, 32, 2, 20)
    video_mask = torch.ones(1, 1, 7, 4, 6)
    video_mask[:, :, :2] = 0.0
    audio_mask = torch.ones(1, 1, 2, 20)
    saved_low_context = {
        "samples": _NestedTensor(
            (torch.ones(1, 24, 2, 3, 4), torch.ones_like(audio))
        )
    }

    result = module.EasyMiniMaxH3SelfLiftSampler.execute(
        model=object(),
        positive=[],
        vae=object(),
        latent_image={
            "samples": _NestedTensor((video, audio)),
            "noise_mask": _NestedTensor((video_mask, audio_mask)),
        },
        sigmas=torch.tensor([1.0, 0.5, 0.0]),
        seed=42,
        enabled_tiling=True,
        tile_count=4,
        low_context_latent=saved_low_context,
    )

    assert captured["highres_tiling"] is True
    assert captured["tile_count"] == 4
    assert captured["low_context_latent"] is saved_low_context
    assert captured["rho"] == 0.1
    assert captured["w_min"] == 0.25
    assert captured["w_max"] == 0.7
    assert result.values == (sampled, low_result)


def test_selflift_sampler_keeps_audio_only_mask_on_selflift(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    captured = {}
    sampled = {"samples": object()}
    low_context = {"samples": object()}
    sampling_module = types.ModuleType("easy_media.modules.selflift.sampling")

    def progressive_sample_h3(**kwargs):
        captured.update(kwargs)
        return sampled, low_context

    sampling_module.progressive_sample_h3 = progressive_sample_h3
    monkeypatch.setitem(
        sys.modules,
        "easy_media.modules.selflift.sampling",
        sampling_module,
    )
    video = torch.zeros(1, 24, 7, 4, 6)
    audio = torch.zeros(1, 32, 2, 20)
    video_mask = torch.ones(1, 1, 7, 4, 6)
    audio_mask = torch.zeros(1, 1, 2, 20)

    result = module.EasyMiniMaxH3SelfLiftSampler.execute(
        model=object(),
        positive=[],
        vae=object(),
        latent_image={
            "samples": _NestedTensor((video, audio)),
            "noise_mask": _NestedTensor((video_mask, audio_mask)),
        },
        sigmas=torch.tensor([1.0, 0.5, 0.0]),
        seed=42,
    )

    assert captured["rho"] == pytest.approx(0.1)
    assert captured["lowres_scale"] == pytest.approx(0.6)
    assert result.values == (sampled, low_context)


@pytest.mark.parametrize("upscale_model", ["None", "h3_upscale.safetensors"])
@pytest.mark.parametrize(
    ("upscale_by", "expected_size"),
    [(None, (1664, 960)), ([1.0], (1344, 768)), ([1.234], (1664, 960)), (2.0, (2688, 1536))],
)
def test_multitrack_project_uses_flat_upscale_by(
    monkeypatch, tmp_path, upscale_model, upscale_by, expected_size
):
    module = _load_minimax_node(monkeypatch)
    module.comfy_nodes.NODE_CLASS_MAPPINGS["ImageResizeKJv2"] = _ImageResizeKJWithNvidia
    monkeypatch.setattr(module.folder_paths, "get_output_directory", lambda: str(tmp_path))
    inputs = _h3_project_inputs(sampling_mode=["dual"], upscale_model=[upscale_model])
    if upscale_by is not None:
        inputs["upscale_by"] = upscale_by

    result = module.EasyMultiTrackProject.execute(**inputs)

    conditioning = _graph_node(result, "easy minimaxH3ToVideo")["inputs"]
    assert (conditioning["width"], conditioning["height"]) == (1344, 768)
    artifact_info = _graph_node(result, "easy h3NativeArtifact")["inputs"]["tracks_info"]
    assert (artifact_info["width"], artifact_info["height"]) == expected_size
    assert not (tmp_path / "easy_media/projects/default/project.json").exists()
    manifest = _graph_node(result, "easy h3NativeArtifact")["inputs"]["tracks_info"]
    assert (manifest["width"], manifest["height"]) == expected_size
    assert (inputs["tracks_info"][0]["width"], inputs["tracks_info"][0]["height"]) == (1344, 768)
    upscale_type = "ImageResizeKJv2" if upscale_model == "None" else "easy minimaxH3LatentUpscaler"
    if expected_size == (1344, 768):
        assert not any(node["class_type"] == upscale_type for node in result.expand.values())
    else:
        upscale = _graph_node(result, upscale_type)["inputs"]
        prefix = "" if upscale_model == "None" else "mode."
        assert (upscale[f"{prefix}width"], upscale[f"{prefix}height"]) == expected_size
        conditionings = [node["inputs"] for node in result.expand.values()
                        if node["class_type"] == "easy minimaxH3ToVideo"]
        assert (conditionings[1]["width"], conditionings[1]["height"]) == expected_size


def test_multitrack_reference_dual_reuses_conditioning_for_second_pass(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    module.comfy_nodes.NODE_CLASS_MAPPINGS["ImageResizeKJv2"] = _ImageResizeKJWithNvidia
    inputs = _h3_project_inputs(sampling_mode=_h3_sampling_mode("dual"))
    inputs["tracks_info"][0]["tracks"][0]["segments"][0]["content"]["task_mode"] = "ref"

    result = module.EasyMultiTrackProject.execute(**inputs)
    graph = result.expand
    conditioning_nodes = [
        node for node in graph.values()
        if node["class_type"] == "easy minimaxH3ToVideo"
    ]
    assert len(conditioning_nodes) == 1
    assert conditioning_nodes[0]["inputs"]["mode"] == "reference"
    cache_id = next(
        node_id for node_id, node in graph.items()
        if node["class_type"] == "easy h3ConditioningCache"
    )
    guiders = [
        node for node in graph.values()
        if node["class_type"] == "BasicGuider"
    ]
    assert len(guiders) == 2
    assert all(node["inputs"]["conditioning"] == [cache_id, 0] for node in guiders)


def test_multitrack_h3_project_forwards_override_save_mode(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    result = module.EasyMultiTrackProject.execute(
        **_h3_project_inputs(project_save=["override"])
    )

    artifact = _graph_node(result, "easy h3NativeArtifact")
    assert "project_save" not in artifact["inputs"] # Both modes retain immutable versions.




def test_multitrack_project_rejects_zero_start_number(monkeypatch):
    module = _load_minimax_node(monkeypatch)

    with pytest.raises(ValueError, match="segment_start_number must be at least 1"):
        module.EasyMultiTrackProject.execute(
            **_h3_project_inputs(segment_start_number=[0])
        )


def test_multitrack_h3_medium_dual_uses_selected_latent_upscale_model(
    monkeypatch,
):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    result = module.EasyMultiTrackProject.execute(
        **_h3_project_inputs(
            sampling_mode=_h3_sampling_mode("dual"),
            upscale_model=["h3_upscale.safetensors"],
        )
    )

    nodes = list(result.expand.values())
    sampler_names = [
        node["inputs"]["sampler_name"]
        for node in nodes
        if node["class_type"] == "KSamplerSelect"
    ]
    upscale = next(
        node
        for node in nodes
        if node["class_type"] == "easy minimaxH3LatentUpscaler"
    )
    separate = next(
        node for node in nodes if node["class_type"] == "LTXVSeparateAVLatent"
    )
    concat = next(
        node for node in nodes if node["class_type"] == "LTXVConcatAVLatent"
    )

    assert sampler_names == ["er_sde", "sa_solver"]
    assert sum(node["class_type"] == "ManualSigmas" for node in nodes) == 2
    assert upscale["inputs"]["model_name"] == "h3_upscale.safetensors"
    assert upscale["inputs"]["mode"] == "target dimensions"
    assert upscale["inputs"]["align"] == 32
    assert upscale["inputs"]["enable_temporal_chunking"] is True
    assert upscale["inputs"]["force_unload"] is True
    assert (
        upscale["inputs"]["mode.width"],
        upscale["inputs"]["mode.height"],
    ) == (
        1664,
        960,
    )
    separate_id = next(
        node_id for node_id, node in result.expand.items() if node is separate
    )
    upscale_id = next(
        node_id for node_id, node in result.expand.items() if node is upscale
    )
    assert upscale["inputs"]["latent"] == [separate_id, 0]
    assert concat["inputs"]["video_latent"] == [upscale_id, 0]
    assert concat["inputs"]["audio_latent"] == [separate_id, 1]
    assert not any(
        node["class_type"] == "LatentUpscaleModelLoader" for node in nodes
    )
    assert not any(node["class_type"] == "ImageResizeKJv2" for node in nodes)


def test_multitrack_h3_selected_upscale_model_uses_bundled_node(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    result = module.EasyMultiTrackProject.execute(
        **_h3_project_inputs(
            sampling_mode=_h3_sampling_mode("dual"),
            upscale_model=["h3_upscale.safetensors"],
        )
    )

    assert _graph_node(result, "easy minimaxH3LatentUpscaler")


def test_multitrack_h3_second_pass_at_one_x_reuses_first_pass_latent(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    result = module.EasyMultiTrackProject.execute(
        **_h3_project_inputs(
            sampling_mode=_h3_sampling_mode("dual", upscale_by=[1.0]),
        )
    )

    nodes = list(result.expand.values())
    assert sum(node["class_type"] == "SamplerCustomAdvanced" for node in nodes) == 2
    assert sum(
        node["class_type"] == "easy h3SegmentSamplingStart" for node in nodes
    ) == 2
    assert sum(node["class_type"] == "easy h3NativeArtifact" for node in nodes) == 1
    assert not any(node["class_type"] == "ImageResizeKJv2" for node in nodes)
    assert not any(
        node["class_type"] == "easy minimaxH3LatentUpscaler" for node in nodes
    )


def test_multitrack_h3_connected_sampler_and_sigmas_force_custom(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    custom_sampler = object()
    custom_sigmas = torch.tensor([1.0, 0.0])

    result = module.EasyMultiTrackProject.execute(
        **_h3_project_inputs(
            sampling_plan=["medium"],
            sampler=[custom_sampler],
            sigmas=[custom_sigmas],
        )
    )

    nodes = list(result.expand.values())
    sampling_start = next(
        node
        for node in nodes
        if node["class_type"] == "easy h3SegmentSamplingStart"
    )
    assert sampling_start["inputs"]["sampler"] is custom_sampler
    assert sampling_start["inputs"]["sigmas"] is custom_sigmas
    assert not any(node["class_type"] == "KSamplerSelect" for node in nodes)
    assert not any(node["class_type"] == "ManualSigmas" for node in nodes)


def test_multitrack_h3_dynamic_dual_sampler_and_sigmas_override_each_preset(
    monkeypatch,
):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    first_pass_sampler = object()
    first_pass_sigmas = torch.tensor([1.0, 0.0])
    second_pass_sampler = object()
    second_pass_sigmas = torch.tensor([0.8, 0.0])

    result = module.EasyMultiTrackProject.execute(
        **_h3_project_inputs(
            sampler=[first_pass_sampler],
            sigmas=[first_pass_sigmas],
            seed=[12],
                sampling_mode=_h3_sampling_mode(
                    "dual",
                    sampler_2nd=[second_pass_sampler],
                    sigmas_2nd=[second_pass_sigmas],
                    upscale_by=[1.0],
                ),
        )
    )

    samples = [
        node
        for node in result.expand.values()
        if node["class_type"] == "SamplerCustomAdvanced"
    ]
    noise_seeds = [
        node["inputs"]["noise_seed"]
        for node in result.expand.values()
        if node["class_type"] == "RandomNoise"
    ]
    sampling_starts = [
        node
        for node in result.expand.values()
        if node["class_type"] == "easy h3SegmentSamplingStart"
    ]
    assert sampling_starts[0]["inputs"]["sampler"] is first_pass_sampler
    assert sampling_starts[0]["inputs"]["sigmas"] is first_pass_sigmas
    assert sampling_starts[1]["inputs"]["sampler"] is second_pass_sampler
    assert sampling_starts[1]["inputs"]["sigmas"] is second_pass_sigmas
    assert samples[1]["inputs"]["sampler"] == [
        next(
            node_id
            for node_id, node in result.expand.items()
            if node is sampling_starts[1]
        ),
        2,
    ]
    assert noise_seeds == [12, 12]
    assert not any(node["class_type"] == "KSamplerSelect" for node in result.expand.values())
    assert not any(node["class_type"] == "ManualSigmas" for node in result.expand.values())


def test_multitrack_h3_dynamic_dual_model_loader_uses_second_model_for_second_pass(
    monkeypatch,
):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    first_model = _MiniMaxH3Model()
    second_model = _MiniMaxH3Model()
    first_vae = object()
    first_audio_vae = object()
    second_vae = object()
    second_audio_vae = object()

    result = module.EasyMultiTrackProject.execute(
        **_h3_project_inputs(
            model_loader=[
                {
                    "model": first_model,
                    "clip": object(),
                    "vae": first_vae,
                    "audio_vae": first_audio_vae,
                }
            ],
                sampling_mode=_h3_sampling_mode(
                    "dual",
                    model_loader_2nd=[
                    {
                        "model": second_model,
                        "clip": object(),
                        "vae": second_vae,
                        "audio_vae": second_audio_vae,
                        }
                    ],
                    upscale_by=[1.0],
                ),
        )
    )

    guider_models = [
        node["inputs"]["model"]
        for node in result.expand.values()
        if node["class_type"] == "BasicGuider"
    ]
    assert guider_models == [first_model, second_model]
    assert _graph_node(result, "VAEDecode")["inputs"]["vae"] is first_vae
    assert _graph_node(result, "VAEDecodeAudio")["inputs"]["vae"] is first_audio_vae


def test_multitrack_h3_selflift_uses_second_model_only_for_high_resolution(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    first_model = _MiniMaxH3Model()
    second_model = _MiniMaxH3Model()

    result = module.EasyMultiTrackProject.execute(
        **_h3_project_inputs(
            model_loader=[{
                "model": first_model,
                "clip": object(),
                "vae": object(),
                "audio_vae": object(),
            }],
            sampling_mode=_h3_sampling_mode(
                "selflift",
                model_loader_2nd=[{"model": second_model}],
            ),
        )
    )

    selflift = _graph_node(result, "easy minimaxH3SelfLiftSampler")
    assert selflift["inputs"]["model"] is first_model
    assert selflift["inputs"]["highres_model"] is second_model


def test_linked_selflift_forwards_second_model_loader(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    result = module.EasyMultiTrackProject.execute(
        **_h3_project_inputs(
            model_loader=[["first-loader", 0]],
            sampling_mode=_h3_sampling_mode(
                "selflift", model_loader_2nd=[["second-loader", 0]]
            ),
        )
    )

    model_id, model_prepare = next(
        (node_id, node)
        for node_id, node in result.expand.items()
        if node["class_type"] == "easy h3ProjectStaticPrepare"
        and node_id.endswith("project_model_prepare")
    )
    selflift = _graph_node(result, "easy minimaxH3SelfLiftSampler")
    assert model_prepare["inputs"]["model_loader_2nd"] == ["second-loader", 0]
    assert selflift["inputs"]["model"] == [model_id, 2]
    assert selflift["inputs"]["highres_model"] == [model_id, 3]


def test_multitrack_h3_rejects_incomplete_custom_sampling(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    with pytest.raises(ValueError, match="both sampler and sigmas"):
        module.EasyMultiTrackProject.execute(
            **_h3_project_inputs(sampler=[object()])
        )


def test_multitrack_h3_first_pass_preview_skips_second_model_loader(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    result = module.EasyMultiTrackProject.execute(
        **_h3_project_inputs(
            sampling_mode=_h3_sampling_mode(
                "dual",
                    **{"1st_pass_only": [True]},
                model_loader_2nd=[{}],
            )
        )
    )

    assert sum(
        node["class_type"] == "SamplerCustomAdvanced"
        for node in result.expand.values()
    ) == 1


def test_multitrack_h3_first_pass_preview_only_builds_first_selected_task(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    info = _h3_project_inputs()["tracks_info"][0]
    second = {
        "start_frame": 120,
        "end_frame": 240,
        "content": {
            "task_mode": "l2v",
            "continuity_mode": "context",
            "images": [{"media_index": 0}],
            "user_prompt": "continue",
        },
    }
    info["tracks"][0]["segments"].append(second)

    result = module.EasyMultiTrackProject.execute(
        **_h3_project_inputs(
            tracks_info=[info],
                sampling_mode=_h3_sampling_mode("dual", **{"1st_pass_only": [True]}),
            sampling_plan=["light"],
        )
    )

    nodes = list(result.expand.values())
    assert sum(node["class_type"] == "easy multiTrackTaskOutput" for node in nodes) == 1
    assert sum(node["class_type"] == "SamplerCustomAdvanced" for node in nodes) == 1
    assert not any(node["class_type"] == "ImageResizeKJv2" for node in nodes)
    conditioning = next(
        node for node in nodes if node["class_type"] == "easy minimaxH3ToVideo"
    )
    assert (conditioning["inputs"]["width"], conditioning["inputs"]["height"]) == (
        1344,
        768,
    )
    artifact = next(
        node for node in nodes if node["class_type"] == "easy h3NativeArtifact"
    )
    assert json.loads(_graph_node(result, "easy h3NativePrepare")["inputs"]["recipe_json"])["first_pass_only"] is True
    first_pass_sample_id = next(
        node_id
        for node_id, node in result.expand.items()
        if node["class_type"] == "SamplerCustomAdvanced"
    )
    result_node = result.expand[artifact["inputs"]["latent"][0]]
    assert result_node["class_type"] == "easy h3NativeResult"
    assert result_node["inputs"]["latent"] == [first_pass_sample_id, 1]
    assert result_node["inputs"]["low_latent"] == [first_pass_sample_id, 1]










def test_multitrack_h3_first_context_task_does_not_add_an_empty_prefix(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    inputs = _h3_project_inputs()
    info = inputs["tracks_info"][0]
    info["tracks"][0]["segments"][0]["content"]["continuity_mode"] = "context"
    info["tracks"].append(
        {
            "type": "audio",
            "audio_locked": True,
            "segments": [
                {
                    "start_frame": 0,
                    "end_frame": 120,
                    "content": {"media_type": "audio"},
                }
            ],
        }
    )

    result = module.EasyMultiTrackProject.execute(**inputs)
    nodes = list(result.expand.values())
    conditioning = next(
        node for node in nodes if node["class_type"] == "easy minimaxH3ToVideo"
    )
    plan = json.loads(_graph_node(result, 'easy h3NativePrepare')['inputs']['plan_json'])
    assert plan['context_frames'] == 0 and plan['raw_frames'] == 124
    assert _graph_node(result, 'easy h3NativeAudioLock')
    assert not any(n['class_type'] == 'easy MiniMaxH3MotionContextHard' for n in nodes)






def test_multitrack_h3_connected_second_pass_sampling_overrides_context_preset(
    monkeypatch,
):
    module = _load_minimax_node(monkeypatch)
    info = _h3_project_inputs()["tracks_info"][0]
    info["tracks"][0]["segments"].append(
        {
            "start_frame": 120,
            "end_frame": 240,
            "content": {
                "task_mode": "l2v",
                "continuity_mode": "context",
                "images": [{"media_index": 0}],
                "user_prompt": "continue",
            },
        }
    )
    second_pass_sampler = object()
    second_pass_sigmas = torch.tensor([0.4, 0.2, 0.0])

    result = module.EasyMultiTrackProject.execute(
        **_h3_project_inputs(
            tracks_info=[info],
            sampling_plan=["medium"],
            sampling_mode=_h3_sampling_mode(
                "dual",
                sampler_2nd=[second_pass_sampler],
                sigmas_2nd=[second_pass_sigmas],
                upscale_by=[1.0],
            ),
        )
    )

    second_pass_starts = [
        node
        for node in result.expand.values()
        if node["class_type"] == "easy h3SegmentSamplingStart"
        and node["inputs"]["sampling_pass"] == "second"
    ]
    assert len(second_pass_starts) == 2
    assert all(
        node["inputs"]["sampler"] is second_pass_sampler
        and node["inputs"]["sigmas"] is second_pass_sigmas
        for node in second_pass_starts
    )
    assert not any(
        node["class_type"] == "ManualSigmas"
        and node["inputs"]["sigmas"] == "0.50, 0.30, 0.14, 0.06, 0.0"
        for node in result.expand.values()
    )


















def test_multitrack_h3_project_uses_prompt_graph_as_last_turbo_fallback(
    monkeypatch, capsys
):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    prompt = {
        "3": {
            "class_type": "easy multitrackProject",
            "inputs": {"model_loader": ["2", 0]},
        },
        "2": {
            "class_type": "easy modelLoaderPack",
            "inputs": {"model": ["1", 0]},
        },
        "1": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"lora_name": "minimax_h3_turbo_4step.safetensors"},
        },
    }
    graph_result = types.SimpleNamespace(
        is_turbo=True,
        as_dict=lambda: {
            "status": "turbo",
            "is_turbo": True,
            "source": "graph_prompt",
            "evidence": "node 1",
            "patch_count": 0,
        },
    )
    graph_calls = []
    monkeypatch.setattr(
        module._project_module,
        "detect_turbo_lora_from_prompt",
        lambda received_prompt, node_id: graph_calls.append(
            (received_prompt, node_id)
        )
        or graph_result,
    )
    module.EasyMultiTrackProject.hidden = types.SimpleNamespace(
        prompt=prompt,
        unique_id="3",
    )

    module.EasyMultiTrackProject.execute(**_h3_project_inputs())

    assert graph_calls == [(prompt, "3")]
    assert graph_calls == [(prompt, "3")]


def test_multitrack_h3_project_skips_prompt_graph_after_model_turbo_match(
    monkeypatch,
):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    model_result = types.SimpleNamespace(
        is_turbo=True,
        as_dict=lambda: {
            "status": "turbo",
            "is_turbo": True,
            "source": "model_patches",
            "evidence": "patch match",
            "patch_count": 1,
        },
    )
    monkeypatch.setattr(
        module._project_module,
        "detect_turbo_model",
        lambda model: model_result,
    )

    def fail_graph_fallback(prompt, node_id):
        raise AssertionError("graph fallback must not run after a model match")

    monkeypatch.setattr(
        module._project_module, "detect_turbo_lora_from_prompt", fail_graph_fallback
    )

    module.EasyMultiTrackProject.execute(**_h3_project_inputs())


def test_multitrack_h3_project_requires_a_model_loader_dictionary(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    with pytest.raises(TypeError, match="FAST_MODEL_LOADER dictionary"):
        module.EasyMultiTrackProject.execute(
            model_loader=[],
            tracks_info=[{}],
        )


def test_multitrack_h3_project_requires_core_model_components(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    with pytest.raises(ValueError, match="clip, vae"):
        module.EasyMultiTrackProject.execute(
            model_loader=[{"model": object()}],
            tracks_info=[{}],
        )


def test_multitrack_project_rejects_non_minimax_h3_model(monkeypatch):
    module = _load_minimax_node(monkeypatch)

    with pytest.raises(ValueError, match="supports only MiniMaxH3"):
        module.EasyMultiTrackProject.execute(
            **_h3_project_inputs(model_loader=[{
                "model": object(),
                "clip": object(),
                "vae": object(),
                "audio_vae": object(),
            }])
        )


def test_h3_project_artifact_writes_manifest_and_rotates_ten_generations(
    monkeypatch, tmp_path
):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    monkeypatch.setattr(
        module.folder_paths,
        "get_output_directory",
        lambda: str(tmp_path),
    )
    info = _h3_project_inputs()["tracks_info"][0]
    project_dir = tmp_path / "easy_media" / "projects" / "demo"
    project_dir.mkdir(parents=True)

    for run in range(12):
        staged = project_dir / f".staged_{run}.mp4"
        staged.write_bytes(f"video-{run}".encode())
        high_context = _h3_context_latent(run, video_steps=12)
        high_context["anchor_samples"] = _h3_video_anchor_latent(run)["samples"]
        low_context = _h3_context_latent(-run, video_steps=12)
        low_context["anchor_samples"] = _h3_video_anchor_latent(-run)["samples"]
        output = module.EasyH3ProjectArtifact.execute(
            project_name="demo",
            project_save="new",
            segment_index=0,
            context_latent=high_context,
            context_latent_low=low_context,
            video_path=f"output/{staged.relative_to(tmp_path)}",
            tracks_info=info,
            seed=0xFFFFFFFFFFFFFFFF,
        )
        assert output.values == ("demo",)

    assert len(list(project_dir.glob("video_0_*.mp4"))) == 10
    assert not list(project_dir.glob("latent_0_*"))
    assert len(list(project_dir.glob("context_latent_0_*.safetensors"))) == 10
    assert len(list(project_dir.glob("context_latent_low_0_*.safetensors"))) == 10
    assert not list(project_dir.glob("anchor_latent_0_*.safetensors"))
    assert not list(project_dir.glob("anchor_latent_low_0_*.safetensors"))
    manifest = json.loads((project_dir / "project.json").read_text())
    assert manifest["version"] == 2
    assert manifest["project_name"] == "demo"
    assert (manifest["width"], manifest["height"], manifest["fps"]) == (
        1344,
        768,
        24.0,
    )
    assert "tracks_info" not in manifest
    assert manifest["task_segments"] == [
        {
            "index": 0,
            "continuity_mode": "shot",
            "task_mode": "default",
            "audio_locked": False,
        },
    ]
    assert manifest["segments"]["0"]["task_mode"] == "default"
    assert len(manifest["segments"]["0"]["generations"]) == 10
    loaded = module.EasyH3ProjectContextLatentLoad.execute("demo", 0)
    loaded_streams = loaded.values[0]["samples"].unbind()
    assert len(loaded_streams) == 2
    assert all(isinstance(stream, torch.Tensor) for stream in loaded_streams)
    assert loaded_streams[0].shape[2] == 7
    assert loaded_streams[1].shape[-1] == 37
    assert loaded.values[0]["anchor_samples"].shape == (1, 24, 2, 2, 2)
    loaded_low = module.EasyH3ProjectContextLatentLoad.execute(
        "demo", 0, resolution="low"
    )
    loaded_low_streams = loaded_low.values[0]["samples"].unbind()
    assert loaded_low_streams[0].shape[2] == 12
    assert loaded_low_streams[1].shape[-1] == 65
    assert loaded_low.values[0]["anchor_samples"].shape == (1, 24, 2, 2, 2)
    active_generation = str(manifest["segments"]["0"]["active_generation"])
    active_files = manifest["segments"]["0"]["generations"][active_generation]
    assert active_files["context_latent_low"].startswith("context_latent_low_0_")
    assert "anchor_latent" not in active_files
    assert "anchor_latent_low" not in active_files
    assert active_files["continuity_mode"] == "shot"
    assert active_files["seed"] == 0xFFFFFFFFFFFFFFFF


def test_h3_project_artifact_shot_saves_outgoing_context(monkeypatch, tmp_path):
    module = _load_minimax_node(monkeypatch)
    monkeypatch.setattr(module.folder_paths, "get_output_directory", lambda: str(tmp_path))
    project_dir = tmp_path / "easy_media" / "projects" / "demo"
    project_dir.mkdir(parents=True)
    staged = project_dir / ".staging_video_0.mp4"
    staged.write_bytes(b"video")

    tracks_info = _h3_project_inputs()["tracks_info"][0]
    tracks_info["tracks"][0]["segments"][0]["content"]["task_mode"] = "passthrough"
    module.EasyH3ProjectArtifact.execute(
        project_name="demo",
        project_save="new",
        segment_index=0,
        context_latent=_h3_context_latent(1),
        video_path=f"output/{staged.relative_to(tmp_path)}",
        tracks_info=tracks_info,
        continuity_mode="shot",
    )

    manifest = json.loads((project_dir / "project.json").read_text())
    active = str(manifest["segments"]["0"]["active_generation"])
    generation = manifest["segments"]["0"]["generations"][active]
    assert "context_cut" not in generation
    assert generation["task_mode"] == "passthrough"
    assert generation["context_latent"] == f"context_latent_0_{active}.safetensors"
    assert sys.modules["easy_media.utils.h3_project"].has_h3_context_latent(
        "demo", 0, resolution="high", output_directory=tmp_path,
    )

    context_path = project_dir / generation["context_latent"]
    old_context = context_path.read_bytes()
    staged.write_bytes(b"replacement video")
    module.EasyH3ProjectArtifact.execute(
        project_name="demo",
        project_save="override",
        segment_index=0,
        context_latent=_h3_context_latent(2),
        video_path=f"output/{staged.relative_to(tmp_path)}",
        tracks_info=tracks_info,
        continuity_mode="shot",
    )
    assert context_path.exists()
    assert context_path.read_bytes() != old_context


def test_h3_project_artifact_notifies_only_after_new_video_is_in_manifest(
    monkeypatch, tmp_path
):
    module = _load_minimax_node(monkeypatch)
    monkeypatch.setattr(
        module.folder_paths,
        "get_output_directory",
        lambda: str(tmp_path),
    )
    project_dir = tmp_path / "easy_media" / "projects" / "demo"
    project_dir.mkdir(parents=True)
    staged = project_dir / ".staged.mp4"
    staged.write_bytes(b"video")
    notifications = []

    def send_sync(event, payload):
        manifest = json.loads((project_dir / "project.json").read_text())
        active_generation = str(
            manifest["segments"]["0"]["active_generation"]
        )
        video_name = manifest["segments"]["0"]["generations"][
            active_generation
        ]["video"]
        notifications.append((event, payload, (project_dir / video_name).is_file()))

    server = types.ModuleType("server")
    server.PromptServer = types.SimpleNamespace(
        instance=types.SimpleNamespace(send_sync=send_sync)
    )
    monkeypatch.setitem(sys.modules, "server", server)

    module.EasyH3ProjectArtifact.execute(
        project_name="demo",
        project_save="new",
        segment_index=0,
        context_latent=_h3_context_latent(),
        video_path=f"output/{staged.relative_to(tmp_path)}",
        tracks_info=_h3_project_inputs()["tracks_info"][0],
    )

    assert notifications == [
        (
            "easy_multitrack_project_refresh",
            {"project_name": "demo", "phase": "after_save", "segment_index": 0},
            True,
        )
    ]


def test_h3_project_artifact_preserves_complete_first_pass_checkpoint(
    monkeypatch, tmp_path
):
    module = _load_minimax_node(monkeypatch)
    monkeypatch.setattr(
        module.folder_paths,
        "get_output_directory",
        lambda: str(tmp_path),
    )
    info = _h3_project_inputs()["tracks_info"][0]
    project_dir = tmp_path / "easy_media" / "projects" / "demo"
    project_dir.mkdir(parents=True)
    staged = project_dir / ".first-pass.mp4"
    staged.write_bytes(b"first-pass")

    module.EasyH3ProjectArtifact.execute(
        project_name="demo",
        project_save="new",
        segment_index=0,
        context_latent=_h3_context_latent(1, video_steps=12),
        context_latent_low=_h3_context_latent(1, video_steps=12),
        video_path=f"output/{staged.relative_to(tmp_path)}",
        tracks_info=info,
        sampling_pass="first",
    )

    high = module.EasyH3ProjectContextLatentLoad.execute("demo", 0).values[0]
    low = module.EasyH3ProjectContextLatentLoad.execute(
        "demo", 0, resolution="low"
    ).values[0]
    for latent in (high, low):
        video, audio = latent["samples"].unbind()
        assert video.shape[2] == 12
        assert audio.shape[-1] == 65


def test_h3_project_context_round_trip_matches_runtime_context(monkeypatch, tmp_path):
    module = _load_minimax_node(monkeypatch)
    monkeypatch.setattr(
        module.folder_paths,
        "get_output_directory",
        lambda: str(tmp_path),
    )
    project_dir = tmp_path / "easy_media" / "projects" / "demo"
    project_dir.mkdir(parents=True)
    staged = project_dir / ".context-round-trip.mp4"
    staged.write_bytes(b"video")
    high = module.trim_motion_context_latent(
        _h3_context_latent(3, video_steps=12)
    )
    low = module.trim_motion_context_latent(
        _h3_context_latent(7, video_steps=12)
    )
    high["anchor_samples"] = _h3_video_anchor_latent(3)["samples"]
    low["anchor_samples"] = _h3_video_anchor_latent(7)["samples"]

    module.EasyH3ProjectArtifact.execute(
        project_name="demo",
        project_save="new",
        segment_index=0,
        context_latent=high,
        context_latent_low=low,
        video_path=f"output/{staged.relative_to(tmp_path)}",
        tracks_info=_h3_project_inputs()["tracks_info"][0],
        sampling_pass="single",
    )

    for resolution, runtime in (("high", high), ("low", low)):
        loaded = module.EasyH3ProjectContextLatentLoad.execute(
            "demo", 0, resolution=resolution
        ).values[0]
        for actual, expected in zip(
            loaded["samples"].unbind(), runtime["samples"].unbind()
        ):
            assert torch.equal(actual, expected)
        assert torch.equal(loaded["anchor_samples"], runtime["anchor_samples"])


def test_h3_project_artifact_override_reuses_latest_generation(monkeypatch, tmp_path):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    monkeypatch.setattr(
        module.folder_paths,
        "get_output_directory",
        lambda: str(tmp_path),
    )
    info = _h3_project_inputs()["tracks_info"][0]
    project_dir = tmp_path / "easy_media" / "projects" / "default"
    project_dir.mkdir(parents=True)

    for run, project_save in enumerate(("new", "new", "override")):
        staged = project_dir / f".override_{run}.mp4"
        staged.write_bytes(f"video-{run}".encode())
        module.EasyH3ProjectArtifact.execute(
            project_name="",
            project_save=project_save,
            segment_index=3,
            context_latent=_h3_context_latent(run),
            video_path=f"output/{staged.relative_to(tmp_path)}",
            tracks_info=info,
        )

    assert sorted(path.name for path in project_dir.glob("video_3_*.mp4")) == [
        "video_3_1.mp4",
        "video_3_2.mp4",
    ]
    assert (project_dir / "video_3_1.mp4").read_bytes() == b"video-0"
    assert (project_dir / "video_3_2.mp4").read_bytes() == b"video-2"
    assert not list(project_dir.glob("latent_3_*"))
    assert sorted(
        path.name for path in project_dir.glob("context_latent_3_*.safetensors")
    ) == [
        "context_latent_3_1.safetensors",
        "context_latent_3_2.safetensors",
    ]
    manifest = json.loads((project_dir / "project.json").read_text())
    assert manifest["segments"]["3"]["active_generation"] == 2
    assert set(manifest["segments"]["3"]["generations"]) == {"1", "2"}


@pytest.mark.parametrize("project_save", ["new", "override"])
def test_h3_project_artifact_uses_embedded_audio_without_sidecar(
    monkeypatch, tmp_path, project_save,
):
    module = _load_minimax_node(monkeypatch)
    monkeypatch.setattr(
        module.folder_paths,
        "get_output_directory",
        lambda: str(tmp_path),
    )
    info = _h3_project_inputs()["tracks_info"][0]
    project_dir = tmp_path / "easy_media" / "projects" / "demo"
    project_dir.mkdir(parents=True)
    if project_save == "override":
        (project_dir / "locked_audio_0_1.wav").write_bytes(b"legacy-audio")
        (project_dir / "project.json").write_text(json.dumps({
            "segments": {"0": {"generations": {"1": {
                "locked_audio": "locked_audio_0_1.wav",
            }}}},
        }))
    staged = project_dir / ".staged.mp4"
    staged.write_bytes(b"video")

    module.EasyH3ProjectArtifact.execute(
        project_name="demo",
        project_save=project_save,
        segment_index=0,
        context_latent=_h3_context_latent(1),
        video_path=f"output/{staged.relative_to(tmp_path)}",
        tracks_info=info,
    )

    manifest = json.loads((project_dir / "project.json").read_text())
    generation = manifest["segments"]["0"]["generations"]["1"]
    assert "locked_audio" not in generation
    assert not list(project_dir.glob("locked_audio_*.wav"))
    assert (project_dir / generation["video"]).read_bytes() == b"video"


def test_reference_bridge_uses_fixed_inputs_instead_of_autogrow(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    schema = module.EasyMiniMaxH3ReferenceToVideoBridge.define_schema()
    input_names = [port.name for port in schema.inputs]

    assert schema.node_id == module.REFERENCE_BRIDGE_NODE_ID
    assert "ref_images" not in input_names
    assert input_names[-18:] == [
        *[f"ref_image_{index}" for index in range(9)],
        *[f"ref_video_{index}" for index in range(3)],
        *[f"ref_video_audio_{index}" for index in range(3)],
        *[f"ref_audio_{index}" for index in range(3)],
    ]


def test_multi_frames_routes_first_and_last_expanded_images(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    clip = _Clip()
    vae = _Vae()

    output = module.EasyMiniMaxH3ToVideo.execute(
        **_base_inputs(
            clip=[clip],
            vae=[vae],
            images=[[_image_values(0, 1)], _image_values(2)],
        )
    )

    conditioning = _graph_node(output, "MiniMaxH3ImageToVideo")
    assert conditioning["inputs"]["first_frame"][0, 0, 0, 0].item() == 0
    assert conditioning["inputs"]["last_frame"][0, 0, 0, 0].item() == 2


def test_multi_frames_with_one_image_routes_only_first_frame(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    output = module.EasyMiniMaxH3ToVideo.execute(
        **_base_inputs(images=[_image_values(7)])
    )

    conditioning = _graph_node(output, "MiniMaxH3ImageToVideo")
    assert "first_frame" in conditioning["inputs"]
    assert "last_frame" not in conditioning["inputs"]


def test_last_frame_routes_only_last_expanded_image(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    output = module.EasyMiniMaxH3ToVideo.execute(
        **_base_inputs(
            mode=["last_frame"],
            images=[[_image_values(0, 1)], _image_values(2)],
        )
    )

    conditioning = _graph_node(output, "MiniMaxH3ImageToVideo")
    assert "first_frame" not in conditioning["inputs"]
    assert conditioning["inputs"]["last_frame"][0, 0, 0, 0].item() == 2


def test_last_frame_with_one_image_routes_only_last_frame(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    output = module.EasyMiniMaxH3ToVideo.execute(
        **_base_inputs(mode=["last_frame"], images=[_image_values(7)])
    )

    conditioning = _graph_node(output, "MiniMaxH3ImageToVideo")
    assert "first_frame" not in conditioning["inputs"]
    assert conditioning["inputs"]["last_frame"].shape == (1, 1, 1, 1)
    assert conditioning["inputs"]["last_frame"].item() == 7


@pytest.mark.parametrize("mode", ["reference", "multi_frames", "last_frame"])
def test_empty_media_routes_to_text_to_video_for_every_mode(monkeypatch, mode):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    clip = _Clip()

    output = module.EasyMiniMaxH3ToVideo.execute(
        **_base_inputs(clip=[clip], mode=[mode])
    )

    conditioning = _graph_node(output, "MiniMaxH3ImageToVideo")
    assert conditioning["inputs"]["prompt"] == "prompt"
    assert "first_frame" not in conditioning["inputs"]
    assert "last_frame" not in conditioning["inputs"]


@pytest.mark.parametrize(
    ("mode", "media_kind"),
    [
        ("multi_frames", "video"),
        ("multi_frames", "audio"),
        ("last_frame", "video"),
        ("last_frame", "audio"),
    ],
)
def test_frame_modes_with_video_or_audio_route_to_reference_subgraph(
    monkeypatch, mode, media_kind
):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    audio = {"waveform": torch.ones(1, 1, 4), "sample_rate": 32000}
    overrides = {
        "audio_vae": [_AudioVae()],
        "images": [_image_values(1, 2)],
        "videos": [object()] if media_kind == "video" else [],
        "audios": [audio] if media_kind == "audio" else [],
    }

    output = module.EasyMiniMaxH3ToVideo.execute(
        **_base_inputs(mode=[mode], **overrides)
    )

    nodes_by_type = {node["class_type"]: node for node in output.expand.values()}
    conditioning = nodes_by_type[module.REFERENCE_BRIDGE_NODE_ID]
    assert "MiniMaxH3ImageToVideo" not in nodes_by_type
    assert "ref_image_0" in conditioning["inputs"]
    assert "ref_image_1" in conditioning["inputs"]
    if media_kind == "video":
        assert "GetVideoComponents" in nodes_by_type
        assert "ref_video_0" in conditioning["inputs"]
    else:
        assert conditioning["inputs"]["ref_audio_0"] is audio


def test_reference_video_extraction_is_deferred_to_a_cacheable_subnode(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    video = object()

    output = module.EasyMiniMaxH3ToVideo.execute(
        **_base_inputs(
            mode=["reference"],
            audio_vae=[_AudioVae()],
            videos=[video],
        )
    )

    assert output.expand is not None
    nodes_by_type = {node["class_type"]: node for node in output.expand.values()}
    components = nodes_by_type["GetVideoComponents"]
    conditioning = nodes_by_type[module.REFERENCE_BRIDGE_NODE_ID]
    components_id = next(
        node_id
        for node_id, node in output.expand.items()
        if node["class_type"] == "GetVideoComponents"
    )
    assert components["inputs"] == {"video": video}
    assert "easy minimaxH3ResampleVideoFrames" not in nodes_by_type
    assert conditioning["inputs"]["ref_video_0"] == [components_id, 0]
    assert conditioning["inputs"]["ref_video_audio_0"] == [components_id, 1]


def test_locked_video_timing_is_forwarded_to_reference_bridge(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    output = module.EasyMiniMaxH3ToVideo.execute(
        **_base_inputs(
            mode=["reference"],
            videos=[object()],
            locked_video_timing_frames=[56],
        )
    )

    conditioning = _graph_node(output, module.REFERENCE_BRIDGE_NODE_ID)
    assert conditioning["inputs"]["locked_video_timing_frames"] == 56


def test_easy_node_reports_progress_for_each_media_input(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    audio = {"waveform": torch.ones(1, 1, 4), "sample_rate": 32000}

    module.EasyMiniMaxH3ToVideo.execute(
        **_base_inputs(
            mode=["reference"],
            audio_vae=[_AudioVae()],
            images=[_image_values(1, 2)],
            videos=[object(), object()],
            audios=[audio],
        )
    )

    progress = _ProgressBar.instances[-1]
    assert progress.total == 6
    assert progress.updates == [
        (1, 6),
        (2, 6),
        (3, 6),
        (4, 6),
        (5, 6),
        (6, 6),
    ]


def test_easy_node_reports_complete_progress_for_empty_inputs(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    module.EasyMiniMaxH3ToVideo.execute(**_base_inputs(mode=["reference"]))

    progress = _ProgressBar.instances[-1]
    assert progress.total == 1
    assert progress.updates == [(1, 1)]


def test_multi_frames_reports_progress_for_each_expanded_image(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    module.EasyMiniMaxH3ToVideo.execute(
        **_base_inputs(
            mode=["multi_frames"],
            images=[_image_values(1, 2, 3)],
        )
    )

    progress = _ProgressBar.instances[-1]
    assert progress.total == 4
    assert progress.updates == [(1, 4), (2, 4), (3, 4), (4, 4)]


def test_reference_encodes_images_video_audio_and_standalone_audio(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    clip = _Clip()
    vae = _Vae()
    audio_vae = _AudioVae()
    video_audio = {"waveform": torch.ones(1, 1, 8), "sample_rate": 32000}

    class _Video:
        def get_components(self):
            return types.SimpleNamespace(
                images=_image_values(0, 1, 2, 3, 4),
                audio=video_audio,
                frame_rate=Fraction(12),
            )

    standalone_audio = {"waveform": torch.zeros(1, 1, 4), "sample_rate": 16000}
    output = module.EasyMiniMaxH3ToVideo.execute(
        **_base_inputs(
            clip=[clip],
            vae=[vae],
            mode=["reference"],
            audio_vae=[audio_vae],
            images=[_image_values(9, 10)],
            videos=[[_Video()]],
            audios=[[standalone_audio]],
        )
    )

    conditioning = _graph_node(output, module.REFERENCE_BRIDGE_NODE_ID)
    components = _Video().get_components()
    fallback_output = module.MiniMaxH3ReferenceToVideoFallback.execute(
        clip=clip,
        vae=vae,
        audio_vae=audio_vae,
        prompt="prompt",
        width=32,
        height=32,
        length=5,
        ref_images={
            "ref_image_0": _image_values(9),
            "ref_image_1": _image_values(10),
        },
        ref_videos={"ref_video_0": components.images},
        ref_video_audios={"ref_video_audio_0": components.audio},
        ref_audios={"ref_audio_0": standalone_audio},
    )

    refs = fallback_output.values[0][0][1]["minimax_refs"]
    assert [ref["kind"] for ref in refs] == ["image", "image", "video_audio", "audio"]
    assert [
        item["type"] for item in clip.tokenize_calls[0]["kwargs"]["minimax_ref_items"]
    ] == [
        "image",
        "image",
        "audio",
        "video",
        "audio",
    ]
    assert vae.encoded[-1].shape[0] == 5
    assert len(audio_vae.encoded) == 2
    assert conditioning["inputs"]["ref_video_0"][1] == 0


def test_reference_bridge_groups_fixed_inputs_before_direct_execute(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    calls = []

    class _NativeReferenceNode:
        @classmethod
        def execute(cls, **kwargs):
            calls.append(kwargs)
            return _NodeOutput("conditioning", "latent")

    module.comfy_nodes.NODE_CLASS_MAPPINGS[
        "MiniMaxH3ReferenceToVideo"
    ] = _NativeReferenceNode
    audio = {"waveform": torch.ones(1, 1, 8), "sample_rate": 32000}

    output = module.EasyMiniMaxH3ReferenceToVideoBridge.execute(
        clip=_Clip(),
        vae=_Vae(),
        audio_vae=_AudioVae(),
        prompt="prompt",
        width=32,
        height=32,
        length=5,
        ref_image_0=_image_values(9),
        ref_video_0=_image_values(0, 1, 2, 3, 4),
        ref_video_audio_0=audio,
        ref_audio_0=audio,
    )

    assert output.values == ("conditioning", "latent")
    assert list(calls[0]["ref_images"]) == ["ref_image_0"]
    assert list(calls[0]["ref_videos"]) == ["ref_video_0"]
    assert list(calls[0]["ref_video_audios"]) == ["ref_video_audio_0"]
    assert list(calls[0]["ref_audios"]) == ["ref_audio_0"]


def test_reference_bridge_pads_video_tail_without_resizing(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    calls = []

    def unexpected_resize(*_args, **_kwargs):
        raise AssertionError("reference bridge must leave spatial resizing to core")

    monkeypatch.setattr(module, "_resize", unexpected_resize)

    class _NativeReferenceNode:
        @classmethod
        def execute(cls, **kwargs):
            calls.append(kwargs)
            return _NodeOutput("conditioning", "latent")

    module.comfy_nodes.NODE_CLASS_MAPPINGS[
        "MiniMaxH3ReferenceToVideo"
    ] = _NativeReferenceNode
    frames = _image_values(*range(120))

    module.EasyMiniMaxH3ReferenceToVideoBridge.execute(
        clip=_Clip(),
        vae=_Vae(),
        prompt="prompt",
        width=32,
        height=32,
        length=120,
        ref_video_0=frames,
    )

    aligned = calls[0]["ref_videos"]["ref_video_0"]
    assert aligned.shape == (124, 1, 1, 1)
    assert aligned[:120, 0, 0, 0].tolist() == list(map(float, range(120)))
    assert aligned[120:, 0, 0, 0].tolist() == [119.0] * 4


@pytest.mark.parametrize("source_frame_count", [39, 40, 41, 55, 56, 57])
def test_reference_bridge_uniformly_fits_locked_video_timing(
    monkeypatch,
    source_frame_count,
):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    calls = []

    class _NativeReferenceNode:
        @classmethod
        def execute(cls, **kwargs):
            calls.append(kwargs)
            return _NodeOutput("conditioning", "latent")

    module.comfy_nodes.NODE_CLASS_MAPPINGS[
        "MiniMaxH3ReferenceToVideo"
    ] = _NativeReferenceNode
    target_frame_count = module._align_frame_count(source_frame_count)
    frames = _image_values(*range(source_frame_count))

    module.EasyMiniMaxH3ReferenceToVideoBridge.execute(
        clip=_Clip(),
        vae=_Vae(),
        prompt="prompt",
        width=32,
        height=32,
        length=target_frame_count,
        locked_video_timing_frames=target_frame_count,
        ref_video_0=frames,
    )

    fitted = calls[0]["ref_videos"]["ref_video_0"]
    expected_indexes = torch.linspace(
        0,
        source_frame_count - 1,
        target_frame_count,
    ).round().long()
    assert fitted.shape == (target_frame_count, 1, 1, 1)
    assert torch.equal(fitted[:, 0, 0, 0], expected_indexes.float())
    restored_indexes = torch.linspace(
        0,
        target_frame_count - 1,
        source_frame_count,
    ).round().long()
    assert fitted.index_select(0, restored_indexes)[:, 0, 0, 0].tolist() == list(
        map(float, range(source_frame_count))
    )


def test_reference_fallback_pads_video_tail_instead_of_dropping_frames(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    vae = _Vae()
    frames = _image_values(*range(120))

    module.MiniMaxH3ReferenceToVideoFallback.execute(
        clip=_Clip(),
        vae=vae,
        audio_vae=None,
        prompt="prompt",
        width=32,
        height=32,
        length=120,
        ref_videos={"ref_video_0": frames},
    )

    encoded = vae.encoded[-1]
    assert encoded.shape[0] == 124
    assert encoded[119:, 0, 0, 0].tolist() == [119.0] * 5


def test_reference_bridge_directly_executes_fallback_when_native_is_missing(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    output = module.EasyMiniMaxH3ReferenceToVideoBridge.execute(
        clip=_Clip(),
        vae=_Vae(),
        prompt="prompt",
        width=32,
        height=32,
        length=5,
        ref_image_0=_image_values(9),
    )

    refs = output.values[0][0][1]["minimax_refs"]
    assert [ref["kind"] for ref in refs] == ["image"]


def test_reference_audio_requires_audio_vae(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    audio = {"waveform": torch.ones(1, 1, 4), "sample_rate": 32000}

    with pytest.raises(ValueError, match="audio_vae is required"):
        module.EasyMiniMaxH3ToVideo.execute(
            **_base_inputs(mode=["reference"], audios=[audio])
        )


def test_silent_reference_video_does_not_require_audio_vae(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    output = module.EasyMiniMaxH3ToVideo.execute(
        **_base_inputs(mode=["reference"], videos=[object()])
    )

    conditioning = _graph_node(output, module.REFERENCE_BRIDGE_NODE_ID)
    assert conditioning["inputs"]["audio_vae"] is None


@pytest.mark.parametrize(
    ("overrides", "media_name", "limit"),
    [
        ({"images": [_image_values(*range(10))]}, "images", 9),
        ({"videos": [object()] * 4, "audio_vae": [_AudioVae()]}, "videos", 3),
        (
            {
                "audios": [{"waveform": torch.ones(1, 1, 4), "sample_rate": 32000}] * 4,
                "audio_vae": [_AudioVae()],
            },
            "audios",
            3,
        ),
    ],
)
def test_reference_media_over_native_limits_is_rejected(
    monkeypatch, overrides, media_name, limit
):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    with pytest.raises(
        ValueError,
        match=rf"reference mode supports at most {limit} {media_name}",
    ):
        module.EasyMiniMaxH3ToVideo.execute(
            **_base_inputs(mode=["reference"], **overrides)
        )


def test_minimax_node_has_complete_chinese_localization():
    locale_path = Path(__file__).parents[1] / "locales" / "zh" / "nodeDefs.json"
    node_defs = json.loads(locale_path.read_text(encoding="utf-8"))

    translation = node_defs["easy minimaxH3ToVideo"]

    assert translation["display_name"] == "简易 MiniMax H3 视频生成"
    assert set(translation["inputs"]) == {
        "clip",
        "vae",
        "audio_vae",
        "images",
        "videos",
        "audios",
        "prompt",
        "mode",
        "width",
        "height",
        "length",
        "ref_image_size",
    }
    assert translation["inputs"]["mode"]["options"] == {
        "reference": "参考生视频",
        "multi_frames": "首尾帧生视频",
        "last_frame": "尾帧生视频",
    }
    assert translation["inputs"]["ref_image_size"]["options"] == {
        "match": "匹配生成尺寸",
        "max": "最大参考尺寸",
    }
    assert translation["outputs"] == {
        "0": {"name": "正向条件"},
        "1": {"name": "潜空间"},
    }
    assert "重采样" not in translation["description"]
    assert "重采样" not in translation["inputs"]["videos"]["tooltip"]
    assert "任何参考视频" in translation["inputs"]["audio_vae"]["tooltip"]
    assert "仅缩小，不放大" in translation["inputs"]["ref_image_size"]["tooltip"]
    assert "输入后会自动改走参考生视频" in translation["inputs"]["videos"]["tooltip"]
    assert "输入后会自动改走参考生视频" in translation["inputs"]["audios"]["tooltip"]

    for node_id in ["MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo"]:
        fallback_translation = node_defs[node_id]
        assert fallback_translation["display_name"]
        assert fallback_translation["description"]
        assert fallback_translation["inputs"]
        assert fallback_translation["outputs"] == {
            "0": {"name": "正向条件"},
            "1": {"name": "潜空间"},
        }


def test_remove_h3_motion_context_latent_schema(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    assert module is not None

    schema = module.EasyRemoveH3MotionContextLatent.define_schema()

    assert schema.node_id == "easy removeH3MotionContextLatent"
    assert schema.display_name == "!!Remove h3 motion context latent"
    assert schema.is_output_node is True
    assert schema.not_idempotent is True
    assert schema.inputs[0].name == "filename_path"
    assert schema.inputs[0].kwargs["default"] == "h3_context/clip"
    assert schema.inputs[1].name == "input"
    assert [output.name for output in schema.outputs] == ["output", "deleted_count"]


def test_remove_h3_motion_context_latent_has_matching_chinese_localization():
    node_defs = json.loads(
        (Path(__file__).parents[1] / "locales" / "zh" / "nodeDefs.json").read_text()
    )
    translation = node_defs["easy removeH3MotionContextLatent"]

    assert translation["inputs"]["input"]["name"] == "输入"
    assert translation["outputs"] == {
        "0": {"name": "输出"},
        "1": {"name": "已删除数量"},
    }


def test_remove_h3_motion_context_latent_deletes_matching_output_files(
    monkeypatch, tmp_path
):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    latent_directory = tmp_path / "h3_context"
    latent_directory.mkdir()
    matching_files = [
        latent_directory / "clip_00001.safetensors",
        latent_directory / "clip_00002_.safetensors",
    ]
    for path in matching_files:
        path.write_bytes(b"latent")
    preserved_file = latent_directory / "other_00001.safetensors"
    preserved_file.write_bytes(b"latent")

    folder_paths = types.ModuleType("folder_paths")
    folder_paths.get_output_directory = lambda: str(tmp_path)
    monkeypatch.setitem(sys.modules, "folder_paths", folder_paths)
    monkeypatch.setattr(module, "folder_paths", folder_paths)

    passthrough = object()
    output = module.EasyRemoveH3MotionContextLatent.execute(
        passthrough,
        "h3_context/clip",
    )

    assert output.values == (passthrough, 2)
    assert not any(path.exists() for path in matching_files)
    assert preserved_file.exists()


@pytest.mark.parametrize(
    "filename_path",
    ["", "../clip", "h3_context/../clip", "/h3_context/clip"],
)
def test_remove_h3_motion_context_latent_rejects_unsafe_paths(
    monkeypatch, tmp_path, filename_path
):
    module = _load_minimax_node(monkeypatch)
    assert module is not None
    folder_paths = types.ModuleType("folder_paths")
    folder_paths.get_output_directory = lambda: str(tmp_path)
    monkeypatch.setitem(sys.modules, "folder_paths", folder_paths)

    with pytest.raises(ValueError, match="filename_path"):
        module.EasyRemoveH3MotionContextLatent.execute(object(), filename_path)


@pytest.mark.parametrize("mode,first_only", [("single", False), ("dual", False), ("dual", True)])
@pytest.mark.parametrize("with_context", [False, True])
def test_audio_only_project_never_decodes_or_saves_video(monkeypatch, mode, first_only, with_context):
    module = _load_minimax_node(monkeypatch)
    inputs = _h3_project_inputs(
        sampling_mode=_h3_sampling_mode(mode, **{"1st_pass_only": first_only}),
        upscale_model=["unused-upscaler"],
    )
    info = inputs["tracks_info"][0]
    info.update(width=32, height=32)
    if with_context:
        info["tracks"][0]["segments"].append({
            "start_frame": 120, "end_frame": 240,
            "content": {"continuity_mode": "context", "user_prompt": "continue"},
        })
    result = module.EasyMultiTrackProject.execute(**inputs)
    types_in_graph = {node["class_type"] for node in result.expand.values()}
    assert "VAEDecodeAudio" in types_in_graph
    assert not types_in_graph.intersection({
        "VAEDecode", "VAEEncode", "ImageResizeKJv2", "easy minimaxH3LatentUpscaler",
        "easy saveVideo", "easy h3SegmentEncodingStart", "easy h3SegmentSaveEnd",
        "easy h3LockedAudioDurationAlign",
    })
    artifacts = [n for n in result.expand.values() if n["class_type"] == "easy h3NativeArtifact"]
    for artifact in artifacts:
        assert "audio" in artifact["inputs"]
        assert "video_path" not in artifact["inputs"]
    if with_context and not first_only:
        assert "easy h3NativePrepare" in types_in_graph
        trims = [n for n in result.expand.values() if n["class_type"] == "easy h3NativeMediaView"]
        assert trims
        assert all("images" not in n["inputs"] for n in trims)


@pytest.mark.parametrize("dimensions", [(32, 64), (64, 32), (64, 64)])
def test_audio_only_project_requires_both_dimensions_to_be_32(monkeypatch, dimensions):
    module = _load_minimax_node(monkeypatch)
    inputs = _h3_project_inputs(sampling_mode=_h3_sampling_mode("single"))
    inputs["tracks_info"][0].update(width=dimensions[0], height=dimensions[1])
    result = module.EasyMultiTrackProject.execute(**inputs)
    assert _graph_node(result, "VAEDecode")
    assert _graph_node(result, "easy saveVideo")


def test_audio_only_project_saves_original_locked_audio(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    inputs = _h3_project_inputs()
    inputs["tracks_info"][0].update(width=32, height=32)
    inputs["tracks_info"][0]["tracks"].append({
        "type": "audio", "audio_locked": True,
        "segments": [{"start_frame": 0, "end_frame": 120, "content": {"media_type": "audio"}}],
    })
    result = module.EasyMultiTrackProject.execute(**inputs)
    artifact = _graph_node(result, "easy h3NativeArtifact")
    selector = result.expand[artifact["inputs"]["audio"][0]]
    assert selector["class_type"] == "easy h3LockedAudioSelect"
    assert selector["inputs"]["locked_audio"][0].endswith("native_audio_lock_0")


def test_audio_only_context_trims_samples_and_retains_encoded_audio(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    audio = {"waveform": torch.arange(80).reshape(1, 1, 80).float(), "sample_rate": 48}
    trimmed = module.EasyH3ContextMediaTrim.execute(
        audio=audio, trim_frames=5, output_frames=22, fps=24,
    )
    assert trimmed.values[0] is None
    assert trimmed.values[1]["waveform"].flatten().tolist() == list(range(10, 54))
    encoded = torch.randn(1, 32, 2, 37)
    context = module.EasyH3AudioContextLatent.execute({"samples": encoded}, 22)
    video, retained_audio = context.values[0]["samples"].unbind()
    assert video.shape == (1, 24, 7, 2, 2)
    assert torch.count_nonzero(video) == 0
    assert retained_audio is encoded


def test_audio_only_artifact_writes_wav_manifest_and_cleans_up(monkeypatch, tmp_path):
    import soundfile as sf

    module = _load_minimax_node(monkeypatch)
    monkeypatch.setattr(module.folder_paths, "get_output_directory", lambda: str(tmp_path))
    info = _h3_project_inputs()["tracks_info"][0]
    info.update(width=32, height=32)
    audio = {"waveform": torch.linspace(-0.5, 0.5, 4800).reshape(1, 1, -1), "sample_rate": 48000}
    project_dir = tmp_path / "easy_media" / "projects" / "audio-demo"
    for mode in ("new", "new", "override"):
        module.EasyH3ProjectArtifact.execute(
            project_name="audio-demo", project_save=mode, segment_index=0,
            context_latent=_h3_context_latent(), tracks_info=info, audio=audio,
        )
    assert len(list(project_dir.glob("audio_0_*.wav"))) == 2
    assert not list(project_dir.glob("*.mp4"))
    manifest = json.loads((project_dir / "project.json").read_text())
    active = manifest["segments"]["0"]["generations"]["1"]
    assert "video" not in active
    waveform, sr = sf.read(project_dir / active["audio"])
    assert sr == 48000
    assert len(waveform) == 4800
    assert torch.allclose(torch.from_numpy(waveform), audio["waveform"].flatten().double(), atol=1e-6)
    loaded_video, loaded_audio = module.EasyH3ProjectContextLatentLoad.execute(
        "audio-demo", 0
    ).values[0]["samples"].unbind()
    assert loaded_video.shape[2] == 7
    assert loaded_audio.shape[-1] == 37
    sys.modules["easy_media.utils.h3_project"].clear_h3_project_segments_from("audio-demo", 0, tmp_path)
    assert not list(project_dir.glob("*.wav"))
    assert not list(project_dir.glob("*.safetensors"))


@pytest.mark.parametrize("hidden_id", ["15", ["15"]])
def test_audio_only_project_rejects_video_combine_before_any_project_changes(monkeypatch, hidden_id):
    module = _load_minimax_node(monkeypatch)
    inputs = _h3_project_inputs(project_save=["override"], segment_count=[-1])
    inputs["tracks_info"][0].update(width=32, height=32)
    module.EasyMultiTrackProject.hidden = types.SimpleNamespace(
        unique_id=hidden_id,
        prompt={"17": {
            "class_type": "easy multitrackProjectVideoCombine",
            "inputs": {"project_name": ["15", 0]},
        }},
    )
    def unexpected_work(*args, **kwargs):
        pytest.fail("Unsupported audio/video combination must fail before project or sampling work")

    for name in ("initialize_h3_project", "clear_h3_project_segments_from", "GraphBuilder", "detect_turbo_model"):
        monkeypatch.setattr(module._project_module, name, unexpected_work, raising=False)
    with pytest.raises(ValueError, match="Project audio merging is not implemented yet"):
        module.EasyMultiTrackProject.execute(**inputs)


@pytest.mark.parametrize("width,height,source", [
    (32, 32, ["99", 0]),
    (32, 32, "another-project"),
    (32, 64, ["15", 0]),
    (64, 32, ["15", 0]),
])
def test_audio_project_preflight_does_not_block_other_projects_or_video(monkeypatch, width, height, source):
    module = _load_minimax_node(monkeypatch)
    inputs = _h3_project_inputs(sampling_mode=_h3_sampling_mode("single"))
    inputs["tracks_info"][0].update(width=width, height=height)
    module.EasyMultiTrackProject.hidden = types.SimpleNamespace(
        unique_id="15",
        prompt={"17": {
            "class_type": "easy multitrackProjectVideoCombine",
            "inputs": {"project_name": source},
        }},
    )
    result = module.EasyMultiTrackProject.execute(**inputs)
    assert _graph_node(result, "easy h3NativeArtifact")


def test_project_timing_keeps_native_nodes_and_inputs(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    module.comfy_nodes.NODE_CLASS_MAPPINGS["ImageResizeKJv2"] = _ImageResizeKJWithNvidia
    inputs = _h3_project_inputs(
        project_name="timing-demo",
        sampling_mode=_h3_sampling_mode("dual"),
    )
    inputs["tracks_info"][0]["tracks"][0]["segments"].append({
        "start_frame": 120,
        "end_frame": 240,
        "content": {
            "task_mode": "l2v",
            "continuity_mode": "context",
            "images": [],
            "user_prompt": "continue",
        },
    })
    module.GraphBuilder.set_default_prefix("timing", 0, 0)
    result = module.EasyMultiTrackProject.execute(**inputs)
    tagged = [node for node in result.expand.values() if "easy_media_timing" in node.get("_meta", {})]
    assert len(tagged) >= 5
    assert {node["class_type"] for node in tagged} == {
        "SamplerCustomAdvanced",
        "VAEEncode",
        "VAEDecode",
        "VAEDecodeAudio",
    }
    labels = [node["_meta"]["easy_media_timing"] for node in tagged]
    assert len(set(labels)) == len(labels)
    assert all(label.startswith("timing-demo / ") for label in labels)
    assert any("first_pass_sample_0" in label for label in labels)
    assert any("second_pass_sample_0" in label for label in labels)

    monkeypatch.setattr(
        module._project_module,
        "_timed_h3_project_graph",
        lambda graph, _name, _segments=None: graph.finalize(),
    )
    module.GraphBuilder.set_default_prefix("timing", 0, 0)
    without_timing = module.EasyMultiTrackProject.execute(**inputs)
    graph_without_metadata = {
        node_id: {key: value for key, value in node.items() if key != "_meta"}
        for node_id, node in result.expand.items()
    }
    assert graph_without_metadata == without_timing.expand






def test_audio_lock_priority_keeps_locked_video_timing(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    inputs = _h3_project_inputs(sampling_mode=_h3_sampling_mode("single"))
    info = inputs["tracks_info"][0]
    info["tracks"][0]["segments"][0]["end_frame"] = 125
    info["tracks"].extend([
        {
            "type": "video",
            "audio_locked": True,
            "segments": [{
                "start_frame": 0,
                "end_frame": 125,
                "content": {"media_type": "video"},
            }],
        },
        {
            "type": "audio",
            "audio_locked": True,
            "segments": [{
                "start_frame": 0,
                "end_frame": 125,
                "content": {"media_type": "audio"},
            }],
        },
    ])

    for task in inputs['tracks_info'][0]['tracks'][0]['segments']:
        task['content']['task_mode'] = 'ref'
    for track in inputs['tracks_info'][0]['tracks'][1:]:
        for segment in track['segments']:
            segment['end_frame'] = max(segment['end_frame'], 124)
    result = module.EasyMultiTrackProject.execute(**inputs)

    conditioning = _graph_node(result, "easy minimaxH3ToVideo")
    assert conditioning["inputs"]["length"] == 124
    audio_lock = _graph_node(result, "easy h3NativeAudioLock")
    assert audio_lock["inputs"]["audio"] == {"prepared_locked_audio": True}




@pytest.mark.parametrize("duration", [120, 125, 124])
@pytest.mark.parametrize("prefix", [0, 22])
def test_video_locked_trim_fits_full_generated_span_without_dropping_tail(
    monkeypatch, duration, prefix,
):
    module = _load_minimax_node(monkeypatch)
    generated = module._align_frame_count(duration) + (34 if prefix else 0)
    images = torch.arange(generated, dtype=torch.float32).reshape(-1, 1, 1, 1)
    audio = {"waveform": torch.arange(generated * 2).reshape(1, 1, -1), "sample_rate": 48}
    result = module.EasyH3ContextMediaTrim.execute(
        images,
        audio,
        trim_frames=prefix,
        output_frames=duration,
        pad_audio=False,
        fit_video_duration=True,
        fps=24,
    )
    available = images[prefix:]
    expected_indexes = torch.linspace(0, len(available) - 1, duration).round().long()
    assert torch.equal(result.values[0], available.index_select(0, expected_indexes))
    assert result.values[0][0].item() == images[prefix].item()
    assert result.values[0][-1].item() == images[-1].item()
    assert torch.equal(result.values[1]["waveform"], audio["waveform"][..., prefix * 2:(prefix + duration) * 2])


def test_audio_locked_trim_discards_generated_tail_without_time_compression(
    monkeypatch,
):
    module = _load_minimax_node(monkeypatch)
    duration = 187
    prefix = 22
    generated = module._align_frame_count(duration) + 34
    images = torch.arange(generated, dtype=torch.float32).reshape(-1, 1, 1, 1)
    audio = {
        "waveform": torch.arange(generated * 2).reshape(1, 1, -1),
        "sample_rate": 48,
    }

    result = module.EasyH3ContextMediaTrim.execute(
        images,
        audio,
        trim_frames=prefix,
        output_frames=duration,
        pad_audio=False,
        fps=24,
    )

    assert torch.equal(result.values[0], images[prefix:prefix + duration])
    assert result.values[0][-1].item() == images[prefix + duration - 1].item()
    assert result.values[0][-1].item() != images[-1].item()
    assert torch.equal(
        result.values[1]["waveform"],
        audio["waveform"][..., prefix * 2:(prefix + duration) * 2],
    )




def test_minimax_prompt_override_node_serializes_ordered_prompts(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    result = module.EasyMinimaxPromptOverride.execute(
        {
            "prompt_1": "second prompt",
            "prompt_0": "@图片1 first prompt",
            "prompt_2": None,
        },
        duration="10,5,10",
        generation_type="r2v,i2v,l2v",
        continuity_mode="shot,context,context_swap",
        system_prompt="custom system prompt",
        video_track_lock=1,
        audio_track_lock=2,
    )

    payload = json.loads(result.values[0])
    assert payload["type"] == "minimax_prompt_override"
    assert payload["prompts"] == ["@图片1 first prompt", "second prompt"]
    assert payload["duration"] == "10,5,10"
    assert payload["generation_type"] == "r2v,i2v,l2v"
    assert payload["continuity_mode"] == "shot,context,context_swap"
    assert payload["system_prompt"] == "custom system prompt"
    assert payload["video_track_lock"] == 1
    assert payload["audio_track_lock"] == 2


def test_minimax_prompt_override_node_has_complete_chinese_localization():
    node_defs = json.loads(
        (Path(__file__).parents[1] / "locales" / "zh" / "nodeDefs.json").read_text()
    )
    translation = node_defs["easy minimaxPromptOverride"]

    assert translation["display_name"] == "MiniMax 提示词覆盖"
    assert set(translation["inputs"]) == {
        "prompts",
        "duration",
        "generation_type",
        "continuity_mode",
        "system_prompt",
        "video_track_lock",
        "audio_track_lock",
    }
    assert translation["inputs"]["duration"]["name"] == "生成时长"
    assert translation["inputs"]["generation_type"]["name"] == "生成类型"
    assert translation["inputs"]["continuity_mode"]["name"] == "衔接模式"
    assert translation["inputs"]["system_prompt"]["name"] == "系统提示词"
    assert translation["inputs"]["video_track_lock"]["name"] == "视频轨锁定"
    assert translation["inputs"]["audio_track_lock"]["name"] == "音频轨锁定"
    assert translation["outputs"] == {"0": {"name": "提示词覆盖"}}


def test_linked_project_context_preserves_custom_second_schedule_at_runtime(monkeypatch):
    module = _load_minimax_node(monkeypatch)
    second_sigmas = torch.tensor([0.72, 0.5, 0.3, 0.14, 0.06, 0.0])
    result = module._project_module.EasyH3ProjectStaticPrepare.execute(
        model_loader=_h3_project_inputs()['model_loader'][0],
        sampling_mode='dual', run_second_pass=True, has_context_second_pass=True,
        sampling_plan='custom', sampler='first', sigmas=torch.tensor([1.0, 0.0]),
        sampler_2nd='second', sigmas_2nd=second_sigmas)
    assert result.values[11] == 'second'
    assert result.values[12] is second_sigmas
    assert result.values[13] is second_sigmas
