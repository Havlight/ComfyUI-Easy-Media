from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "easy-media-multitrack-workflow"
SCRIPTS = SKILL_ROOT / "scripts"
TEMPLATE = SKILL_ROOT / "assets" / "templates" / "v1.3.0-blank-workflow.json"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def patch_workflow_module():
    module = _load_module("multitrack_patch_workflow_under_test", SCRIPTS / "patch_workflow.py")
    sys.modules["patch_workflow"] = module
    yield module
    sys.modules.pop("patch_workflow", None)


@pytest.fixture(scope="module")
def customize_template_module(patch_workflow_module):
    del patch_workflow_module
    return _load_module(
        "multitrack_customize_template_under_test",
        SCRIPTS / "customize_template.py",
    )


@pytest.fixture(scope="module")
def submit_workflow_module():
    return _load_module("multitrack_submit_workflow_under_test", SCRIPTS / "submit_workflow.py")


@pytest.fixture(scope="module")
def settings_check():
    """ComfyUI's settings check against the Project's and Editor's definitions from /object_info."""
    settings = _load_module("node_settings_for_patch_workflow", ROOT / "utils" / "node_settings.py")
    definitions = json.loads((ROOT / "tests" / "fixtures" / "node_definitions.json").read_text(encoding="utf-8"))
    return lambda node, changes: settings.change_settings(definitions[node["type"]], node, changes)


@pytest.mark.parametrize("content, expected", [
    (None, "http://127.0.0.1:8188"),
    ("", "http://127.0.0.1:8188"),
    ("WEB_VERSION: dev\n", "http://127.0.0.1:8188"),
    ("COMFYUI_URL:\n", "http://127.0.0.1:8188"),
    ('COMFYUI_URL: "  "\n', "http://127.0.0.1:8188"),
    ('COMFYUI_URL: "https://comfy.example/prefix/" # comment\n', "https://comfy.example/prefix"),
    ("COMFYUI_URL: localhost:9000\n", "http://localhost:9000"),
])
def test_submit_url_config_and_fallback(submit_workflow_module, tmp_path, content, expected):
    path = tmp_path / "config.yaml"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert submit_workflow_module.resolve_comfyui_url(None, path) == expected


