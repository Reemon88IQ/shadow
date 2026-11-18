"""render_core v3.4: frames piped into ffmpeg (no frame files), capture='png' | 'jpeg' (Fast capture), <name>.part.mp4.
Usage: python tests/test_capture.py   exit 0 = all PASS. Windows and Linux (software WebGL, tests/fixtures only).
  C1 png and jpeg both give a full mp4 (size, audio, duration), info['capture'] set, no .part.mp4 left
  C2 a jpeg frame looks like the png frame (same colours: mean difference tiny)
  C3 progress order load -> audio -> frames -> encode -> done
  C4 cancel while frames are being piped: Cancelled, no mp4, no .part.mp4
  C5 ffmpeg fails (bad encoder): RuntimeError with ffmpeg's own message, no mp4, no .part.mp4
  C6 a bad capture value is refused before anything starts
  C7 an existing file with the output name is not touched by a cancelled render"""
import os, shutil, subprocess, sys, tempfile, threading
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import render_core as rc
from regression import WEBGL, PRESET, KW, probe

TMP = tempfile.mkdtemp(prefix='shadow_capture_')
res = []
def check(name, ok, info=''):
    res.append(bool(ok)); print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else f'  ({info})'))

def dur(mp4):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', mp4], capture_output=True, text=True)
    return float(r.stdout.strip() or 0)

def frame(mp4, t, png):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(t), '-i', mp4, '-frames:v', '1', png], check=True)
    from PIL import Image
    return Image.open(png).convert('RGB')

def leftovers(folder):
    return [f for f in os.listdir(folder) if '.part' in f]

try:
    outs = {}
    for cap in ('png', 'jpeg'):
        out = os.path.join(TMP, f'c1 {cap}.mp4'); info = {}; stages = []
        rc.render(WEBGL, out, PRESET, info=info, capture=cap, progress=lambda s, *a: stages.append(s) if not stages or stages[-1] != s else None,
                  **dict(KW, max_frames=None))
        pr = probe(out); outs[cap] = out
        check(f'C1 {cap}: 640x360 with audio, {dur(out):.2f} s, info capture={info.get("capture")}, no .part left',
              pr == (640, 360, True) and abs(dur(out) - 4.0) < 0.1 and info.get('capture') == cap and not leftovers(TMP), (pr, dur(out), info, leftovers(TMP)))
        if cap == 'png':
            check('C3 progress order load, audio, frames, encode, done', stages == ['load', 'audio', 'frames', 'encode', 'done'], stages)

    from PIL import ImageChops, ImageStat
    a = frame(outs['png'], 2.0, os.path.join(TMP, 'a.png')); b = frame(outs['jpeg'], 2.0, os.path.join(TMP, 'b.png'))
    mean = sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3
    check(f'C2 jpeg frame matches the png frame (mean colour difference {mean:.2f} of 255)', mean < 2.0, mean)

    ev = threading.Event()
    def stop_at_3(stage, n, *r):
        if stage == 'frames' and n >= 3: ev.set()
    out = os.path.join(TMP, 'c4.mp4')
    try: rc.render(WEBGL, out, PRESET, cancel=ev, progress=stop_at_3, capture='jpeg', **dict(KW, max_frames=60)); ok = False
    except rc.Cancelled: ok = True
    check('C4 cancel during piping: Cancelled, no mp4, no .part.mp4', ok and not os.path.exists(out) and not leftovers(TMP), (ok, leftovers(TMP)))

    real = rc.x264_args; rc.x264_args = lambda: ['-c:v', 'no_such_encoder_xyz', '-pix_fmt', 'yuv420p']
    out = os.path.join(TMP, 'c5.mp4'); msg = ''
    try:
        rc.render(WEBGL, out, PRESET, **KW); ok = False
    except RuntimeError as e: msg = str(e); ok = msg.startswith('ffmpeg failed:') and 'encoder' in msg.lower()
    finally: rc.x264_args = real
    check(f'C5 ffmpeg failure reported with its own message: {msg[:90]!r}', ok and not os.path.exists(out) and not leftovers(TMP), (msg, leftovers(TMP)))

    try: rc.render(WEBGL, os.path.join(TMP, 'c6.mp4'), PRESET, capture='webp', **KW); ok = False
    except ValueError: ok = True
    check('C6 capture="webp" refused with ValueError', ok)

    out = os.path.join(TMP, 'c7.mp4'); open(out, 'wb').write(b'keep me'); ev = threading.Event()
    try: rc.render(WEBGL, out, PRESET, cancel=ev, progress=lambda s, n, *r: ev.set() if s == 'frames' and n >= 2 else None, **dict(KW, max_frames=60))
    except rc.Cancelled: pass
    check('C7 an existing file with the output name is untouched after a cancelled render', open(out, 'rb').read() == b'keep me' and not leftovers(TMP))
    # C8/C9: the app side - the Capture setting reaches render_core, and it is remembered (temp settings file only)
    import json, app
    rec = {}; real = rc.render
    rc.render = lambda *a, **k: rec.update(k)
    try: app.render_item(WEBGL, os.path.join(TMP, 'c8.mp4'), PRESET, dict(vertical=False, sound='none', audio_file=None, capture='jpeg'))
    finally: rc.render = real
    check('C8 queue item passes capture to render_core', rec.get('capture') == 'jpeg', rec)
    app.CFG = os.path.join(TMP, 'cfg.json'); app.LOG = os.path.join(TMP, 'shadow.log'); app.LOG_DIR = TMP
    fast = [k for k, v in app.CAPTURES.items() if v == 'jpeg'][0]
    json.dump({'capture': fast}, open(app.CFG, 'w'))
    a = app.App(); a.withdraw()
    try:
        loaded = a.capture.get(); a.capture.set(next(iter(app.CAPTURES))); a.save()
        saved = json.load(open(app.CFG))['capture']
    finally:
        a.after_cancel(a._poll_id); a.destroy()
    check(f'C9 Capture setting loads ({loaded!r}) and saves ({saved!r})', loaded == fast and app.CAPTURES[saved] == 'png', (loaded, saved))
finally:
    shutil.rmtree(TMP, ignore_errors=True)
print(f'\n{sum(res)}/{len(res)} passed'); sys.exit(0 if all(res) else 1)
