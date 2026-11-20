"""Project files (.shadow): the queue (scenes in order, each row's narration) and its options as JSON, with paths relative
to the project file. Split out of app.py (3.16); app.py imports these names back."""
import json, os
import render_core as rc

PROJECT_EXT = '.shadow'   # project file: the queue + its options (JSON)

PROJECT_VERSION = 1

def _rel(p, base):
    """p relative to base when it is on the same drive (a project folder can be moved / copied), else absolute."""
    try: return os.path.relpath(p, base)
    except ValueError: return p

def write_project(path, data):
    """Save project_data() as JSON; file paths relative to the project's folder. Written via a temp file + rename."""
    base = os.path.dirname(os.path.abspath(path)); rel = lambda p: _rel(p, base) if isinstance(p, str) and p else p
    narr = {rel(f): {k: (rel(v) if k in ('wav', 'words') else v) for k, v in n.items()} for f, n in data['narration'].items()}
    o = dict(data['options']); o['sound_file'] = rel(o.get('sound_file') or '')
    out = dict(shadow_project=PROJECT_VERSION, shadow_version=rc.VERSION, files=[rel(f) for f in data['files']], narration=narr, options=o)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f: json.dump(out, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)

def read_project(path):
    """A .shadow file -> dict(files (absolute, in order), narration {file: settings}, options). Raises ValueError when it is
    not a Shadow project."""
    with open(path, encoding='utf-8-sig') as f: d = json.load(f)
    if not isinstance(d, dict) or 'shadow_project' not in d or not isinstance(d.get('files'), list): raise ValueError('not a Shadow project file')
    base = os.path.dirname(os.path.abspath(path))
    ab = lambda p: os.path.normpath(os.path.join(base, p)) if isinstance(p, str) and p else p
    narr = {ab(f): {k: (ab(v) if k in ('wav', 'words') else v) for k, v in (n or {}).items()} for f, n in (d.get('narration') or {}).items()}
    o = dict(d.get('options') or {}); o['sound_file'] = ab(o.get('sound_file') or '') or ''
    return dict(files=[ab(f) for f in d['files'] if isinstance(f, str)], narration=narr, options=o)
