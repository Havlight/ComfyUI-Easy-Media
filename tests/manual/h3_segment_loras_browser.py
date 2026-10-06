"""Isolated Chromium test of real ComfyUI widget edits, history and serialization.

Requires Playwright (install into a separate test-deps directory if desired).
Never opens a user's existing profile or queues generation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8191')
    parser.add_argument('--output', required=True)
    parser.add_argument('--executable', required=True)
    parser.add_argument('--test-deps')
    args = parser.parse_args()
    if args.test_deps:
        sys.path.insert(0, str(Path(args.test_deps).resolve()))
    from playwright.sync_api import sync_playwright

    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(executable_path=args.executable, headless=True)
        page = browser.new_page(viewport={'width': 1500, 'height': 1050})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(args.url, wait_until='networkidle')
        page.wait_for_function("Boolean(window.comfyAPI?.app?.app?.graph && window.LiteGraph?.registered_node_types['easy h3SegmentLoras'])")
        page.evaluate("""() => {
          const app=window.comfyAPI.app.app;
          app.graph.clear();
          const lora=LiteGraph.createNode('easy h3SegmentLoras');app.graph.add(lora);lora.pos=[80,100];lora.setSize([640,720]);
          const editor=LiteGraph.createNode('easy multiTrackEditor');app.graph.add(editor);editor.pos=[1800,500];
          const set=(node,name,value)=>{const w=node.widgets.find(w=>w.name===name);w.value=value;w.callback?.(value);};
          set(editor,'format','MiniMax');
          set(editor,'track_data',JSON.stringify({frame_rate:24,total_length:141,h3_native:{version:2},task_markers:[],tracks:[
            {id:'tasks',name:'Tasks',type:'task',segments:[
              {id:'a',start_frame:0,end_frame:90,content:{task_mode:'default',continuity_mode:'shot',images:[],user_prompt:'A garden'}},
              {id:'b',start_frame:90,end_frame:141,content:{task_mode:'default',continuity_mode:'context',images:[],user_prompt:'A garden'}}]}]}));
          for(const [name,mode,x] of [['lora-ui-dual','dual',820],['lora-ui-single','single',1500]]) {
            const project=LiteGraph.createNode('easy multitrackProject');app.graph.add(project);project.pos=[x,100];
            set(project,'project_name',name);set(project,'sampling_mode',mode);
            editor.connect(0,project,project.inputs.findIndex(i=>i.name==='tracks_info'));
            lora.connect(0,project,project.inputs.findIndex(i=>i.name==='segment_loras'));
          }
          app.canvas.ds.scale=1;app.canvas.ds.offset=[20,50];app.canvas.setDirty(true,true);
        }""")
        page.wait_for_timeout(1500)
        read = "JSON.parse(window.comfyAPI.app.app.graph._nodes.find(n=>n.type==='easy h3SegmentLoras').widgets.find(w=>w.name==='rules').value)"
        page.get_by_role('button', name='Add LoRA', exact=True).click()
        page.get_by_role('button', name='LoRA', exact=True).click()
        page.get_by_role('textbox', name='Search LoRAs').fill('h3-realism-people')
        page.get_by_role('option', name='mmh3/h3-realism-people-t2v-i2v-r2v.safetensors', exact=True).click()
        page.get_by_label('Start segment', exact=True).fill('2')
        page.get_by_label('Start segment', exact=True).press('Enter')
        page.get_by_role('button', name='Advanced settings', exact=True).click()
        page.get_by_role('combobox', name='Apply to', exact=True).click()
        page.get_by_role('option', name='Second stage', exact=True).click()
        page.get_by_role('button', name='Effective plan', exact=True).click()
        page.get_by_role('combobox', name='Project', exact=True).wait_for()
        page.wait_for_function(read + ".rules[0].stage === 'second'")
        page.get_by_role('cell', name='mmh3/h3-realism-people-t2v-i2v-r2v.safetensors × 1', exact=True).wait_for()
        before = page.evaluate(read)
        page.screenshot(path=str(output / 'segment-loras-ui-wide.png'))
        # Exercise the actual graph history manager through its keyboard handler.
        page.mouse.click(750, 900)
        page.keyboard.press('Control+z')
        page.wait_for_function(read + ".rules[0].stage === 'all'")
        page.wait_for_function("!window.comfyAPI.app.app.extensionManager.workflow.activeWorkflow.changeTracker._restoringState")
        page.keyboard.press('Control+y')
        page.wait_for_function(read + ".rules[0].stage === 'second'")
        assert page.evaluate(read) == before
        page.wait_for_function("!window.comfyAPI.app.app.extensionManager.workflow.activeWorkflow.changeTracker._restoringState")
        page.get_by_role('button', name='Effective plan', exact=True).click()
        page.get_by_role('combobox', name='Project', exact=True).click()
        page.get_by_role('option', name='lora-ui-single', exact=False).click()
        page.get_by_text('Stage not used in this mode', exact=True).wait_for()
        saved = page.evaluate('window.comfyAPI.app.app.graph.serialize()')
        (output / 'segment-loras-ui-workflow.json').write_text(json.dumps(saved, indent=2), encoding='utf8')
        page.evaluate('async (saved) => {await window.comfyAPI.app.app.loadGraphData(saved)}', saved)
        page.wait_for_function(read + ".rules[0].stage === 'second'")
        assert page.evaluate(read) == before
        page.evaluate("""() => {
          const app=window.comfyAPI.app.app;
          const n=app.graph._nodes.find(n=>n.type==='easy h3SegmentLoras');n.pos=[40,80];n.setSize([360,640]);
          app.canvas.ds.scale=1;app.canvas.ds.offset=[20,50];app.canvas.setDirty(true,true);
        }""")
        page.set_viewport_size({'width': 750, 'height': 960})
        page.get_by_label('Start segment', exact=True).wait_for()
        page.screenshot(path=str(output / 'segment-loras-ui-narrow.png'))
        assert not errors, errors
        (output / 'segment-loras-ui-report.json').write_text(json.dumps({
            'passed': True, 'rules': before, 'checks': ['search', 'range edit', 'stage', 'two projects', 'undo', 'redo', 'save/reload', 'narrow layout'],
            'page_errors': errors,
        }, indent=2), encoding='utf8')
        browser.close()
    print('Real ComfyUI browser interactions passed', flush=True)


if __name__ == '__main__':
    main()
