"""External-audio true-peak limiter and x264 encoder settings (render_core v2.9). Windows and Linux.
Usage: python tests/test_audio_encode.py      exit 0 = all PASS, 1 = any FAIL
Needs ffmpeg; the end-to-end check also needs playwright chromium (software WebGL, like regression.py).
Loud fixtures are generated into a temp folder (tests never read scenes/)."""
import os, re, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import render_core as rc
import qc

WEBGL = os.path.join(ROOT, 'tests', 'fixtures', 'webgl_min.html')
PRESET = dict(w=640, samples=2)
KW = dict(channel=None, extra_args=['--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
TP_MAX = qc.TRUE_PEAK_MAX   # -1.0 dBTP, the same limit the QC report uses
results = []

def check(ok, label, seen, want):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {seen}" + ('' if ok else f'   (expected {want})'))

def ff(*args):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *args], check=True)

def measure(path):
    """{'lufs', 'tp'} with the QC module's own ebur128 parse (true peak = 4x oversampled)."""
    r = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', path, '-af', qc.AF, '-f', 'null', '-'], capture_output=True, text=True)
    return qc.parse_ebur128(r.stderr)

def main():
    d = tempfile.mkdtemp(prefix='shadow_audio_encode_')
    try:
        # ---- encoder args (pure)
        a = rc.x264_args()
        check(a[a.index('-preset') + 1] == 'medium' and a[a.index('-crf') + 1] == '16' and 'libx264' in a and 'yuv420p' in a,
              'x264 args: preset medium, crf 16, libx264, yuv420p', ' '.join(a), '-preset medium -crf 16')
        src = open(os.path.join(ROOT, 'render_core.py'), encoding='utf-8').read()
        check("'slow'" not in src and 'x264_args()' in src, 'render() uses x264_args(), no preset slow left', "'slow' absent" if "'slow'" not in src else "'slow' found", 'no slow')
        check(tuple(map(int, rc.VERSION.split('.'))) >= (2, 9), 'VERSION', rc.VERSION, '>= 2.9 (limiter + crf 16 arrived in 2.9)')
        # ---- filter chain (pure)
        af = rc.EXT_AUDIO_AF
        m = re.search(r'alimiter=limit=([0-9.]+)', af)
        check(af.startswith('loudnorm=I=-15:') and m and abs(float(m.group(1)) - 10 ** (-1.5 / 20)) < 1e-3 and 'level=0' in af
              and af.index('aresample=192000') < af.index('alimiter') < af.index('aresample=48000') and af.endswith('apad'),
              'external chain: loudnorm -15 LUFS -> 4x oversampled alimiter at -1.5 dB (no auto level) -> 48 kHz -> apad', af, 'that order')

        # ---- loud fixtures (generated)
        hot = os.path.join(d, 'hot.wav')      # float wav ABOVE full scale (bursts at +3 dBFS sample peak, 1/10 bed): what a limiter must catch
        ff('-f', 'lavfi', '-i', "aevalsrc='1.41*sin(2*PI*997*t)*if(lt(mod(t,0.2),0.05),1,0.1)':s=48000:d=6", '-ac', '2', '-c:a', 'pcm_f32le', hot)
        song = os.path.join(d, 'loud master.mp3')   # brickwalled 44.1 kHz master, about -8 LUFS (like a modern loud song)
        ff('-f', 'lavfi', '-i', 'anoisesrc=d=12:c=pink:a=0.25:s=44100,volume=20dB,alimiter=limit=0.95:level=0:attack=0.1:release=5', '-ac', '2', '-c:a', 'libmp3lame', '-b:a', '320k', song)
        tail = os.path.join(d, 'tail.wav')    # quiet bed, a full-scale inter-sample burst in the last 100 ms
        ff('-f', 'lavfi', '-i', "aevalsrc='if(gt(t,5.9),0.99*sin(2*PI*11000*t+PI/4),0.02*sin(2*PI*220*t))':s=48000:d=6", '-ac', '2', '-c:a', 'pcm_s16le', tail)
        mh, ms = measure(hot), measure(song)
        check(mh['tp'] is not None and mh['tp'] > 0, 'fixture is hot: hot.wav true peak above 0 dBTP', f"{mh['tp']} dBTP", '> 0')
        check(ms['lufs'] is not None and ms['lufs'] > -11, 'fixture is loud: loud master.mp3 above -11 LUFS', f"{ms['lufs']} LUFS, {ms['tp']} dBTP", '> -11 LUFS')

        # ---- the limiter on its own (no loudnorm in front): catches inter-sample peaks
        lim = os.path.join(d, 'hot_lim.wav')
        ff('-i', hot, '-af', rc.TP_LIMITER, '-ar', '48000', lim)
        r = measure(lim)
        check(r['tp'] is not None and -3.0 <= r['tp'] <= TP_MAX, 'limiter alone: signal over 0 dBTP -> at most -1 dBTP (target -1.5)', f"{mh['tp']} -> {r['tp']} dBTP", f'-3 .. {TP_MAX}')

        # ---- the full external chain, exactly as render() runs it
        for name, f, lufs in (('loud master', song, True), ('end burst', tail, False), ('hot', hot, True)):
            out = os.path.join(d, f'{name}_out.wav')
            ff('-i', f, '-af', rc.EXT_AUDIO_AF, '-ar', '48000', '-t', '6', out)
            r = measure(out)
            ok = r['tp'] is not None and r['tp'] <= TP_MAX and (not lufs or abs(r['lufs'] + 15) <= 1.0)
            check(ok, f'full chain, {name}: true peak <= -1 dBTP' + (' and -15 LUFS +-1' if lufs else ''), f"{r['lufs']} LUFS, {r['tp']} dBTP",
                  f'tp <= {TP_MAX}' + (', -16..-14 LUFS' if lufs else ''))

        # ---- end to end: render() with Sound = External file, a loud song; the AAC in the mp4 is measured
        mp4 = os.path.join(d, 'e2e loud #1.mp4')
        info = {}
        rc.render(WEBGL, mp4, PRESET, sound='file', audio_file=song, info=info, **KW)
        r = measure(mp4)
        check(r['tp'] is not None and r['tp'] <= TP_MAX and abs(r['lufs'] + 15) <= 1.0, 'render(): mp4 (AAC) from the loud master is -15 LUFS +-1, true peak <= -1 dBTP',
              f"{r['lufs']} LUFS, {r['tp']} dBTP", f'-16..-14 LUFS, <= {TP_MAX} dBTP')
        blob = open(mp4, 'rb').read()   # x264 writes its settings into the stream: medium = subme=7 ref=3 me=hex (slow: subme=8 ref=5 me=umh)
        sei = re.search(rb'x264 - core .{0,2000}?(?=\x00)', blob, re.S)
        s = sei.group(0).decode('latin1') if sei else ''
        check(all(k in s for k in ('crf=16.0', 'subme=7', ' ref=3', 'me=hex', 'rc_lookahead=40')), 'render(): the mp4 was encoded with preset medium, crf 16',
              ' '.join(x for x in s.split() if x.split('=')[0] in ('crf', 'subme', 'ref', 'me', 'rc_lookahead')) or 'no x264 settings found', 'crf=16.0 subme=7 ref=3 me=hex rc_lookahead=40')
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print(f'{sum(results)}/{len(results)} passed')
    return 0 if all(results) else 1

if __name__ == '__main__':
    sys.exit(main())
