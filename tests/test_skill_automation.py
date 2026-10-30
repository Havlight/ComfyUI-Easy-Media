"""The skill's automation scripts against a fake ComfyUI and page: tabs, plans, runs, probing, review."""

import asyncio
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/easy-media-multitrack-workflow/scripts"
TEMPLATE = ROOT / "skills/easy-media-multitrack-workflow/assets/templates/v1.3.0-blank-workflow.json"
TAB = "workflows/mv.json"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


submissions = _load("workflow_submission_for_skill", ROOT / "utils/workflow_submission.py")
records = _load("agent_records_for_skill", ROOT / "utils/agent_records.py")


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


CONVERSION = {"track_data": converted_track_data(), "errors": [], "warnings": ["Task 2: Timing adjusted."], "tasks": [
    {"index": 1, "id": "s01", "change": "added", "seconds": 5.167, "mode": "default", "continuity": "shot", "images": 1, "videos": 0, "audios": 0,
     "labels": {"<Picture 1>": "first frame: alice.png"}},
    {"index": 2, "id": "s02", "change": "added", "seconds": 5.167, "mode": "default", "continuity": "shot", "images": 0, "videos": 0, "audios": 0},
], "tracks": []}


class FakePage:
    """A ComfyUI page with one open tab, doing what the frontend bridge does with each job."""

    def __init__(self, workflow: dict):
        self.workflow = workflow
        self.jobs: list[dict] = []

    def _node(self, node_type: str) -> dict:
        return next(node for node in self.workflow["nodes"] if node["type"] == node_type)

    def plan(self) -> dict:
        """The plan the tab's Editor exports (reduced to ids and prompts)."""
        data = json.loads(self._node("easy multiTrackEditor")["widgets_values"][4])
        return {"format": "easy-media-h3-plan", "version": 1, "tasks": [
            {"id": segment["id"], "prompt": segment["content"].get("user_prompt", "")} for segment in data["tracks"][0]["segments"]]}

    def answer(self, job: dict) -> dict:
        kind = job.get("kind", "workflow")
        if kind == "workflow":
            self.workflow = job["workflow"]
            return {"status": "loaded", "tab": TAB}
        if kind == "tabs":
            tabs = [{"key": TAB, "name": "mv", "active": True, "modified": False, "temporary": False}]
            if not job.get("tab"):
                return {"status": "read", "tabs": tabs}
            return {"status": "read", "tabs": tabs, "tab": {"key": TAB, "name": "mv", "workflow": json.loads(json.dumps(self.workflow)),
                                                            "plans": {"14": self.plan()}}}
        if kind == "plan":
            return {"status": "converted", "conversion": CONVERSION}
        if job.get("changes"):
            changes = job["changes"]
            self._node("easy multiTrackEditor")["widgets_values"][4] = json.dumps(changes["track_data"])
            project = self._node("easy multitrackProject")["widgets_values"]
            for name, index in (("project_name", 0), ("segment_start_number", 2), ("segment_count", 3)):
                project[index] = changes["project"][name]
        return {"status": "queued", "prompt_id": "p1"} if job["queue"] else {"status": "applied"}

    async def serve(self, client: TestClient, jobs: int) -> None:
        body = {"client_id": "page-1", "session_id": "socket-1"}
        while len(self.jobs) < jobs:
            job = (await (await client.post("/easy-media/workflow/poll", json=body)).json())["job"]
            if job:
                self.jobs.append(job)
                await client.post(f'/easy-media/workflow/submissions/{job["request_id"]}/result',
                                  json={"claim_token": job["claim_token"], **self.answer(job)})
            await asyncio.sleep(0.01)


class FakeComfy:
    """The routes the scripts call, recording what they receive."""

    def __init__(self, tmp_path: Path):
        self.server_files = {"C:/mv/alice.png"}
        self.uploads: list[tuple[str, str, bytes]] = []
        self.status_requests: list[dict] = []
        # The Project's check of each task's saved result; tasks not listed have none.
        self.statuses = {"s01": "saved"}
        self.records = tmp_path / "records"
        self.tmp_path = tmp_path

    def routes(self) -> web.RouteTableDef:
        routes = web.RouteTableDef()
        submissions.register_workflow_routes(routes, lambda _: True)
        records.register_record_routes(routes, lambda: self.records, lambda name: re.sub(r"[^A-Za-z0-9_-]", "_", str(name)))

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
                {"segment_id": task["id"], "index": index, "status": self.statuses.get(task["id"], "missing")}
                for index, task in enumerate(tasks)]})

        @routes.get("/easy-media/project")
        async def project(request):
            return web.json_response({"project_name": request.query["project_name"], "clips": [
                {"id": "s01", "index": 0, "file_path": "easy_media/projects/mv/s01.mp4"},
                {"id": "s02", "index": 1, "file_path": "easy_media/projects/mv/s02.mp4"},
            ]})

        @routes.get("/history/p1")
        async def history(_request):
            return web.json_response({"p1": {"status": {"status_str": "success", "completed": True}, "outputs": {
                "17": {"gifs": [{"filename": "mv.mp4", "subfolder": "easy_media/projects/mv", "type": "output"}]}}}})

        @routes.post("/easy-media/review/contact-sheet")
        async def contact_sheet(request):
            items = (await request.json())["items"]
            return web.json_response({"results": [
                {"filename": f"{Path(item['file_path']).stem}.jpg", "subfolder": "easy_media_review", "type": "temp"} for item in items]})

        @routes.get("/view")
        async def view(request):
            return web.Response(body=f"jpeg:{request.query['filename']}".encode())

        return routes

    def record(self, project: str, path: str) -> object:
        return json.loads((self.records / project / path).read_text(encoding="utf-8"))


