"""Scene check (3.13): what a scene is missing is explained BEFORE / AT THE START of a render, and ffmpeg errors show their
last 3 lines. Scenes are generated in a temp folder from tests/fixtures/minimal_silent.html (never scenes/).
  S1 contract_problems (static): complete scene -> []; each missing part named; local <script src> and ES module imports
     are read, CDN ones are not; a missing file is not a crash
  S2 runtime_problems (pure) and ffmpeg_message (pure, last 3 lines on one line)
  S3 render(): no __ready -> fails at once (not after 3 minutes); seek missing / DURATION a string / #c a <div> -> one clear
     message naming each; real ffmpeg failure -> 'ffmpeg failed: ...' with its own lines
  S4 GUI Render: the scene-check question lists the problems; No = row 'Skipped (scene check)', Cancel = nothing queued
  S5 MCP plan: a warning per broken scene, scene_check on the row
Usage: python tests/test_scene_check.py     exit 0 = all PASS"""
import os, re, shutil, subprocess, sys, tempfile, time
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, 'tools')); sys.path.insert(0, os.path.dirname(__file__))
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import render_core as rc

fails = []
def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f'  ({info})' if info and not ok else ''))
    if not ok: fails.append(name)

tmp = tempfile.mkdtemp(prefix='shadow_scenecheck_')
GOOD = open(os.path.join(ROOT, 'tests', 'fixtures', 'minimal_silent.html'), encoding='utf-8').read()
def scene(name, text):
    p = os.path.join(tmp, name)
    with open(p, 'w', encoding='utf-8') as f: f.write(text)
    return p
KEYS = {'ready': '__ready', 'seek': 'seek(t)', 'duration': 'DURATION', 'canvas': 'canvas'}
def which(problems): return sorted(k for k, v in rc.CONTRACT.items() if v in problems)

# ---------------------------------------------------------------- S1 static
good = scene('good.html', GOOD)
check('S1 complete scene -> no problems', rc.contract_problems(good) == [], rc.contract_problems(good))
no_ready = scene('no_ready.html', GOOD.replace('window.__ready = true;', ''))
no_seek = scene('no_seek.html', GOOD.replace('window.seek = (t)', 'window.draw = (t)').replace('window.seek(0)', 'window.draw(0)'))
no_dur = scene('no_dur.html', GOOD.replace('window.DURATION = 1.5;', 'const LEN = 1.5;').replace('window.DURATION', 'LEN'))
no_canvas = scene('no_canvas.html', GOOD.replace('<canvas id=c></canvas>', '<canvas id=stage></canvas>').replace("getElementById('c')", "getElementById('stage')"))
check('S1 each missing part is named (and only that one)', [which(rc.contract_problems(p)) for p in (no_ready, no_seek, no_dur, no_canvas)]
      == [['ready'], ['seek'], ['duration'], ['canvas']], [which(rc.contract_problems(p)) for p in (no_ready, no_seek, no_dur, no_canvas)])
check('S1 messages say what to do', all(re.search(w, rc.CONTRACT[k]) for k, w in (('ready', r'set window\.__ready = true'), ('seek', 'every frame'),
      ('duration', 'seconds'), ('canvas', 'id "c"'))))
os.makedirs(os.path.join(tmp, 'js'))
with open(os.path.join(tmp, 'js', 'kit.js'), 'w') as f: f.write('window.seek = t => {}; window.DURATION = 2; window.__ready = true;')
with open(os.path.join(tmp, 'js', 'mod.js'), 'w') as f: f.write("export const go = () => { window.__ready = true };")
split = scene('split.html', '<canvas id="c"></canvas><script src="js/kit.js"></script>')
mod = scene('mod.html', '<canvas id="c"></canvas><script type="module">import { go } from "./js/mod.js"; window.seek=()=>{}; window.DURATION=1; go();</script>')
cdn = scene('cdn.html', '<canvas id="c"></canvas><script src="https://cdn.example.com/kit.js"></script><script src="js/gone.js"></script>')
check('S1 local <script src> and ES module imports are read', rc.contract_problems(split) == [] and rc.contract_problems(mod) == [],
      (rc.contract_problems(split), rc.contract_problems(mod)))
check('S1 CDN script not fetched, a missing local file is no crash -> the 3 parts reported', which(rc.contract_problems(cdn)) == ['duration', 'ready', 'seek'])
check('S1 unreadable / missing scene -> [] (never raises)', rc.contract_problems(os.path.join(tmp, 'nope.html')) == [])
check('S1 the test fixtures pass (broken_no_seek mentions seek in its title, so only the load-time check catches it)',
      all(rc.contract_problems(os.path.join(ROOT, 'tests', 'fixtures', f)) == [] for f in ('minimal_silent.html', 'webgl_min.html', 'narr_2d.html', 'outdated_no_h.html')))

# ---------------------------------------------------------------- S2 pure helpers
R = rc.runtime_problems
check('S2 runtime: all there -> []', R(dict(seek='function', dur=4, canvas='CANVAS')) == [])
check('S2 runtime: seek missing, DURATION a string, #c a <div> -> 3 messages with the found values',
      [m.split(' ')[0] for m in R(dict(seek='undefined', dur='4', canvas='DIV'))] == ['window.seek(t)', 'window.DURATION', 'no']
      and "(it is '4')" in R(dict(seek='function', dur='4', canvas='CANVAS'))[0] and '<div>' in R(dict(seek='function', dur=4, canvas='DIV'))[0])
