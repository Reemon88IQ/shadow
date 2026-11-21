"""Regression checks for render_core.render() (no GUI). Windows and Linux.
Usage: python tests/regression.py            exit code 0 = all PASS, 1 = any FAIL

Renders tiny clips (640 px, 2 samples, 10 frames) with software WebGL, like smoke_test.py. Everything goes into one temp
folder that is deleted at the end; render_core's own temp folders are pointed there too, so leftovers can be checked.
"""
import json, os, shutil, subprocess, sys, tempfile, threading, time, traceback
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)
import render_core as rc

FIX = os.path.join(ROOT, 'tests', 'fixtures')
WEBGL = os.path.join(FIX, 'webgl_min.html')   # tests never read scenes/ (those are the owner's real videos)
PRESET = dict(w=640, samples=2)
KW = dict(max_frames=10, channel=None, extra_args=['--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
WIDE, TALL = (640, 360), (360, 640)

def probe(mp4):
    """(width, height, has_audio) or None."""
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,width,height', '-of', 'csv=p=0', mp4],
                       capture_output=True, text=True)
    if r.returncode != 0: return None
    v = [l.split(',') for l in r.stdout.split() if l.startswith('video')]
    return (int(v[0][1]), int(v[0][2]), 'audio' in r.stdout) if v else None

def render_dirs(tmp):
    return sorted(d for d in os.listdir(tmp) if d.startswith('bq_render_'))

def browsers():
    """Command lines of running browsers started by Playwright (they get --remote-debugging-pipe)."""
    try:
        if os.name == 'nt':
            out = subprocess.run(['powershell', '-NoProfile', '-Command',
                                  "Get-CimInstance Win32_Process | ForEach-Object { $_.CommandLine }"],
                                 capture_output=True, text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
        else:
            out = subprocess.run(['ps', '-eo', 'args'], capture_output=True, text=True).stdout
    except Exception:
        return None
    return [l for l in out.splitlines() if '--remote-debugging-pipe' in l]

# ---------------------------------------------------------------- cases: each returns (ok, reason)
def t1(tmp):
    """Scene path with spaces and '#'."""
    d = os.path.join(tmp, 'folder with spaces #1'); os.makedirs(d)
    scene = os.path.join(d, 'webgl scene #1.html'); shutil.copy(WEBGL, scene)
    out = os.path.join(tmp, 't1 out #1.mp4'); info = {}
    rc.render(scene, out, PRESET, info=info, **KW)
    p = probe(out)
    if p != (*WIDE, True): return False, f'expected 640x360 with audio, got {p}'
    return True, f"640x360 with audio from '{os.path.relpath(scene, tmp)}' (sound={info.get('sound')})"

def t2(tmp):
    """Scene without a sound score, asked for generated sound: renders silent, no crash."""
    out = os.path.join(tmp, 't2.mp4'); info = {}
    rc.render(os.path.join(FIX, 'minimal_silent.html'), out, PRESET, sound='generated', info=info, **KW)
    p = probe(out)
    if info.get('sound') != 'none': return False, f"info['sound'] should be 'none', got {info.get('sound')!r}"
    if p != (*WIDE, False): return False, f'expected 640x360 with no audio stream, got {p}'
    return True, "fell back to silent (info['sound']='none'), 640x360, no audio stream"

def t3(tmp):
    """Old scene that ignores the h param. v2.0 behaviour: render_core does NOT refuse 9:16 - the refusal/warning lives in
    the GUI (app.scene_info before rendering, size check after). So: the pre-check flags it, a 9:16 render does not crash but
    comes out at the wrong size (which is what the GUI's size check catches), and 16:9 renders normally."""
    old = os.path.join(FIX, 'outdated_no_h.html')
    try:
        os.environ['SHADOW_LANG'] = 'en'   # 3.20: the tests read the English texts
        import app   # pure helper only; no window is created
        flagged = app.scene_info(old)['vertical'] is False and app.scene_info(WEBGL)['vertical'] is True
    except Exception as e:
        return False, f'could not import app.scene_info: {e}'
    if not flagged: return False, 'app.scene_info did not flag the old scene as not supporting 9:16'
    tall = os.path.join(tmp, 't3_tall.mp4'); rc.render(old, tall, PRESET, vertical=True, **KW)
    pt = probe(tall)
    if pt is None or (pt[0], pt[1]) == TALL: return False, f'9:16 of an old scene should come out at the wrong size, got {pt}'
    wide = os.path.join(tmp, 't3_wide.mp4'); rc.render(old, wide, PRESET, **KW)
    pw = probe(wide)
    if pw is None or (pw[0], pw[1]) != WIDE: return False, f'16:9 should be 640x360, got {pw}'
    return True, f'flagged by scene_info; 9:16 gave {pt[0]}x{pt[1]} (not 360x640, caught by the size check); 16:9 gave 640x360'

def t4(tmp):
    """Cancel after a few frames: Cancelled is raised, no output, no leftover temp folder, no browser left running."""
    base = tempfile.gettempdir()     # where render_core makes its bq_render_* folder (set in main)
    before = browsers(); dirs0 = render_dirs(base)
    ev = threading.Event(); seen = []
    def prog(stage, a, b, eta=None):
        if stage == 'frames':
            seen.append(a)
            if a >= 3: ev.set()
    out = os.path.join(tmp, 't4.mp4')
    try:
        rc.render(WEBGL, out, PRESET, progress=prog, cancel=ev, **dict(KW, max_frames=60))
        return False, 'render finished; expected Cancelled'
    except rc.Cancelled:
        pass
    problems = []
    if os.path.exists(out): problems.append('an output file was written')
    if render_dirs(base) != dirs0: problems.append(f'temp folder left: {set(render_dirs(base)) - set(dirs0)}')
    after = browsers()
    if before is not None and after is not None and len(after) > len(before): problems.append(f'{len(after) - len(before)} browser process(es) still running')
    if problems: return False, '; '.join(problems)
    return True, f'Cancelled after frame {max(seen)}; no output, no temp folder, no browser left' + ('' if after is not None else ' (process check skipped)')

def t5(tmp):
    """External audio file mode: a short tone is muxed in, info['sound'] == 'file'."""
    tone = os.path.join(tmp, 'tone.wav')
    r = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', tone], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(tone): return False, f'could not make the tone: {r.stderr.strip()}'
    out = os.path.join(tmp, 't5.mp4'); info = {}
    rc.render(os.path.join(FIX, 'minimal_silent.html'), out, PRESET, sound='file', audio_file=tone, info=info, **KW)
    p = probe(out)
    if info.get('sound') != 'file': return False, f"info['sound'] should be 'file', got {info.get('sound')!r}"
    if p != (*WIDE, True): return False, f'expected 640x360 with audio, got {p}'
    return True, "sound='file', 640x360 with an audio stream"

def t6(tmp):
    """Broken scene (no seek): render() raises a clear error quickly, writes no mp4, leaves no temp folder or browser."""
    base = tempfile.gettempdir(); dirs0 = render_dirs(base); before = browsers()
    out = os.path.join(tmp, 't6.mp4'); res = {}
    def go():
        try: rc.render(os.path.join(FIX, 'broken_no_seek.html'), out, PRESET, **KW); res['ok'] = True
        except Exception as e: res['err'] = e
    th = threading.Thread(target=go, daemon=True); th.start(); th.join(120)
    if th.is_alive(): return False, 'render() still running after 120 s (hang)'
    if 'ok' in res: return False, 'render() finished without an error'
    e = res['err']; msg = (str(e).strip().splitlines() or [''])[0]
    problems = []
    if isinstance(e, rc.Cancelled): problems.append('raised Cancelled instead of an error')
    if 'seek' not in msg: problems.append(f'error does not mention seek: {msg!r}')
    if os.path.exists(out): problems.append('an mp4 was written')
    if render_dirs(base) != dirs0: problems.append(f'temp folder left: {set(render_dirs(base)) - set(dirs0)}')
    after = browsers()
    if before is not None and after is not None and len(after) > len(before): problems.append(f'{len(after) - len(before)} browser process(es) still running')
    if problems: return False, '; '.join(problems)
    return True, f'{type(e).__name__}: {msg}; no mp4, no temp folder, no browser left'

def t7(tmp):
    """A normal (full, uncapped) render reports info['scene_duration'] and info['duration'] (> 0), and info['duration']
    matches the mp4's real duration within 0.3 s."""
    out = os.path.join(tmp, 't7.mp4'); info = {}
    rc.render(WEBGL, out, PRESET, info=info, **dict(KW, max_frames=None))
    sd, d = info.get('scene_duration'), info.get('duration')
    if not all(isinstance(x, (int, float)) and x > 0 for x in (sd, d)):
        return False, f"info should hold scene_duration and duration > 0, got {info!r}"
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', out], capture_output=True, text=True)
    try: real = float(r.stdout.strip())
    except ValueError: return False, f'could not read the mp4 duration: {r.stdout!r} {r.stderr.strip()!r}'
    if abs(real - d) > 0.3: return False, f"info['duration'] {d:.3f} s vs mp4 {real:.3f} s (more than 0.3 s apart)"
    return True, f"scene_duration={sd:g} s, duration={d:.3f} s, mp4={real:.3f} s"

def t8(tmp):
    """The CLI (app.cli, what 'Shadow.exe --render' runs) still exits 0 and now also writes <name>.qc.txt and logs a 'cli qc'
    line. Same software WebGL / 10-frame cap as the other cases (wrapped in this process only); log lines are captured
    in memory, the real shadow.log is not touched."""
    import logging
    import app
    out = os.path.join(tmp, 't8 out #1.mp4'); lines = []
    h = logging.Handler(); h.emit = lambda r: lines.append(r.getMessage())
    old_level = app.log.level; app.log.addHandler(h); app.log.setLevel(logging.INFO)
    real = rc.render
    rc.render = lambda html, o, p, **kw: real(html, o, p, **{**kw, 'channel': None, 'extra_args': KW['extra_args'], 'max_frames': 10})
    try:
        code = app.cli(['--render', WEBGL, out, '--preset', 'draft'])
    finally:
        rc.render = real; app.log.removeHandler(h); app.log.setLevel(old_level)
    rep = os.path.splitext(out)[0] + '.qc.txt'
    qline = next((l for l in lines if l.startswith('cli qc ')), None)
    if code != 0: return False, f'exit code {code}, expected 0; log: {lines[-3:]}'
    if not os.path.isfile(out): return False, 'no mp4 written'
    if not os.path.isfile(rep): return False, f'no report {os.path.basename(rep)}; log: {lines[-3:]}'
    with open(rep, encoding='utf-8') as fh: first = fh.readline().strip()
    if not first.startswith('QC '): return False, f'report does not start with "QC ...": {first!r}'
    if not qline: return False, f"no 'cli qc' log line; log: {lines}"
    return True, f"exit 0, {os.path.basename(rep)} written ({first.split('  ')[0]}), log: {qline.split(' C:')[0].split(' /')[0]}"

# ---------------------------------------------------------------- narration (app.render_item / App._run_one, no window)
NARR_FIX = os.path.join(FIX, 'narr_2d.html')

def _narr_setup(tmp):
    """Copy the 2D narration fixture into a folder with spaces, make a 3 s 300 Hz 'voice' and words.json."""
    d = os.path.join(tmp, 'scene folder #2'); os.makedirs(d)
    scene = os.path.join(d, 'narr_2d.html'); shutil.copy(NARR_FIX, scene)
    wav = os.path.join(tmp, 'voice.wav')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=f=300:d=3,volume=0.6', '-ac', '1', '-c:a', 'pcm_s16le', wav], check=True)
    words = os.path.join(tmp, 'words.json')
    with open(words, 'w', encoding='utf-8') as fh: json.dump({'words': [{'w': 'Hello', 's': 0.5, 'e': 1.0}, {'w': 'storms', 's': 1.0, 'e': 1.6}]}, fh)
    return d, scene, dict(wav=wav, words=words, style='pop')

def _band_db(mp4, t0=0.3, t1=1.8):
    r = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-ss', str(t0), '-t', str(t1 - t0), '-i', mp4,
                        '-af', 'bandpass=f=300:width_type=h:w=40,volumedetect', '-f', 'null', '-'], capture_output=True, text=True, errors='replace')
    v = next((l.split('mean_volume:')[1].split()[0] for l in r.stderr.splitlines() if 'mean_volume' in l), None)
    return float(v) if v not in (None, '-inf') else -120.0