async def run_script(*args: str) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        sys.executable, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()
    return process.returncode, stdout.decode("utf-8"), stderr.decode("utf-8")


def with_comfy(fake: FakeComfy, page: FakePage, jobs: int, *script: str) -> tuple[int, str, str]:
    """Run a script while the page answers the given number of jobs."""
    async def scenario():
        app = web.Application()
        app.add_routes(fake.routes())
        async with TestClient(TestServer(app)) as client:
            await client.post("/easy-media/workflow/poll", json={"client_id": "page-1", "session_id": "socket-1"})
            run = run_script(*script, "--url", str(client.make_url("")))
            if not jobs:
                return await asyncio.wait_for(run, timeout=30)
            return (await asyncio.wait_for(asyncio.gather(run, page.serve(client, jobs)), timeout=30))[0]
    return asyncio.run(scenario())


def template_page() -> FakePage:
    return FakePage(json.loads(TEMPLATE.read_text(encoding="utf-8")))


def write_plan(tmp_path: Path, plan: dict) -> Path:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def test_apply_converts_on_the_page_and_sets_the_tab_from_the_first_task_to_run(tmp_path):
    fake, page = FakeComfy(tmp_path), template_page()
    song = tmp_path / "song.wav"
    song.write_bytes(b"wav-bytes")
    plan_path = write_plan(tmp_path, {"format": "easy-media-h3-plan", "version": 1, "tasks": [
        {"id": "s01", "seconds": 5, "images": ["C:/mv/alice.png"]},
        {"id": "s02", "seconds": 5},
    ], "tracks": [{"type": "audio", "audio_locked": True, "clips": [{"file": str(song), "at": 1}]}]})

    code, stdout, stderr = with_comfy(fake, page, 3, str(SCRIPTS / "plan_workflow.py"), "apply", str(plan_path),
                                      "--tab", TAB, "--project-name", "mv-alice")
    assert code == 0, stderr
    report = json.loads(stdout)
    read, convert, apply = page.jobs
    assert read["kind"] == "tabs" and read["tab"] == TAB
    # The server could read alice.png itself; song.wav only exists where the agent runs.
    sent = convert["plan"]
    assert sent["tasks"][0]["images"] == [{"input": "easy_media/imports/feed/alice.png"}]
    [(subfolder, name, data)] = fake.uploads
    assert subfolder.startswith("easy_media/imports/") and name == "song.wav" and data == b"wav-bytes"
    assert sent["tracks"][0]["clips"] == [{"input": f"{subfolder}/song.wav", "at": 1}]
    assert report["uploaded"] == [str(song)]
    # s01's saved result matches it, so the run starts at task 2 and runs the rest.
    assert report["run"] == {"start": 2, "count": -1}
    assert [task["status"] for task in report["tasks"]] == ["saved", "missing"]
    assert report["warnings"] == ["Task 2: Timing adjusted."]
    assert [task["labels"] for task in report["tasks"]] == [{"<Picture 1>": "first frame: alice.png"}, {}]
    assert fake.status_requests[0]["project_name"] == "mv-alice"
    assert fake.status_requests[0]["project_snapshot"]["recipe"] == {"sampling_mode": "single", "first_pass_only": False}
    # The tab changed only where the agent writes; no workflow file or record was written.
    assert apply["kind"] == "apply" and apply["queue"] is False
    assert apply["changes"]["project"] == {"project_name": "mv-alice", "segment_start_number": 2, "segment_count": -1}
    assert [task["id"] for task in apply["changes"]["track_data"]["tracks"][0]["segments"]] == ["s01", "s02"]
    assert not list(tmp_path.glob("*.workflow.json")) and not fake.records.exists()


