"""Light (gray) / Dark switching of the real App (hidden window): no light gray may stay behind in Dark.
Usage: python tests/test_theme.py   exit 0 = all PASS. Settings / log of the real user are not touched."""
import json, os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import app
from tkinter import ttk

tmp = tempfile.mkdtemp(prefix='shadow_theme_')
app.CFG = os.path.join(tmp, 'cfg.json'); app.LOG = os.path.join(tmp, 'shadow.log'); app.LOG_DIR = tmp
json.dump({'theme': 'light'}, open(app.CFG, 'w'))
res = []
def check(name, ok, info=''):
    res.append(ok); print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else f'  ({info})'))

a = app.App(); a.withdraw()
def pump():
    t0 = time.time()
    while time.time() - t0 < 0.3: a.update(); time.sleep(0.02)
def state():
    st = ttk.Style(a)
    return {k: str(v) for k, v in {'root': a.cget('background'), '.': st.lookup('.', 'background'), 'TFrame': st.lookup('TFrame', 'background'),
            'TLabel': st.lookup('TLabel', 'background'), 'Treeview': st.lookup('Treeview', 'fieldbackground')}.items()}
try:
    pump(); s = state()
    check('Light: window, frames, labels and table are LIGHT_BG', {v for k, v in s.items() if k != '.'} == {app.LIGHT_BG}, s)
    for i in (1, 2):
        a.toggle_theme(); pump(); s = state()
        check(f'Dark #{i}: nothing is light gray (root window included)', app.LIGHT_BG not in s.values() and s['root'] == s['TFrame'], s)
        a.toggle_theme(); pump(); s = state()
        check(f'Light #{i + 1}: gray again', {v for k, v in s.items() if k != '.'} == {app.LIGHT_BG}, s)
finally:
    a.after_cancel(a._poll_id); a.destroy()
    app.close_log(); import shutil; shutil.rmtree(tmp, ignore_errors=True)
print(f'\n{sum(res)}/{len(res)} passed'); sys.exit(0 if all(res) else 1)
