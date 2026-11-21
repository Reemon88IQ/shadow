"""The app's languages (3.20). English is written in the code; the other languages live in locales/<code>.json as
{"English text": "translation"}. Only what the user READS is translated: buttons, labels, messages, the choices of the
drop-down lists and the Status column. The values Shadow keeps and compares (settings, projects, the Sound / Format /
Quality keys, the log, the MCP server) stay English, so switching the language never changes what a setting means.

  t('Project saved: {0}', path)   the translation of the template, then .format(*args) (English when there is none)
  status(text)                    a Status-column text ('Done · QC OK · thumb', 'Frames 12/300') with its known words translated
  Combo(parent, var, values)      a read-only drop-down that SHOWS translated choices while `var` keeps the English key
The language: setting 'language' ('auto' = Windows' own language when Shadow has it, else English); env SHADOW_LANG wins
(the tests set it to 'en'). A change takes effect after a restart."""
import json, locale, os, re, sys

LANGS = {   # code -> its own name (the most spoken languages; English first)
    'en': 'English', 'ar': 'العربية', 'zh': '中文 (简体)', 'hi': 'हिन्दी', 'es': 'Español', 'fr': 'Français',
    'pt': 'Português', 'ru': 'Русский', 'id': 'Bahasa Indonesia', 'de': 'Deutsch', 'ja': '日本語',
}
RTL = {'ar'}
_cur = 'en'
_tab = {}

def locale_dir():
    for b in (getattr(sys, '_MEIPASS', None), os.path.dirname(os.path.abspath(__file__))):
        if b and os.path.isdir(os.path.join(b, 'locales')): return os.path.join(b, 'locales')
    return None

def load(code):
    d = locale_dir()
    if code == 'en' or not d: return {}
    try:
        with open(os.path.join(d, f'{code}.json'), encoding='utf-8') as f: data = json.load(f)
        return {k: v for k, v in data.items() if isinstance(v, str) and v and not k.startswith('_')}
    except Exception:
        return {}

def windows_lang():
    """The Windows display language's 2-letter code, or ''."""
    try:
        import ctypes
        lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        name = locale.windows_locale.get(lcid, '')
        return name.split('_')[0].lower()
    except Exception:
        try: return (locale.getlocale()[0] or '').split('_')[0].lower()
        except Exception: return ''

def pick(setting):
    """The language to use: env SHADOW_LANG, else the setting, else ('auto') Windows' language if we have it, else en."""
    want = (os.environ.get('SHADOW_LANG') or setting or 'auto').lower()
    if want == 'auto': want = windows_lang()
    return want if want in LANGS else 'en'

def set_lang(code):
    global _cur, _tab
    _cur = code if code in LANGS else 'en'; _tab = load(_cur)
    return _cur

# Sun Valley's fonts (Segoe UI Variable) have no Arabic / Devanagari / CJK letters: Windows then falls back to a small,
# blurry font. These Windows fonts have them (all ship with Windows 10/11).
FONTS = {'ar': 'Segoe UI', 'hi': 'Nirmala UI', 'zh': 'Microsoft YaHei UI', 'ja': 'Yu Gothic UI'}

def apply_fonts(root):
    """Switch every named Tk font to the language's Windows font (keeps sizes and weights). Nothing for other languages."""
    fam = FONTS.get(_cur)
    if not fam: return False
    import tkinter.font as tkf
    try:
        if fam not in tkf.families(root): return False
        for n in tkf.names(root):
            try: tkf.nametofont(n, root=root).configure(family=fam)
            except Exception: pass
        return True
    except Exception:
        return False

def current(): return _cur
def name(code=None): return LANGS.get(code or _cur, 'English')

def t(text, *args, **kw):
    s = _tab.get(text, text)
    if args or kw:
        try: return s.format(*args, **kw)
        except Exception: return text.format(*args, **kw)   # a broken translation never breaks the app
    return s

# ---- the Status column: dynamic texts made of known words
STATUS_WORDS = ['Fixed by AI - render again', 'Skipped (up to date)', 'Skipped (scene check)', 'Skipped (outdated, no 9:16)',
                'Loading scene', 'Rendering sound', 'Encoding MP4', 'Checking output', 'Cancelled', 'Queued', 'Done', 'Failed',
                'QC OK', 'QC WARN', 'QC FAIL', 'Frames', 'thumb', 'Skipped', 'Rendering', 'Error']
_SW = re.compile('|'.join(re.escape(w) for w in sorted(STATUS_WORDS, key=len, reverse=True)))

def status(text):
    if _cur == 'en' or not text: return text
    return _SW.sub(lambda m: _tab.get(m.group(0), m.group(0)), text)

# ---- the time estimate ('about 12 min · done ~15:40 (+ 2 not estimated)'): its known phrases
_PH = re.compile(r'under 1 min|about|not estimated|done|min|h')
def phrase(text):
    if _cur == 'en' or not text: return text
    return _PH.sub(lambda m: _tab.get(m.group(0), m.group(0)), text)

# ---- drop-down lists that show translations but keep English keys
def Combo(parent, var, values, **kw):
    """ttk.Combobox (read-only) showing t(value) for each English value; `var` (a StringVar) keeps the English value, so
    every .get() / .set() / trace in the app sees the same keys as before. Returns the combobox."""
    import tkinter as tk
    from tkinter import ttk
    values = list(values)
    shown = tk.StringVar(master=parent)
    cb = ttk.Combobox(parent, textvariable=shown, values=[t(v) for v in values], state='readonly', **kw)
    busy = [False]
    def from_var(*_):
        if busy[0]: return
        busy[0] = True
        try: shown.set(t(var.get()))
        finally: busy[0] = False
    def from_box(*_):
        if busy[0]: return
        disp = shown.get(); key = next((v for v in values if t(v) == disp), disp)
        busy[0] = True
        try:
            if var.get() != key: var.set(key)
        finally: busy[0] = False
    var.trace_add('write', from_var); shown.trace_add('write', from_box); from_var()
    cb.shown = shown
    def set_values(new):   # the folder list grows ("New folder...")
        values[:] = list(new); cb.config(values=[t(v) for v in values]); from_var()
    cb.set_values = set_values
    return cb
