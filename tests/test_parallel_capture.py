"""Parallel frame capture + 1440p / 4K presets (3.15). Fixture scenes only, software WebGL (like regression.py), temp folder.
  F1 capture_workers: auto by cores, int as given, never more than frames, SHADOW_CAPTURE_WORKERS override
  F2 _FrameSink: frames arriving out of order from several senders are written in order; back-pressure; stop; ffmpeg error
  F3 render(): workers 0 (old path) / 1 / 3 give the same frames for a pure scene (framemd5), the right frame count, info
  F4 a scene whose seek() throws at one frame -> clear 'frame capture failed' error, no mp4 / .part left
  F5 cancel during parallel capture -> Cancelled, no mp4 / .part left
  F6 presets 1440p / 4K exist with the right sizes (16:9 and 9:16), MCP quality names, a 1440p render has the right size
Usage: python tests/test_parallel_capture.py     exit 0 = all PASS"""
import hashlib, io, os, shutil, subprocess, sys, tempfile, threading, time
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, 'tools')); sys.path.insert(0, os.path.dirname(__file__))
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import render_core as rc
from regression import KW

fails = []
def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f'  ({info})' if info and not ok else ''))
    if not ok: fails.append(name)

TMP = tempfile.mkdtemp(prefix='shadow_parallel_test_')
FIX = os.path.join(ROOT, 'tests', 'fixtures'); WEBGL = os.path.join(FIX, 'webgl_min.html'); SILENT = os.path.join(FIX, 'minimal_silent.html')
SW = dict(channel=KW['channel'], extra_args=KW['extra_args'])
def frames_md5(mp4):
    out = subprocess.run(['ffmpeg', '-v', 'error', '-i', mp4, '-map', '0:v', '-f', 'framemd5', '-'], capture_output=True, text=True).stdout
    rows = [l.split(',')[-1].strip() for l in out.splitlines() if l and not l.startswith('#')]
    return len(rows), hashlib.md5(''.join(rows).encode()).hexdigest()

# ---------------------------------------------------------------- F1
saved = os.environ.pop('SHADOW_CAPTURE_WORKERS', None)
try:
    auto = rc.capture_workers('auto'); c = os.cpu_count() or 2
    check('F1 auto: 4 on 8+ cores, 2 on 4+, else 1', auto == (4 if c >= 8 else 2 if c >= 4 else 1), (auto, c))
    check('F1 int as given, 0 = old path, never more than frames', rc.capture_workers(3) == 3 and rc.capture_workers(0) == 0 and rc.capture_workers('auto', 2) == min(auto, 2))
    os.environ['SHADOW_CAPTURE_WORKERS'] = '1'
    check('F1 SHADOW_CAPTURE_WORKERS=1 overrides auto (not an explicit number)', rc.capture_workers('auto') == 1 and rc.capture_workers(3) == 3)
finally:
    os.environ.pop('SHADOW_CAPTURE_WORKERS', None)
    if saved is not None: os.environ['SHADOW_CAPTURE_WORKERS'] = saved

# ---------------------------------------------------------------- F2
out = io.BytesIO(); s = rc._FrameSink(); s.open(out, 12, window=4)
order = [1, 3, 0, 2, 5, 7, 4, 6, 9, 11, 8, 10]
codes = []
th = [threading.Thread(target=lambda i=i: codes.append(s.put(i, bytes([i])))) for i in order]
for t in th: t.start(); time.sleep(0.01)
for t in th: t.join(10)
check('F2 out-of-order frames from many senders are written in frame order', out.getvalue() == bytes(range(12)) and s.written == 12 and set(codes) == {204}, (out.getvalue(), codes))
check('F2 a frame sent twice is ignored (409)', s.put(3, b'x') == 409 and out.getvalue() == bytes(range(12)))
s2 = rc._FrameSink(); s2.open(io.BytesIO(), 100, window=4); r = {}
t = threading.Thread(target=lambda: r.setdefault('code', s2.put(10, b'z'))); t.start(); time.sleep(0.6)
check('F2 back-pressure: a frame far ahead waits', t.is_alive() and 'code' not in r)
s2.stop(); t.join(5)
check('F2 stop releases it with 409', r.get('code') == 409)
class Broken:
    def write(self, b): raise OSError('pipe closed')
