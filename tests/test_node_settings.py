"""A node's settings by ComfyUI's widget names, checked against its definition as /object_info gives it."""

import asyncio
import importlib.util
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

spec = importlib.util.spec_from_file_location("node_settings", Path(__file__).resolve().parents[1] / "utils/node_settings.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

TILING = ["COMFY_DYNAMICCOMBO_V3", {"options": [
    {"key": "false", "inputs": {"required": {}}},
    {"key": "true", "inputs": {"required": {"tile_count": ["INT", {"default": 2, "min": 2, "max": 8, "step": 2}]}}}]}]
PROJECT = {
    "required": {
        "tracks_info": ["TRACKS_INFO", {}],
        "project_name": ["STRING", {"default": ""}],
        "project_save": ["COMBO", {"default": "override", "options": ["new", "override"]}],
        "seed": ["INT", {"default": 42, "min": 0, "max": 2 ** 64 - 1, "control_after_generate": "fixed"}],
        "sampling_mode": ["COMFY_DYNAMICCOMBO_V3", {"options": [
            {"key": "single", "inputs": {"required": {}}},
            {"key": "dual", "inputs": {"required": {
                "use_saved_1st_pass": ["BOOLEAN", {"default": False}],
                "upscale_by": ["FLOAT", {"default": 1.25, "min": 1.0, "max": 8.0}],
                "upscale_model": [["None", "h3_upscaler.safetensors"], {"default": "h3_upscaler.safetensors"}],
                "enabled_tiling": TILING}}},
            {"key": "selflift", "inputs": {"required": {
                "lowres_scale": ["FLOAT", {"default": 0.6, "min": 0.25, "max": 1.0}], "enabled_tiling": TILING}}}]}],
    },
    "optional": {
        "sampler": ["SAMPLER", {"lazy": True, "rawLink": True}],
        "allow_vae_fallback": ["BOOLEAN", {"default": False}],
    },
}


def project(**named):
    values = {"project_name": "mv", "project_save": "new", "seed": 7, "control_after_generate": "randomize",
              "sampling_mode": "single", "allow_vae_fallback": False, "easy-media-sampling-preview": "", **named}
    return {"id": 15, "type": "easy multitrackProject", "widgets_values": list(values.values()),
            "widgets_values_named": values, "inputs": [{"name": "seed", "widget": {"name": "seed"}, "link": None}]}


def test_settings_are_read_by_name_without_sockets_or_the_frontends_own_widgets():
    result = module.change_settings(PROJECT, project(), {})
    assert result["changes"] == []
    assert result["settings"] == {"project_name": "mv", "project_save": "new", "seed": 7,
                                  "control_after_generate": "randomize", "sampling_mode": "single",
                                  "allow_vae_fallback": False}


def test_switching_an_option_puts_its_settings_after_the_combo_at_their_defaults():
    result = module.change_settings(PROJECT, project(), {
        "sampling_mode.upscale_by": "1.5", "sampling_mode": "dual", "sampling_mode.enabled_tiling": True,
        "sampling_mode.enabled_tiling.tile_count": "4", "seed": "123", "control_after_generate": "fixed"})
    # A combo comes before its options; text from a command line becomes the widget's type.
    assert result["changes"] == [["sampling_mode", "dual"], ["seed", 123], ["control_after_generate", "fixed"],
                                 ["sampling_mode.upscale_by", 1.5], ["sampling_mode.enabled_tiling", "true"],
                                 ["sampling_mode.enabled_tiling.tile_count", 4]]
    assert list(result["widgets_values_named"].items()) == [
        ("project_name", "mv"), ("project_save", "new"), ("seed", 123), ("control_after_generate", "fixed"),
        ("sampling_mode", "dual"), ("sampling_mode.use_saved_1st_pass", False), ("sampling_mode.upscale_by", 1.5),
        ("sampling_mode.upscale_model", "h3_upscaler.safetensors"), ("sampling_mode.enabled_tiling", "true"),
        ("sampling_mode.enabled_tiling.tile_count", 4), ("allow_vae_fallback", False),
        ("easy-media-sampling-preview", "")]
    assert result["widgets_values"] == list(result["widgets_values_named"].values())
    # Leaving an option drops its settings; the new one brings its own.
    after = module.change_settings(PROJECT, {**project(), "widgets_values_named": result["widgets_values_named"]},
                                   {"sampling_mode": "selflift"})
    assert [name for name in after["settings"] if name.startswith("sampling_mode")] == [
        "sampling_mode", "sampling_mode.lowres_scale", "sampling_mode.enabled_tiling"]


@pytest.mark.parametrize(("changes", "message"), [
    ({"steps": 4}, "has no setting steps; its settings are: project_name, project_save, seed"),
    ({"sampling_mode.upscale_by": 1.5}, "is a setting of sampling_mode dual, and sampling_mode is 'single'; "
                                        "set sampling_mode to dual as well"),
    ({"sampling_mode": "dual", "sampling_mode.lowres_scale": 0.5}, "is a setting of sampling_mode selflift"),
    ({"sampling_mode": "dual", "sampling_mode.enabled_tiling.tile_count": 4},
     "is a setting of sampling_mode.enabled_tiling true"),
    ({"sampler": "euler"}, "sampler is not a setting with a value"),
    ({"tracks_info": "{}"}, "tracks_info is not a setting with a value"),
    ({"easy-media-sampling-preview": "x"}, "has no setting easy-media-sampling-preview"),
    ({"project_save": "keep"}, "project_save must be one of: new, override"),
    ({"seed": -1}, "seed must be from 0 to"),
    ({"seed": 1.5}, "seed must be a whole number"),
    ({"seed": True}, "seed must be a whole number"),
    ({"sampling_mode": "dual", "sampling_mode.enabled_tiling": "true", "sampling_mode.enabled_tiling.tile_count": 3},
     "tile_count must be a multiple of 2"),
    ({"sampling_mode": "dual", "sampling_mode.upscale_by": "big"}, "sampling_mode.upscale_by must be a number"),
    ({"allow_vae_fallback": "yes"}, "allow_vae_fallback must be true or false"),
    ({"project_name": 3}, "project_name must be text"),
    ({"control_after_generate": "always"}, "control_after_generate must be one of: fixed, increment"),
])
def test_unknown_settings_and_values_say_what_the_node_takes(changes, message):
    with pytest.raises(module.SettingsError) as error:
        module.change_settings(PROJECT, project(), changes)
    assert message in str(error.value)


def test_a_setting_the_saved_values_lack_goes_where_the_node_lays_it_out():
    # Saved before the optional input existed: no allow_vae_fallback, no seed mode.
    named = {"project_name": "mv", "project_save": "new", "seed": 7, "sampling_mode": "dual",
             "sampling_mode.use_saved_1st_pass": False, "easy-media-sampling-preview": ""}
    node = {**project(), "widgets_values_named": named}
    result = module.change_settings(PROJECT, node, {"allow_vae_fallback": "true", "control_after_generate": "fixed",
                                                    "sampling_mode.upscale_by": 2})
    assert list(result["widgets_values_named"]) == [
        "project_name", "project_save", "seed", "control_after_generate", "sampling_mode",
        "sampling_mode.use_saved_1st_pass", "sampling_mode.upscale_by", "allow_vae_fallback", "easy-media-sampling-preview"]


def test_a_missing_option_setting_goes_where_its_option_lays_it_out():
    named = {"project_name": "mv", "sampling_mode": "dual", "sampling_mode.use_saved_1st_pass": False,
             "sampling_mode.upscale_model": "None", "sampling_mode.enabled_tiling": "true",
             "sampling_mode.enabled_tiling.tile_count": 4, "allow_vae_fallback": False}
    result = module.change_settings(PROJECT, {**project(), "widgets_values_named": named}, {"sampling_mode.upscale_by": 2})
    assert list(result["widgets_values_named"])[1:5] == [
        "sampling_mode", "sampling_mode.use_saved_1st_pass", "sampling_mode.upscale_by", "sampling_mode.upscale_model"]


@pytest.mark.parametrize(("changes", "message"), [
    ({"seed": "1" + "0" * 400}, "seed must be a whole number"),
    ({"seed": 2 ** 60}, "seed must be at most 9007199254740991"),
    ({"project_save": None}, "project_save needs one of its options"),
])
def test_values_the_page_cannot_hold_are_refused(changes, message):
    with pytest.raises(module.SettingsError, match=message):
        module.change_settings(PROJECT, project(), changes)


def test_the_error_lists_settings_the_saved_values_lack():
    named = {"project_name": "mv", "seed": 7, "sampling_mode": "single"}
    with pytest.raises(module.SettingsError, match="its settings are: project_name, seed, sampling_mode, project_save, "
                                                   "allow_vae_fallback"):
        module.change_settings(PROJECT, {**project(), "widgets_values_named": named}, {"steps": 4})


def test_a_setting_fed_by_a_link_or_a_node_without_named_values_is_refused():
    linked = {**project(), "inputs": [{"name": "seed", "widget": {"name": "seed"}, "link": 31}, {"widget": {}, "link": 5}]}
    with pytest.raises(module.SettingsError, match="seed comes from a link"):
        module.change_settings(PROJECT, linked, {"seed": 1})
    # Setting it to the value it has changes nothing.
    assert module.change_settings(PROJECT, linked, {"seed": 7})["changes"] == [["seed", 7]]
    # The page hides a linked number's mode.
    with pytest.raises(module.SettingsError, match="belongs to seed, which comes from a link"):
        module.change_settings(PROJECT, linked, {"control_after_generate": "fixed"})
    with pytest.raises(module.SettingsError, match="no named widget values"):
        module.change_settings(PROJECT, {"id": 15, "type": "easy multitrackProject", "widgets_values": []}, {})


def test_the_route_checks_a_node_against_its_registered_definition():
    async def scenario():
        app = web.Application()
        routes = web.RouteTableDef()
        module.register_settings_routes(routes, {"easy multitrackProject": PROJECT}.get)
        app.add_routes(routes)
        async with TestClient(TestServer(app)) as client:
            ok = await client.post("/easy-media/workflow/settings", json={"node": project(), "changes": {"seed": 5}})
            unknown = await client.post("/easy-media/workflow/settings", json={"node": {**project(), "type": "Other"}})
            bad = await client.post("/easy-media/workflow/settings", json={"node": project(), "changes": {"seed": -1}})
            return (ok.status, await ok.json()), (unknown.status, await unknown.json()), (bad.status, await bad.json())

    (ok_status, ok), (unknown_status, unknown), (bad_status, bad) = asyncio.run(scenario())
    assert ok_status == 200 and ok["changes"] == [["seed", 5]] and ok["settings"]["seed"] == 5
    assert unknown_status == 400 and unknown["error"] == "ComfyUI has no node type 'Other'"
    assert bad_status == 400 and "seed must be from 0" in bad["error"]
