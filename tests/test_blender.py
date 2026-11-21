"""Blender scenes in Shadow (blender_core.py + the queue / CLI / MCP paths). Windows and Linux, needs Blender + ffmpeg.
Usage: python tests/test_blender.py      exit 0 = every check PASS (or Blender not installed: SKIP), 1 = any FAIL

The .blend files are BUILT here in a temp folder by tests/fixtures/make_blend_fixture.py (nothing binary is checked in;
scenes/ is never touched).
  B1 16:9 draft: 1280x720, 24 fps scene re-timed to 30 fps (same 2 s), the scene's own sound strip mixed in, thumbnail marker
  B2 9:16: 720x1280 through the scene's "Camera 9x16"   B3 no 9:16 camera: centre-cropped 16:9 camera + warning
  B4 no sound in the scene: silent like an HTML scene without SOUND   B5 Cycles on the GPU (OptiX/CUDA when found) + Workbench
  B6 cancel mid-render: no mp4, no .part.mp4, Blender gone   B7 missing texture: a warning, and a problem in the preview
  B8 a broken .blend: clean error   B9 preview(): contact sheet 16:9 + 9:16
  B10 the GUI queue function app.render_scene: short_*.blend -> <out>/x/x_shorts.mp4, Sound Auto (<scene>.wav next to
      it, also when a words.json lies next to it), QC report, thumbnail; narration is refused for .blend
  B11 CLI: app.py --render x.blend out.mp4 --preset draft   B12 MCP: plan kind 'blender', a job renders it, .blend-only needs no Chrome"""
import json, os, shutil, subprocess, sys, tempfile, threading, time, traceback, wave, math, struct
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, 'tools')); sys.path.insert(0, HERE)
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import render_core as rc
import blender_core as bc
os.environ['SHADOW_LANG'] = 'en'   # 3.20: the tests read the English texts
import app
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import no_dialogs; no_dialogs.block(app)   # never a real window on the screen

MAKE = os.path.join(ROOT, 'tests', 'fixtures', 'make_blend_fixture.py')
DRAFT = rc.PRESETS['Draft (fast, 720p)']
RESULTS = []
def check(name, ok, detail=''):
    RESULTS.append(bool(ok)); print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f'  --  {detail}' if detail else '')); return ok

def ffprobe(mp4):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,width,height,nb_frames:format=duration', '-of', 'json', mp4],
                       capture_output=True, text=True)
    j = json.loads(r.stdout or '{}'); v = next((s for s in j.get('streams', []) if s['codec_type'] == 'video'), {})
    return dict(w=v.get('width'), h=v.get('height'), frames=int(v.get('nb_frames') or 0), dur=float(j.get('format', {}).get('duration') or 0),
                audio=any(s['codec_type'] == 'audio' for s in j.get('streams', [])))

def make(exe, d, name, *flags):
    os.makedirs(d, exist_ok=True); out = os.path.join(d, name)
    r = subprocess.run([exe, '-b', '--factory-startup', '--python', MAKE, '--', out, *flags], capture_output=True, text=True, errors='replace', timeout=300)
    if not os.path.isfile(out): raise RuntimeError(f'fixture {name} not built: {r.stdout[-800:]}')
    return out

def blender_running(tmp):
    """Blender processes started on a .blend inside tmp (the test's own)."""
    try:
        if os.name == 'nt':
            out = subprocess.run(['powershell', '-NoProfile', '-Command', "Get-CimInstance Win32_Process -Filter \"Name='blender.exe'\" | ForEach-Object { $_.CommandLine }"],
                                 capture_output=True, text=True, creationflags=rc.NOWIN).stdout
        else:
            out = subprocess.run(['ps', '-eo', 'args'], capture_output=True, text=True).stdout
        return [l for l in out.splitlines() if os.path.basename(tmp) in l]
    except Exception:
        return []

