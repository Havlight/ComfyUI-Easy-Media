"""Per-task H3 reference plan: which media a task references, and in which order.

The plan is pure data computed from TRACK_DATA alone, so the Editor shows the same
``<Picture N>`` / ``<Video N>`` / ``<Audio N>`` numbering the sampler receives.
``frontend/src/lib/h3-references.ts`` mirrors it and ``tests/fixtures/h3_references.json``
checks both.

Images keep the task's list order; the Editor already puts shared images first.
Videos and audios list shared clips first, in track order and deduplicated by source,
then one window per overlapping track in track order. A task window runs from the
task start to the next task's start, or to the task end for the last task. Muted
audio tracks and audio-locked audio tracks give no reference. A task on an
audio-locked video track references only that track's window. Core labels each
video soundtrack ``<Audio N>`` before the standalone audio, so audio labels count
soundtracks first.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SLOT_PREFIX = "__slot__:"


@dataclass(frozen=True)
class MediaReference:
    key: str
    media_type: str
    # "shared" (whole source from its start), "track" (a timeline window) or
    # "locked" (the locked video's window, which also drives the video timing).
    kind: str
    track_id: str
    segment_id: str = ""
    start_frame: int = 0
    frames: int = 0
    soundtrack: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "media_type": self.media_type, "kind": self.kind,
                "track_id": self.track_id, "segment_id": self.segment_id,
                "start_frame": self.start_frame, "frames": self.frames, "soundtrack": self.soundtrack}


@dataclass(frozen=True)
class TaskReferencePlan:
    images: tuple[dict[str, Any], ...]
    videos: tuple[MediaReference, ...]
    audios: tuple[MediaReference, ...]

    def labels(self) -> dict[str, list[Any]]:
        """Return the prompt labels in the order core assigns them."""
        soundtracks: list[str | None] = []
        audio_number = 0
        for video in self.videos:
            if video.soundtrack:
                audio_number += 1
                soundtracks.append(f"Audio {audio_number}")
            else:
                soundtracks.append(None)
        return {
            "images": [f"Picture {i}" for i in range(1, len(self.images) + 1)],
            "videos": [f"Video {i}" for i in range(1, len(self.videos) + 1)],
            "video_soundtracks": soundtracks,
            "audios": [f"Audio {audio_number + i}" for i in range(1, len(self.audios) + 1)],
        }

    def as_dict(self) -> dict[str, Any]:
        return {"images": [dict(image) for image in self.images],
                "videos": [video.as_dict() for video in self.videos],
                "audios": [audio.as_dict() for audio in self.audios],
                "labels": self.labels()}


def media_identity(content: dict[str, Any]) -> tuple[str, str] | None:
    """Return the source identity that deduplicates shared clips (mirrors multitrack_media_identity)."""
    slot = content.get("slot_name") if isinstance(content.get("slot_name"), str) else None
    for key in ("file_path", "local_path", "url", "file_name"):
        value = content.get(key)
        if not slot and isinstance(value, str) and value.startswith(SLOT_PREFIX):
            slot = value[len(SLOT_PREFIX):]
    if not slot and content.get("source_type") == "slot" and isinstance(content.get("file_name"), str):
        slot = content["file_name"]
    if slot:
        return "slot", slot.removeprefix(SLOT_PREFIX)
    path = content.get("file_path") or content.get("local_path") or content.get("url") or content.get("file_name")
    return (str(content.get("source_type", "input")), str(path)) if path else None


def _frame(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _media_segments(track: dict[str, Any]) -> list[dict[str, Any]]:
    media_type = track.get("type")
    return [segment for segment in track.get("segments", [])
            if isinstance(segment, dict) and isinstance(segment.get("content"), dict)
            and segment["content"].get("media_type") == media_type]


def shared_reference_clip(track: dict[str, Any]) -> dict[str, Any] | None:
    """Return the track's shared clip; an audio-locked track has none."""
    if track.get("audio_locked") is True:
        return None
    return next((segment for segment in _media_segments(track)
                 if segment["content"].get("shared_reference") is True), None)


def _window_end(track: dict[str, Any], start: int, end: int, skip: dict[str, Any] | None) -> int | None:
    ends = [min(_frame(segment.get("end_frame"), -1), end) for segment in _media_segments(track)
            if segment is not skip and _frame(segment.get("end_frame"), -1) > start
            and _frame(segment.get("start_frame")) < end]
    return max(ends) if ends else None


def locked_video_track(tracks: list[Any], start: int, end: int) -> dict[str, Any] | None:
    """Return the first audio-locked video track with media inside the task."""
    for track in tracks:
        if (isinstance(track, dict) and track.get("type") == "video" and track.get("audio_locked") is True
                and _window_end(track, start, end, None) is not None):
            return track
    return None


def plan_task_references(
    tracks: list[Any],
    task: dict[str, Any],
    next_start: int | None = None,
    *,
    audio_only: bool = False,
) -> TaskReferencePlan:
    """Plan one task's references from the timeline tracks.

    ``task`` is a task segment with ``start_frame``, ``end_frame`` and ``content``;
    ``next_start`` is the next task's start frame, if any.
    """
    content = task.get("content") if isinstance(task.get("content"), dict) else {}
    images = tuple(image for image in content.get("images", [])
                   if isinstance(image, dict) and image.get("muted") is not True)
    start = _frame(task.get("start_frame"))
    task_end = max(start, _frame(task.get("end_frame"), start))
    window = max(start, next_start) if next_start is not None else task_end
    shared: dict[str, list[MediaReference]] = {"video": [], "audio": []}
    windows: dict[str, list[MediaReference]] = {"video": [], "audio": []}
    seen: dict[str, set[tuple[str, str]]] = {"video": set(), "audio": set()}
    for track in tracks:
        if not isinstance(track, dict) or track.get("type") not in {"video", "audio"}:
            continue
        media_type = str(track["type"])
        track_id = str(track.get("id", ""))
        muted = track.get("muted") is True
        if media_type == "audio" and (muted or track.get("audio_locked") is True):
            continue
        clip = shared_reference_clip(track)
        identity = media_identity(clip["content"]) if clip is not None else None
        if clip is not None and (identity is None or identity not in seen[media_type]):
            if identity is not None:
                seen[media_type].add(identity)
            shared[media_type].append(MediaReference(
                f"shared:{clip.get('id', track_id)}", media_type, "shared", track_id,
                str(clip.get("id", "")), 0, task_end - start, media_type == "video" and not muted))
        media_end = _window_end(track, start, window, clip)
        if media_end is None or media_end <= start:
            continue
        windows[media_type].append(MediaReference(
            f"track:{track_id}", media_type, "track", track_id, "", start, media_end - start,
            media_type == "video" and not muted))
    videos = shared["video"] + windows["video"]
    locked = None if audio_only else locked_video_track(tracks, start, task_end)
    if locked is not None:
        locked_id = str(locked.get("id", ""))
        videos = [MediaReference(f"locked:{locked_id}", "video", "locked", locked_id, "", start,
                                 task_end - start, locked.get("muted") is not True)]
    return TaskReferencePlan(images, tuple(videos), tuple(shared["audio"] + windows["audio"]))
