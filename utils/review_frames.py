"""Contact sheets of generated videos, so an agent that can see images can judge a run.

A sheet is one JPEG of a few evenly spaced frames labelled with their time, which costs far
fewer image tokens than separate frames. Sheets go to ComfyUI's temp folder and are reused
until the video changes.
"""
from __future__ import annotations

import hashlib
import io
import math
import subprocess
from pathlib import Path

import folder_paths

REVIEW_SUBFOLDER = "easy_media_review"
FRAME_LIMITS = (1, 9)
WIDTH_LIMITS = (256, 2048)


class ReviewError(ValueError):
    """A video that cannot be turned into a contact sheet."""


def _output_file(relative: object) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ReviewError("each item needs the file_path of a video in the output folder")
    root = Path(folder_paths.get_output_directory()).resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ReviewError(f"{relative} is outside the output folder") from error
    if not path.is_file():
        raise ReviewError(f"{relative} was not found")
    return path


def frame_times(duration: float, count: int) -> list[float]:
    """The middle of each of `count` equal parts, so no frame sits on a cut."""
    return [duration * (index + 0.5) / count for index in range(count)]


def _ffmpeg() -> str:
    from .video import get_ffmpeg_path

    ffmpeg = get_ffmpeg_path("ffmpeg")
    if not ffmpeg:
        raise ReviewError("FFmpeg was not found")
    return ffmpeg


def _duration(source: Path) -> float:
    from .video import ffprobe_info

    duration = (ffprobe_info(str(source)) or {}).get("duration")
    if not duration:
        raise ReviewError(f"The length of {source.name} could not be read")
    return float(duration)


def _grab(ffmpeg: str, source: Path, seconds: float):
    from PIL import Image

    result = subprocess.run(
        [ffmpeg, "-v", "error", "-ss", f"{seconds:.3f}", "-i", str(source),
         "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True, timeout=60, check=False,
    )
    if result.returncode != 0 or not result.stdout:
        raise ReviewError(f"No frame could be read from {source.name} at {seconds:.1f}s")
    return Image.open(io.BytesIO(result.stdout)).convert("RGB")


def _compose(images: list, times: list[float], width: int):
    from PIL import Image, ImageDraw

    columns = math.ceil(math.sqrt(len(images)))
    rows = math.ceil(len(images) / columns)
    cell_width = width // columns
    cell_height = max(1, round(cell_width * images[0].height / images[0].width))
    sheet = Image.new("RGB", (cell_width * columns, cell_height * rows), "black")
    draw = ImageDraw.Draw(sheet)
    for index, (image, seconds) in enumerate(zip(images, times)):
        x, y = (index % columns) * cell_width, (index // columns) * cell_height
        sheet.paste(image.resize((cell_width, cell_height)), (x, y))
        label = f"{seconds:.1f}s"
        draw.rectangle((x, y, x + 7 * len(label) + 8, y + 16), fill="black")
        draw.text((x + 4, y + 2), label, fill="white")
    return sheet


def contact_sheet(relative: object, frames: object = 4, width: object = 1024) -> dict[str, object]:
    """Write (or reuse) the sheet of one output video; return where /view serves it."""
    if not isinstance(frames, int) or not FRAME_LIMITS[0] <= frames <= FRAME_LIMITS[1]:
        raise ReviewError(f"frames must be a whole number from {FRAME_LIMITS[0]} to {FRAME_LIMITS[1]}")
    if not isinstance(width, int) or not WIDTH_LIMITS[0] <= width <= WIDTH_LIMITS[1]:
        raise ReviewError(f"width must be a whole number from {WIDTH_LIMITS[0]} to {WIDTH_LIMITS[1]}")
    source = _output_file(relative)
    stat = source.stat()
    key = hashlib.sha256(f"{relative}|{stat.st_mtime_ns}|{stat.st_size}|{frames}|{width}".encode()).hexdigest()[:16]
    folder = Path(folder_paths.get_temp_directory()) / REVIEW_SUBFOLDER
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{source.stem[:60]}-{key}.jpg"
    target = folder / name
    if not target.exists():
        ffmpeg = _ffmpeg()
        times = frame_times(_duration(source), frames)
        sheet = _compose([_grab(ffmpeg, source, seconds) for seconds in times], times, width)
        partial = folder / f".{name}.part"
        sheet.save(partial, "JPEG", quality=85)
        partial.replace(target)
    return {"filename": name, "subfolder": REVIEW_SUBFOLDER, "type": "temp", "frames": frames}