def b13(cube, tmp):
    """B13 the real GUI (window hidden, message boxes recorded, logs in tmp): add a .blend, Blender = Workbench, Render."""
    app.LOG = os.path.join(tmp, 'shadow.log'); app.LOG_DIR = os.path.join(tmp, 'logs')
    for h in list(app.log.handlers): app.log.removeHandler(h)
    boxes = []
    for n in ('showwarning', 'showerror', 'showinfo'): setattr(app.messagebox, n, lambda *x, n=n, **k: boxes.append((n, x)))
    a = app.App(); a.withdraw()
    try:
        out = os.path.join(tmp, 'gui')
        a.open_when_done.set(False); a.skip_done.set(False); a.notify.set(False); a.preset.set('Draft (fast, 720p)'); a.fmt.set(app.FORMAT_AUTO); a.snd.set(app.SOUNDS[0])
        a.folder_choice.set(app.FOLDER_NONE)   # <Save to>\cube\ (Folder = Auto is checked in test_queue_logic / test_mcp_server)
        a.capture.set(list(app.CAPTURES)[1]); a.bl_engine.set(bc.ENGINES['workbench']); a.out_dir.set(out)
        a._add(cube); a._add(os.path.join(tmp, 'not_a_scene.txt'))
        v = a.tree.item(a.rows[cube], 'values') if cube in a.rows else ()
        check('B13 GUI: a .blend is added (only it), Format 16:9, Audio "scene sound"', a.files == [cube] and v and v[0] == 'cube.blend'
              and v[4] == '16:9' and v[3] == 'scene sound', v)
        check('B13 GUI: Blender found in the setup bar', a.env_lbls['Blender'][1].cget('text') == 'Blender', a.env_lbls['Blender'][1].cget('text'))
        a.start(ask=lambda *x: True)
        t0 = time.time()
        while time.time() - t0 < 300:
            a.update(); time.sleep(0.05)
            if a.worker is not None and not a.worker.is_alive() and str(a.go.cget('state')) == 'normal': break
        for _ in range(20): a.update(); time.sleep(0.05)
        st = a.tree.item(a.rows[cube], 'values')[1]; mp4 = os.path.join(out, 'cube', 'cube.mp4'); p = ffprobe(mp4) if os.path.isfile(mp4) else {}
        check('B13 GUI render: row "Done · QC ...", <Save to>/cube/cube.mp4 1280x720 with the scene sound, no error box',
              st.startswith('Done · QC') and (p.get('w'), p.get('h')) == (1280, 720) and p.get('audio') and not any(b[0] == 'showerror' for b in boxes), (st, p, boxes))
        check('B13 GUI: the queue passed engine "workbench" to Blender', a.opts.get('engine') == 'workbench', a.opts)
    finally:
        try: a.destroy()
        except Exception: pass

