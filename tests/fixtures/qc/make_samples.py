"""Regenerate the real ffmpeg output samples used by tests/test_qc_parse.py (run after an ffmpeg upgrade).
Usage: python tests/fixtures/qc/make_samples.py        writes *.txt next to this file; temp files are deleted.

Each sample is the stderr of the same QC pass Shadow is meant to run on a finished mp4:
  ffmpeg -i X -filter_complex "[0:v]scale=320:-2,blackdetect=d=0.5:pix_th=0.10,freezedetect=n=-60dB:d=5[v];[0:a]ebur128=peak=true:framelog=quiet[a]"
         -map [v] -map [a] -f null -        (video part only when the file has no audio)
Temp paths are replaced by <tmp> so the samples hold nothing machine-specific.
"""
import os, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
VF = 'scale=320:-2,blackdetect=d=0.5:pix_th=0.10,freezedetect=n=-60dB:d=5'
AF = 'ebur128=peak=true:framelog=quiet'

def ff(*args):
    r = subprocess.run(['ffmpeg', '-hide_banner', '-y', *args], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode: sys.exit(f'ffmpeg failed: {r.stderr.strip().splitlines()[-1]}')

def qc_pass(mp4, has_audio, tmp):
    graph = f'[0:v]{VF}[v]' + (f';[0:a]{AF}[a]' if has_audio else '')
    maps = ['-map', '[v]'] + (['-map', '[a]'] if has_audio else [])
    r = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', mp4, '-filter_complex', graph, *maps, '-f', 'null', '-'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    return r.stderr.replace(tmp, '<tmp>')

def main():
    tmp = tempfile.mkdtemp(prefix='shadow_qc_samples_')
    try:
        src = {}
        # 1 s black, then a still red picture to the end (freeze with no freeze_end), quiet tone (about -22 LUFS)
        p = src['black_then_frozen_tone'] = (os.path.join(tmp, 'a.mp4'), True)
        ff('-f', 'lavfi', '-i', 'color=black:s=640x360:r=30:d=1', '-f', 'lavfi', '-i', 'color=red:s=640x360:r=30:d=6',
           '-f', 'lavfi', '-i', 'sine=f=440:d=7', '-filter_complex', '[0:v][1:v]concat=n=2:v=1[v]', '-map', '[v]', '-map', '2:a',
           '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', p[0])
        # still red for 6 s, then moving test pattern 2 s (freeze with start AND end), no audio
        p = src['freeze_with_end_no_audio'] = (os.path.join(tmp, 'b.mp4'), False)
        ff('-f', 'lavfi', '-i', 'color=red:s=640x360:r=30:d=6', '-f', 'lavfi', '-i', 'testsrc2=s=640x360:r=30:d=2',
           '-filter_complex', '[0:v][1:v]concat=n=2:v=1[v]', '-map', '[v]', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', p[0])
        # moving pattern 2 s, black 2 s in the middle, pattern 2 s; very loud tone (true peak above -1 dBTP)
        p = src['black_middle_loud'] = (os.path.join(tmp, 'c.mp4'), True)
        ff('-f', 'lavfi', '-i', 'testsrc2=s=640x360:r=30:d=2', '-f', 'lavfi', '-i', 'color=black:s=640x360:r=30:d=2',
           '-f', 'lavfi', '-i', 'testsrc2=s=640x360:r=30:d=2', '-f', 'lavfi', '-i', 'sine=f=440:d=6,volume=8',
           '-filter_complex', '[0:v][1:v][2:v]concat=n=3:v=1[v]', '-map', '[v]', '-map', '3:a',
           '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', p[0])
        # a real Shadow render of an 11.6 s scene (pass its path as SCENE), 16:9, 640 px, generated sound (loudnorm -15 LUFS), software WebGL
        sys.path.insert(0, ROOT); import render_core as rc
        p = src['real_render'] = (os.path.join(tmp, 'real.mp4'), True)
        rc.render(os.environ.get('SCENE', os.path.join(ROOT, 'tests', 'fixtures', 'webgl_min.html')), p[0], dict(w=640, samples=2), channel=None,
                  extra_args=['--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
        for name, (mp4, has_audio) in src.items():
            with open(os.path.join(HERE, name + '.txt'), 'w', encoding='utf-8', newline='\n') as fh: fh.write(qc_pass(mp4, has_audio, tmp))
            print('wrote', name + '.txt')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == '__main__':
    main()