def _temps(d): return sorted(f for f in os.listdir(d) if f.startswith('.shadow_narr_'))

class _FakeApp:
    """Just the fields App._run_one uses, so the worker code runs without a window (narration comes as a parameter)."""
    def __init__(self, opts, narr):
        import queue as _q
        self.q = _q.Queue(); self.opts = opts; self.narr = narr; self.cancel = threading.Event()

def _patched_render(max_frames):
    real = rc.render
    rc.render = lambda html, o, p, **kw: real(html, o, p, **{**kw, 'channel': None, 'extra_args': KW['extra_args'], 'max_frames': max_frames})
    return real

def t9(tmp):
    """Narration through the real queue worker (App._run_one): Sound=None is overridden to generated with a warning,
    the voice is in the mp4, the mp4 is named after the original scene, the hidden temp scene is deleted, a stale one
    (> 1 h) is cleaned up, a fresh foreign one is left alone; bad narration input becomes a normal Error + failure log."""
    import app
    d, scene, narr = _narr_setup(tmp)
    stale, fresh = os.path.join(d, '.shadow_narr_stale_x.html'), os.path.join(d, '.shadow_narr_fresh_y.html')
    for p in (stale, fresh): open(p, 'w').close()
    os.utime(stale, (time.time() - 7200, time.time() - 7200))
    out = os.path.join(tmp, 'out'); os.makedirs(out)
    old_logdir = app.LOG_DIR; app.LOG_DIR = os.path.join(tmp, 'logs')
    real = _patched_render(60)
    try:
        orig = os.path.join(tmp, 'orig.mp4')
        app.render_item(scene, orig, PRESET, dict(vertical=False, sound='generated', audio_file=None))
        fake = _FakeApp(dict(vertical=False, sound='none', audio_file=None), {os.path.normpath(scene): narr})
        done, failed, warns = [], [], []
        app.App._run_one(fake, os.path.normpath(scene), 0, 1, PRESET, out, '16:9 (YouTube)', done, failed, warns, set(), threading.Event(), narr=narr)
        bad = _FakeApp(dict(vertical=False, sound='generated', audio_file=None), {os.path.normpath(scene): dict(narr, wav=os.path.join(tmp, 'missing.wav'))})
        d2, f2, w2 = [], [], []
        app.App._run_one(bad, os.path.normpath(scene), 0, 1, PRESET, out, '16:9 (YouTube)', d2, f2, w2, set(), threading.Event(), narr=dict(narr, wav=os.path.join(tmp, 'missing.wav')))
    finally:
        rc.render = real; app.LOG_DIR = old_logdir
    rows = [m for m in list(fake.q.queue) if m[0] == 'row']; rows2 = [m for m in list(bad.q.queue) if m[0] == 'row']
    want = os.path.join(out, 'narr_2d', 'narr_2d.mp4')   # <out>\<scene name>\<scene>.mp4, named after the ORIGINAL scene
    if failed or done != [want]: return False, f'expected {os.path.basename(want)} done, got done={[os.path.basename(x) for x in done]} failed={failed}'
    if not rows or not rows[-1][2].startswith('Done'): return False, f'row status {rows[-1][2] if rows else None!r}'
    if not any('narration needs generated sound' in w for w in warns): return False, f'no "needs generated sound" warning: {warns}'
    o_db, n_db = _band_db(orig), _band_db(want)
    if n_db < o_db + 6: return False, f'voice not mixed: 300 Hz band narrated {n_db:.1f} dB vs original {o_db:.1f} dB'
    if _temps(d) != ['.shadow_narr_fresh_y.html']: return False, f'temp files left / stale not cleaned: {_temps(d)}'
    if d2 or not f2 or 'narration file not found' not in f2[0] or 'Log:' not in f2[0] or not rows2 or rows2[-1][2] != 'Error':
        return False, f'bad narration should be Error + log: done={d2} failed={f2} row={rows2[-1][2] if rows2 else None}'
    logs = [f for f in os.listdir(os.path.join(out, 'narr_2d')) if f.endswith('.log')]   # the failure log is in the scene's own folder
    if not logs: return False, 'no failure log written in the scene folder'
    if os.path.isdir(os.path.join(tmp, 'logs')) and os.listdir(os.path.join(tmp, 'logs')): return False, 'failure log went to the fallback logs folder'
    if not os.path.isfile(os.path.join(out, 'narr_2d', 'narr_2d.qc.txt')): return False, 'no narr_2d.qc.txt next to the mp4'
    return True, (f'{os.path.basename(want)}, voice {n_db:.1f} dB vs {o_db:.1f} dB, Sound=None -> generated (warned), '
                  f'temp deleted, stale removed, fresh kept; in out/narr_2d/: mp4 + qc.txt + failure log ({len(logs)}) for the bad wav')

