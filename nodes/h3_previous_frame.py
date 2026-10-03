from __future__ import annotations

from typing import Any

import torch
from comfy_api.latest import io

from ..utils.h3_previous_frame import load_previous_frame
from ..utils.minimax import expand_image_inputs


class EasyH3LastFrame(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(node_id="easy h3LastFrame", display_name="H3 Last Frame", category="EasyUse/H3/dev",
                         inputs=[io.Image.Input("images")], outputs=[io.Image.Output("image")], is_dev_only=True)

    @classmethod
    def execute(cls, images: torch.Tensor) -> io.NodeOutput:
        return io.NodeOutput(images[-1:].detach().cpu().clone())



class EasyH3PreviousFrame(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="easy h3PreviousFrame", display_name="H3 Previous Frame", category="EasyUse/H3/dev",
            is_input_list=True, is_dev_only=True, not_idempotent=True,
            inputs=[io.Image.Input("images"), io.String.Input("project_name"), io.Int.Input("segment_index"),
                    io.Int.Input("position"), io.Boolean.Input("resume", default=False),
                    io.AnyType.Input("previous", optional=True), io.AnyType.Input("run_state", optional=True),
                    io.String.Input("parent_segment_id", optional=True)],
            outputs=[io.Image.Output("images", is_output_list=True), io.String.Output("source")],
        )

    @classmethod
    def execute(cls, images: list, project_name: list[str], segment_index: list[int], position: list[int],
                resume: list[bool], previous: Any = None, run_state: list[dict[str, Any]] | None = None,
                parent_segment_id: list[str] | None = None) -> io.NodeOutput:
        if run_state is not None and parent_segment_id is not None:
            from ..utils.h3_native_preflight import run_source
            from ..utils.h3_native_artifacts import file_checksum, native_child_path
            from ..utils.h3_native_timing import NativePlanError
            from ..utils.h3_previous_frame import read_video_frames, video_frame_count, video_identity
            from .h3_native import _project_directory
            import json

            pinned = run_source(run_state[0], parent_segment_id[0])
            record = pinned['record']
            directory = _project_directory(project_name[0])
            path = native_child_path(directory, record['video'])
            if pinned.get('video_sha256') and file_checksum(path) != pinned['video_sha256']:
                raise NativePlanError('SOURCE_CHANGED', 'The previous-frame video changed after preflight.')
            image = read_video_frames(path, video_frame_count(path) - 1, 1, fps=None)
            source = json.dumps(video_identity(directory, pinned['segment_index'], pinned['generation'], record))
        else:
            image, source = load_previous_frame(project_name[0], segment_index[0], resume[0])
        result = expand_image_inputs(images)
        if len(result) >= 9 or not 0 <= position[0] <= len(result):
            raise ValueError("Previous-frame reference exceeds the available image slots")
        result.insert(position[0], image)
        return io.NodeOutput(result, source)
