"""Quick look at a Shadow scene before the full render: a contact sheet of frames in 16:9 and 9:16, plus a purity check.
Usage: python tools/check_frames.py scene.html [--times 3,25%,50%,75%,end-2] [--out sheet.jpg] [--only 16:9|9:16]
Times: seconds, a percentage of DURATION, or end-N. Writes <scene>.check.jpg next to the scene (or --out) and prints
page errors and whether seek(t) is pure (the same frame drawn twice must be identical). Takes seconds, not minutes.
Uses the installed Chrome via Playwright and serves the scene's folder like Shadow does (narration is not attached here).
A .blend is rendered by Blender (blender_core.preview): the same sheet; no purity check (Blender is deterministic),
but missing textures / linked files are problems and "no 9:16 camera" is a note."""
import argparse, base64, functools, http.server, io, os, sys, threading, urllib.parse


def parse_times(spec, dur):
    out = []
    for s in spec.split(','):
        s = s.strip()
        t = dur * float(s[:-1]) / 100 if s.endswith('%') else dur - float(s[4:]) if s.startswith('end-') else float(s)
        out.append(round(min(max(t, 0.0), max(dur - 1 / 30, 0)), 3))
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('scene'); ap.add_argument('--times', default='3,25%,50%,75%,end-2')
    ap.add_argument('--out'); ap.add_argument('--only', choices=['16:9', '9:16'])
    ap.add_argument('--engine', default='scene', help='.blend only: scene | eevee | cycles | workbench'); a = ap.parse_args()
    if a.scene.lower().endswith('.blend'):   # Blender scene: a few frames rendered by Blender itself (draft samples, 640 px)
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import blender_core as bc
        res = bc.preview(os.path.abspath(a.scene), frames=a.times.split(','), out=a.out, only=a.only, engine=a.engine)
        for w in res['warnings']: print('note:', w)
        if res['sheet']: print('wrote', res['sheet'])
        print('OK: no problems (Blender renders are deterministic: no purity check needed)' if res['ok'] else
              'PROBLEMS:\n  ' + '\n  '.join(res['problems'] or ['no frames rendered']))
        sys.exit(0 if res['ok'] else 1)
    from playwright.sync_api import sync_playwright
    from PIL import Image, ImageDraw
    scene = os.path.abspath(a.scene); d = os.path.dirname(scene)
    class Q(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *x): pass
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Q, directory=d)); threading.Thread(target=srv.serve_forever, daemon=True).start()
    rows, problems = [], []
    sizes = [s for s in ((1920, 1080, '16:9'), (1080, 1920, '9:16')) if not a.only or s[2] == a.only]
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(channel='chrome')
            for W, H, name in sizes:
                pg = b.new_page(viewport={'width': 1280, 'height': 720}); errs = []
                pg.on('pageerror', lambda e: errs.append(str(e)))
                pg.goto(f'http://127.0.0.1:{srv.server_address[1]}/{urllib.parse.quote(os.path.basename(scene))}?render=1&s=4&w={W}&h={H}')
                try: pg.wait_for_function('window.__ready===true', timeout=120000)
                except Exception: problems.append(f'{name}: the scene never became ready {errs[:2]}'); continue
                dur = pg.evaluate('window.DURATION'); times = parse_times(a.times, dur); frames = []
                for t in times:
                    u = pg.evaluate(f'(()=>{{window.seek({t});return document.getElementById("c").toDataURL("image/jpeg",0.9)}})()')
                    frames.append((t, Image.open(io.BytesIO(base64.b64decode(u.split(',')[1]))).convert('RGB')))
                one = pg.evaluate(f'(()=>{{window.seek({times[len(times) // 2]});return document.getElementById("c").toDataURL()}})()')
                two = pg.evaluate(f'(()=>{{window.seek(0);window.seek({times[len(times) // 2]});return document.getElementById("c").toDataURL()}})()')
                if one != two:   # Chrome's GPU canvas can differ by 1-2 levels in a few pixels between draws (measured); that is not impurity
                    from PIL import ImageChops
                    im1, im2 = (Image.open(io.BytesIO(base64.b64decode(x.split(',')[1]))).convert('RGB') for x in (one, two))
                    dif = ImageChops.difference(im1, im2); worst = max(c[1] for c in dif.getextrema())
                    changed = sum(dif.convert('L').point(lambda v: 255 if v > 0 else 0).histogram()[255:]) / (im1.width * im1.height)
                    if worst > 4 or changed > 0.0005:
                        problems.append(f'{name}: seek(t) is NOT pure - the same time drew different frames ({changed:.2%} of pixels, up to {worst} levels: Math.random / real-time state?)')
                    else: print(f'{name}: tiny GPU rounding difference between two draws ({worst} levels, {changed:.4%} of pixels) - fine')
                if errs: problems.append(f'{name}: page errors {errs[:3]}')
                rows.append((name, frames)); pg.close()
            b.close()
    finally:
        srv.shutdown()
    th = 260; sheet_rows = []
    for name, frames in rows:
        tiles = [im.resize((round(th * im.width / im.height), th)) for _, im in frames]
        row = Image.new('RGB', (sum(t.width for t in tiles) + 8 * len(tiles), th + 30), (20, 20, 24)); x = 0; dr = ImageDraw.Draw(row)
        for (t, _), tile in zip(frames, tiles):
            row.paste(tile, (x, 30)); dr.text((x + 4, 8), f'{name}  t={t:.1f}s', fill=(230, 230, 230)); x += tile.width + 8
        sheet_rows.append(row)
    if sheet_rows:
        W = max(r.width for r in sheet_rows); sheet = Image.new('RGB', (W, sum(r.height for r in sheet_rows)), (20, 20, 24)); y = 0
        for r in sheet_rows: sheet.paste(r, (0, y)); y += r.height
        out = a.out or os.path.splitext(scene)[0] + '.check.jpg'; sheet.save(out, quality=88); print('wrote', out)
    print('OK: no page errors, seek(t) is pure' if not problems else 'PROBLEMS:\n  ' + '\n  '.join(problems))
    sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main()
