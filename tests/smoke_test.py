"""Headless smoke test (Windows/Linux/CI): renders 10 frames of a scene with software WebGL. Usage: python tests/smoke_test.py [scene.html]  (default: tests/fixtures/webgl_min.html)"""
import os, sys, shutil, subprocess, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import render_core as rc
scene = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'webgl_min.html')
SW = ['--use-angle=swiftshader', '--enable-unsafe-swiftshader']
tmp = tempfile.mkdtemp(prefix='shadow_smoke_')
try:
    tone = os.path.join(tmp, 'tone.wav'); subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', tone], capture_output=True)
    for name, kw in [('wide_generated', {}), ('vertical_none', dict(vertical=True, sound='none')), ('wide_file', dict(sound='file', audio_file=tone))]:
        out = os.path.join(tmp, f'smoke_{name}.mp4')
        rc.render(scene, out, dict(w=640, samples=2), max_frames=10, channel=None, extra_args=SW, **kw)
        r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,width,height', '-of', 'csv=p=0', out], capture_output=True, text=True).stdout.split()
        print(name, os.path.getsize(out), r)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
