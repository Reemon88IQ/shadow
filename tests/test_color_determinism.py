"""BT.709 colour + the same Math.random / Date in every render page (3.17). Scenes are generated in a temp folder (never
scenes/); software WebGL / bundled Chromium like regression.py.
  K1 the mp4 is tagged BT.709 / limited range (ffprobe), PNG and JPEG capture
  K2 decoded back to RGB (honouring the tags), pure red / green / blue / grey bars come out as drawn (within 4 levels);
     raw Y of the red bar is BT.709's 63, not BT.601's 81 (what 3.16 and older wrote)
  K3 a scene that picks its layout with Math.random() and Date.now() while loading: 1 page and 3 pages give the same frames,
     and two renders give the same video (before 3.17 every copy drew a different layout)
  K4 Date still works for the scene: Date() as a function, new Date(y, m, d), instanceof Date; Math.random stays in [0, 1)
  K5 Blender path uses the same filter + tags (no Blender run needed: the command line is checked)
Usage: python tests/test_color_determinism.py     exit 0 = all PASS"""
import hashlib, os, shutil, subprocess, sys, tempfile
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(__file__))
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import render_core as rc
from regression import KW

fails = []
def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f'  ({info})' if info and not ok else ''))
    if not ok: fails.append(name)

TMP = tempfile.mkdtemp(prefix='shadow_color_test_')
SW = dict(channel=KW['channel'], extra_args=KW['extra_args'])
P = dict(w=640, samples=1)
BARS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (128, 128, 128)]
def scene(name, body):
    p = os.path.join(TMP, name)
    with open(p, 'w', encoding='utf-8') as f:
        f.write('<!doctype html><meta charset=utf-8><body style="margin:0"><canvas id=c></canvas><script>\n'
                "const q = new URLSearchParams(location.search), W = +q.get('w') || 640, H = +q.get('h') || 360;\n"
                "const c = document.getElementById('c'); c.width = W; c.height = H; const g = c.getContext('2d');\n"
                + body + '\nwindow.DURATION = 1; window.seek(0); window.__ready = true;\n</script>')
    return p
bars = scene('bars.html', 'const B = %s; window.seek = (t) => { B.forEach((b, i) => { g.fillStyle = `rgb(${b})`; g.fillRect(i * W / 4, 0, W / 4, H); }); };' % [list(b) for b in BARS])
def probe_tags(mp4):
    out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v', '-show_entries', 'stream=color_space,color_primaries,color_transfer,color_range',
                          '-of', 'default=nw=1', mp4], capture_output=True, text=True).stdout
    return dict(l.split('=', 1) for l in out.split())
def pixels(mp4, fmt='rgb24', w=640, h=360):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', mp4, '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', fmt, '-'], capture_output=True).stdout
    return raw
def bar_rgb(raw, i, w=640, h=360):
    x, y = int((i + 0.5) * w / 4), h // 2; o = (y * w + x) * 3
    return tuple(raw[o:o + 3])

# ---------------------------------------------------------------- K1 + K2
for cap in ('png', 'jpeg'):
    out = os.path.join(TMP, f'bars_{cap}.mp4')
    rc.render(bars, out, P, sound='none', capture=cap, max_frames=3, **SW)
    tags = probe_tags(out)
    check(f'K1 {cap}: tagged bt709 / bt709 / bt709 / tv', tags == {'color_space': 'bt709', 'color_primaries': 'bt709', 'color_transfer': 'bt709', 'color_range': 'tv'}, tags)
    raw = pixels(out); got = [bar_rgb(raw, i) for i in range(4)]
    worst = max(abs(a - b) for g_, e in zip(got, BARS) for a, b in zip(g_, e))
    check(f'K2 {cap}: red / green / blue / grey bars decode as drawn (worst {worst} levels, limit {6 if cap == "jpeg" else 4})',
          worst <= (6 if cap == 'jpeg' else 4), got)
y = pixels(os.path.join(TMP, 'bars_png.mp4'), 'yuv420p')   # the stored Y plane itself (first w*h bytes), no conversion
red_y = y[(360 // 2) * 640 + 80]
check('K2 raw luma of pure red is BT.709 limited (63 +-2), not BT.601 (81)', abs(red_y - 63) <= 2, red_y)

# ---------------------------------------------------------------- K3 + K4
rnd = scene('rnd.html', '''
const seed = Date.now() % 1000; const dots = [];
for (let i = 0; i < 40; i++) dots.push([Math.random() * W, Math.random() * H, 4 + Math.random() * 20, Math.floor(Math.random() * 360)]);
window.K4 = { str: typeof Date() === 'string', y: new Date(2020, 1, 3).getFullYear(), inst: new Date() instanceof Date,
              range: Array.from({length: 1000}, Math.random).every(v => v >= 0 && v < 1), now: Date.now(), seed };
window.seek = (t) => { g.fillStyle = `hsl(${seed % 360},40%,10%)`; g.fillRect(0, 0, W, H);
  dots.forEach(([x, y0, r, h], i) => { g.fillStyle = `hsl(${h},80%,60%)`; g.beginPath(); g.arc((x + t * 60 * (i % 5)) % W, y0, r, 0, 7); g.fill(); }); };''')
def frames_md5(mp4):
    out = subprocess.run(['ffmpeg', '-v', 'error', '-i', mp4, '-map', '0:v', '-f', 'framemd5', '-'], capture_output=True, text=True).stdout
    rows = [l.split(',')[-1].strip() for l in out.splitlines() if l and not l.startswith('#')]
    return len(rows), hashlib.md5(''.join(rows).encode()).hexdigest()
res = {}
for name, w in (('one', 1), ('three', 3), ('three_again', 3), ('old_path', 0)):
    o = os.path.join(TMP, f'rnd_{name}.mp4'); rc.render(rnd, o, P, sound='none', max_frames=24, workers=w, **SW); res[name] = frames_md5(o)
check('K3 Math.random + Date.now at setup: 1 page, 3 pages, again, old path -> the same 24 frames',
      res['one'][0] == 24 and len({v[1] for v in res.values()}) == 1, res)
from playwright.sync_api import sync_playwright
import functools, http.server, threading, urllib.parse
class Q(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Q, directory=TMP)); threading.Thread(target=srv.serve_forever, daemon=True).start()
try:
    with sync_playwright() as p:
        b = p.chromium.launch(args=KW['extra_args']); pg = b.new_page(); pg.add_init_script(rc.RENDER_INIT_JS)
        pg.goto(f'http://127.0.0.1:{srv.server_address[1]}/rnd.html?render=1&w=640&h=360'); pg.wait_for_function('window.__ready===true')
        k4 = pg.evaluate('window.K4'); b.close()
finally: srv.shutdown()
check('K4 Date() string, new Date(2020,1,3), instanceof, Math.random in [0,1), Date.now frozen at 2026-01-01',
      k4['str'] and k4['y'] == 2020 and k4['inst'] and k4['range'] and k4['now'] == rc.RENDER_EPOCH_MS, k4)

# ---------------------------------------------------------------- K5
src = open(os.path.join(ROOT, 'blender_core.py'), encoding='utf-8').read()
check('K5 the Blender path uses rc.video_filter() + rc.x264_args() (same conversion and tags)', "rc.video_filter(), *rc.x264_args()" in src)

shutil.rmtree(TMP, ignore_errors=True)
print('\nALL PASS' if not fails else f'\n{len(fails)} FAIL: {fails}'); sys.exit(1 if fails else 0)
