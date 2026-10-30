"""The skill's automation scripts against a fake ComfyUI: plan conversion, probing, waiting, review."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/easy-media-multitrack-workflow/scripts"
TEMPLATE = ROOT / "skills/easy-media-multitrack-workflow/assets/templates/v1.3.0-blank-workflow.json"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


submissions = _load("workflow_submission_for_skill", ROOT / "utils/workflow_submission.py")


def converted_track_data() -> dict:
    """What the page returns for a two-task plan on the blank template."""
    editor = next(node for node in json.loads(TEMPLATE.read_text(encoding="utf-8"))["nodes"] if node["type"] == "easy multiTrackEditor")
    data = json.loads(editor["widgets_values"][4])
    content = {"media_type": "none", "task_mode": "default", "continuity_mode": "shot", "ref_image_size": "match", "images": []}
    data["tracks"][0]["segments"] = [
        {"id": "s01", "start_frame": 0, "end_frame": 124, "color": "", "content": {**content, "user_prompt": "One"}},
        {"id": "s02", "start_frame": 124, "end_frame": 248, "color": "", "content": {**content, "user_prompt": "Two"}},
    ]
    data["total_length"] = 248
    data["h3_native"] = {"version": 2}
    return data


class FakeComfy:
    """The routes the scripts call, recording what they receive."""

    def __init__(self, tmp_path: Path):
        self.server_files = {"C:/mv/alice.png"}
        self.uploads: list[tuple[str, str, bytes]] = []
        self.status_requests: list[dict] = []
        self.converted_plans: list[dict] = []
        self.history: dict = {}
        self.tmp_path = tmp_path

    def routes(self) -> web.RouteTableDef:
        routes = web.RouteTableDef()
        submissions.register_workflow_routes(routes, lambda _: True)

        @routes.post("/easy-media/plan/resolve-media")
        async def resolve(request):
            results = []
            for item in (await request.json())["items"]:
                path = item.get("file") or item.get("local")
                if path is not None:
                    if path in self.server_files and "file" in item:
                        name = Path(path).name
                        results.append({"source_type": "input", "file_path": f"easy_media/imports/feed/{name}", "file_name": name})
                    else:
                        results.append({"error": f"{path} was not found"})
                    continue
                name = Path(item["input"]).name
                probe = {"duration": 92.3} if item.get("probe") else {}
                results.append({"source_type": "input", "file_path": item["input"], "file_name": name, **probe})
            return web.json_response({"results": results})

        @routes.post("/upload/image")
        async def upload(request):
            form = await request.post()
            self.uploads.append((form["subfolder"], form["image"].filename, form["image"].file.read()))
            return web.json_response({"name": form["image"].filename, "subfolder": form["subfolder"], "type": "input"})

        @routes.post("/easy-media/project/timeline-status")
        async def timeline_status(request):
            body = await request.json()
            self.status_requests.append(body)
            tasks = body["tracks_info"]["tracks"][0]["segments"]
            return web.json_response({"project_name": body["project_name"], "tasks": [
                {"segment_id": task["id"], "index": index, "status": "saved" if task["id"] == "s01" else "missing"}
                for index, task in enumerate(tasks)]})

        @routes.get("/easy-media/project")
        async def project(request):
            return web.json_response({"project_name": request.query["project_name"], "clips": [
                {"id": "s01", "index": 0, "file_path": "easy_media/projects/mv/s01.mp4"},
                {"id": "s02", "index": 1, "file_path": "easy_media/projects/mv/s02.mp4"},
            ]})

        @routes.post("/easy-media/review/contact-sheet")
        async def contact_sheet(request):
            items = (await request.json())["items"]
            return web.json_response({"results": [
                {"filename": f"{Path(item['file_path']).stem}.jpg", "subfolder": "easy_media_review", "type": "temp"} for item in items]})

        @routes.get("/view")
        async def view(request):
            return web.Response(body=f"jpeg:{request.query['filename']}".encode())

        return routes

    async def browser(self, client: TestClient, conversion: dict) -> None:
        """A ComfyUI page that converts the first plan it is given."""
        body = {"client_id": "page-1", "session_id": "socket-1"}
        while True:
            job = (await (await client.post("/easy-media/workflow/poll", json=body)).json())["job"]
            if job:
                self.converted_plans.append(job["plan"])
                await client.post(f'/easy-media/workflow/submissions/{job["request_id"]}/result',
                                  json={"claim_token": job["claim_token"], "status": "converted", "conversion": conversion})
                return
            await asyncio.sleep(0.01)


async def run_script(*args: str) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        sys.executable, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()
    return process.returncode, stdout.decode("utf-8"), stderr.decode("utf-8")


def test_convert_places_files_converts_on_the_page_and_starts_after_saved_tasks(tmp_path):
    fake = FakeComfy(tmp_path)
    song = tmp_path / "song.wav"
    song.write_bytes(b"wav-bytes")
    plan = {"format": "easy-media-h3-plan", "version": 1, "tasks": [
        {"id": "s01", "seconds": 5, "images": ["C:/mv/alice.png"]},
        {"id": "s02", "seconds": 5},
    ], "tracks": [{"type": "audio", "audio_locked": True, "clips": [{"file": str(song), "at": 1}]}]}
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    conversion = {"track_data": converted_track_data(), "errors": [], "warnings": ["Task 2: Timing adjusted."], "tasks": [
        {"index": 1, "id": "s01", "change": "added", "seconds": 5.167, "mode": "default", "continuity": "shot", "images": 1, "videos": 0, "audios": 0,
         "labels": {"<Picture 1>": "first frame: alice.png"}},
        {"index": 2, "id": "s02", "change": "added", "seconds": 5.167, "mode": "default", "continuity": "shot", "images": 0, "videos": 0, "audios": 0},
    ], "tracks": []}

    async def scenario():
        app = web.Application()
        app.add_routes(fake.routes())
        async with TestClient(TestServer(app)) as client:
            await client.post("/easy-media/workflow/poll", json={"client_id": "page-1", "session_id": "socket-1"})
            script = run_script(str(SCRIPTS / "plan_workflow.py"), "convert", str(TEMPLATE), str(plan_path),
                                "--project-name", "mv-alice", "--url", str(client.make_url("")))
            return (await asyncio.wait_for(asyncio.gather(script, fake.browser(client, conversion)), timeout=30))[0]

    code, stdout, stderr = asyncio.run(scenario())
    assert code == 0, stderr
    report = json.loads(stdout)
    # The server could read alice.png itself; song.wav only exists where the agent runs.
    sent = fake.converted_plans[0]
    assert sent["tasks"][0]["images"] == [{"input": "easy_media/imports/feed/alice.png"}]
    [(subfolder, name, data)] = fake.uploads
    assert subfolder.startswith("easy_media/imports/") and name == "song.wav" and data == b"wav-bytes"
    assert sent["tracks"][0]["clips"] == [{"input": f"{subfolder}/song.wav", "at": 1}]
    assert report["uploaded"] == [str(song)]
    # s01 already has its saved result, so the run starts at task 2 and runs the rest.
    assert report["run"] == {"start": 2, "count": -1}
    assert [task["saved"] for task in report["tasks"]] == [True, False]
    assert [task["labels"] for task in report["tasks"]] == [{"<Picture 1>": "first frame: alice.png"}, {}]
    assert fake.status_requests[0]["project_name"] == "mv-alice"
    assert fake.status_requests[0]["project_snapshot"]["recipe"] == {"sampling_mode": "single", "first_pass_only": False}
    workflow = json.loads((tmp_path / "plan.workflow.json").read_text(encoding="utf-8"))
    project = next(node for node in workflow["nodes"] if node["type"] == "easy multitrackProject")
    editor = next(node for node in workflow["nodes"] if node["type"] == "easy multiTrackEditor")
    assert project["widgets_values"][:4] == ["mv-alice", "override", 2, -1]
    assert [task["id"] for task in json.loads(editor["widgets_values"][4])["tracks"][0]["segments"]] == ["s01", "s02"]


def test_convert_reports_plan_errors_without_writing_a_workflow(tmp_path):
    fake = FakeComfy(tmp_path)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({"version": 1, "tasks": [{"seconds": 5, "images": ["D:/nowhere.png"]}]}), encoding="utf-8")

    async def scenario():
        app = web.Application()
        app.add_routes(fake.routes())
        async with TestClient(TestServer(app)) as client:
            await client.post("/easy-media/workflow/poll", json={"client_id": "page-1", "session_id": "socket-1"})
            return await run_script(str(SCRIPTS / "plan_workflow.py"), "convert", str(TEMPLATE), str(plan_path),
                                    "--project-name", "mv", "--url", str(client.make_url("")))

    code, stdout, stderr = asyncio.run(scenario())
    assert code == 2, stderr
    assert json.loads(stdout) == {"errors": ["task 1 images: D:/nowhere.png: D:/nowhere.png was not found"]}
    assert not (tmp_path / "plan.workflow.json").exists()


def test_probe_reads_lengths_and_uploads_what_the_server_cannot_see(tmp_path):
    fake = FakeComfy(tmp_path)
    song = tmp_path / "song.wav"
    song.write_bytes(b"wav")

    async def scenario():
        app = web.Application()
        app.add_routes(fake.routes())
        async with TestClient(TestServer(app)) as client:
            return await run_script(str(SCRIPTS / "plan_workflow.py"), "probe", str(song), "--url", str(client.make_url("")))

    code, stdout, stderr = asyncio.run(scenario())
    assert code == 0, stderr
    assert json.loads(stdout) == [{"file": str(song), "media_type": "audio", "seconds": 92.3}]
    assert len(fake.uploads) == 1


def test_review_is_off_unless_configured_or_asked_for(tmp_path):
    fake = FakeComfy(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text("AGENT_FRAME_REVIEW: false\n", encoding="utf-8")

    async def scenario():
        app = web.Application()
        app.add_routes(fake.routes())
        async with TestClient(TestServer(app)) as client:
            url = str(client.make_url(""))
            off = await run_script(str(SCRIPTS / "review_frames.py"), "--project", "mv", "--config", str(config), "--url", url)
            asked = await run_script(str(SCRIPTS / "review_frames.py"), "--project", "mv", "--tasks", "s02", "--user-asked",
                                     "--out", str(tmp_path / "sheets"), "--config", str(config), "--url", url)
            return off, asked

    (off_code, _, off_err), (code, stdout, stderr) = asyncio.run(scenario())
    assert off_code == 2 and "Frame review is off" in off_err
    assert code == 0, stderr
    [sheet] = json.loads(stdout)
    assert sheet["task"] == "s02" and sheet["number"] == 2
    assert Path(sheet["image"]).read_bytes() == b"jpeg:s02.jpg"


@pytest.fixture
def submit_module():
    return _load("submit_workflow_for_wait", SCRIPTS / "submit_workflow.py")


def test_wait_reports_progress_then_the_finished_outputs(submit_module, monkeypatch):
    answers = {
        "/history/p1": [{}, {}, {"p1": {"status": {"status_str": "success", "completed": True}, "outputs": {
            "9": {"gifs": [{"filename": "mv_00001.mp4", "subfolder": "mv", "type": "output"}], "text": ["done"]}}}}],
        "/queue": [{"queue_running": [[0, "p1"]], "queue_pending": []}, {"queue_running": [], "queue_pending": [[1, "x"], [2, "p1"]]}],
    }
    monkeypatch.setattr(submit_module, "request_json", lambda _base, path, *_args, **_kwargs: answers[path].pop(0))
    monkeypatch.setattr(submit_module.time, "sleep", lambda _seconds: None)

    assert submit_module.generation_state("http://comfy", "p1") == {"state": "running"}
    assert submit_module.generation_state("http://comfy", "p1") == {"state": "pending", "ahead": 1}
    assert submit_module.wait_for_generation("http://comfy", "p1", 60) == {"state": "success", "outputs": [
        {"node": "9", "kind": "gifs", "filename": "mv_00001.mp4", "subfolder": "mv", "type": "output"}]}


def test_wait_reports_the_node_that_failed(submit_module, monkeypatch):
    entry = {"p1": {"status": {"status_str": "error", "completed": False, "messages": [
        ["execution_start", {}], ["execution_error", {"node_type": "easy multitrackProject", "exception_message": "Out of memory"}]]}}}
    monkeypatch.setattr(submit_module, "request_json", lambda *_args, **_kwargs: entry)
    assert submit_module.generation_state("http://comfy", "p1") == {
        "state": "error", "node_type": "easy multitrackProject", "message": "Out of memory"}
