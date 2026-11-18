"""Checks qc.run_qc / qc.write_report on tiny clips made with ffmpeg lavfi (no render, no browser). Windows and Linux.
Usage: python tests/test_qc_run.py      exit 0 = all PASS, 1 = any FAIL
Clips live in a temp folder that is deleted at the end."""
import os, shutil, subprocess, sys, tempfile
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import qc

S = '640x360'
TONE = ['-f', 'lavfi', '-i', 'sine=f=440:d={d}']
NORM = 'loudnorm=I=-15:TP=-2:LRA=11'          # like Shadow's own mix, a little more peak headroom for the short clip
results = []

def ff(*args):
    r = subprocess.run(['ffmpeg', '-hide_banner', '-v', 'error', '-y', *args], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode: raise RuntimeError(f'could not make a test clip: {r.stderr.strip()}')

def clip(path, video, n_inputs, audio=None):
    """video: list of lavfi sources concatenated; audio: (lavfi source, filter) or None."""
    args = []
    for src in video: args += ['-f', 'lavfi', '-i', f'{src}:s={S}:r=30']
    graph = ''.join(f'[{i}:v]' for i in range(len(video))) + f'concat=n={len(video)}:v=1[v]'
    if audio:
        args += ['-f', 'lavfi', '-i', audio[0]]; graph += f';[{len(video)}:a]{audio[1]}[a]'
    args += ['-filter_complex', graph, '-map', '[v]'] + (['-map', '[a]', '-c:a', 'aac', '-ar', '48000'] if audio else [])
    ff(*args, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-shortest', path)
    return path

def check(label, res, want_status, want_reason=None):
    ok = res['status'] == want_status and (want_reason is None or any(want_reason in r for r in res['reasons']))
    results.append(ok)
    m = res.get('measured') or {}
    seen = f"{res['status']}: {'; '.join(res['reasons']) or 'no reasons'}"
    if m: seen += f"   [{m.get('width')}x{m.get('height')}, {m.get('duration')} s, lufs {m.get('lufs')}, tp {m.get('tp')}]"
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {seen}" + ('' if ok else f'   (expected {want_status}' + (f' with {want_reason!r})' if want_reason else ')')))

def expect(d, silent=False, w=640, h=360): return dict(width=w, height=h, duration=d, silent=silent)

def main():
    tmp = tempfile.mkdtemp(prefix='shadow_qc_run_')
    try:
        j = lambda n: os.path.join(tmp, n)
        normal = clip(j('normal.mp4'), ['testsrc2=d=4'], 1, ('sine=f=440:d=4', NORM))
        black = clip(j('black second.mp4'), ['testsrc2=d=2', 'color=black:d=1', 'testsrc2=d=2'], 3, ('sine=f=440:d=5', NORM))
        frozen = clip(j('frozen.mp4'), ['color=red:d=6'], 1, ('sine=f=440:d=6', NORM))
        loud = clip(j('loud.mp4'), ['testsrc2=d=4'], 1, ('sine=f=440:d=4', 'volume=8'))
        silent = clip(j('silent.mp4'), ['testsrc2=d=4'], 1)
        corrupt = j('corrupt.mp4')
        with open(normal, 'rb') as fh: head = fh.read(3000)
        with open(corrupt, 'wb') as fh: fh.write(head)          # the first 3 kB of a real mp4: header only, no media
        before = {p: (os.path.getsize(p), os.path.getmtime(p)) for p in (normal, black, frozen, loud, silent, corrupt)}

        check('normal clip', qc.run_qc(normal, expect(4.0)), 'OK')
        check('1 s black in the middle', qc.run_qc(black, expect(5.0)), 'Warn', 'black')
        check('6 s frozen picture', qc.run_qc(frozen, expect(6.0)), 'Warn', 'frozen picture')
        check('very loud tone', qc.run_qc(loud, expect(4.0)), 'Warn', 'true peak')
        check('silent clip, silent by design', qc.run_qc(silent, expect(4.0, silent=True)), 'OK')
        check('silent clip, sound expected', qc.run_qc(silent, expect(4.0)), 'Fail', 'no audio stream')
        check('corrupt file', qc.run_qc(corrupt, expect(4.0)), 'Unknown')
        check('wrong resolution', qc.run_qc(normal, expect(4.0, w=1080, h=1920)), 'Fail', 'resolution')
        check('duration off by 2 s', qc.run_qc(normal, expect(6.0)), 'Fail', 'duration')
        check('missing file', qc.run_qc(j('nope.mp4'), expect(4.0)), 'Unknown', 'not found')
        check('ffmpeg not found', qc.run_qc(normal, expect(4.0), ffmpeg=j('no_ffmpeg.exe')), 'Unknown', 'ffmpeg not found')
        check('ffprobe not found', qc.run_qc(normal, expect(4.0), ffprobe=j('no_ffprobe.exe')), 'Unknown', 'ffprobe not found')
        check('timeout', qc.run_qc(normal, expect(4.0), timeout=0.001), 'Unknown', 'timed out')
        check('expect=None: no size/duration checks, no raise', qc.run_qc(normal, None), 'OK')

        # write_report
        res = qc.run_qc(black, expect(5.0)); path = qc.write_report(black, res)
        ok = path == j('black second.qc.txt') and os.path.isfile(path)
        txt = open(path, encoding='utf-8').read() if ok else ''
        ok = ok and txt.startswith('QC Warn  black second.mp4') and 'black' in txt and 'loudness' in txt
        results.append(ok); print(f"{'PASS' if ok else 'FAIL'}  write_report next to the mp4: {os.path.basename(path) if path else path}")
        bad = qc.write_report(j(os.path.join('no such folder', 'x.mp4')), res)
        results.append(bad is None); print(f"{'PASS' if bad is None else 'FAIL'}  write_report into a missing folder returns None: {bad!r}")
        bad = qc.write_report(None, res)
        results.append(bad is None); print(f"{'PASS' if bad is None else 'FAIL'}  write_report(None) returns None: {bad!r}")

        after = {p: (os.path.getsize(p), os.path.getmtime(p)) for p in before}
        ok = before == after; results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  QC never changed a clip (size and time unchanged for all {len(before)})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f'{sum(results)}/{len(results)} passed')
    return 0 if results and all(results) else 1

if __name__ == '__main__':
    sys.exit(main())
