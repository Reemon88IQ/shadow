"""Loudness per channel (3.13): songs -14 LUFS (rc.MUSIC_LUFS), everything else -15 (rc.DEFAULT_LUFS), or the scene's own
<meta name="shadow-lufs">. Scenes and a loud test song are generated in a temp folder (never scenes/).
  L1 target_lufs: meta wins (inside rc.LUFS_RANGE), a song next to the scene / External file -> -14, narration / own sound -> -15,
     shadow-folder Music -> -14
  L2 the ffmpeg chains carry the target; QC's loudness window follows it (target +-2, old -17..-13 without one)
  L3 real renders through app.render_item: the same loud song lands at about -14 LUFS (song) vs about -15 (lufs=-15 forced),
     true peak still <= -1 dBTP, QC report names the target
Usage: python tests/test_loudness.py     exit 0 = all PASS"""
import math, os, shutil, struct, sys, tempfile, wave
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(__file__))
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import render_core as rc, qc, app
from regression import KW

fails = []
def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f'  ({info})' if info and not ok else ''))
    if not ok: fails.append(name)

tmp = tempfile.mkdtemp(prefix='shadow_lufs_')
app.LOG = os.path.join(tmp, 'shadow.log'); app.LOG_DIR = os.path.join(tmp, 'logs')
GOOD = open(os.path.join(ROOT, 'tests', 'fixtures', 'minimal_silent.html'), encoding='utf-8').read()
def scene(name, head=''):
    d = os.path.join(tmp, os.path.splitext(name)[0]); os.makedirs(d, exist_ok=True); p = os.path.join(d, name)
    with open(p, 'w', encoding='utf-8') as f: f.write(GOOD.replace('<title>', head + '<title>', 1))
    return p
def song(path, secs=3, amp=0.9):
    """A loud, dense test 'master' (chord + fast tremolo), well above -14 LUFS before Shadow's chain."""
    with wave.open(path, 'wb') as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(48000); fr = []
        for i in range(48000 * secs):
            t = i / 48000; v = sum(math.sin(2 * math.pi * f * t) for f in (110, 220, 277, 330, 440)) / 5 * (0.6 + 0.4 * math.sin(2 * math.pi * 6 * t))
            s = int(max(-1, min(1, v * amp * 1.6)) * 32767); fr.append(struct.pack('<hh', s, s))
        w.writeframes(b''.join(fr))
    return path

# ---------------------------------------------------------------- L1
plain = scene('plain.html')
check('L1 own sound / silent / narrated -> -15', app.target_lufs(plain) == -15 and app.target_lufs(plain, 'none') == -15
      and app.target_lufs(plain, 'file', 'x.mp3', narrated=True) == -15)
check('L1 External song file -> -14', app.target_lufs(plain, 'file', os.path.join(tmp, 'a.mp3')) == -14)
auto = scene('auto.html'); song(os.path.join(os.path.dirname(auto), 'auto.wav'))
check('L1 Sound Auto finds <scene>.wav next to it -> -14; without it -> -15', app.target_lufs(auto, 'auto') == -14 and app.target_lufs(plain, 'auto') == -15)
music = scene('music.html', '<meta name="shadow-folder" content="Music/2026">')
check('L1 shadow-folder Music (moonshift scenes) -> -14 even with its own sound', app.target_lufs(music, 'generated') == -14)
own = scene('own.html', '<meta name="shadow-lufs" content="-16.5 LUFS">'); wild = scene('wild.html', '<meta name="shadow-lufs" content="-3">')
check('L1 <meta name="shadow-lufs"> wins; outside -24..-9 ignored', app.target_lufs(own, 'file', 'a.mp3') == -16.5 and app.target_lufs(wild, 'file', 'a.mp3') == -14)
check('L1 a .blend uses the same song rule (no meta read)', app.target_lufs(os.path.join(tmp, 'x.blend'), 'file', 'a.wav') == -14)

# ---------------------------------------------------------------- L2
check('L2 chains: -15 default (old constants unchanged), -14 for songs', rc.LOUDNORM == 'loudnorm=I=-15:TP=-1.5:LRA=11'
      and rc.loudnorm_af(-14) == 'loudnorm=I=-14:TP=-1.5:LRA=11' and rc.ext_audio_af(-14).startswith('loudnorm=I=-14:') and rc.EXT_AUDIO_AF.endswith(',apad'))
M = dict(width=1, height=1, has_audio=True, tp=-2.0)
check('L2 QC window: -12.5 LUFS warns at target -15, passes at target -14; no target = old -17..-13',
      qc.classify(dict(M, lufs=-12.5), dict(width=1, height=1))[0] == qc.WARN and qc.classify(dict(M, lufs=-12.5), dict(width=1, height=1, lufs=-14))[0] == qc.OK
      and qc.lufs_window({}) == (-17.0, -13.0) and qc.lufs_window({'lufs': -14}) == (-16, -12))

# ---------------------------------------------------------------- L3 real renders (render_item = what the queue runs)
PRESET = dict(w=320, samples=1); loud = song(os.path.join(tmp, 'loud.wav'), secs=4)
def render(lufs=None):
    """Full-length render (4 s song, 1.5 s scene) with the target the queue would pick (lufs=None) or a forced one."""
    out = os.path.join(tmp, f'r{lufs}.mp4'); info = {}
    rc.render(plain, out, PRESET, sound='file', audio_file=loud, capture='jpeg', info=info,
              lufs=app.target_lufs(plain, 'file', loud) if lufs is None else lufs, channel=KW['channel'], extra_args=KW['extra_args'])
    return out, info
try:
    m14, i14 = render(); m15, i15 = render(-15.0)
    l14, tp14 = rc._loud(rc.find_ffmpeg(), m14); l15, tp15 = rc._loud(rc.find_ffmpeg(), m15)
    check('L3 song -> about -14 LUFS in the mp4, forced -15 -> about -15 (1 dB apart)', abs(l14 + 14) <= 0.8 and abs(l15 + 15) <= 0.8 and 0.4 <= l14 - l15 <= 1.6,
          (l14, l15))
    check('L3 true peak still <= -1 dBTP at -14', tp14 is not None and tp14 <= -1.0, tp14)
    check('L3 info carries the target', i14.get('lufs') == -14 and i15.get('lufs') == -15, (i14.get('lufs'), i15.get('lufs')))
    res, rep = app.quality_check(m14, PRESET, False, i14, 'file')
    txt = open(rep, encoding='utf-8').read() if rep else ''
    check('L3 QC report: allowed -16..-12, target -14', 'target -14' in txt and '-16..-12' in txt, txt[:400])
    out = os.path.join(tmp, 'item.mp4'); info = {}
    app.render_item(plain, out, PRESET, dict(vertical=False, sound='file', audio_file=loud, capture='jpeg'), info=info)   # no lufs given: decided inside
    check('L3 render_item picks -14 by itself for a song (skills that call it directly get it too)', info.get('lufs') == -14, info.get('lufs'))
except Exception as e:
    import traceback; traceback.print_exc(); check('L3 renders ran', False, e)

shutil.rmtree(tmp, ignore_errors=True)
print('\nALL PASS' if not fails else f'\n{len(fails)} FAIL: {fails}'); sys.exit(1 if fails else 0)
