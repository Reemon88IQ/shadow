"""Shadow's frame check, shared by tools/check_frames.py (CLI), the app's "Check frames" button and `Shadow.exe --check`:
a contact sheet of a few frames in 16:9 and 9:16 plus the scene's problems, in seconds instead of a full render.
HTML: the installed Chrome via Playwright, the scene's folder served like Shadow does (narration is not attached here);
page errors and whether seek(t) is pure (the same time drawn twice must give the same frame).
.blend: Blender renders the frames (blender_core.preview); no purity check (Blender is deterministic), missing textures /
linked files are problems and "no 9:16 camera" is a note."""
import base64, functools, http.server, io, os, threading, urllib.parse

DEFAULT_TIMES = '3,25%,50%,75%,end-2'


def parse_times(spec, dur):
    """'3,25%,end-2' -> seconds inside the scene (a number, a percentage of DURATION, or end-N)."""
    out = []
    for s in spec.split(','):
        s = s.strip()
        t = dur * float(s[:-1]) / 100 if s.endswith('%') else dur - float(s[4:]) if s.startswith('end-') else float(s)
        out.append(round(min(max(t, 0.0), max(dur - 1 / 30, 0)), 3))
    return out


def sheet_path(scene):
    """Where the contact sheet goes by default: <scene>.check.jpg next to the scene."""
    return os.path.splitext(os.path.abspath(scene))[0] + '.check.jpg'


def check(scene, times=None, out=None, only=None, engine='scene', cancel=None, channel='chrome', extra_args=()):
    """Contact sheet + problems for one scene. only: None (both) | '16:9' | '9:16'. engine: .blend only.
    cancel: optional threading.Event, checked between frames. channel/extra_args: Chrome options (tests use software WebGL).
    Returns dict(ok, sheet (path or None), problems [str], notes [str]). Never raises for a bad scene (it is a problem)."""
    scene = os.path.abspath(scene); times = times or DEFAULT_TIMES
    if scene.lower().endswith('.blend'):
        import blender_core as bc
        res = bc.preview(scene, frames=times.split(','), out=out, only=only, engine=engine)
        ok = bool(res['ok'])
        return dict(ok=ok, sheet=res.get('sheet'), problems=[] if ok else (res.get('problems') or ['no frames rendered']),
                    notes=list(res.get('warnings') or []))
    from playwright.sync_api import sync_playwright
    from PIL import Image, ImageChops, ImageDraw
    d = os.path.dirname(scene)
    class Q(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *x): pass
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Q, directory=d))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    rows, problems, notes = [], [], []
    sizes = [s for s in ((1920, 1080, '16:9'), (1080, 1920, '9:16')) if not only or s[2] == only]
    decode = lambda u: Image.open(io.BytesIO(base64.b64decode(u.split(',')[1]))).convert('RGB')
    try:
        with sync_playwright() as p:
            kw = dict(args=list(extra_args))
            if channel: kw['channel'] = channel
            b = p.chromium.launch(**kw)
            try:
                for W, H, name in sizes:
                    if cancel is not None and cancel.is_set(): break
                    pg = b.new_page(viewport={'width': 1280, 'height': 720}); errs = []
                    pg.on('pageerror', lambda e: errs.append(str(e)))
                    pg.goto(f'http://127.0.0.1:{srv.server_address[1]}/{urllib.parse.quote(os.path.basename(scene))}?render=1&s=4&w={W}&h={H}')
                    try: pg.wait_for_function('window.__ready===true', timeout=120000)
                    except Exception:
                        problems.append(f'{name}: the scene never became ready {errs[:2]}'); pg.close(); continue
                    dur = pg.evaluate('window.DURATION')
                    if not isinstance(dur, (int, float)) or dur <= 0:
                        problems.append(f'{name}: window.DURATION is not a positive number ({dur!r})'); pg.close(); continue
                    if not pg.evaluate('typeof window.seek === "function"'):
                        problems.append(f'{name}: window.seek(t) is missing'); pg.close(); continue
                    tl = parse_times(times, dur); frames = []
                    for t in tl:
                        if cancel is not None and cancel.is_set(): break
                        u = pg.evaluate(f'(()=>{{window.seek({t});return document.getElementById("c").toDataURL("image/jpeg",0.9)}})()')
                        frames.append((t, decode(u)))
                    mid = tl[len(tl) // 2]
                    one = pg.evaluate(f'(()=>{{window.seek({mid});return document.getElementById("c").toDataURL()}})()')
                    two = pg.evaluate(f'(()=>{{window.seek(0);window.seek({mid});return document.getElementById("c").toDataURL()}})()')
                    if one != two:   # Chrome's GPU canvas can differ by 1-2 levels in a few pixels between draws (measured); that is not impurity
                        im1, im2 = decode(one), decode(two)
                        dif = ImageChops.difference(im1, im2); worst = max(c[1] for c in dif.getextrema())
                        changed = sum(dif.convert('L').point(lambda v: 255 if v > 0 else 0).histogram()[255:]) / (im1.width * im1.height)
                        if worst > 4 or changed > 0.0005:
                            problems.append(f'{name}: seek(t) is NOT pure - the same time drew different frames ({changed:.2%} of pixels, up to {worst} levels: Math.random / real-time state?)')
                        else:
                            notes.append(f'{name}: tiny GPU rounding difference between two draws ({worst} levels, {changed:.4%} of pixels) - fine')
                    if errs: problems.append(f'{name}: page errors {errs[:3]}')
                    rows.append((name, frames)); pg.close()
            finally:
                b.close()
    except Exception as e:   # Chrome missing, Playwright broken...: one problem line instead of a crash
        problems.append(f'{type(e).__name__}: {(str(e).strip().splitlines() or [""])[0]}')
    finally:
        srv.shutdown()
    sheet = None
    th = 260; sheet_rows = []
    for name, frames in rows:
        if not frames: continue
        tiles = [im.resize((round(th * im.width / im.height), th)) for _, im in frames]
        row = Image.new('RGB', (sum(t.width for t in tiles) + 8 * len(tiles), th + 30), (20, 20, 24)); x = 0; dr = ImageDraw.Draw(row)
        for (t, _), tile in zip(frames, tiles):
            row.paste(tile, (x, 30)); dr.text((x + 4, 8), f'{name}  t={t:.1f}s', fill=(230, 230, 230)); x += tile.width + 8
        sheet_rows.append(row)
    if sheet_rows:
        W = max(r.width for r in sheet_rows); img = Image.new('RGB', (W, sum(r.height for r in sheet_rows)), (20, 20, 24)); y = 0
        for r in sheet_rows: img.paste(r, (0, y)); y += r.height
        sheet = out or sheet_path(scene)
        try: img.save(sheet, quality=88)
        except OSError as e: problems.append(f'could not write the contact sheet: {e}'); sheet = None
    if cancel is not None and cancel.is_set(): problems.append('cancelled')
    if not rows and not problems: problems.append('no frames drawn')
    return dict(ok=not problems, sheet=sheet, problems=problems, notes=notes)