def test_apply_runs_the_range_the_agent_sets_and_warns_about_tasks_left_out(tmp_path):
    fake, page = FakeComfy(tmp_path), template_page()
    fake.statuses = {"s01": "edited", "s02": "parent_changed"}
    plan_path = write_plan(tmp_path, {"version": 1, "tasks": [{"id": "s01", "seconds": 5}, {"id": "s02", "seconds": 5}]})

    code, stdout, stderr = with_comfy(fake, page, 3, str(SCRIPTS / "plan_workflow.py"), "apply", str(plan_path),
                                      "--tab", TAB, "--project-name", "mv-alice", "--start", "1", "--count", "1")
    assert code == 0, stderr
    report = json.loads(stdout)
    assert report["run"] == {"start": 1, "count": 1}
    assert [task["status"] for task in report["tasks"]] == ["edited", "parent_changed"]
    assert report["warnings"][-1] == "Tasks 2 do not match their saved results but are outside the run range."
    assert page.jobs[-1]["changes"]["project"] == {"project_name": "mv-alice", "segment_start_number": 1, "segment_count": 1}


def test_read_reports_the_tab_and_writes_its_plan(tmp_path):
    fake, page = FakeComfy(tmp_path), template_page()
    page._node("easy multiTrackEditor")["widgets_values"][4] = json.dumps(converted_track_data())
    out = tmp_path / "current.json"
    code, stdout, stderr = with_comfy(fake, page, 1, str(SCRIPTS / "plan_workflow.py"), "read", "--tab", TAB, "-o", str(out))
    assert code == 0, stderr
    report = json.loads(stdout)
    assert report["project"]["project_name"] == "" and report["editor"]["format"] == "MiniMax"
    # Whether each task's saved result still matches the tab, as the Project checks it.
    assert [(task["id"], task["status"]) for task in report["tasks"]] == [("s01", "saved"), ("s02", "missing")]
    assert fake.status_requests[0]["tracks_info"] == converted_track_data()
    assert json.loads(out.read_text(encoding="utf-8")) == page.plan()


def test_apply_reports_plan_errors_without_touching_the_tab(tmp_path):
    fake, page = FakeComfy(tmp_path), template_page()
    plan_path = write_plan(tmp_path, {"version": 1, "tasks": [{"seconds": 5, "images": ["D:/nowhere.png"]}]})
    code, stdout, stderr = with_comfy(fake, page, 1, str(SCRIPTS / "plan_workflow.py"), "apply", str(plan_path),
                                      "--tab", TAB, "--project-name", "mv")
    assert code == 2, stderr
    assert json.loads(stdout) == {"errors": ["task 1 images: D:/nowhere.png: D:/nowhere.png was not found"]}
    assert [job["kind"] for job in page.jobs] == ["tabs"]


def test_running_a_tab_records_the_run_and_waits_for_it(tmp_path):
    fake, page = FakeComfy(tmp_path), template_page()
    page._node("easy multiTrackEditor")["widgets_values"][4] = json.dumps(converted_track_data())
    page._node("easy multitrackProject")["widgets_values"][:4] = ["mv-alice", "override", 2, -1]
    code, stdout, stderr = with_comfy(fake, page, 2, str(SCRIPTS / "submit_workflow.py"), "--tab", TAB, "--until-done")
    assert code == 0, stderr
    result = json.loads(stdout)
    assert result["status"] == "queued" and result["generation"]["state"] == "success"
    assert result["record"] == {"project_name": "mv-alice", "run": 1}
    read, run = page.jobs
    assert run["kind"] == "apply" and run["queue"] is True and run["changes"] is None
    [entry] = fake.record("mv-alice", "history.json")
    assert entry["run"] == 1 and entry["prompt_id"] == "p1" and "state" not in entry
    assert (entry["start"], entry["count"], entry["plan"]) == (2, -1, "plans/run-001.json")
    assert entry["resolution"]["resolution"] == "width x height (megapixels)"
    assert fake.record("mv-alice", "plans/run-001.json") == page.plan()


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
            kept = await run_script(str(SCRIPTS / "review_frames.py"), "--project", "mv", "--tasks", "s01", "--user-asked",
                                    "--config", str(config), "--url", url)
            return off, asked, kept

    (off_code, _, off_err), (code, stdout, stderr), (kept_code, kept_out, kept_err) = asyncio.run(scenario())
    assert off_code == 2 and "Frame review is off" in off_err
    assert code == 0, stderr
    [sheet] = json.loads(stdout)
    assert sheet["task"] == "s02" and sheet["number"] == 2
    assert Path(sheet["image"]).read_bytes() == b"jpeg:s02.jpg"
    # Without --out, sheets go to the project's agent records when this machine can reach them.
    assert kept_code == 0, kept_err
    assert Path(json.loads(kept_out)[0]["image"]) == fake.records / "mv/review/01-s01.jpg"


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
