"""Per-task H3 reference plan: which media a task references, and in which order.

The plan is pure data computed from TRACK_DATA alone, so the Editor shows the same
``<Picture N>`` / ``<Video N>`` / ``<Audio N>`` numbering the sampler receives.
``frontend/src/lib/h3-references.ts`` mirrors it and ``tests/fixtures/h3_references.json``
checks both.

Images keep the task's list order; the Editor already puts shared images first.

Videos and audios come from the timeline and, in ref and edit tasks, from the task's
own ``videos`` / ``audios`` lists. Timeline references are shared clips (deduplicated
by source, in track order) and then one window per overlapping track in track order.
A window runs from the task start to the next task's start, or to the task end for the
last task. Muted audio tracks and audio-locked audio tracks give no reference.

A task list holds uploads and timeline placeholders (``source_type: "track"`` with a
``track_id``, plus ``segment_id`` for a shared clip). A placeholder places, mutes or
adjusts a timeline reference; timeline references without one come first, in timeline
order, then the list in its order. At most three videos and three audios are used.

Audio fitting cuts a reference to the task window (timeline, the default) or to the
task length (shared clips and uploads); without it the reference takes up to 15 s of
its source. Videos always fit. A task on an audio-locked video track references only
that track's window. Core labels each video soundtrack ``<Audio N>`` before the
standalone audio, so audio labels count soundtracks first.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

SLOT_PREFIX = "__slot__:"
TRACK_SOURCE = "track"
CUSTOM_REFERENCE_MODES = frozenset({"ref", "edit"})
MEDIA_REFERENCE_LIMIT = 3
MAX_REFERENCE_SECONDS = 15
SOURCE_FIELDS = ("source_type", "file_path", "local_path", "url", "slot_name", "file_name")


@dataclass(frozen=True)
class MediaReference:
    key: str
    media_type: str
    # "shared" (a timeline shared clip from its start), "track" (a timeline window),
    # "locked" (the locked video's window, which also drives the video timing) or
    # "upload" (a task's own file from its start).
    kind: str
    track_id: str
    segment_id: str = ""
    start_frame: int = 0
    frames: int = 0
    soundtrack: bool = False
    # Fitted references cover exactly ``frames``; others take at most ``frames``.
    fit: bool = True
    # An upload's source fields, as (field, value) pairs.
    source: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "media_type": self.media_type, "kind": self.kind,
                "track_id": self.track_id, "segment_id": self.segment_id,
                "start_frame": self.start_frame, "frames": self.frames, "soundtrack": self.soundtrack,
                "fit": self.fit, "source": dict(self.source)}


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


def placeholder_key(item: dict[str, Any]) -> str | None:
    """Return the timeline reference key a task-list placeholder stands for."""
    if item.get("source_type") != TRACK_SOURCE:
        return None
    segment_id, track_id = item.get("segment_id"), item.get("track_id")
    if isinstance(segment_id, str) and segment_id:
        return f"shared:{segment_id}"
    return f"track:{track_id}" if isinstance(track_id, str) and track_id else None


def reference_cap_frames(frame_rate: float) -> int:
    return max(1, round(MAX_REFERENCE_SECONDS * frame_rate))


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


def _task_media(content: dict[str, Any], media_type: str) -> list[dict[str, Any]]:
    value = content.get("videos" if media_type == "video" else "audios")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _upload_source(item: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple((field, item[field]) for field in SOURCE_FIELDS
                 if isinstance(item.get(field), str) and item[field])


def has_upload_source(item: dict[str, Any]) -> bool:
    return any(isinstance(item.get(field), str) and item[field]
               for field in ("file_path", "local_path", "url", "slot_name"))


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
    frame_rate: float = 24.0,
) -> TaskReferencePlan:
    """Plan one task's references from the timeline tracks and the task's own lists.

    ``task`` is a task segment with ``start_frame``, ``end_frame`` and ``content``;
    ``next_start`` is the next task's start frame, if any.
    """
    content = task.get("content") if isinstance(task.get("content"), dict) else {}
    images = tuple(image for image in content.get("images", [])
                   if isinstance(image, dict) and image.get("muted") is not True)
    start = _frame(task.get("start_frame"))
    task_end = max(start, _frame(task.get("end_frame"), start))
    task_frames = task_end - start
    window = max(start, next_start) if next_start is not None else task_end
    cap = reference_cap_frames(frame_rate)
    custom = str(content.get("task_mode", "default")) in CUSTOM_REFERENCE_MODES
    items = {media_type: _task_media(content, media_type) if custom else [] for media_type in ("video", "audio")}
    placed: dict[str, dict[str, dict[str, Any]]] = {"video": {}, "audio": {}}
    for media_type, media_items in items.items():
        for item in media_items:
            key = placeholder_key(item)
            if key is not None:
                placed[media_type].setdefault(key, item)

    def fits(media_type: str, key: str) -> bool:
        return media_type == "video" or placed[media_type].get(key, {}).get("fit") is not False

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
            key = f"shared:{clip.get('id', track_id)}"
            fit = fits(media_type, key)
            shared[media_type].append(MediaReference(
                key, media_type, "shared", track_id, str(clip.get("id", "")), 0,
                task_frames if fit else cap, media_type == "video" and not muted, fit))
        key = f"track:{track_id}"
        fit = fits(media_type, key)
        media_end = _window_end(track, start, window if fit else max(window, start + cap), clip)
        if media_end is None or media_end <= start:
            continue
        windows[media_type].append(MediaReference(
            key, media_type, "track", track_id, "", start, media_end - start,
            media_type == "video" and not muted, fit))

    def ordered(media_type: str) -> list[MediaReference]:
        timeline = shared[media_type] + windows[media_type]
        by_key = {ref.key: ref for ref in timeline}
        result = [ref for ref in timeline if ref.key not in placed[media_type]]
        used: set[str] = set()
        for item in items[media_type]:
            if item.get("source_type") == TRACK_SOURCE:
                # The first placeholder of a reference counts; later copies are ignored.
                key = placeholder_key(item)
                if key is None or key in used:
                    continue
                used.add(key)
                ref = by_key.get(key)
                if ref is None or item.get("muted") is True:
                    continue
                if media_type == "video" and isinstance(item.get("soundtrack"), bool):
                    ref = replace(ref, soundtrack=item["soundtrack"])
                result.append(ref)
                continue
            if item.get("muted") is True or not has_upload_source(item):
                continue
            fit = media_type == "video" or item.get("fit") is True
            result.append(MediaReference(
                f"upload:{item.get('id', '')}", media_type, "upload", "", "", 0,
                task_frames if fit else cap, media_type == "video" and item.get("soundtrack") is not False,
                fit, _upload_source(item)))
        return result[:MEDIA_REFERENCE_LIMIT]

    videos = ordered("video")
    locked = None if audio_only else locked_video_track(tracks, start, task_end)
    if locked is not None:
        locked_id = str(locked.get("id", ""))
        videos = [MediaReference(f"locked:{locked_id}", "video", "locked", locked_id, "", start,
                                 task_frames, locked.get("muted") is not True)]
    return TaskReferencePlan(images, tuple(videos), tuple(ordered("audio")))
