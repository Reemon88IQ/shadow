"""tools/publish_update.py: version helpers + a --dry-run (asks, checks, prints the plan, changes nothing). No build, no upload.
Usage: python tests/test_publish_update.py   exit 0 = all PASS. The dry-run needs git + gh signed in; it is skipped otherwise."""
import os, shutil, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'tools')); sys.path.insert(0, ROOT)
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import publish_update as pu

res = []
def check(name, ok, info=''):
    res.append(ok); print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else f'  ({info})'))

t = 'import os\r\nVERSION = "3.0"\r\nX = 1\r\n'
check('bump_version changes only the VERSION line (CRLF kept)', pu.bump_version(t, '3.1') == 'import os\r\nVERSION = "3.1"\r\nX = 1\r\n')
check('current_version reads it', pu.current_version(t) == '3.0')
try: pu.bump_version('x = 1\n', '3.1'); check('bump_version without VERSION raises', False)
except ValueError: check('bump_version without VERSION raises', True)
check('valid_new 3.1 > 3.0', pu.valid_new('3.1', '3.0') is None)
check('valid_new 3.0.1 > 3.0', pu.valid_new('3.0.1', '3.0') is None)
check('valid_new 2.9 rejected', pu.valid_new('2.9', '3.0') is not None)
check('valid_new junk rejected', pu.valid_new('v3.1 beta', '3.0') is not None and pu.valid_new('', '3.0') is not None)

ready = shutil.which('gh') and subprocess.run(['gh', 'auth', 'status'], capture_output=True).returncode == 0 and \
        subprocess.run(['git', 'branch', '--show-current'], cwd=ROOT, capture_output=True, text=True).stdout.strip() == 'master' and \
        os.path.isdir(os.path.join(os.path.dirname(ROOT), 'shadow-public', '.git'))
if not ready:
    print('SKIP dry-run (needs gh signed in, master, ../shadow-public)')
else:
    before = (subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout,
              open(os.path.join(ROOT, 'render_core.py'), 'rb').read())
    cur = pu.current_version(); new = cur + '.9' if cur.count('.') == 1 else '99.0'
    r = subprocess.run([sys.executable, os.path.join(ROOT, 'tools', 'publish_update.py'), '--dry-run'], cwd=ROOT,
                       input=f'{new}\ntest notes\n', capture_output=True, text=True, encoding='utf-8')
    check('dry-run exits 0 and prints the plan', r.returncode == 0 and f'Plan for v{new}' in r.stdout and 'nothing was changed' in r.stdout, r.stdout[-400:] + r.stderr[-400:])
    after = (subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout,
             open(os.path.join(ROOT, 'render_core.py'), 'rb').read())
    check('dry-run changed nothing (HEAD, render_core.py)', before == after)
    r = subprocess.run([sys.executable, os.path.join(ROOT, 'tools', 'publish_update.py'), '--dry-run'], cwd=ROOT,
                       input='1.0\n', capture_output=True, text=True, encoding='utf-8')
    check('an older version is refused (asked again, then stops)', r.returncode == 1 and 'not newer' in r.stdout, r.stdout[-300:])
    sug = '.'.join(cur.split('.')[:-1] + [str(int(cur.split('.')[-1]) + 1)])
    r = subprocess.run([sys.executable, os.path.join(ROOT, 'tools', 'publish_update.py'), '--dry-run'], cwd=ROOT,
                       input='\n\ny\n', capture_output=True, text=True, encoding='utf-8')
    check(f'Enter takes the suggested version {sug}', r.returncode == 0 and f'Plan for v{sug}' in r.stdout, r.stdout[-400:])
    r = subprocess.run([sys.executable, os.path.join(ROOT, 'tools', 'publish_update.py'), '--dry-run'], cwd=ROOT,
                       input='abc\n\n\ny\n', capture_output=True, text=True, encoding='utf-8')
    check('a wrong entry is asked again', r.returncode == 0 and 'use numbers like' in r.stdout and f'Plan for v{sug}' in r.stdout, r.stdout[-400:])
print(f'\n{sum(res)}/{len(res)} passed'); sys.exit(0 if all(res) else 1)