def main():
    exe = bc.find_blender()
    if not exe:
        print('SKIP  Blender not installed (winget install -e --id BlenderFoundation.Blender)'); return 0
    print(f'Blender {bc.blender_version(exe)} at {exe}')
    tmp = tempfile.mkdtemp(prefix='shadow_blend_test_')
    try:
        d = os.path.join(tmp, 'cube'); cube = make(exe, d, 'cube.blend')
        # B1
        info = {}; stages = []
        out = bc.render(cube, os.path.join(tmp, 'w.mp4'), DRAFT, info=info, progress=lambda s, *a: stages.append(s))
        p = ffprobe(out); m = info['blender']
        check('B1 16:9 draft: 1280x720, 24 fps scene -> 60 frames at 30 fps (2.0 s), audio', (p['w'], p['h'], p['frames']) == (1280, 720, 60)
              and abs(p['dur'] - 2.0) < 0.05 and p['audio'] and m['src_fps'] == 24, p)
        check('B1 the scene sound strip is used (sound generated), stages in render_core order', info['sound'] == 'generated'
              and [s for i, s in enumerate(stages) if s not in stages[:i]] == ['load', 'audio', 'frames', 'encode', 'done'], (info['sound'], stages[:3], stages[-2:]))
        lufs, tp = rc._loud(rc.find_ffmpeg(), out)
        check('B1 audio inside the QC limits after AAC (-17..-13 LUFS, <= -1 dBTP)', lufs is not None and -17 <= lufs <= -13 and tp <= -1.0, (lufs, tp))
        check('B1 thumbnail marker at 1 s (on the 24 fps timeline) -> thumbnail_t 1.0', abs((info.get('thumbnail_t') or -9) - 1.0) < 0.05, info.get('thumbnail_t'))
        check('B1 16:9 uses "Camera" (not the 9:16 one), Eevee, draft samples', m['camera'] == 'Camera' and m['engine'].startswith('BLENDER_EEVEE')
              and m['samples'].startswith('16'), (m['camera'], m['engine'], m['samples']))
        # B2
        info = {}; out = bc.render(cube, os.path.join(tmp, 'v.mp4'), DRAFT, vertical=True, info=info, max_frames=12); p = ffprobe(out)
        check('B2 9:16: 720x1280 through "Camera 9x16", no warning', (p['w'], p['h']) == (720, 1280) and info['blender']['camera'] == 'Camera 9x16'
              and info['blender']['vertical_camera'] and not info['warnings'], (p, info['blender']['camera'], info['warnings']))
        # B3
        nov = make(exe, os.path.join(tmp, 'nov'), 'novcam.blend', '--no-vcam')
        info = {}; out = bc.render(nov, os.path.join(tmp, 'nv.mp4'), DRAFT, vertical=True, info=info, max_frames=6); p = ffprobe(out)
        check('B3 no 9:16 camera: 720x1280 from the 16:9 camera + warning', (p['w'], p['h']) == (720, 1280) and info['blender']['camera'] == 'Camera'
              and any('no 9:16 camera' in w for w in info['warnings']), info['warnings'])
        # B4
        mute = make(exe, os.path.join(tmp, 'mute'), 'mute.blend', '--no-sound')
        info = {}; out = bc.render(mute, os.path.join(tmp, 'm.mp4'), DRAFT, info=info, max_frames=6); p = ffprobe(out)
        check('B4 no sound in the scene + Sound generated -> silent mp4, info sound none', info['sound'] == 'none' and not p['audio'], (info['sound'], p))
        # B5
        info = {}; out = bc.render(cube, os.path.join(tmp, 'c.mp4'), DRAFT, info=info, max_frames=3, engine='cycles'); p = ffprobe(out)
        dev = info['blender'].get('device') or ''
        check('B5 Cycles: renders, device chosen (GPU when found)', p['frames'] == 3 and info['blender']['engine'] == 'CYCLES' and dev, dev)
        print(f'      Cycles device: {dev}, {info["blender"]["samples"]}')
        info = {}; out = bc.render(cube, os.path.join(tmp, 'wb.mp4'), DRAFT, info=info, max_frames=3, engine='workbench'); p = ffprobe(out)
        check('B5 Workbench renders', p['frames'] == 3 and info['blender']['engine'] == 'BLENDER_WORKBENCH', info['blender']['engine'])
        # B6 cancel
        ev = threading.Event(); res = {}
        def prog(stage, a, b, *r):
            if stage == 'frames' and a >= 5: ev.set()
        def run():
            try: bc.render(cube, os.path.join(tmp, 'x.mp4'), DRAFT, progress=prog, cancel=ev); res['r'] = 'finished'
            except rc.Cancelled: res['r'] = 'cancelled'
            except Exception as e: res['r'] = repr(e)
        t = threading.Thread(target=run); t.start(); t.join(120); time.sleep(1)
        check('B6 cancel mid-render: Cancelled, no mp4, no .part.mp4, Blender process gone', res.get('r') == 'cancelled'
              and not os.path.exists(os.path.join(tmp, 'x.mp4')) and not os.path.exists(os.path.join(tmp, 'x.part.mp4')) and not blender_running(tmp),
              (res, blender_running(tmp)))
        # B7 missing texture
        mt = make(exe, os.path.join(tmp, 'mt'), 'mt.blend', '--missing-texture')
        info = {}; bc.render(mt, os.path.join(tmp, 'mt.mp4'), DRAFT, info=info, max_frames=2)
        check('B7 missing texture: render works, warning names it', any('does_not_exist.png' in w for w in info['warnings']), info['warnings'])
        pv = bc.preview(mt, frames=['0'], only='16:9', out=os.path.join(tmp, 'mt.jpg'))
        check('B7 preview: missing texture is a problem (ok False), sheet still written', not pv['ok'] and any('does_not_exist.png' in x for x in pv['problems'])
              and os.path.isfile(pv['sheet']), pv['problems'])
        # B8 broken file
        bad = os.path.join(tmp, 'broken.blend')
        with open(bad, 'wb') as fh: fh.write(b'not a blend file at all' * 10)
        try: bc.render(bad, os.path.join(tmp, 'b.mp4'), DRAFT); r8 = 'no error'
        except rc.Cancelled: r8 = 'cancelled'
        except RuntimeError as e: r8 = str(e)
        check('B8 broken .blend: RuntimeError, no mp4 left', r8.startswith('Blender') and not os.path.exists(os.path.join(tmp, 'b.mp4')), r8[:200])
        # B9 preview
        pv = bc.preview(cube, out=os.path.join(tmp, 'sheet.jpg'))
        from PIL import Image
        im = None
        if pv.get('sheet'):
            with Image.open(pv['sheet']) as f0: im = f0.copy()   # closed at once, so the temp folder can be deleted
        check('B9 preview: ok, one sheet with a 16:9 and a 9:16 row (5 frames each)', pv['ok'] and im is not None and im.height > 500
              and set(pv['info']) == {'16:9', '9:16'}, (pv['problems'], im.size if im else None))
        # B10 the GUI queue function
        sd = os.path.join(tmp, 'sq'); sq = make(exe, sd, 'short_cube.blend', '--no-sound')
        with wave.open(os.path.join(sd, 'short_cube.wav'), 'wb') as wf:   # a song next to it (Sound Auto) ...
            wf.setnchannels(2); wf.setsampwidth(2); wf.setframerate(48000)
            wf.writeframes(b''.join(struct.pack('<hh', v, v) for v in (int(12000 * math.sin(2 * math.pi * 220 * i / 48000)) for i in range(48000 * 2))))
        with open(os.path.join(sd, 'short_cube.words.json'), 'w') as fh: json.dump({'words': [{'w': 'x', 's': 0, 'e': 1}]}, fh)   # ... with a words.json too
        ra = app.resolve_audio(sq, 'auto')
        check('B10 Sound Auto for a .blend: <scene>.wav is its song even with a words.json next to it', ra['label'] == 'short_cube.wav', ra)
        check('B10 narration is refused for .blend (captions are HTML-only)', 'HTML scenes only' in (app.narration_problem(sq, os.path.join(sd, 'short_cube.wav'), os.path.join(sd, 'short_cube.words.json')) or ''))
        check('B10 scene_info(.blend): 9:16 capable (no outdated warning)', app.scene_info(sq)['vertical'])
        out = os.path.join(tmp, 'out'); warns = []
        r = app.render_scene(sq, DRAFT, out, dict(vertical=False, format=app.FORMAT_AUTO, sound='auto', audio_file=None, capture='jpeg',
                                                  engine='eevee', device='auto', folder=app.FOLDER_NONE), warns=warns)
        want = os.path.join(out, 'cube', 'cube_shorts.mp4'); p = ffprobe(want) if os.path.isfile(want) else {}   # 3.12: a Short sits next to its video
        check('B10 render_scene: short_cube.blend -> <out>/cube/cube_shorts.mp4, 720x1280, with the song',
              r['status'] == 'done' and r['mp4'] == want and (p.get('w'), p.get('h')) == (720, 1280) and p.get('audio'), (r['status'], r['error'], p))
        check('B10 QC report + thumbnail (marker), status "Done · QC ..."', r['row'].startswith('Done · QC') and r['qc_report'] and os.path.isfile(r['qc_report'])
              and r['thumb'] and os.path.isfile(r['thumb']), (r['row'], r['qc'] and r['qc']['reasons']))
        try: app.render_item(sq, os.path.join(tmp, 'n.mp4'), DRAFT, dict(vertical=False, sound='generated', audio_file=None), narr={'wav': 'x'}); r11 = 'rendered'
        except ValueError as e: r11 = str(e)
        check('B10 render_item with narration on a .blend -> ValueError (no silent wrong video)', 'HTML scenes only' in r11, r11)
        # B11 CLI
        cli_out = os.path.join(tmp, 'cli.mp4')
        cp = subprocess.run([sys.executable, os.path.join(ROOT, 'app.py'), '--render', mute, cli_out, '--preset', 'draft', '--fast', '--engine', 'workbench'],
                            capture_output=True, text=True, timeout=600)
        p = ffprobe(cli_out) if os.path.isfile(cli_out) else {}
        check('B11 CLI renders a .blend (exit 0, 1280x720, QC report)', cp.returncode == 0 and (p.get('w'), p.get('h')) == (1280, 720)
              and os.path.isfile(os.path.join(tmp, 'cli.qc.txt')), (cp.returncode, p))
        # B12 MCP
        import mcp_server as ms
        plan = ms.build_plan([d, os.path.join(sd)], out_dir=os.path.join(tmp, 'mcp'), quality='draft', engine='workbench', capture='jpeg')
        kinds = {os.path.basename(x['scene']): x['kind'] for x in plan['rows']}
        check('B12 MCP plan: folders give their .blend scenes, kind "blender", Format Auto per name', kinds == {'cube.blend': 'blender', 'short_cube.blend': 'blender'}
              and [x['format'] for x in plan['rows']] == ['16:9', '9:16'], (kinds, [x['format'] for x in plan['rows']]))
        try: ms.build_plan([d], engine='octane'); bad_eng = False
        except ValueError: bad_eng = True
        check('B12 MCP: unknown engine -> ValueError', bad_eng)
        job = ms.Job(plan); th = threading.Thread(target=job.run); th.start(); th.join(600); s = job.snapshot()
        check('B12 MCP job renders both .blend scenes (Done · QC)', s['state'] == 'finished' and all(i['status'].startswith('Done') for i in s['items']),
              [i['status'] for i in s['items']])
        b13(cube, tmp)
    except Exception:
        check('crash', False, traceback.format_exc())
    finally:
        sys.modules['app'].close_log() if 'app' in sys.modules else None   # shadow.log in the temp folder is open: close it first
        shutil.rmtree(tmp, ignore_errors=True)
    print(f'{sum(RESULTS)}/{len(RESULTS)} passed'); return 0 if RESULTS and all(RESULTS) else 1

if __name__ == '__main__':
    sys.exit(main())