def test_explicit_url_ignores_even_invalid_config(submit_workflow_module, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("COMFYUI_URL: [invalid", encoding="utf-8")
    assert submit_workflow_module.resolve_comfyui_url("localhost:9999/", path) == "http://localhost:9999"


@pytest.mark.parametrize("content", ["COMFYUI_URL: [invalid", "- value", "COMFYUI_URL: 42", "COMFYUI_URL: ftp://example.com", "COMFYUI_URL: http://localhost:99999"])
def test_invalid_config_does_not_silently_change_instance(submit_workflow_module, tmp_path, content):
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        submit_workflow_module.resolve_comfyui_url(None, path)


def test_config_location_for_bundled_and_copied_skill(submit_workflow_module, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert submit_workflow_module.default_config_path() == ROOT / "config.yaml"
    copied_script = tmp_path / "installed/skill/scripts/submit_workflow.py"
    monkeypatch.setattr(submit_workflow_module, "__file__", str(copied_script))
    assert submit_workflow_module.default_config_path() == tmp_path / "config.yaml"
    monkeypatch.chdir(SCRIPTS)
    assert submit_workflow_module.default_config_path() == ROOT / "config.yaml"


def _template_workflow() -> dict:
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


def _node(workflow: dict, node_id: int) -> dict:
    return next(node for node in workflow["nodes"] if node.get("id") == node_id)


def test_loader_replacement_updates_named_widget_values(customize_template_module):
    workflow = _template_workflow()

    customize_template_module.replace_hybrid_with_unet(
        workflow,
        node_id=11,
        unet_name="replacement.safetensors",
        weight_dtype="fp8_e4m3fn",
    )

    loader = _node(workflow, 11)
    assert loader["widgets_values"] == ["replacement.safetensors", "fp8_e4m3fn"]
    assert loader["widgets_values_named"] == {
        "unet_name": "replacement.safetensors",
        "weight_dtype": "fp8_e4m3fn",
    }


def test_attention_replacement_updates_or_removes_named_widget_values(
    customize_template_module,
):
    pathch_workflow = _template_workflow()
    customize_template_module.replace_attention_backend(
        pathch_workflow,
        node_id=13,
        backend="pathch-sage",
        sage_attention="sageattn_qk_int8_pv_fp16_cuda",
        allow_compile=True,
    )
    assert _node(pathch_workflow, 13)["widgets_values_named"] == {
        "sage_attention": "sageattn_qk_int8_pv_fp16_cuda",
        "allow_compile": True,
    }

    memory_workflow = _template_workflow()
    customize_template_module.replace_attention_backend(
        memory_workflow,
        node_id=13,
        backend="minimax-memory-efficient",
        sage_attention="auto",
        allow_compile=False,
    )
    assert "widgets_values_named" not in _node(memory_workflow, 13)


def test_locked_tracks_need_not_cover_every_task(patch_workflow_module):
    # As in the Editor: tasks outside the locked audio run without it, and an empty locked track locks nothing.
    track_data = {
        "frame_rate": 24,
        "total_length": 240,
        "tracks": [
            {
                "id": "tasks",
                "type": "task",
                "segments": [
                    {
                        "id": "task-1",
                        "start_frame": 0,
                        "end_frame": 120,
                        "content": {"images": []},
                    },
                    {
                        "id": "task-2",
                        "start_frame": 120,
                        "end_frame": 240,
                        "content": {"images": []},
                    },
                ],
            },
            {
                "id": "master-audio",
                "type": "audio",
                "audio_locked": True,
                "segments": [
                    {
                        "id": "audio-1",
                        "start_frame": 0,
                        "end_frame": 120,
                        "content": {"media_type": "audio"},
                    }
                ],
            },
        ],
    }

    patch_workflow_module.validate_track_data(track_data, recalculate=False)
    track_data["tracks"][1]["segments"] = []
    patch_workflow_module.validate_track_data(track_data, recalculate=False)


def test_audio_and_video_tracks_may_be_locked_together(patch_workflow_module, settings_check):
    track_data = {
        "frame_rate": 24,
        "total_length": 120,
        "tracks": [
            {
                "id": "tasks",
                "type": "task",
                "segments": [{
                    "id": "task-1",
                    "start_frame": 0,
                    "end_frame": 120,
                    "content": {"images": []},
                }],
            },
            {
                "id": "locked-video",
                "type": "video",
                "audio_locked": True,
                "segments": [{
                    "id": "video-1",
                    "start_frame": 0,
                    "end_frame": 120,
                    "content": {"media_type": "video"},
                }],
            },
            {
                "id": "locked-audio",
                "type": "audio",
                "audio_locked": True,
                "segments": [{
                    "id": "audio-1",
                    "start_frame": 0,
                    "end_frame": 120,
                    "content": {"media_type": "audio"},
                }],
            },
        ],
    }

    patch_workflow_module.validate_track_data(track_data, recalculate=False)

    _result, report = patch_workflow_module.apply_plan(
        _template_workflow(),
        {
            "editor": {"track_data": track_data},
            "project": {"segment_start_number": 1, "segment_count": 1},
        },
        settings_check,
    )
    assert report["settings_changes"] == {"project": {"segment_count": {"from": -1, "to": 1}}}


def test_locked_video_accepts_drift_tasks(patch_workflow_module):
    track_data = {
        "frame_rate": 24,
        "total_length": 360,
        "tracks": [
            {
                "id": "tasks",
                "type": "task",
                "segments": [
                    {
                        "id": f"task-{index + 1}",
                        "start_frame": index * 240,
                        "end_frame": min((index + 1) * 240, 360),
                        "content": {
                            "images": [],
                            "continuity_mode": "context_drift",
                        },
                    }
                    for index in range(2)
                ],
            },
            {
                "id": "reference-video",
                "type": "video",
                "audio_locked": True,
                "segments": [{
                    "id": "video-1",
                    "start_frame": 0,
                    "end_frame": 360,
                    "content": {
                        "media_type": "video",
                        "source_type": "input",
                        "file_path": "reference.mp4",
                    },
                }],
            },
        ],
    }

    patch_workflow_module.validate_track_data(track_data, recalculate=False)


def test_rejects_unknown_task_continuity_mode(patch_workflow_module):
    track_data = {
        "frame_rate": 24,
        "total_length": 24,
        "tracks": [{
            "id": "tasks",
            "type": "task",
            "segments": [{
                "id": "task-1",
                "start_frame": 0,
                "end_frame": 24,
                "content": {"images": [], "continuity_mode": "swap"},
            }],
        }],
    }

    with pytest.raises(
        patch_workflow_module.WorkflowError,
        match="continuity_mode must be 'shot', 'context', 'context_drift' or 'context_anchor'",
    ):
        patch_workflow_module.validate_track_data(track_data, recalculate=False)


def _single_task_track_data(content):
    return {
        "frame_rate": 24,
        "total_length": 24,
        "tracks": [{
            "id": "tasks",
            "type": "task",
            "segments": [{"id": "task-1", "start_frame": 0, "end_frame": 24, "content": {"images": [], **content}}],
        }],
    }


@pytest.mark.parametrize("field,value,valid", [
    ("drift_taper_steps", 3, True), ("drift_taper_steps", 0, True), ("drift_taper_steps", 12, True),
    ("drift_taper_steps", 13, False), ("drift_taper_steps", 1.5, False), ("drift_taper_floor", 0.7, True),
    ("drift_taper_floor", 1.5, False), ("drift_taper_floor", float("nan"), False),
    ("drift_taper_low_only", True, True), ("drift_taper_low_only", 1, False),
])
def test_validates_drift_taper_settings(patch_workflow_module, field, value, valid):
    _check_task_setting(patch_workflow_module, "context_drift", field, value, valid)


@pytest.mark.parametrize("field,value,valid", [
    ("anchor_placement", "keystone_front", True), ("anchor_placement", "middle", False), ("anchor_placement", ["x"], False),
    ("anchor_late", 0.25, True), ("anchor_late", 0.3, False), ("anchor_late", True, False),
    ("anchor_restart", True, True), ("anchor_restart", "yes", False),
    ("anchor_noise", 0, True), ("anchor_noise", 0.6, True), ("anchor_noise", 0.5, False), ("anchor_noise", True, False),
])
def test_validates_anchor_settings(patch_workflow_module, field, value, valid):
    _check_task_setting(patch_workflow_module, "context_anchor", field, value, valid)


def _check_task_setting(patch_workflow_module, continuity, field, value, valid):
    track_data = _single_task_track_data({"continuity_mode": continuity, field: value})

    if valid:
        patch_workflow_module.validate_track_data(track_data, recalculate=False)
        return
    with pytest.raises(patch_workflow_module.WorkflowError, match=field):
        patch_workflow_module.validate_track_data(track_data, recalculate=False)


@pytest.mark.parametrize("item,error", [
    ({"id": "u", "source_type": "input", "file_path": "line.wav", "fit": False}, None),
    ({"id": "p", "source_type": "track", "track_id": "voice", "muted": True}, None),
    ({"id": "p", "source_type": "track"}, "needs a track_id"),
    ({"id": "u", "source_type": "input"}, "needs a file_path"),
    ({"id": "u", "source_type": "input", "file_path": "line.wav", "fit": "no"}, "fit must be a boolean"),
])
def test_validates_task_reference_media(patch_workflow_module, item, error):
    track_data = _single_task_track_data({"task_mode": "ref", "audios": [item]})

    if error is None:
        patch_workflow_module.validate_track_data(track_data, recalculate=False)
        return
    with pytest.raises(patch_workflow_module.WorkflowError, match=error):
        patch_workflow_module.validate_track_data(track_data, recalculate=False)


def test_shared_media_accepts_task_images_audio_and_video(
    patch_workflow_module,
):
    track_data = {
        "frame_rate": 24,
        "total_length": 24,
        "tracks": [
            {
                "id": "tasks",
                "type": "task",
                "segments": [{
                    "id": "task-1",
                    "start_frame": 0,
                    "end_frame": 24,
                    "content": {
                        "images": [{
                            "id": "shared-image",
                            "source_type": "input",
                            "file_path": "shared.png",
                            "shared_reference": True,
                        }],
                    },
                }],
            },
            {
                "id": "audio",
                "type": "audio",
                "segments": [{
                    "id": "audio-1",
                    "start_frame": 0,
                    "end_frame": 24,
                    "content": {
                        "media_type": "audio",
                        "source_type": "input",
                        "file_path": "voice.wav",
                        "shared_reference": True,
                    },
                }],
            },
            {
                "id": "video",
                "type": "video",
                "segments": [{
                    "id": "video-1",
                    "start_frame": 0,
                    "end_frame": 24,
                    "content": {
                        "media_type": "video",
                        "source_type": "input",
                        "file_path": "reference.mp4",
                        "shared_reference": True,
                    },
                }],
            },
        ],
    }

    patch_workflow_module.validate_track_data(track_data, recalculate=False)
    video_summary = patch_workflow_module.summarize_track(track_data["tracks"][2])
    assert video_summary["media"][0]["shared_reference"] is True


def test_shared_media_rejects_multiple_references_on_one_media_track(
    patch_workflow_module,
):
    track_data = {
        "frame_rate": 24,
        "total_length": 48,
        "tracks": [{
            "id": "video",
            "type": "video",
            "segments": [
                {
                    "id": f"video-{index}",
                    "start_frame": index * 24,
                    "end_frame": (index + 1) * 24,
                    "content": {
                        "media_type": "video",
                        "shared_reference": True,
                    },
                }
                for index in range(2)
            ],
        }],
    }

    with pytest.raises(
        patch_workflow_module.WorkflowError,
        match="Each audio/video track may contain only one shared reference",
    ):
        patch_workflow_module.validate_track_data(track_data, recalculate=False)


def test_template_metadata_matches_documented_asset():
    workflow = _template_workflow()

    assert workflow["version"] == 0.4
    assert len(workflow["nodes"]) == 21
    assert len(workflow["links"]) == 26
    assert hashlib.sha256(TEMPLATE.read_bytes().replace(b"\r\n", b"\n")).hexdigest() == (
        "ee7f41d90460a26f2dc486dd4b4246bc5c2a0a8f50f6c1328b1039e8a750e2c0"
    )
    assert [51, 11, 0, 13, 0, "MODEL"] in workflow["links"]
    assert [56, 13, 0, 26, 0, "MODEL"] in workflow["links"]


def test_project_settings_update_positional_and_named_widgets(patch_workflow_module, settings_check):
    result, report = patch_workflow_module.apply_plan(
        _template_workflow(),
        {"project": {"segment_start_number": 2}},
        settings_check,
    )

    project = _node(result, report["project_node_id"])
    assert project["widgets_values"][2] == 2
    assert project["widgets_values_named"]["segment_start_number"] == 2
    assert report["settings_changes"] == {"project": {"segment_start_number": {"from": 1, "to": 2}}}
    assert report["changed_node_ids"] == [report["project_node_id"]]


def test_project_sampling_mode_writes_its_option_widgets_by_name(patch_workflow_module, settings_check):
    result, report = patch_workflow_module.apply_plan(
        _template_workflow(),
        {"project": {"sampling_mode": "dual", "sampling_mode.upscale_by": 1.5,
                     "sampling_mode.enabled_tiling": True, "sampling_mode.enabled_tiling.tile_count": 4}},
        settings_check,
    )

    project = _node(result, report["project_node_id"])
    # The options follow the combo at the defaults this ComfyUI gives them: its installed upscaler.
    assert project["widgets_values"][7:] == [
        "dual", False, 1.5, "minimax_h3_latent_upscaler_3d_fp16.safetensors", False, False, "true", 4,
    ]
    assert project["widgets_values"] == list(project["widgets_values_named"].values())
    assert report["settings_changes"]["project"]["sampling_mode.upscale_by"] == {"from": None, "to": 1.5}
    inspected = patch_workflow_module.inspect_workflow(result, settings_check)["projects"][0]
    assert inspected["settings"]["sampling_mode"] == "dual"
    assert inspected["settings"]["sampling_mode.enabled_tiling.tile_count"] == 4


def test_project_sampling_mode_keeps_unchanged_options_and_drops_a_left_one(patch_workflow_module, settings_check):
    dual, _report = patch_workflow_module.apply_plan(
        _template_workflow(), {"project": {"sampling_mode": "dual", "sampling_mode.upscale_by": 2.0}}, settings_check)
    kept, report = patch_workflow_module.apply_plan(
        dual, {"project": {"sampling_mode.use_saved_1st_pass": True}}, settings_check)
    named = _node(kept, report["project_node_id"])["widgets_values_named"]
    assert (named["sampling_mode.use_saved_1st_pass"], named["sampling_mode.upscale_by"]) == (True, 2.0)

    single, report = patch_workflow_module.apply_plan(dual, {"project": {"sampling_mode": "single"}}, settings_check)
    project = _node(single, report["project_node_id"])
    assert project["widgets_values"][7:] == ["single"]
    assert not any(key.startswith("sampling_mode.") for key in project["widgets_values_named"])


@pytest.mark.parametrize("plan, error", [
    ({"sampling_mode": "triple"}, "sampling_mode must be one of: single, dual, selflift, passthrough"),
    ({"sampling_mode.upscale_by": 2}, "is a setting of sampling_mode dual"),
    ({"sampling_mode": "dual", "sampling_mode.upscale_by": 9}, "sampling_mode.upscale_by must be from 1.0 to 8.0"),
    ({"sampling_mode": "dual", "sampling_mode.enabled_tiling.tile_count": 3},
     "is a setting of sampling_mode.enabled_tiling true"),
    ({"steps": 4}, "has no setting steps"),
])
def test_project_settings_the_node_does_not_take_are_refused(patch_workflow_module, settings_check, plan, error):
    with pytest.raises(ValueError, match=error):
        patch_workflow_module.apply_plan(_template_workflow(), {"project": plan}, settings_check)


def test_editor_resolution_is_patched_by_name_with_the_track_data(patch_workflow_module, settings_check):
    workflow = _template_workflow()
    editor = next(node for node in workflow["nodes"] if node["type"] == "easy multiTrackEditor")
    track_data = json.loads(editor["widgets_values_named"]["track_data"])
    track_data["frame_rate"] = 24
    result, report = patch_workflow_module.apply_plan(workflow, {"editor": {
        "resolution": "width x height (custom)", "resolution.width": 1024, "resolution.height": 576,
        "track_data": track_data}}, settings_check)

    editor = _node(result, report["editor_node_id"])
    assert list(editor["widgets_values_named"])[:5] == [
        "resolution", "resolution.width", "resolution.height", "resolution.resize_method", "format"]
    assert editor["widgets_values"][:4] == ["width x height (custom)", 1024, 576, "stretch"]
    assert json.loads(editor["widgets_values_named"]["track_data"])["frame_rate"] == 24
    assert editor["widgets_values"] == list(editor["widgets_values_named"].values())
    with pytest.raises(ValueError, match="resolution.width must be a multiple of 8"):
        patch_workflow_module.apply_plan(workflow, {"editor": {
            "resolution": "width x height (custom)", "resolution.width": 1001}}, settings_check)


def test_an_old_object_form_setting_says_how_to_write_it(patch_workflow_module, settings_check):
    with pytest.raises(patch_workflow_module.WorkflowError, match='write each setting by its own name'):
        patch_workflow_module.apply_plan(_template_workflow(), {"project": {
            "sampling_mode": {"sampling_mode": "dual", "upscale_by": 1.5}}}, settings_check)


def test_widget_values_out_of_step_with_their_names_are_not_patched(patch_workflow_module, settings_check):
    workflow = _template_workflow()
    project = next(node for node in workflow["nodes"] if node["type"] == "easy multitrackProject")
    project["widgets_values"][7:] = ["dual", 1.5]

    with pytest.raises(patch_workflow_module.WorkflowError, match="do not match their names"):
        patch_workflow_module.apply_plan(workflow, {"project": {"seed": 1}}, settings_check)
