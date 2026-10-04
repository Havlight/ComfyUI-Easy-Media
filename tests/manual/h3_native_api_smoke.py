"""Queue an isolated native H3 project through a running ComfyUI API.

Requires the standard H3 model/CLIP/VAEs and this plugin. Each invocation creates
a unique validation project, leaving existing projects and workflows untouched.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8191')
    for name in ('model', 'clip', 'lora', 'video-vae', 'audio-vae', 'report'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--segments', type=int, default=3)
    parser.add_argument('--project-name', help='Explicitly resume this validation project instead of creating a unique one')
    parser.add_argument('--start', type=int, default=1)
    parser.add_argument('--lock-audio', help='Absolute path to an external audio fixture')
    parser.add_argument('--lock-video', help='Absolute path to an external video fixture covering the whole timeline')
    parser.add_argument('--upscale-model', default='None')
    parser.add_argument('--alternate-prompt')
    parser.add_argument('--seed', type=int, default=721)
    parser.add_argument('--method', choices=('context', 'context_drift'), default='context')
    parser.add_argument('--mode', choices=('single', 'dual', 'selflift'), default='single')
    parser.add_argument('--prompt', default='A woman wearing a blue jacket slowly turns and raises her hand in a sunny garden. Continuous camera movement, stable face, natural light. Leaves rustle softly.')
    args = parser.parse_args()
    if not 2 <= args.segments <= 20:
        parser.error('--segments must be between 2 and 20')

    def request(path: str, data: dict[str, Any] | None = None) -> Any:
        payload = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(args.url + path, data=payload, headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(error.read().decode()) from error

    schemas = request('/object_info')

    def model_name(kind: str, field: str, requested: str) -> str:
        definition = schemas[kind]['input']['required'][field]
        choices = definition[0] if isinstance(definition[0], list) else definition[1]['options']
        return next((name for name in choices if name.replace('\\', '/') == requested.replace('\\', '/')), requested)

    Path(args.report).resolve().parent.mkdir(parents=True, exist_ok=True)
    nodes: dict[str, Any] = {}

    def node(identity: str, kind: str, **inputs: Any) -> list[Any]:
        nodes[identity] = {'class_type': kind, 'inputs': inputs}
        return [identity, 0]

    model = node('1', 'UNETLoader', unet_name=model_name('UNETLoader', 'unet_name', args.model), weight_dtype='default')
    model = node('2', 'LoraLoaderModelOnly', model=model, lora_name=model_name('LoraLoaderModelOnly', 'lora_name', args.lora), strength_model=1.0)
    clip = node('3', 'CLIPLoader', clip_name=model_name('CLIPLoader', 'clip_name', args.clip), type='minimax', device='default')
    video_vae = node('4', 'VAELoader', vae_name=model_name('VAELoader', 'vae_name', args.video_vae))
    audio_vae = node('5', 'VAELoader', vae_name=model_name('VAELoader', 'vae_name', args.audio_vae))
    loader = node('6', 'easy modelLoaderPack', model=model, clip=clip, vae=video_vae, audio_vae=audio_vae)
    second_model = node('2b', 'LoraLoaderModelOnly', model=['1', 0], lora_name=model_name('LoraLoaderModelOnly', 'lora_name', args.lora), strength_model=.8)
    second_loader = node('6b', 'easy modelLoaderPack', model=second_model, clip=clip, vae=video_vae, audio_vae=audio_vae)
    segments = []
    end = 0
    for index in range(args.segments):
        start, end = end, end + (90 if index == 0 else 51)
        segments.append({'id': f'task-{index}', 'start_frame': start, 'end_frame': end,
                         'content': {'media_type': 'none', 'task_mode': 'ref' if args.lock_video else 'default', 'images': [],
                                     'continuity_mode': 'shot' if index == 0 else args.method, 'user_prompt': args.alternate_prompt if args.alternate_prompt and index >= args.segments // 2 else args.prompt}})
    tracks = [{'id': 'tasks', 'type': 'task', 'segments': segments}]
    for kind, path in (('audio', args.lock_audio), ('video', args.lock_video)):
        if path:
            tracks.append({'id': 'locked-' + kind, 'type': kind, 'audio_locked': True, 'segments': [
                {'id': 'source-' + kind, 'start_frame': 0, 'end_frame': end,
                 'content': {'media_type': kind, 'source_type': 'local', 'local_path': path}}]})
    info = node('7', 'easy multiTrackEditor', resolution='width x height (custom)',
                **{'resolution.width': 320, 'resolution.height': 256, 'resolution.resize_method': 'stretch'},
                format='MiniMax', track_data=json.dumps({'frame_rate': 24, 'total_length': end,
                    'h3_native': {'version': 2},
                    'task_markers': [], 'tracks': tracks}))
    sigmas = node('8', 'ManualSigmas', sigmas='1, .995, .9825, .9607, .9234, .8553, .7207, .4249, .2125, 0')
    sampler = node('9', 'KSamplerSelect', sampler_name='euler')
    second_sigmas = node('8b', 'ManualSigmas', sigmas='.72, .5, .3, .14, .06, 0')
    name = args.project_name or 'native-validation-' + uuid.uuid4().hex[:12]
    node('10', 'easy multitrackProject', tracks_info=info, model_loader=loader, project_name=name,
         project_save='new', segment_start_number=args.start, segment_count=-1, seed=args.seed, allow_vae_fallback=False,
         sampling_plan='custom', sampling_mode=args.mode, **{'1st_pass_only': False},
         disable_2nd_noise=False, upscale_by=2.0 if args.upscale_model != 'None' and args.mode == 'dual' else 1.0, upscale_model=args.upscale_model, enabled_tiling='false',
         sampler=sampler, sigmas=sigmas,
         **({'sampler_2nd': sampler, 'sigmas_2nd': second_sigmas} if args.mode == 'dual' else {}),
         **({'model_loader_2nd': second_loader} if args.mode != 'single' else {}),
         **({'sampling_mode.transition_ratio': .6, 'sampling_mode.lowres_scale': .5} if args.mode == 'selflift' else {}))
    video = node('11', 'easy multitrackProjectVideoCombine', project_name=['10', 0], project_data='{}')
    node('12', 'SaveVideo', video=video, filename_prefix='easy_media/native-validation/' + name, format='mp4', **{'format.codec': 'h264'})
    submitted = request('/prompt', {'prompt': nodes, 'client_id': name})
    identity = submitted['prompt_id']
    print(json.dumps({'prompt_id': identity, 'project': name}), flush=True)
    began = time.monotonic()
    while time.monotonic() - began < 1800:
        history = request('/history/' + identity)
        if identity in history:
            report = {'project': name, 'prompt_id': identity, 'seconds': time.monotonic() - began,
                      'segments': args.segments, 'method': args.method, 'mode': args.mode,
                      'history': history[identity]}
            Path(args.report).write_text(json.dumps(report, indent=2), encoding='utf8')
            if history[identity]['status']['status_str'] != 'success':
                errors = [value for kind, value in history[identity]['status']['messages'] if kind == 'execution_error']
                raise RuntimeError(errors[-1].get('exception_message', 'Execution failed; inspect report') if errors else 'Execution failed; inspect report')
            print('Native API project completed: ' + name, flush=True)
            return
        time.sleep(5)
    raise TimeoutError('Validation prompt is still queued/running; inspect its prompt ID before retrying.')


if __name__ == '__main__':
    main()
