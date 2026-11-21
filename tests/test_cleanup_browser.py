"""Cleanup, browser fallback and self-test (3.14). Temp folders only (never scenes/, never the real %TEMP% contents).
  C1 find_browser: Chrome first, Edge when Chrome is missing, None when neither; resolve_channel; check_env names it
  C2 clean_stale_temp: removes render temp folders whose process is gone (or > 1 day old without owner file); keeps live /
     young / unrelated ones; pid_alive; clean_old_updates removes installed update setups only
  C3 selftest: passes (bundled Chromium, software WebGL like the other tests); a missing ffmpeg / browser fails at that step
     with what to install; the real installed browser (Chrome or Edge) renders a WebGL scene too
  C4 GUI: Self-test button result in the status line; the setup bar says "Edge (Chrome not found)" on an Edge-only PC
  C5 CLI: python app.py --selftest -> exit 0 + "Self-test passed"
Usage: python tests/test_cleanup_browser.py     exit 0 = all PASS"""
import os, shutil, subprocess, sys, tempfile, time
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(__file__))
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
os.environ['SHADOW_LANG'] = 'en'   # 3.20: the tests read the English texts
import render_core as rc, app
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import no_dialogs; no_dialogs.block(app)   # never a real window on the screen
from regression import KW

fails = []
def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f'  ({info})' if info and not ok else ''))
    if not ok: fails.append(name)

TMP = tempfile.mkdtemp(prefix='shadow_cleanup_test_')
app.LOG = os.path.join(TMP, 'shadow.log'); app.LOG_DIR = os.path.join(TMP, 'logs'); app.close_log()

# ---------------------------------------------------------------- C1 browser
def fake_env(chrome, edge):
    root = tempfile.mkdtemp(dir=TMP)
    for ok, rel in ((chrome, rc.BROWSERS[0][2]), (edge, rc.BROWSERS[1][2])):
        if ok:
            p = root + rel; os.makedirs(os.path.dirname(p), exist_ok=True); open(p, 'w').close()
    return {'PROGRAMFILES': root, 'PROGRAMFILES(X86)': root, 'LOCALAPPDATA': root, 'PATH': TMP}
