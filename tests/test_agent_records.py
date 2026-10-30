"""The agent records folder: small text files beside a project, written through the server."""

import asyncio
import importlib.util
import re
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

spec = importlib.util.spec_from_file_location("agent_records", Path(__file__).resolve().parents[1] / "utils/agent_records.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def safe_name(name: object) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(name))


def test_records_are_written_read_and_listed_inside_the_project_folder(tmp_path):
    records = module.AgentRecords(lambda: tmp_path, safe_name)
    assert records.read("mv alice", "history.json") == {"path": str(tmp_path / "mv_alice/history.json"), "text": None}
    assert records.list("mv alice") == {"directory": str(tmp_path / "mv_alice"), "files": []}
    records.write("mv alice", "README.md", "# MV\n")
    records.write("mv alice", "plans/run-001.json", "{}")
    assert records.read("mv alice", "README.md")["text"] == "# MV\n"
    assert records.list("mv alice")["files"] == ["README.md", "plans/run-001.json"]
    assert not list((tmp_path / "mv_alice").glob(".record-*"))


@pytest.mark.parametrize("path", ["../escape.md", "plans/../../x.json", "notes.txt", "plans/a/b.json", "script.py", ""])
def test_records_refuse_other_paths(tmp_path, path):
    records = module.AgentRecords(lambda: tmp_path, safe_name)
    with pytest.raises(ValueError):
        records.write("mv", path, "x")
    assert not any(tmp_path.rglob("*.*"))


def test_record_routes_and_their_guards(tmp_path):
    async def scenario():
        routes = web.RouteTableDef()
        module.register_record_routes(routes, lambda: tmp_path, safe_name)
        app = web.Application()
        app.add_routes(routes)
        async with TestClient(TestServer(app)) as client:
            response = await client.post("/easy-media/agent/records", json={"project": "mv", "path": "README.md", "text": "notes"})
            assert (await response.json())["path"] == str(tmp_path / "mv/README.md")
            response = await client.get("/easy-media/agent/records", params={"project": "mv", "path": "README.md"})
            assert (await response.json())["text"] == "notes"
            response = await client.get("/easy-media/agent/records", params={"project": "mv"})
            assert (await response.json())["files"] == ["README.md"]
            response = await client.post("/easy-media/agent/records", json={"project": "mv", "path": "../x.md", "text": "x"})
            assert response.status == 400
            response = await client.post("/easy-media/agent/records", json={"project": "mv", "path": "README.md", "text": "x"},
                                         headers={"Origin": "https://evil.example"})
            assert response.status == 403
            response = await client.post("/easy-media/agent/records", data="project=mv")
            assert response.status == 415
    asyncio.run(scenario())
    assert (tmp_path / "mv/README.md").read_text(encoding="utf-8") == "notes"
