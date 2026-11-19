"""Quick look at a Shadow scene before the full render: a contact sheet of frames in 16:9 and 9:16, plus a purity check.
Usage: python tools/check_frames.py scene.html [--times 3,25%,50%,75%,end-2] [--out sheet.jpg] [--only 16:9|9:16]
Times: seconds, a percentage of DURATION, or end-N. Writes <scene>.check.jpg next to the scene (or --out) and prints
page errors and whether seek(t) is pure (the same frame drawn twice must be identical). Takes seconds, not minutes.
Uses the installed Chrome via Playwright and serves the scene's folder like Shadow does (narration is not attached here).
A .blend is rendered by Blender (blender_core.preview): the same sheet; no purity check (Blender is deterministic),
but missing textures / linked files are problems and "no 9:16 camera" is a note.
The work itself is frame_check.check() (shared with the app's "Check frames" button and Shadow.exe --check)."""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import frame_check   # noqa: E402
parse_times = frame_check.parse_times   # kept for older callers


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('scene'); ap.add_argument('--times', default=frame_check.DEFAULT_TIMES)
    ap.add_argument('--out'); ap.add_argument('--only', choices=['16:9', '9:16'])
    ap.add_argument('--engine', default='scene', help='.blend only: scene | eevee | cycles | workbench'); a = ap.parse_args()
    res = frame_check.check(a.scene, times=a.times, out=a.out, only=a.only, engine=a.engine)
    blend = a.scene.lower().endswith('.blend')
    for n in res['notes']: print(('note: ' if blend else '') + n)
    if res['sheet']: print('wrote', res['sheet'])
    if res['ok']: print('OK: no problems (Blender renders are deterministic: no purity check needed)' if blend else 'OK: no page errors, seek(t) is pure')
    else: print('PROBLEMS:\n  ' + '\n  '.join(res['problems']))
    sys.exit(0 if res['ok'] else 1)


if __name__ == '__main__':
    main()