check('S2 runtime: DURATION 0 / None / NaN / True are all rejected', all(R(dict(seek='function', dur=d, canvas='CANVAS')) for d in (0, None, float('nan'), True, -1)))
check('S2 ffmpeg_message: last 3 lines on one line; empty -> exit code',
      rc.ffmpeg_message(['a', '', 'b', 'c', 'd  '], 1) == 'ffmpeg failed: b | c | d' and rc.ffmpeg_message([], 234) == 'ffmpeg failed: exit code 234')

# ---------------------------------------------------------------- S3 real renders
from regression import KW
PRESET = dict(w=320, samples=1)
def render_err(p):
    t0 = time.time()
    try: rc.render(p, os.path.join(tmp, os.path.basename(p) + '.mp4'), PRESET, sound='none', **KW); return None, time.time() - t0
    except Exception as e: return (str(e).strip().splitlines() or [''])[0], time.time() - t0
msg, secs = render_err(no_ready)
check('S3 no __ready -> fails at once with the reason (no 3-minute wait)', secs < 5 and '__ready' in msg and 'set window.__ready = true' in msg, (round(secs, 1), msg))
hidden = scene('hidden.html', GOOD.replace('window.DURATION = 1.5;', "window['DUR'+'ATION'] = '1.5';").replace('window.DURATION', '1.5')
               .replace('<canvas id=c></canvas>', '<div id=c></div><canvas id=k></canvas>').replace("getElementById('c')", "getElementById('k')")
               .replace('window.seek = (t)', "window['se'+'ek2'] = (t)").replace('window.seek(0)', "window['se'+'ek2'](0)") + '<!-- seek DURATION id="c" -->')
check('S3 (fixture sanity) the static check is fooled by this scene', rc.contract_problems(hidden) == [], rc.contract_problems(hidden))
msg, secs = render_err(hidden)
check('S3 load-time check: seek, DURATION (a string) and #c (a <div>) named in ONE line, before any frame',
      msg and msg.startswith('The scene cannot render:') and 'seek(t)' in msg and "(it is '1.5')" in msg and '<div>' in msg, msg)
check('S3 no mp4 / part file left', not [f for f in os.listdir(tmp) if f.endswith('.mp4')], os.listdir(tmp))
old_ready = rc.READY_TIMEOUT_MS; rc.READY_TIMEOUT_MS = 3000
stuck = scene('stuck.html', GOOD.replace('window.__ready = true;', 'if (window.NEVER) window.__ready = true; undefinedFn();'))
msg, secs = render_err(stuck); rc.READY_TIMEOUT_MS = old_ready
check('S3 never ready: says how long it waited and shows the page error', msg and 'after 3 s' in msg and 'undefinedFn' in msg, msg)
ff = rc.find_ffmpeg()
try: rc._ff([ff, '-hide_banner', '-i', os.path.join(tmp, 'missing.wav'), os.path.join(tmp, 'o.wav')]); m = ''
except RuntimeError as e: m = str(e)
check('S3 real ffmpeg failure -> "ffmpeg failed: ..." with its own words, one line', m.startswith('ffmpeg failed:') and 'missing.wav' in m and '\n' not in m, m)

# ---------------------------------------------------------------- S4 GUI
try:
    import app
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import no_dialogs; no_dialogs.block(app)   # never a real window on the screen
    app.LOG = os.path.join(tmp, 'shadow.log'); app.LOG_DIR = os.path.join(tmp, 'logs')
    for h in list(app.log.handlers): app.log.removeHandler(h)
    a = app.App(); a.withdraw()
    a.notify.set(False); a.skip_done.set(False); a.fmt.set('16:9 (YouTube)'); a.out_dir.set(os.path.join(tmp, 'out'))
    for p in (no_seek, no_dur): a._add(p)
    asked = []
    a.start(ask=lambda title, text: asked.append((title, text)) or None)
    check('S4 Render asks first, naming each scene and its problem; Cancel = nothing queued', len(asked) == 1 and asked[0][0] == 'Scene check'
          and 'no_seek.html' in asked[0][1] and rc.CONTRACT['seek'] in asked[0][1] and rc.CONTRACT['duration'] in asked[0][1] and a.worker is None,
          (asked, a.worker))
    a.start(ask=lambda *x: False)
    check('S4 No = both skipped with "Skipped (scene check)", nothing renders', a.worker is None
          and [a.tree.item(a.rows[p], 'values')[1] for p in (no_seek, no_dur)] == [app.SKIPPED_SCENE_CHECK] * 2)
    a.destroy()
except Exception as e:
    import traceback; traceback.print_exc(); check('S4 GUI test ran', False, e)

# ---------------------------------------------------------------- S5 MCP plan
try:
    import mcp_server as ms
    plan = ms.build_plan([good, no_ready], out_dir=os.path.join(tmp, 'mcp'))
    rows = {os.path.basename(r['scene']): r for r in plan['rows']}
    check('S5 MCP plan: scene_check per row + one warning for the broken scene', rows['good.html']['scene_check'] == []
          and rows['no_ready.html']['scene_check'] == [rc.CONTRACT['ready']]
          and [w for w in plan['warnings'] if 'will probably fail' in w] == [f"no_ready.html will probably fail: {rc.CONTRACT['ready']}"], plan['warnings'])
except Exception as e:
    import traceback; traceback.print_exc(); check('S5 MCP test ran', False, e)

sys.modules['app'].close_log() if 'app' in sys.modules else None   # shadow.log in the temp folder is open: close it first
shutil.rmtree(tmp, ignore_errors=True)
print('\nALL PASS' if not fails else f'\n{len(fails)} FAIL: {fails}'); sys.exit(1 if fails else 0)