s3 = rc._FrameSink(); s3.open(Broken(), 5)
check('F2 ffmpeg gone -> 500 and sink.error set', s3.put(0, b'a') == 500 and s3.error is not None)
try: s3.put(99, b'x'); check('F2 frame number outside the render -> ValueError', False)
except ValueError: check('F2 frame number outside the render -> ValueError', True)

# ---------------------------------------------------------------- F3
P = dict(w=640, samples=2); res = {}
for w in (0, 1, 3):
    o = os.path.join(TMP, f'w{w}.mp4'); info = {}
    rc.render(WEBGL, o, P, sound='none', max_frames=24, workers=w, info=info, **SW); res[w] = (frames_md5(o), info.get('workers'))
check('F3 24 frames each, info says how many pages', all(v[0][0] == 24 for v in res.values()) and [res[w][1] for w in (0, 1, 3)] == [0, 1, 3], res)
check('F3 old path, 1 page and 3 pages give the same frames (pure scene)', res[0][0][1] == res[1][0][1] == res[3][0][1], res)
o = os.path.join(TMP, 'snd.mp4'); info = {}
rc.render(WEBGL, o, P, sound='generated', max_frames=45, workers=3, info=info, **SW)
pr = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', o], capture_output=True, text=True).stdout.split()
check('F3 parallel render with generated sound: video + audio streams', sorted(pr) == ['audio', 'video'] and info.get('sound') == 'generated', (pr, info.get('sound')))

# ---------------------------------------------------------------- F4
src = open(SILENT, encoding='utf-8').read()
bad = os.path.join(TMP, 'throws.html')
open(bad, 'w', encoding='utf-8').write(src.replace('window.seek = (t) => {', "window.seek = (t) => { if (Math.round(t * 30) === 17) throw new Error('boom at 17');", 1))
o = os.path.join(TMP, 'throws.mp4')
try: rc.render(bad, o, P, sound='none', workers=3, **SW); msg = 'rendered'
except Exception as e: msg = str(e)
check('F4 seek throws at one frame -> "frame capture failed: ... boom at 17"', 'frame capture failed' in msg and 'boom at 17' in msg, msg)
check('F4 no mp4 / .part left', not [f for f in os.listdir(TMP) if f.startswith('throws') and f.endswith('.mp4')], os.listdir(TMP))

# ---------------------------------------------------------------- F5
long = os.path.join(TMP, 'long.html'); open(long, 'w', encoding='utf-8').write(src.replace('window.DURATION = 1.5;', 'window.DURATION = 60;'))
ev = threading.Event(); seen = []
def prog(stage, a, b, *x):
    seen.append(stage)
    if stage == 'frames' and a >= 20: ev.set()
o = os.path.join(TMP, 'long.mp4'); t0 = time.time()
try: rc.render(long, o, P, sound='none', workers=3, cancel=ev, progress=prog, **SW); got = 'finished'
except rc.Cancelled: got = 'cancelled'
check('F5 cancel during parallel capture -> Cancelled quickly, no mp4 / .part left', got == 'cancelled' and time.time() - t0 < 60
      and not [f for f in os.listdir(TMP) if f.startswith('long.') and f.endswith('.mp4')], (got, round(time.time() - t0, 1), os.listdir(TMP)))

# ---------------------------------------------------------------- F6
q = rc.PRESETS.get('1440p (QHD, 64 samples)'); u = rc.PRESETS.get('4K (UHD, 64 samples)')
import app
check('F6 presets: 1440p 2560 wide, 4K 3840 wide; expected sizes 16:9 and 9:16', q and u and q['w'] == 2560 and u['w'] == 3840
      and app.expected_size(u, False) == (3840, 2160) and app.expected_size(u, True) == (2160, 3840) and app.expected_size(q, False) == (2560, 1440))
import mcp_server as ms
check('F6 MCP quality names 1440p / 4k', ms.QUALITY['1440p'] == '1440p (QHD, 64 samples)' and ms.QUALITY['4k'] == '4K (UHD, 64 samples)')
o = os.path.join(TMP, 'qhd.mp4'); rc.render(SILENT, o, dict(q, samples=2), sound='none', max_frames=6, **SW)
wh = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v', '-show_entries', 'stream=width,height', '-of', 'csv=p=0', o], capture_output=True, text=True).stdout.strip()
check('F6 a 1440p render is 2560x1440', wh == '2560,1440', wh)

app.close_log(); shutil.rmtree(TMP, ignore_errors=True)
print('\nALL PASS' if not fails else f'\n{len(fails)} FAIL: {fails}'); sys.exit(1 if fails else 0)
