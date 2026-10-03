"""Check whole-run rejection and read-only edit status against a validation project.

Run after h3_native_api_smoke.py in the ComfyUI Python environment. Only isolated
native-validation-* projects are accepted. Expected failures must not sample or
change the existing manifest.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import time
from typing import Any
import urllib.request
import uuid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8191')
    parser.add_argument('--source-report', required=True, help='Successful Single API report')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = json.loads(Path(args.source_report).read_text(encoding='utf8'))
    name = report['project']
    if not name.startswith('native-validation-') or report['mode'] != 'single':
        raise ValueError('Use a successful isolated Single validation project.')
    root = Path(__file__).resolve().parents[4] / 'output/easy_media/projects'
    manifest = root / name / 'project.json'
    original = manifest.read_bytes()
    base = report['history']['prompt'][2]

    def request(path: str, body: dict[str, Any] | None = None) -> Any:
        req = urllib.request.Request(args.url + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)

    data = json.loads(base['7']['inputs']['track_data'])
    saved = request('/easy-media/project/timeline-status', {'project_name': name, 'tracks_info': data})
    assert all(task['status'] == 'saved' for task in saved['tasks']), saved
    changed = deepcopy(data)
    changed['tracks'][0]['segments'][0]['content']['user_prompt'] = 'A changed predecessor prompt'
    edited = request('/easy-media/project/timeline-status', {'project_name': name, 'tracks_info': changed})
    assert [t['status'] for t in edited['tasks']] == ['edited', 'parent_changed', 'parent_changed'], edited
    cases = []
    for kind, expected in [('invalid_schedule', 'SCHEDULE'), ('missing_low_stage', 'VAE_FALLBACK_REQUIRED'),
                           ('edited_parent', 'PARENT_EDITED')]:
        nodes = deepcopy(base)
        project = nodes['10']['inputs']
        project['allow_vae_fallback'] = False
        if kind == 'invalid_schedule':
            project['project_name'] = 'native-validation-reject-' + uuid.uuid4().hex[:10]
            nodes['8']['inputs']['sigmas'] = '1, .5, .8, 0'
        else:
            project['segment_start_number'] = 2
            if kind == 'missing_low_stage':
                project.update(sampling_mode='dual', upscale_by=1.0, upscale_model='None',
                               sampler_2nd=['9', 0], sigmas_2nd=['8b', 0], model_loader_2nd=['6b', 0])
            else:
                nodes['7']['inputs']['track_data'] = json.dumps(changed)
        identity = request('/prompt', {'prompt': nodes, 'client_id': 'native-preflight-audit'})['prompt_id']
        began = time.monotonic()
        while time.monotonic() - began < 600:
            history = request('/history/' + identity).get(identity)
            if history is not None:
                break
            time.sleep(2)
        else:
            raise TimeoutError(f'Inspect queued prompt {identity} before retrying.')
        errors = [payload for event, payload in history['status']['messages'] if event == 'execution_error']
        assert errors and expected in errors[-1]['exception_message'], history['status']
        error = errors[-1]
        # Comfy reports a dynamic node's visible parent as node_type.
        assert any('h3_native.py' in line for line in error['traceback']), error
        assert not any('sample_' in str(node) or 'sampling_start' in str(node) for node in error.get('executed', [])), error
        assert manifest.read_bytes() == original, 'Preflight changed saved generations'
        if kind == 'invalid_schedule':
            assert not (root / project['project_name'] / 'project.json').exists()
        cases.append({'case': kind, 'prompt_id': identity, 'error': error['exception_message'],
                      'node_type': error['node_type'], 'executed': error.get('executed', [])})
        print(f'{kind}: rejected before sampling', flush=True)
    Path(args.output).write_text(json.dumps({'project': name, 'saved': saved, 'edited': edited, 'cases': cases}, indent=2), encoding='utf8')


if __name__ == '__main__':
    main()