def t10(tmp):
    """Cancel with narration: (a) cancel already set -> Cancelled before the temp scene is even built;
    (b) cancel after a few frames -> Cancelled, no mp4, temp scene deleted."""
    import app
    d, scene, narr = _narr_setup(tmp)
    opts = dict(vertical=False, sound='generated', audio_file=None)
    real = _patched_render(90)
    try:
        ev = threading.Event(); ev.set(); built = []
        orig_build = app.narrate_for_render
        app.narrate_for_render = lambda *a, **k: built.append(1) or orig_build(*a, **k)
        try: app.render_item(scene, os.path.join(tmp, 'a.mp4'), PRESET, opts, narr=narr, cancel=ev); ra = 'finished'
        except rc.Cancelled: ra = 'Cancelled'
        finally: app.narrate_for_render = orig_build
        ev2 = threading.Event(); seen = []
        def prog(stage, a, b, eta=None):
            if stage == 'frames':
                seen.append(a)
                if a >= 3: ev2.set()
        try: app.render_item(scene, os.path.join(tmp, 'b.mp4'), PRESET, opts, narr=narr, cancel=ev2, progress=prog); rb = 'finished'
        except rc.Cancelled: rb = 'Cancelled'
    finally:
        rc.render = real
    if ra != 'Cancelled' or built: return False, f'(a) cancel before: {ra}, temp scene built {len(built)} time(s)'
    if rb != 'Cancelled' or os.path.exists(os.path.join(tmp, 'b.mp4')): return False, f'(b) cancel during frames: {rb}, mp4 exists={os.path.exists(os.path.join(tmp, "b.mp4"))}'
    if _temps(d): return False, f'temp scene left: {_temps(d)}'
    return True, f'(a) Cancelled before building; (b) Cancelled after frame {max(seen)}, no mp4, no temp scene left'

CASES = [('T1 path with spaces and #', t1), ('T2 silent scene', t2), ('T3 old scene without h', t3), ('T4 cancel', t4),
         ('T5 external audio file', t5), ('T6 broken scene without seek', t6), ('T7 info durations', t7),
         ('T8 CLI writes a QC report', t8), ('T9 narration via the queue worker', t9), ('T10 cancel with narration', t10)]

def main():
    root = tempfile.mkdtemp(prefix='shadow_regression_')
    tempfile.tempdir = root          # render_core's mkdtemp() lands here too, so T4 can see leftovers
    fails = 0
    try:
        for name, fn in CASES:
            case = os.path.join(root, name.split()[0]); os.makedirs(case)
            try:
                ok, why = fn(case)
            except Exception as e:
                ok, why = False, f'{type(e).__name__}: {(str(e).strip().splitlines() or [""])[0]}'
                traceback.print_exc()
            fails += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {name}: {why}", flush=True)
    finally:
        tempfile.tempdir = None
        shutil.rmtree(root, ignore_errors=True)
    print(f'{len(CASES) - fails}/{len(CASES)} passed')
    return 1 if fails else 0

if __name__ == '__main__':
    sys.exit(main())