saved = {k: os.environ.get(k) for k in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA', 'PATH')}
try:
    got = []
    for c, e in ((True, True), (False, True), (False, False)):
        os.environ.update(fake_env(c, e)); got.append(rc.find_browser())
    os.environ.update(fake_env(False, True)); env = rc.check_env(); auto = rc.resolve_channel('auto')
    os.environ.update(fake_env(False, False)); none_auto = rc.resolve_channel('auto')
finally:
    for k, v in saved.items():
        if v is None: os.environ.pop(k, None)
        else: os.environ[k] = v
check('C1 Chrome first, Edge when Chrome is missing, None without both', got == ['chrome', 'msedge', None], got)
check('C1 Edge-only PC: check_env ok + browser "Edge", auto -> msedge', env['chrome'] and env['browser'] == 'Edge' and auto == 'msedge', (env, auto))
check('C1 no browser: auto -> chrome (the error names Chrome); None stays None (bundled)', none_auto == 'chrome' and rc.resolve_channel(None) is None
      and rc.resolve_channel('msedge') == 'msedge')

# ---------------------------------------------------------------- C2 cleanup
base = os.path.join(TMP, 'temp'); os.makedirs(base)
dead = subprocess.Popen([sys.executable, '-c', 'pass']); dead.wait()
def folder(name, pid=None, age=3600, size=1000):
    p = os.path.join(base, name); os.makedirs(p)
    with open(os.path.join(p, 'audio.wav'), 'wb') as f: f.write(b'x' * size)
    if pid is not None:
        with open(os.path.join(p, rc.OWNER_FILE), 'w') as f: f.write(str(pid))
    t = time.time() - age; os.utime(p, (t, t)); return p
live = folder('bq_render_live', os.getpid()); gone = folder('bq_render_gone', dead.pid, size=5000)
blend = folder('shadow_blend_gone', dead.pid); old = folder('shadow_blendpv_old', None, age=2 * 86400)
recent = folder('bq_render_noowner', None, age=3600); young = folder('bq_render_young', dead.pid, age=60); other = folder('other_gone', dead.pid)
n, freed = rc.clean_stale_temp(base)
left = sorted(os.listdir(base))
check('C2 removes gone-process / over-a-day folders, keeps live, recent-without-owner, young, unrelated',
      left == ['bq_render_live', 'bq_render_noowner', 'bq_render_young', 'other_gone'] and n == 3 and 7000 <= freed < 7100, (left, n, freed))   # + the owner.pid files
check('C2 pid_alive: this process yes, a finished one no', rc.pid_alive(os.getpid()) and not rc.pid_alive(dead.pid) and not rc.pid_alive(0))
check('C2 a missing base folder -> (0, 0)', rc.clean_stale_temp(os.path.join(TMP, 'nope')) == (0, 0))
up = os.path.join(TMP, 'Shadow-update'); os.makedirs(up)
for v in ('3.12', rc.VERSION, '99.0'): open(os.path.join(up, f'Shadow-{v}-Setup.exe'), 'wb').write(b'x' * 100)
freed = app.clean_old_updates(up)
check('C2 update setups: installed (older / this version) deleted, a newer one kept', os.listdir(up) == ['Shadow-99.0-Setup.exe'] and freed == 200, os.listdir(up))
d = tempfile.mkdtemp(prefix='shadow_x_'); sub = os.path.join(d, 'r'); os.makedirs(sub); rc.mark_temp_owner(sub)
check('C2 render temp folders carry their owner pid', open(os.path.join(sub, rc.OWNER_FILE)).read() == str(os.getpid())); shutil.rmtree(d)

# ---------------------------------------------------------------- C3 self-test
res = app.selftest(channel=None, extra_args=KW['extra_args'])
check('C3 self-test passes with all 7 steps (bundled Chromium + software WebGL)', res['ok'] and len(res['steps']) == 7
      and [s[0] for s in res['steps']][-1] == 'Quality check', app.selftest_text(res))
check('C3 no temp folder left by the self-test', not [x for x in os.listdir(tempfile.gettempdir()) if x.startswith('shadow_selftest_')
      and os.path.getmtime(os.path.join(tempfile.gettempdir(), x)) > time.time() - 120])
real_ff = rc.find_ffmpeg; rc.find_ffmpeg = lambda: None
try: r2 = app.selftest(channel=None)
finally: rc.find_ffmpeg = real_ff
check('C3 missing ffmpeg -> fails at "ffmpeg" with the winget command, nothing rendered', not r2['ok'] and r2['steps'][-1][0] == 'ffmpeg'
      and 'winget install' in r2['steps'][-1][2], r2['steps'])
real_env = rc.check_env; rc.check_env = lambda: dict(real_env(), chrome=False, browser=None)
try: r3 = app.selftest()
finally: rc.check_env = real_env
check('C3 no browser -> fails at "Browser" with the Chrome install command', not r3['ok'] and r3['steps'][0][0] == 'Browser'
      and 'Google.Chrome' in r3['steps'][0][2], r3['steps'])
installed = rc.find_browser()
if installed:
    out = os.path.join(TMP, 'real.mp4'); info = {}
    rc.render(os.path.join(ROOT, 'tests', 'fixtures', 'webgl_min.html'), out, dict(w=640, samples=2), sound='none', info=info, max_frames=10)
    check(f'C3 the installed browser renders a WebGL scene (auto -> {rc.browser_name(installed)})', os.path.getsize(out) > 0
          and info.get('browser') == rc.browser_name(installed), info)
else: print('SKIP  C3 no Chrome / Edge installed here')

# ---------------------------------------------------------------- C4 GUI
try:
    a = app.App(); a.withdraw(); a.selftest_box_off = True
    r = a.run_selftest(wait=True, channel=None, extra_args=KW['extra_args'])
    check('C4 Self-test button: result in the status line, button enabled again', r['ok'] and a.status.cget('text').startswith('Self-test passed')
          and str(a.selftest_btn.cget('state')) == 'normal', a.status.cget('text'))
    real_env = rc.check_env; rc.check_env = lambda: dict(real_env(), chrome=True, browser='Edge')
    try: a.check()
    finally: rc.check_env = real_env
    check('C4 Edge-only PC: setup bar says "Edge (Chrome not found)" with a green dot', a.env_lbls['Chrome'][1].cget('text') == 'Edge (Chrome not found)',
          a.env_lbls['Chrome'][1].cget('text'))
    a.destroy()
except Exception as e:
    import traceback; traceback.print_exc(); check('C4 GUI ran', False, e)

# ---------------------------------------------------------------- C5 CLI
env = dict(os.environ, TEMP=TMP, TMP=TMP, SHADOW_LOG=os.path.join(TMP, 'cli.log'))
cp = subprocess.run([sys.executable, os.path.join(ROOT, 'app.py'), '--selftest'], capture_output=True, text=True, timeout=180, env=env)
check('C5 app.py --selftest: exit 0, prints "Self-test passed"', cp.returncode == 0 and 'Self-test passed' in cp.stdout, (cp.returncode, cp.stdout[-400:], cp.stderr[-300:]))

app.close_log(); shutil.rmtree(TMP, ignore_errors=True)
print('\nALL PASS' if not fails else f'\n{len(fails)} FAIL: {fails}'); sys.exit(1 if fails else 0)
