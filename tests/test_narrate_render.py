"""Narration really reaches the MP4: renders scenes with and without embedded narration through render_core and compares.
Usage: python tests/test_narrate_render.py      exit 0 = all PASS, 1 = any FAIL
  - voice mixed: the 300 Hz band (the test narration is a 300 Hz tone) is clearly louder than in the original render,
    for the WebGL fixture tests/fixtures/webgl_min.html (SOUND kit in a <script type="module">, like the real three.js scenes)
    and the 2D fixture tests/fixtures/narr_2d.html (SOUND in a classic script, already ready when the block runs);
  - captions: drawn on the 2D fixture, reported as unsupported on WebGL scenes;
  - a scene whose SOUND cannot be wrapped never becomes ready (render fails clearly, no silent video).
Software WebGL, 640 px, 75 frames, like the other tests. Fixtures are copied into a temp folder; scenes/ is never read."""
import functools, http.server, os, shutil, subprocess, sys, tempfile, threading, urllib.parse
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, 'tools', 'narrate'))
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import render_core as rc
import make_narrated_scene as mns

KW = dict(channel=None, extra_args=['--use-angle=swiftshader', '--enable-unsafe-swiftshader'], max_frames=75)
PRESET = dict(w=640, samples=2)
WORDS = [{'w': 'Stars', 's': 0.5, 'e': 1.0}, {'w': 'shine', 's': 1.0, 'e': 1.6}, {'w': 'are', 's': 1.7, 'e': 2.0}, {'w': 'bright.', 's': 2.0, 'e': 2.4}]
T_WORD = 1.3              # inside "storms"
BAND = (0.3, 2.3)         # seconds compared
MIN_GAIN_DB = 6.0         # narrated 300 Hz band must be at least this much louder than the original
SCENES = [('WebGL fixture', os.path.join(ROOT, 'tests', 'fixtures', 'webgl_min.html'), True),
          ('2D fixture', os.path.join(ROOT, 'tests', 'fixtures', 'narr_2d.html'), False)]
results = []

def check(label, ok, seen=''):
    results.append(bool(ok)); print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f': {seen}' if seen else ''), flush=True)

def band_db(mp4):
    r = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-ss', str(BAND[0]), '-t', str(BAND[1] - BAND[0]), '-i', mp4,
                        '-af', 'bandpass=f=300:width_type=h:w=40,volumedetect', '-f', 'null', '-'], capture_output=True, text=True, errors='replace')
    v = next((l.split('mean_volume:')[1].split()[0] for l in r.stderr.splitlines() if 'mean_volume' in l), None)
    return float(v) if v not in (None, '-inf') else -120.0

def yellow_pixels(mp4, t):
    """Pixels of the caption highlight colour (#ffd23f) in the frame at t."""
    r = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(t), '-i', mp4, '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True)
    b = r.stdout
    return sum(1 for i in range(0, len(b) - 2, 3) if b[i] > 230 and 175 < b[i + 1] < 235 and b[i + 2] < 100)

def main():
    tmp = tempfile.mkdtemp(prefix='shadow_narr_render_')
    try:
        voice = os.path.join(tmp, 'voice.wav')
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=f=300:d=3.5,volume=0.6', '-ac', '1', '-c:a', 'pcm_s16le', voice], check=True)
        for name, src, webgl in SCENES:
            d = os.path.join(tmp, name.replace(' ', '_')); os.makedirs(d)
            with open(src, encoding='utf-8') as fh: html = fh.read()
            shutil.copy(src, os.path.join(d, 'orig.html'))
            info = {}
            with open(os.path.join(d, 'narr.html'), 'w', encoding='utf-8') as fh:
                fh.write(mns.build_narrated_html(html, voice, WORDS, style='pop', info=info))
            webgl_warn = any('captions not supported (WebGL)' in w for w in info['warnings'])
            check(f'{name}: WebGL caption warning {"present" if webgl else "absent"}', webgl_warn == webgl, '; '.join(info['warnings']) or 'no warnings')
            o_mp4, n_mp4 = os.path.join(d, 'orig.mp4'), os.path.join(d, 'narr.mp4'); ri = {}
            try:
                rc.render(os.path.join(d, 'orig.html'), o_mp4, PRESET, **KW)
                rc.render(os.path.join(d, 'narr.html'), n_mp4, PRESET, info=ri, **KW)
            except Exception as e:
                check(f'{name}: renders with narration', False, f'{type(e).__name__}: {str(e).splitlines()[0]}'); continue
            o_db, n_db = band_db(o_mp4), band_db(n_mp4)
            check(f'{name}: voice mixed (300 Hz band)', ri.get('sound') == 'generated' and n_db >= o_db + MIN_GAIN_DB,
                  f'narrated {n_db:.1f} dB vs original {o_db:.1f} dB (+{n_db - o_db:.1f}, need +{MIN_GAIN_DB:g}), sound={ri.get("sound")}')
            if not webgl:
                yo, yn = yellow_pixels(o_mp4, T_WORD), yellow_pixels(n_mp4, T_WORD)
                check(f'{name}: caption highlight visible at {T_WORD}s', yn > 100 and yo == 0, f'yellow pixels narrated {yn}, original {yo}')

        # a scene whose SOUND has no renderOffline: the wrap fails, __ready must stay false and a page error must say why
        with open(os.path.join(ROOT, 'tests', 'fixtures', 'narr_2d.html'), encoding='utf-8') as fh: html = fh.read()
        broken = html.replace('async renderOffline(', 'async renderOfflineGone(')
        d = os.path.join(tmp, 'broken'); os.makedirs(d)
        with open(os.path.join(d, 'narr.html'), 'w', encoding='utf-8') as fh: fh.write(mns.build_narrated_html(broken, voice, WORDS))
        class Q(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a): pass
        srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Q, directory=d)); threading.Thread(target=srv.serve_forever, daemon=True).start()
        from playwright.sync_api import sync_playwright
        errs = []
        try:
            with sync_playwright() as p:
                b = p.chromium.launch(**{k: v for k, v in KW.items() if k == 'channel'}, args=KW['extra_args'])
                pg = b.new_page(); pg.on('pageerror', lambda e: errs.append(str(e)))
                pg.goto(f'http://127.0.0.1:{srv.server_address[1]}/narr.html?render=1&w=640&h=360'); pg.wait_for_timeout(2500)
                ready, nerr = pg.evaluate('[window.__ready===true, window.__narrError||null]'); b.close()
        finally:
            srv.shutdown()
        check('wrap fails -> scene never ready, page error says why', ready is False and nerr and any('narration:' in e for e in errs),
              f'__ready={ready}, __narrError={nerr!r}, page errors={errs[:1]}')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f'{sum(results)}/{len(results)} passed')
    return 0 if results and all(results) else 1

if __name__ == '__main__':
    sys.exit(main())
