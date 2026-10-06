"""Whole-run rejection, read-only status and resume on an isolated LoRA project."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import time
from typing import Any
import urllib.error
import urllib.request
import uuid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8191')
    parser.add_argument('--source-report', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    source = json.loads(Path(args.source_report).read_text(encoding='utf8'))
    name = source['project']
    if not name.startswith('native-validation-') or source['mode'] != 'single':
        raise ValueError('Use an isolated Single API validation project.')
    root = Path(__file__).resolve().parents[4]
    directory = root / 'output/easy_media/projects' / name
    manifest = directory / 'project.json'
    original = manifest.read_bytes()
    graph = source['history']['prompt'][2]
    rules = json.loads(graph['13']['inputs']['rules'])
    data = json.loads(graph['7']['inputs']['track_data'])
    count = len(data['tracks'][0]['segments'])
    result: dict[str, Any] = {'project': name, 'passed': False, 'cases': []}

    def request(path: str, body: Any = None) -> Any:
        req = urllib.request.Request(args.url + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(error.read().decode()) from error

    def execute(nodes: dict[str, Any]) -> dict[str, Any]:
        identity = request('/prompt', {'prompt': nodes, 'client_id': 'segment-loras-api-audit'})['prompt_id']
        began = time.monotonic()
        while time.monotonic() - began < 900:
            history = request('/history/' + identity).get(identity)
            if history is not None:
                return history
            time.sleep(2)
        raise TimeoutError(f'Inspect prompt {identity} before retrying.')

    snapshot = {'known': True, 'segment_loras': rules, 'recipe': {'sampling_mode': 'single', 'first_pass_only': False},
                'start_segment': 2, 'segment_count': -1}
    preview = request('/easy-media/project/segment-loras-preview', {'tracks_info': data, 'project_snapshot': snapshot})
    assert len(preview['tasks']) == count and preview['tasks'][1]['number'] == 2
    assert not preview['tasks'][0]['selected'] and preview['tasks'][1]['selected']
    pending = request('/easy-media/project/segment-loras-preview', {'project_snapshot': {'known': False}})
    assert pending['pending'] is True and not pending['tasks']
    assert manifest.read_bytes() == original
    result['preview'] = preview

    fixture_dir = root / 'models/loras' / ('_easy_media_validation_' + uuid.uuid4().hex[:8])
    fixture_dir.mkdir()
    fixture = fixture_dir / 'wrong-shape.safetensors'
    try:
        import torch
        from safetensors.torch import save_file
        save_file({'diffusion_model.blocks.0.attn.out_proj.lora_A.weight': torch.ones(1, 1),
                   'diffusion_model.blocks.0.attn.out_proj.lora_B.weight': torch.ones(1, 1)}, str(fixture))
        for case, expected in [('missing_last', 'unavailable'), ('wrong_shape_last', 'shape mismatch'),
                               ('duplicate', 'duplicate LoRA'), ('stale_parent', 'PARENT_EDITED'),
                               ('stale_parent_with_fallback', 'PARENT_EDITED')]:
            nodes = deepcopy(graph)
            current = deepcopy(rules)
            project = nodes['10']['inputs']
            if case.startswith('stale_parent'):
                current['rules'][0]['strength'] += .125
                project['segment_start_number'] = 2
                project['allow_vae_fallback'] = case.endswith('fallback')
            else:
                project['project_name'] = 'native-validation-reject-' + uuid.uuid4().hex[:10]
                extra = deepcopy(current['rules'][0])
                extra['id'] = 'invalid-extra'
                if case == 'duplicate':
                    current['rules'].append(extra)
                else:
                    extra.update(start_segment=count, segment_count=1,
                                 lora='missing-segment-lora.safetensors' if case == 'missing_last' else fixture.relative_to(root / 'models/loras').as_posix())
                    current['rules'].append(extra)
            nodes['13']['inputs']['rules'] = json.dumps(current)
            history = execute(nodes)
            errors = [payload for event, payload in history['status']['messages'] if event == 'execution_error']
            assert errors and expected in errors[-1]['exception_message'], history['status']
            error = errors[-1]
            assert not any('sample_' in str(node) or 'sampling_start' in str(node) for node in error.get('executed', [])), error
            assert manifest.read_bytes() == original, 'Rejected run changed saved generations'
            if not case.startswith('stale_parent'):
                assert not (directory.parent / project['project_name'] / 'project.json').exists()
            result['cases'].append({'case': case, 'error': error['exception_message'], 'executed': error.get('executed', [])})
            print(f'{case}: rejected before sampling', flush=True)

        status = request('/easy-media/project/timeline-status', {'project_name': name, 'tracks_info': data, 'project_snapshot': snapshot})
        assert all(task['status'] == 'saved' for task in status['tasks']), status
        changed = deepcopy(snapshot)
        changed['segment_loras']['rules'][0]['strength'] += .125
        edited = request('/easy-media/project/timeline-status', {'project_name': name, 'tracks_info': data, 'project_snapshot': changed})
        assert [task['status'] for task in edited['tasks']] == ['edited'] + ['parent_changed'] * (count - 1), edited
        disconnected = deepcopy(snapshot)
        disconnected['segment_loras']['rules'] = []
        without = request('/easy-media/project/timeline-status', {'project_name': name, 'tracks_info': data, 'project_snapshot': disconnected})
        assert without['tasks'][0]['status'] == 'edited'
        assert manifest.read_bytes() == original
        result.update(status=status, edited=edited, disconnected=without)

        nodes = deepcopy(graph)
        nodes['10']['inputs']['segment_start_number'] = 2
        history = execute(nodes)
        assert history['status']['status_str'] == 'success', history['status']
        after = json.loads(manifest.read_text())
        before = json.loads(original)
        assert after['segments']['0'] == before['segments']['0'], 'Partial resume changed the unselected parent'
        for index in range(1, count):
            segment = after['segments'][str(index)]
            assert segment['active_generation'] > before['segments'][str(index)]['active_generation']
        result.update(passed=True, resume_history=history)
        print('Absolute-index resume succeeded; original first segment preserved', flush=True)
    finally:
        fixture.unlink(missing_ok=True)
        fixture_dir.rmdir()
        Path(args.report).write_text(json.dumps(result, indent=2), encoding='utf8')


if __name__ == '__main__':
    main()
