import datetime, glob, importlib, json, logging, os, queue, re, shutil, subprocess, sys, threading, time, traceback, uuid
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_core as rc
import qc
import updater

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    Root = TkinterDnD.Tk
    HAS_DND = True
except Exception:
    Root = tk.Tk
    HAS_DND = False
try:
    import sv_ttk
except Exception:
    sv_ttk = None
try:
    import darkdetect
except Exception:
    darkdetect = None

NOWIN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
CFG = os.path.join(os.path.expanduser('~'), '.shadow_renderer.json')
LOG = os.path.join(os.path.dirname(CFG), 'shadow.log')
LOG_DIR = os.path.join(os.path.dirname(LOG), 'logs')   # fallback only: a failure log normally goes into the scene's own output folder
LIGHT_TINT = 39   # Light theme = light gray: sv-ttk's near-white (#fafafa) darkened by this much, for the window and its images alike
LIGHT_BG = LIGHT_FIELD = '#%02x%02x%02x' % ((250 - LIGHT_TINT,) * 3)   # #d3d3d3 (owner's choice)
SOUNDS = ['Generated (built into the scene)', 'None (silent)', 'External file...', 'Auto (file next to scene)']
SONG_EXTS = ('.wav', '.mp3', '.m4a', '.flac', '.ogg', '.aac')   # Auto: <scene>.<ext> next to the .html, first one found wins
OUTDATED = 'This scene is outdated and does not support 9:16'
FORMAT_AUTO = 'Auto (by file name)'   # per file: a name starting with "short_" -> 9:16, every other file -> 16:9
FORMAT_CHOICES = list(rc.FORMATS) + [FORMAT_AUTO]
# Capture: how each frame leaves the browser (render_core.render(capture=...)). PNG = lossless; JPEG = about 2x faster render,
# final video very slightly softer (measured SSIM 0.986 -> 0.983 on a 1080p scene) and ~1.7x larger file.
CAPTURES = {'Best quality (PNG)': 'png', 'Fast (JPEG, ~2x faster)': 'jpeg'}
SHORT_PREFIX = 'short_'
SHORT_SUFFIXES = tuple(sep + w for sep in ('-', '_', ' ') for w in ('short', 'shorts'))   # rain-short.html, rain_shorts.html ... -> 9:16 too

log = logging.getLogger('shadow')
def setup_log():
    if log.handlers: return
    try:
        from logging.handlers import RotatingFileHandler
        h = RotatingFileHandler(LOG, maxBytes=1_000_000, backupCount=1, encoding='utf-8')
        h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        log.addHandler(h); log.setLevel(logging.INFO)
    except Exception:
        log.addHandler(logging.NullHandler())

def load_cfg():
    try:
        with open(CFG, encoding='utf-8') as f: return json.load(f)
    except Exception: return {}
def save_cfg(d):
    try:
        with open(CFG, 'w', encoding='utf-8') as f: json.dump(d, f)
    except Exception: pass
def fmt(sec):
    sec = int(sec); return f'{sec // 60}:{sec % 60:02d}'
def open_path(p):
    os.startfile(p) if hasattr(os, 'startfile') else subprocess.Popen(['xdg-open', p])

def system_theme():
    try: return 'dark' if (darkdetect.theme() or '').lower() == 'dark' else 'light'
    except Exception: return 'light'

def scene_info(path):
    """What the scene source supports: 9:16 needs it to read the h URL param (get('h')). Sound is checked at render time."""
    try:
        with open(path, encoding='utf-8', errors='replace') as f: src = f.read()
    except Exception:
        return {'vertical': False}
    return {'vertical': bool(re.search(r"""get\(\s*['"]h['"]\s*\)""", src))}

def unique_path(path, used=()):
    """path, or path_2, path_3, ... (before the extension): the first one that is not already on disk and not in `used`
    (paths already given out in this batch, compared case-insensitively like Windows). Pure apart from os.path.exists."""
    taken = {os.path.normcase(os.path.abspath(u)) for u in used}
    base, ext = os.path.splitext(path); cand = path; i = 2
    while os.path.exists(cand) or os.path.normcase(os.path.abspath(cand)) in taken:
        cand = f'{base}_{i}{ext}'; i += 1
    return cand

_WIN_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}

def scene_folder_name(scene_path):
    """Name of a scene's output folder (and the base of its file names): the ORIGINAL scene file name without extension.
    The hidden .shadow_narr_<id>_ prefix of a narration temp scene is dropped, so narrated renders use the original name.
    Windows-safe: no leading/trailing dots or spaces, no reserved device names (CON, NUL, COM1, ...), never empty."""
    name = re.sub(r'^\.shadow_narr_[0-9a-f]+_', '', os.path.basename(scene_path))
    name = os.path.splitext(name)[0].strip(' .')   # leading dots too: ".html" has no extension for splitext, a leading dot hides the folder
    if not name: name = 'scene'
    if name.split('.')[0].upper() in _WIN_RESERVED: name = '_' + name
    return name

def sound_mode(label):
    """Sound combobox label -> 'generated' | 'none' | 'file' | 'auto'."""
    return 'file' if label.startswith('External') else 'none' if label.startswith('None') else 'auto' if label.startswith('Auto') else 'generated'

def find_scene_audio(scene_path):
    """Auto mode: the audio file with the scene's base name next to it (.wav, .mp3, .m4a, .flac, .ogg, .aac, in that
    order), or None. <scene>.wav together with <scene>.words.json is a narration voice, not a song, so it is skipped."""
    base = os.path.splitext(scene_path)[0]
    voice = os.path.isfile(base + '.words.json')
    for ext in SONG_EXTS:
        if ext == '.wav' and voice: continue
        for e in (ext, ext.upper()):
            if os.path.isfile(base + e): return base + e
    return None

def format_vertical(scene_path, fmt_label):
    """True = 9:16 for this file. fmt_label is the Format combobox label; in Auto (by file name) a scene whose file name
    starts with "short_" or ends with "-short" / "_short" / " short" (also "...shorts"; any case) is 9:16, every other one
    16:9. Unknown labels -> 16:9. Pure (no disk access)."""
    if fmt_label == FORMAT_AUTO:
        stem = os.path.splitext(os.path.basename(scene_path))[0].lower()
        return stem.startswith(SHORT_PREFIX) or stem.endswith(SHORT_SUFFIXES)
    return bool(rc.FORMATS.get(fmt_label, False))

def format_label(vertical):
    """The queue's Format column text."""
    return '9:16' if vertical else '16:9'

def resolve_audio(scene_path, mode, audio_file=None, narrated=False):
    """What sound one queue item uses. mode: 'generated' | 'none' | 'file' | 'auto'; audio_file: the manual External file;
    narrated: the item has narration (attached or embedded), whose voice lives in the page's own sound.
    Returns dict(sound, audio_file, label, warn): sound/audio_file go to render_core, label is the queue's Audio column,
    warn is None or why Auto fell back to the scene's own sound. Pure apart from os.path.isfile."""
    if narrated:
        return dict(sound='generated', audio_file=None, label='narration', warn=None)
    if mode == 'auto':
        song = find_scene_audio(scene_path)
        if song: return dict(sound='file', audio_file=song, label=os.path.basename(song), warn=None)
        return dict(sound='generated', audio_file=None, label='scene sound (no file)',
                    warn=f'{os.path.basename(scene_path)}: no audio file next to it, used the scene sound')
    if mode == 'file':
        return dict(sound='file', audio_file=audio_file, label=os.path.basename(audio_file or '') or '?', warn=None)
    if mode == 'none':
        return dict(sound='none', audio_file=None, label='silent', warn=None)
    return dict(sound='generated', audio_file=None, label='scene sound', warn=None)

def write_failure_log(scene, out_mp4, preset, opts, exc, tb_text, log_dir=None, when=None):
    """Write <scene name>_<YYYYMMDD-HHMMSS>.log for one failed render and return its path (None if it cannot be written).
    log_dir: the scene's output folder (the queue passes it); without it the fallback LOG_DIR is used.
    ffmpeg's output only reaches us through render_core's error text ('ffmpeg failed: <its last line>'), so that is what is kept."""
    try:
        d = log_dir or LOG_DIR; os.makedirs(d, exist_ok=True)
        stamp = (when or datetime.datetime.now()).strftime('%Y%m%d-%H%M%S')
        path = unique_path(os.path.join(d, f'{os.path.splitext(os.path.basename(scene))[0]}_{stamp}.log'))
        msg = str(exc).strip()
        ff = [l for l in msg.splitlines() if 'ffmpeg' in l.lower()]
        lines = [f'Shadow v{rc.VERSION} - render failed {stamp}', '',
                 f'scene:   {scene}', f'output:  {out_mp4}', f'preset:  {json.dumps(preset)}',
                 f'options: {json.dumps(opts, ensure_ascii=False)}', '',
                 f'error:   {type(exc).__name__}: {msg.splitlines()[0] if msg else ""}', '',
                 'ffmpeg output (last lines):', *(ff or ['(not available: ffmpeg did not fail, or gave no output)']), '',
                 'traceback:', tb_text.rstrip()]
        with open(path, 'w', encoding='utf-8') as fh: fh.write('\n'.join(lines) + '\n')
        return path
    except Exception:
        log.exception('could not write the failure log')
        return None

# ---------------------------------------------------------------- narration (tools/narrate/make_narrated_scene.py)
NARR_PREFIX = '.shadow_narr_'      # hidden temp scene written next to the original, so its relative files still load
VOICE_MARK = '[voice]'             # shown after the file name of a queue row that has narration
VOICE_EMBEDDED_MARK = '[voice embedded]'   # the scene file itself already contains narration
NARR_STALE_S = 3600               # older leftovers of a crashed run are removed before writing a new one

def narrate_module():
    """make_narrated_scene, from tools/narrate next to app.py or inside the PyInstaller bundle (_MEIPASS)."""
    base = os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))), 'tools', 'narrate')
    if base not in sys.path: sys.path.insert(0, base)
    return importlib.import_module('make_narrated_scene')

def remove_with_retries(path, tries=3, delay=0.3):
    """Delete a file, retrying a few times (OneDrive / antivirus can hold it for a moment). Never raises."""
    for i in range(tries):
        try:
            if os.path.exists(path): os.remove(path)
            return True
        except OSError:
            if i < tries - 1: time.sleep(delay)
    log.warning('could not delete %s', path)
    return False

def narrate_for_render(scene_path, narr, info=None):
    """Write the scene with narration embedded as a hidden .shadow_narr_<id>_<scene>.html next to the original and return
    its path (the caller deletes it). narr: {'wav', 'words' (path to words.json or a list), 'style', 'yh', 'gain', 'captions'}.
    Stale .shadow_narr_* files older than NARR_STALE_S in that folder are removed first. info gets the tool's
    narration_duration / words / warnings. Raises ValueError for bad narration input (tool errors included)."""
    d = os.path.dirname(os.path.abspath(scene_path))
    for old in glob.glob(os.path.join(d, NARR_PREFIX + '*')):
        try:
            if time.time() - os.path.getmtime(old) > NARR_STALE_S: remove_with_retries(old)
        except OSError: pass
    words = narr.get('words')
    if isinstance(words, str):
        try:
            with open(words, encoding='utf-8') as fh: words = json.load(fh)
        except (OSError, ValueError) as e:
            raise ValueError(f'words file could not be read: {e}')
    with open(scene_path, encoding='utf-8') as fh: html = fh.read()
    html = narrate_module().build_narrated_html(html, narr.get('wav'), words, style=narr.get('style', 'pop'), yh=narr.get('yh', 0.88),
                                                gain=narr.get('gain', 1.0), captions=narr.get('captions', True), info=info)
    path = os.path.join(d, f'{NARR_PREFIX}{uuid.uuid4().hex[:8]}_{os.path.splitext(os.path.basename(scene_path))[0]}.html')
    with open(path, 'w', encoding='utf-8') as fh: fh.write(html)
    if os.name == 'nt':
        try:
            import ctypes; ctypes.windll.kernel32.SetFileAttributesW(path, 0x2)   # FILE_ATTRIBUTE_HIDDEN
        except Exception: pass
    return path

def render_item(scene, mp4, preset, opts, narr=None, cancel=None, progress=lambda *_: None, info=None, warns=None):
    """Render one queue item. Without narration this is exactly rc.render(scene, ...). With narration: check cancel,
    build the hidden temp scene, force sound='generated' (the voice is mixed inside the page's SOUND kit), render it
    to mp4 (the name comes from the caller, i.e. the original scene) and always delete the temp file."""
    if not narr:
        return rc.render(scene, mp4, preset, progress=progress, cancel=cancel, info=info, **opts)
    if cancel is not None and cancel.is_set(): raise rc.Cancelled()
    opts = dict(opts, sound='generated', audio_file=None)
    tmp = None
    try:
        ninfo = {}
        tmp = narrate_for_render(scene, narr, info=ninfo)
        if warns is not None: warns.extend(f'{os.path.basename(scene)}: {w}' for w in ninfo.get('warnings', []))
        if cancel is not None and cancel.is_set(): raise rc.Cancelled()
        return rc.render(tmp, mp4, preset, progress=progress, cancel=cancel, info=info, **opts)
    finally:
        if tmp: remove_with_retries(tmp)

def quality_check(mp4, preset, vertical, info, sound):
    """Post-render QC (qc.run_qc + qc.write_report) for a finished mp4. Returns (result, report_path), or (None, None) if
    the QC code itself broke - that is only logged. Never raises, never touches the mp4.
    info: render_core's info dict (duration = what was rendered); sound: the mode actually used ('none' = silent by design)."""
    try:
        w, h = expected_size(preset, vertical)
        res = qc.run_qc(mp4, dict(width=w, height=h, duration=(info or {}).get('duration'), silent=(sound == 'none')))
        path = qc.write_report(mp4, res)
        log.info('qc %s %s: %s%s', res['status'], mp4, '; '.join(res['reasons']) or 'no issues', f' (report {path})' if path else '')
        return res, path
    except Exception:
        log.error('qc crashed for %s (render kept as is)\n%s', mp4, traceback.format_exc())
        return None, None

def expected_size(preset, vertical):
    w = preset['w']; return (round(w * 9 / 16), w) if vertical else (w, round(w * 9 / 16))

def probe(mp4):
    """(width, height, seconds, has_audio) of a rendered file, or None."""
    ff = rc.find_ffmpeg()
    exe = shutil.which('ffprobe') or (os.path.join(os.path.dirname(ff), 'ffprobe' + ('.exe' if os.name == 'nt' else '')) if ff else None)
    if not exe or not os.path.exists(exe): return None
    r = subprocess.run([exe, '-v', 'error', '-show_entries', 'stream=codec_type,width,height:format=duration', '-of', 'json', mp4],
                       capture_output=True, text=True, creationflags=NOWIN)
    try: j = json.loads(r.stdout)
    except Exception: return None
    v = next((s for s in j.get('streams', []) if s.get('codec_type') == 'video'), None)
    if not v: return None
    return v['width'], v['height'], float(j.get('format', {}).get('duration', 0) or 0), any(s.get('codec_type') == 'audio' for s in j['streams'])

def dark_titlebar(win, dark):
    """Windows 10 1809+/11: DWMWA_USE_IMMERSIVE_DARK_MODE (20, or 19 on old builds). On Windows 11 the caption colour is also
    set (DWMWA_CAPTION_COLOR / TEXT_COLOR), otherwise "accent colour on title bars" keeps the bar coloured."""
    if os.name != 'nt': return
    try:
        import ctypes
        win.update_idletasks()
        dwm = ctypes.windll.dwmapi; hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        def put(attr, v):
            v = ctypes.c_uint(v); return dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
        if put(20, 1 if dark else 0) != 0: put(19, 1 if dark else 0)
        DEFAULT = 0xFFFFFFFF  # DWMWA_COLOR_DEFAULT
        put(35, 0x001C1C1C if dark else DEFAULT)   # caption colour, COLORREF 0x00BBGGRR
        put(36, 0x00FFFFFF if dark else DEFAULT)   # caption text
        # repaint the non-client area now (SWP_NOMOVE|NOSIZE|NOZORDER|NOACTIVATE|FRAMECHANGED)
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
    except Exception:
        pass

def dpi_aware():
    """Sharp text on scaled displays (Tk then scales its fonts itself)."""
    if os.name != 'nt': return
    try:
        import ctypes; ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

class App(Root):
    def __init__(self):
        super().__init__()
        setup_log()
        self.title(f'Shadow v{rc.VERSION}')
        base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))   # exe: unpacked bundle; source: project folder
        ico = os.path.join(base, 'assets', 'shadow.ico')
        try:
            if os.path.exists(ico): self.iconbitmap(default=ico)   # default= also covers the message boxes
        except Exception: pass
        # self.cancel = "Stop queue" (the whole batch); self.item_cancel = a fresh Event per file, the one render() watches,
        # so "Cancel current" stops only that file and can never leak into the next one
        self.q = queue.Queue(); self.cancel = threading.Event(); self.item_cancel = threading.Event(); self.worker = None
        cfg = load_cfg(); self.files = []; self.rows = {}
        # the live queue: self.files (order) + self.state[path] = 'queued' | 'running' | 'finished' | 'skipped'.
        # The worker takes the next 'queued' file under self.lock, so files added/removed while rendering are honoured.
        self.state = {}; self.lock = threading.Lock()
        self.narr = {}   # path -> narration settings for that file (see narrate_for_render); set by the UI later
        self.embedded = set()   # files whose HTML already carries narration (shown as [voice embedded], never narrated again)
        self.theme_choice = cfg.get('theme') if cfg.get('theme') in ('dark', 'light') else None   # None = follow Windows
        self.theme = self.theme_choice or system_theme()
        self.out_dir = tk.StringVar(value=cfg.get('out', os.path.join(os.path.expanduser('~'), 'Videos')))
        q = cfg.get('preset'); self.preset = tk.StringVar(value=q if q in rc.PRESETS else 'Best (1080p, 64 samples)')
        self.open_when_done = tk.BooleanVar(value=cfg.get('open', True))
        f = cfg.get('format'); self.fmt = tk.StringVar(value=f if f in FORMAT_CHOICES else '16:9 (YouTube)')
        c = cfg.get('capture'); self.capture = tk.StringVar(value=c if c in CAPTURES else next(iter(CAPTURES)))
        s = cfg.get('sound'); self.snd = tk.StringVar(value=s if s in SOUNDS else SOUNDS[0]); self.snd_file = tk.StringVar(value=cfg.get('sound_file', ''))
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.apply_theme()
        self.build()
        self.apply_theme(refresh=True)   # sv-ttk set before the widgets exist leaves wrong fonts/colours on the first pass
        self.check()
        self.update_idletasks()   # size the window to its content (fonts scale with the display DPI)
        w, h = max(self.winfo_reqwidth(), int(self.winfo_fpixels('7i'))), self.winfo_reqheight() + int(self.winfo_fpixels('0.3i'))
        self.minsize(self.winfo_reqwidth(), self.winfo_reqheight()); self.geometry(f'{w}x{h}')
        self._poll_id = self.after(150, self.poll)
        self.update_rel = None; self.update_busy = False; self.update_checking = False
        self.after(2500, self.start_update_check)

    # ------------------------------------------------------------------ updates (GitHub Releases, see updater.py)
    def start_update_check(self, manual=False):
        # automatic check (at start): only the installed exe, and silent when offline. manual = the 'Check for updates' button
        if not updater.configured():
            if manual: messagebox.showinfo('Update', 'Updates are not set up in this build (updater.REPO).')
            return
        if not manual and not (getattr(sys, 'frozen', False) or os.environ.get('SHADOW_UPDATE_URL')): return
        if self.update_checking: return
        self.update_checking = True
        if manual: self.update_btn.config(text='Checking...', state='disabled')
        def run():
            try:
                rel = updater.latest_release()
                if rel and updater.is_newer(rel['version'], rc.VERSION): self.q.put(('update', rel, manual))
                else: self.q.put(('update_none', manual, rel['version'] if rel else None))
            except Exception as e:
                log.info('%s', e); self.q.put(('update_none', manual, e))   # offline / rate limited: quiet unless manual
        threading.Thread(target=run, daemon=True).start()
    def on_update_btn(self):
        if self.update_rel: self.do_update()
        else: self.start_update_check(manual=True)
    def show_update(self, rel, manual=False):
        self.update_checking = False; self.update_rel = rel
        self.update_btn.config(text=f'Update to v{rel["version"]}', style='Accent.TButton', state='normal')
        if manual: self.do_update()
    def update_none(self, manual, info):
        self.update_checking = False; self.update_btn.config(text='Check for updates', state='normal')
        if not manual: return
        if isinstance(info, Exception):
            messagebox.showwarning('Update', f'Could not check for updates (no internet?).\n\n{info}')
        else:
            messagebox.showinfo('Update', f'You have the latest version (v{rc.VERSION}).')
    def do_update(self):
        rel = self.update_rel
        if not rel or self.update_busy: return
        if not (getattr(sys, 'frozen', False) or os.environ.get('SHADOW_UPDATE_URL')):
            messagebox.showinfo('Update', f'Shadow v{rel["version"]} is available. This copy runs from source: update it with git pull.'); return
        if self.worker and self.worker.is_alive():
            messagebox.showinfo('Update', 'Wait until the render queue has finished, then press Update again.'); return
        # only the first paragraph (what changed); the rest of the release text is download help for the GitHub page
        first = re.split(r'\r?\n\s*\r?\n', rel['notes'].strip(), maxsplit=1)[0]
        notes = first[:1200] + ('...' if len(first) > 1200 else '')
        if not messagebox.askyesno('Update', f'Shadow v{rel["version"]} is available (you have v{rc.VERSION}).\n\n'
                                   + (notes + '\n\n' if notes else '') + 'Download and install it now? Shadow closes and opens again by itself.'):
            return
        self.update_busy = True; self.update_btn.config(state='disabled'); self.go.config(state='disabled')
        dest = os.path.join(os.environ.get('TEMP') or os.path.expanduser('~'), 'Shadow-update')
        for old in glob.glob(os.path.join(dest, '*')):   # setups from earlier updates
            try: os.remove(old)
            except OSError: pass
        def prog(got, total): self.q.put(('update_p', got, total))
        def run():
            try: self.q.put(('update_ready', updater.download(rel, dest, progress=prog)))
            except Exception as e:
                log.error('update download: %s', e); self.q.put(('update_fail', str(e)))
        threading.Thread(target=run, daemon=True).start()
    def run_installer(self, path):
        # /SILENT: progress window only, same folder and shortcuts as before; /relaunch=1 makes the setup start Shadow again
        try:
            subprocess.Popen([path, '/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/relaunch=1'], env=updater.child_env())
        except Exception as e:
            self.update_failed(str(e)); return
        self.on_close()
    def update_failed(self, msg):
        self.update_busy = False; self.update_btn.config(state='normal'); self.go.config(state='normal')
        self.status.config(text='Update failed'); messagebox.showerror('Update', f'{msg}\n\nYou can also download the setup from:\n{self.update_rel.get("page") or updater.REPO}')

    # ------------------------------------------------------------------ layout
    def build(self):
        P = 14
        top = ttk.Frame(self); top.pack(fill='x', padx=P, pady=(P, 6))
        ttk.Label(top, text='Shadow', font='SunValleyTitleLargeFont').pack(side='left')
        ttk.Label(top, text=f'  v{rc.VERSION}   HTML scene → MP4', foreground='#888').pack(side='left', pady=(6, 0))
        self.theme_btn = ttk.Button(top, width=9, command=self.toggle_theme); self.theme_btn.pack(side='right')
        # 'Check for updates'; becomes the accent 'Update to vX.Y' once a newer release is found
        self.update_btn = ttk.Button(top, text='Check for updates', command=self.on_update_btn); self.update_btn.pack(side='right', padx=(0, 8))
        self._theme_text()

        # compact setup status bar
        env = ttk.Frame(self); env.pack(fill='x', padx=P)
        self.env_lbls = {}
        for k in ('Chrome', 'ffmpeg', 'playwright'):
            dot = ttk.Label(env, text='●'); dot.pack(side='left'); txt = ttk.Label(env, text=k); txt.pack(side='left', padx=(3, 14))
            self.env_lbls[k] = (dot, txt)
        ttk.Button(env, text='Install missing', command=self.install_missing).pack(side='right')
        ttk.Button(env, text='Re-check', command=self.check).pack(side='right', padx=6)

        # drop area + table
        card = ttk.LabelFrame(self, text='Scenes', padding=10); card.pack(fill='both', expand=True, padx=P, pady=10)
        dz = ttk.Frame(card, style='Card.TFrame', padding=14, cursor='hand2'); dz.pack(fill='x')
        self.drop = ttk.Label(dz, anchor='center', cursor='hand2', font='SunValleyBodyLargeFont',
                              text=('⤓   Drop .html scenes here, or click to browse' if HAS_DND else 'Click to add .html scenes'))
        self.drop.pack(fill='x')
        for w in (dz, self.drop): w.bind('<Button-1>', lambda e: self.add())
        self.dz = dz
        tf = ttk.Frame(card); tf.pack(fill='both', expand=True, pady=(8, 8))
        # values: name, status, dur, audio, fmt (fmt is last so older code/tests keep their indices; shown before Audio)
        self.tree = ttk.Treeview(tf, columns=('name', 'status', 'dur', 'audio', 'fmt'), displaycolumns=('name', 'status', 'dur', 'fmt', 'audio'),
                                 show='headings', height=6, selectmode='extended')
        k = self.winfo_fpixels('1i') / 96
        for c, t, w, st in (('name', 'Name', 300, True), ('status', 'Status', 200, True), ('dur', 'Duration', 80, False),
                            ('audio', 'Audio', 170, True), ('fmt', 'Format', 70, False)):
            self.tree.heading(c, text=t, anchor='w'); self.tree.column(c, width=round(w * k), minwidth=round(60 * k), stretch=st, anchor='w')
        sb = ttk.Scrollbar(tf, orient='vertical', command=self.tree.yview); self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True); sb.pack(side='right', fill='y')
        self.tree.bind('<Double-1>', lambda e: self.preview()); self.tree.bind('<Delete>', lambda e: self.remove())
        self.tree.bind('<<TreeviewSelect>>', lambda e: self._update_narr_btn())
        if HAS_DND:
            for w in (self.dz, self.drop, self.tree):
                w.drop_target_register(DND_FILES); w.dnd_bind('<<Drop>>', self.on_drop)
        bt = ttk.Frame(card); bt.pack(fill='x')
        for i, (t, c) in enumerate((('Add...', self.add), ('Remove', self.remove), ('Clear', self.clear), ('Preview', self.preview), ('Narration...', self.open_narration_dialog))):
            b = ttk.Button(bt, text=t, command=c); b.grid(row=0, column=i, sticky='we', padx=(0 if i == 0 else 6, 0))
            bt.columnconfigure(i, weight=1, uniform='b')
        self.narr_btn = b; self.narr_btn.config(state='disabled')   # enabled when one row is selected that can take narration

        # options
        op = ttk.LabelFrame(self, text='Options', padding=10); op.pack(fill='x', padx=P)
        rows = (('Quality', self.preset, list(rc.PRESETS)), ('Format', self.fmt, FORMAT_CHOICES), ('Sound', self.snd, SOUNDS),
                ('Capture', self.capture, list(CAPTURES)))
        for r, (lbl, var, vals) in enumerate(rows):
            ttk.Label(op, text=lbl).grid(row=r, column=0, sticky='w', pady=3)
            ttk.Combobox(op, textvariable=var, values=vals, state='readonly', width=32).grid(row=r, column=1, sticky='w', padx=10, pady=3)
        self.snd.trace_add('write', self.on_sound)
        self.fmt.trace_add('write', lambda *_: self.refresh_audio())   # the Format column follows the Format setting
        ttk.Label(op, textvariable=self.snd_file, foreground='#888').grid(row=2, column=2, columnspan=2, sticky='w')
        ttk.Label(op, text='Save to').grid(row=4, column=0, sticky='w', pady=3)
        ttk.Entry(op, textvariable=self.out_dir).grid(row=4, column=1, columnspan=2, sticky='we', padx=10, pady=3)
        ttk.Button(op, text='Browse...', command=self.pick_out).grid(row=4, column=3, sticky='e')
        op.columnconfigure(2, weight=1)

        # progress
        pr = ttk.Frame(self); pr.pack(fill='x', padx=P, pady=(12, 0))
        self.bar = ttk.Progressbar(pr, maximum=100); self.bar.pack(side='left', fill='x', expand=True)
        self.pct = ttk.Label(pr, text='0%', width=16, anchor='e'); self.pct.pack(side='right', padx=(10, 0))
        self.status = ttk.Label(self, text='Ready', foreground='#888'); self.status.pack(anchor='w', padx=P, pady=(4, 0))

        # actions
        act = ttk.Frame(self); act.pack(fill='x', padx=P, pady=P)
        self.go = ttk.Button(act, text='Render', style='Accent.TButton', command=self.start)
        self.stop_cur = ttk.Button(act, text='Cancel current', command=self.do_cancel_current, state='disabled')
        self.stop = ttk.Button(act, text='Stop queue', command=self.do_cancel, state='disabled')
        for i, w in enumerate((self.go, self.stop_cur, self.stop, ttk.Button(act, text='Open folder', command=self.open_out), ttk.Button(act, text='Open log', command=self.open_log))):
            w.grid(row=0, column=i, sticky='we', padx=(0 if i == 0 else 6, 0)); act.columnconfigure(i, weight=1, uniform='a')
        ttk.Checkbutton(act, text='Open folder when done', variable=self.open_when_done).grid(row=1, column=0, columnspan=5, sticky='w', pady=(8, 0))

    # ------------------------------------------------------------------ theme
    def apply_theme(self, refresh=False):
        if sv_ttk:
            try:
                if refresh: sv_ttk.set_theme('light' if self.theme == 'dark' else 'dark', self)
                sv_ttk.set_theme(self.theme, self)
                if self.theme == 'light': self._light_gray()
                # the root window is a plain Tk widget (not ttk): give it the current theme's background every time,
                # or the light gray stays between the frames after switching back to Dark
                self.configure(background=LIGHT_BG if self.theme == 'light' else (str(ttk.Style(self).lookup('TFrame', 'background')) or '#1c1c1c'))
                self._scale_theme()
            except Exception: log.exception('theme')
        dark_titlebar(self, self.theme == 'dark')
    def _light_gray(self):
        # the owner's Light theme: light gray instead of sv-ttk's near-white #fafafa (only the 'light' theme is touched;
        # buttons, entries and cards keep sv-ttk's lighter images, so they stand out on the gray)
        st = ttk.Style(self)
        st.configure('.', background=LIGHT_BG)
        for s in ('TFrame', 'TLabel', 'TLabelframe', 'TLabelframe.Label', 'TCheckbutton', 'TRadiobutton'):
            st.configure(s, background=LIGHT_BG)
        st.configure('Treeview', background=LIGHT_FIELD, fieldbackground=LIGHT_FIELD)
        st.configure('Treeview.Heading', background=LIGHT_BG)
        self.configure(background=LIGHT_BG)
        if not getattr(self, '_light_tinted', False):
            self._light_tinted = True
            try: self._tint_light_sprites()
            except Exception: log.exception('light gray sprites')   # keeps sv-ttk's white parts, still usable
    def _tint_light_sprites(self):
        # cards, the table, entries and buttons are images cut from sv-ttk's spritesheet_light.png: darken their near-white
        # pixels by LIGHT_TINT (#fafafa -> LIGHT_BG) and copy them back into the theme's own images (in place, light theme only)
        import base64, io
        from PIL import Image
        sheet = Image.open(os.path.join(os.path.dirname(sv_ttk.__file__), 'theme', 'spritesheet_light.png')).convert('RGBA')
        px = [(r - LIGHT_TINT, g - LIGHT_TINT, b - LIGHT_TINT, a) if a and min(r, g, b) >= 236 else (r, g, b, a) for r, g, b, a in sheet.getdata()]
        sheet.putdata(px); buf = io.BytesIO(); sheet.save(buf, 'PNG')
        tinted = tk.PhotoImage(master=self, data=base64.b64encode(buf.getvalue()).decode('ascii'), format='png')
        info = self.tk.splitlist(self.tk.eval('set ::spriteinfo'))   # name x y w h ... (same layout for light and dark)
        imgs = dict(zip(*[iter(self.tk.splitlist(self.tk.eval('array get ::ttk::theme::sv_light::I')))] * 2))
        for i in range(0, len(info), 5):
            name, x, y, w, h = info[i], *map(int, info[i + 1:i + 5])
            if name in imgs: self.tk.call(imgs[name], 'copy', str(tinted), '-from', x, y, x + w, y + h)
        self._tinted_sheet = tinted   # keep the Tk image alive
    def _scale_theme(self):
        # sv-ttk sizes its fonts and rows in pixels, so on a 150-225% display they come out tiny next to everything else
        k = self.winfo_fpixels('1i') / 96
        if not getattr(self, '_fonts_scaled', False):
            import tkinter.font as tkfont
            for n in tkfont.names(self):
                if n.startswith('SunValley'):
                    f = tkfont.nametofont(n, self); sz = int(f.cget('size'))
                    if sz < 0: f.configure(size=-round(-sz * k))
            self._fonts_scaled = True
        ttk.Style(self).configure('Treeview', rowheight=round(26 * k))
    def _theme_text(self):
        self.theme_btn.config(text='Light' if self.theme == 'dark' else 'Dark')
    def toggle_theme(self):
        self.theme = self.theme_choice = 'light' if self.theme == 'dark' else 'dark'
        self.apply_theme(); self._theme_text(); self.save()

    # ------------------------------------------------------------------ setup
    def check(self):
        e = rc.check_env(); self.env = e
        for k, ok in (('Chrome', e['chrome']), ('ffmpeg', e['ffmpeg']), ('playwright', e['playwright'])):
            dot, txt = self.env_lbls[k]
            dot.config(foreground='#3fb950' if ok else '#f85149'); txt.config(text=k if ok else f'{k} (missing)')
    def install_missing(self):
        cmds = []
        frozen = getattr(sys, 'frozen', False)  # in Shadow.exe the python packages are bundled; sys.executable is the exe
        if not frozen and (not self.env['playwright'] or not HAS_DND or not sv_ttk): cmds.append(f'"{sys.executable}" -m pip install playwright tkinterdnd2 sv-ttk darkdetect')
        if not self.env['ffmpeg']: cmds.append('winget install -e --id Gyan.FFmpeg')
        if not self.env['chrome']: cmds.append('winget install -e --id Google.Chrome')
        if not cmds:
            messagebox.showinfo('Setup', 'Everything is installed.'); return
        subprocess.Popen('cmd /c "' + ' && '.join(cmds) + ' && echo. && echo Done. Close this window and restart the app. && pause"', creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
        messagebox.showinfo('Setup', 'Installing in a new window. When it finishes, close this app and open it again (ffmpeg needs a restart to be found).')

    # ------------------------------------------------------------------ file list
    def on_drop(self, ev):
        for f in self.tk.splitlist(ev.data): self._add(f)
    def add(self):
        for f in filedialog.askopenfilenames(filetypes=[('HTML scene', '*.html *.htm')]): self._add(f)
    def _add(self, f):
        f = os.path.normpath(f)
        if f.lower().endswith(('.html', '.htm')) and f not in self.files:
            with self.lock:   # while a batch runs, a new file joins it and renders after the ones before it
                self.files.append(f); self.state[f] = 'queued'
            try:   # narration already built into the scene (the tool's own check): marked, never narrated twice
                with open(f, encoding='utf-8', errors='replace') as fh:
                    if narrate_module().is_narrated(fh.read()): self.embedded.add(f)
            except Exception: pass
            self.rows[f] = self.tree.insert('', 'end', values=(self._row_name(f), 'Queued', '—', '', ''))
            self._auto_narration(f)
            self._refresh_name(f)
            if sound_mode(self.snd.get()) == 'auto':
                w = self._audio(f)['warn']
                if w: self.status.config(text=w)
    def _auto_narration(self, f):
        """<scene>.wav + <scene>.words.json next to the scene -> attach them (style pop) and say so on the status line."""
        if f in self.embedded: return
        base = os.path.splitext(f)[0]; wav, words = base + '.wav', base + '.words.json'
        if not (os.path.isfile(wav) and os.path.isfile(words)): return
        err = self.set_narration(f, wav, words, style='pop')
        name = os.path.basename(f)
        self.status.config(text=f'{name}: narration found next to it ({os.path.basename(wav)} + {os.path.basename(words)})' if err is None
                           else f'{name}: narration files found but not used: {err}')
    def _row_name(self, f):
        mark = f' {VOICE_EMBEDDED_MARK}' if f in self.embedded else f' {VOICE_MARK}' if f in self.narr else ''
        return os.path.basename(f) + mark
    def _selected_path(self):
        sel = self.tree.selection()
        return next((k for k, v in self.rows.items() if len(sel) == 1 and v == sel[0]), None)
    def _narr_blocked(self, f):
        """Why this row cannot get narration now, or None."""
        if f is None: return 'select one scene first'
        if f in self.embedded: return f'{os.path.basename(f)} already has narration built in'
        with self.lock:
            if self.state.get(f) == 'running': return f"{os.path.basename(f)} is rendering; change its narration after it finishes"
        return None
    def _update_narr_btn(self):
        try: self.narr_btn.config(state='disabled' if self._narr_blocked(self._selected_path()) else 'normal')
        except Exception: pass
    def open_narration_dialog(self):
        """The 'Narration...' button. Returns the dialog (tests drive it), or None with a status-line reason."""
        f = self._selected_path(); why = self._narr_blocked(f)
        if why:
            self.status.config(text=why); return None
        return NarrationDialog(self, f)
    def _audio(self, f):
        """resolve_audio for one queued file with the current Sound setting (what the Audio column shows)."""
        mode = sound_mode(self.snd.get())
        narrated = f in self.narr or (f in self.embedded and mode in ('auto', 'generated'))
        return resolve_audio(f, mode, self.snd_file.get() or None, narrated=narrated)
    def _refresh_name(self, f):
        iid = self.rows.get(f)
        if iid and self.tree.exists(iid):
            v = list(self.tree.item(iid, 'values')); v += [''] * (5 - len(v))
            v[0] = self._row_name(f); v[3] = self._audio(f)['label']; v[4] = format_label(format_vertical(f, self.fmt.get()))
            self.tree.item(iid, values=v)
    def refresh_audio(self):
        """Update every row's Audio and Format columns; in Auto sound mode the status line names the scenes without an audio file."""
        for f in list(self.files): self._refresh_name(f)
        if sound_mode(self.snd.get()) == 'auto':
            miss = [os.path.basename(f) for f in self.files if self._audio(f)['warn']]
            if miss: self.status.config(text='No audio file next to: ' + ', '.join(miss) + ' (scene sound is used)')
            return miss
        return []

    def set_narration(self, path, wav, words, style='pop', yh=0.88, gain=1.0, captions=True):
        """Attach narration to a queued file. Checks everything that can be checked now (file in the list, wav and
        words.json readable, style, the scene not narrated already, a Shadow scene with SOUND/seek) and returns an error
        string, or None when it was set. Never raises. A file that is rendering right now keeps what it started with."""
        try:
            f = os.path.normpath(path)
            if f not in self.files: return f'not in the list: {os.path.basename(f)}'
            if not wav or not os.path.isfile(wav): return f'narration WAV not found: {wav}'
            if not words: return 'no words.json given'
            if isinstance(words, str):
                if not os.path.isfile(words): return f'words file not found: {words}'
                try:
                    with open(words, encoding='utf-8') as fh: wj = json.load(fh)
                except (OSError, ValueError) as e: return f'words file could not be read: {e}'
            else:
                wj = words
            wl = wj['words'] if isinstance(wj, dict) and 'words' in wj else wj
            if not isinstance(wl, list) or not wl or not all(isinstance(x, dict) and {'w', 's', 'e'} <= set(x) for x in wl):
                return 'words must be a non-empty list of {"w","s","e"} (or {"words": [...]})'
            mod = narrate_module()
            if style not in mod.STYLES: return f'style must be one of {", ".join(mod.STYLES)}, got {style!r}'
            with open(f, encoding='utf-8') as fh: html = fh.read()
            if mod.is_narrated(html): return f'{os.path.basename(f)} already has narration built in'
            if 'SOUND' not in html or 'seek' not in html: return f'{os.path.basename(f)} is not a Shadow scene with a SOUND kit'
            with self.lock:
                self.narr[f] = dict(wav=wav, words=words, style=style, yh=yh, gain=gain, captions=captions)
            self._refresh_name(f)
            return None
        except Exception as e:
            log.exception('set_narration'); return f'{type(e).__name__}: {e}'

    def clear_narration(self, path):
        """Remove the narration from a queued file (nothing happens if it had none)."""
        f = os.path.normpath(path)
        with self.lock: self.narr.pop(f, None)
        self._refresh_name(f)

    def set_row(self, f, status=None, dur=None):
        iid = self.rows.get(f)
        if not iid or not self.tree.exists(iid): return
        v = list(self.tree.item(iid, 'values'))
        if status is not None: v[1] = status
        if dur is not None: v[2] = dur
        self.tree.item(iid, values=v)
    def _drop(self, paths):
        """Take files out of the list, except the one rendering right now. Returns True if that one was asked for."""
        kept = False
        with self.lock:
            for f in paths:
                if self.state.get(f) == 'running': kept = True; continue
                self.files.remove(f); self.state.pop(f, None); self.narr.pop(f, None); self.embedded.discard(f); self.tree.delete(self.rows.pop(f))
        if kept: self.status.config(text="The file that is rendering can't be removed. Press Cancel to stop it.")
        return kept
    def remove(self):
        self._drop([k for iid in self.tree.selection() for k, v in self.rows.items() if v == iid])
    def clear(self):
        self._drop(list(self.files))
    def preview(self):
        sel = self.tree.selection()
        f = next((k for k, v in self.rows.items() if sel and v == sel[0]), self.files[0] if self.files else None)
        if f: open_path(f)

    # ------------------------------------------------------------------ options
    def on_sound(self, *_):
        if self.snd.get().startswith('External'):
            f = filedialog.askopenfilename(filetypes=[('Audio', '*.wav *.mp3 *.m4a *.aac *.flac *.ogg')])
            if f: self.snd_file.set(f)
            elif not self.snd_file.get(): self.snd.set(SOUNDS[0])
        self.refresh_audio()
    def pick_out(self):
        d = filedialog.askdirectory(initialdir=self.out_dir.get())
        if d: self.out_dir.set(d)
    def open_out(self):
        d = self.out_dir.get(); os.makedirs(d, exist_ok=True); open_path(d)
    def open_log(self):
        if not os.path.exists(LOG): open(LOG, 'a').close()
        open_path(LOG)
    def save(self):
        save_cfg({'out': self.out_dir.get(), 'preset': self.preset.get(), 'open': self.open_when_done.get(), 'format': self.fmt.get(),
                  'capture': self.capture.get(), 'sound': self.snd.get(), 'sound_file': self.snd_file.get(), **({'theme': self.theme_choice} if self.theme_choice else {})})
    def on_close(self):
        self.do_cancel(); self.save()
        try: self.after_cancel(self._poll_id)
        except Exception: pass
        self.destroy()

    # ------------------------------------------------------------------ render
    def start(self, ask=messagebox.askyesnocancel):
        if not self.files: messagebox.showwarning('Render', 'Add at least one .html file.'); return
        self.check()
        miss = [k for k, v in self.env.items() if not v]
        if miss: messagebox.showwarning('Setup', 'Missing: ' + ', '.join(miss) + '. Use "Install missing".'); return
        try: os.makedirs(self.out_dir.get(), exist_ok=True)
        except OSError as e: messagebox.showerror('Save to', str(e)); return
        mode = sound_mode(self.snd.get())
        if mode == 'file' and not os.path.exists(self.snd_file.get()): messagebox.showwarning('Sound', 'Pick an audio file.'); return
        fmt_name = self.fmt.get(); vertical = format_vertical('', fmt_name)   # Auto: decided per file in the worker
        jobs = [(f, scene_info(f)) for f in self.files]
        if any(format_vertical(f, fmt_name) for f, _ in jobs):   # only the files that render 9:16 must read get('h')
            old = [f for f, i in jobs if format_vertical(f, fmt_name) and not i['vertical']]
            if old:
                a = ask('9:16', f'{OUTDATED}:\n\n' + '\n'.join(os.path.basename(f) for f in old) +
                        '\n\nRender them anyway?\nYes = render all   No = skip these   Cancel = stop')
                if a is None: return
                if a is False:
                    for f in old: self.set_row(f, 'Skipped (outdated, no 9:16)')
                    jobs = [j for j in jobs if j[0] not in old]
                    if not jobs: return
        for f, _ in jobs: self.set_row(f, 'Queued', '—')
        self.refresh_audio()   # Audio column + the Auto fallback warning on the status line
        with self.lock:
            keep = {f for f, _ in jobs}
            for f in self.files: self.state[f] = 'queued' if f in keep else 'skipped'
        self.opts = dict(vertical=vertical, format=fmt_name, sound=mode, audio_file=self.snd_file.get() or None,
                         capture=CAPTURES.get(self.capture.get(), 'png'))
        self.cancel.clear(); self.go.config(state='disabled'); self.stop.config(state='normal'); self.stop_cur.config(state='normal')
        self.bar['value'] = 0; self.pct.config(text='0%')
        preset = rc.PRESETS[self.preset.get()]
        log.info('batch start: %d file(s), preset=%s, format=%s, sound=%s', len(jobs), self.preset.get(), self.fmt.get(), mode)
        # Tk variables are read here on the main thread; the worker only gets plain values
        self.worker = threading.Thread(target=self.run, args=(preset, self.out_dir.get(), fmt_name), daemon=True); self.worker.start()

    def do_cancel(self):
        """Stop queue: stop the running file and cancel every file still Queued."""
        with self.lock: self.cancel.set(); self.item_cancel.set()
        if self.status.winfo_exists(): self.status.config(text='Stopping the queue...')
    def do_cancel_current(self):
        """Cancel current: stop only the running file; the queue goes on with the next Queued one."""
        with self.lock: self.item_cancel.set()
        self.status.config(text='Cancelling the current file...')

    def next_queued(self):
        """Mark the first 'queued' file as 'running' and return (file, how many are still queued after it, its own cancel
        Event, a copy of its narration settings or None); (None, 0, None, None) if none. The Event is new for every file;
        it starts set if the queue was already stopped. The narration copy is taken here, under the lock, so the worker
        never reads the live self.narr while the UI changes it."""
        with self.lock:
            f = next((x for x in self.files if self.state.get(x) == 'queued'), None)
            if not f: return None, 0, None, None
            self.state[f] = 'running'
            ev = self.item_cancel = threading.Event()
            if self.cancel.is_set(): ev.set()
            narr = dict(self.narr[f]) if f in self.narr else None
            return f, sum(1 for x in self.files if self.state.get(x) == 'queued'), ev, narr

    def run(self, preset, out, fmt_name):
        done, failed, warns, used = [], [], [], set()
        k = 0
        while True:
            f, left, ev, narr = self.next_queued()   # live list: files added while rendering are picked up, removed ones are not
            if f is None: break
            n = k + 1 + left
            try:
                self._run_one(f, k, n, preset, out, fmt_name, done, failed, warns, used, ev, narr=narr)
            finally:
                with self.lock: self.state[f] = 'finished'
                k += 1
        for w in warns: log.warning(w)
        self.q.put(('end', done, failed, warns, self.cancel.is_set()))

    def _run_one(self, f, k, n, preset, out, fmt_name, done, failed, warns, used, ev, narr=None):
        """One file in the worker thread. narr: the copy of this file's narration settings from next_queued (or None)."""
        if self.cancel.is_set() or ev.is_set():
            self.q.put(('row', f, 'Cancelled', None)); return
        # every scene gets its own folder <out>\<scene name>\ : the mp4, its .qc.txt and a failure log all live there.
        # f is always the ORIGINAL scene (a narrated render uses a hidden temp copy only inside render_item).
        opts = dict(self.opts); used_mode = {}
        if opts.get('format') == FORMAT_AUTO:   # Format = Auto (by file name): 9:16 for short_*.html, 16:9 for the rest
            opts['vertical'] = format_vertical(f, FORMAT_AUTO)
            fmt_name = '9:16 (Shorts)' if opts['vertical'] else '16:9 (YouTube)'
        opts.pop('format', None)   # render_item / render_core do not take it
        base = scene_folder_name(f); folder = os.path.join(out, base)
        name = base + ('_shorts' if opts['vertical'] else '')
        mp4 = unique_path(os.path.join(folder, name + '.mp4'), used)   # never overwrite: not a file on disk, not one from this batch
        used.add(mp4)
        created = False
        if narr and opts['sound'] not in ('generated', 'auto'):   # the voice lives in the page's own sound: External file / None would drop it
            warns.append(f'{os.path.basename(f)}: narration needs generated sound, used Generated for this file.')
        if narr:
            opts['sound'] = 'generated'; opts['audio_file'] = None
        elif opts['sound'] == 'auto':   # per scene: <scene>.mp3/.wav/... next to it, else the scene's own sound (warned)
            ra = resolve_audio(f, 'auto', narrated=f in getattr(self, 'embedded', ()))
            opts['sound'], opts['audio_file'] = ra['sound'], ra['audio_file']
            if ra['warn']: warns.append(ra['warn'])
            log.info('auto audio for %s: %s', f, ra['audio_file'] or ra['label'])
        def prog(stage, a, b, eta=None, k=k, f=f):
            self.q.put(('p', k, n, f, stage, a, b, eta))
        try:
            created = not os.path.isdir(folder)
            os.makedirs(folder, exist_ok=True)
            log.info('render %s -> %s%s', f, mp4, ' (with narration)' if narr else '')
            render_item(f, mp4, preset, opts, narr=narr, cancel=ev, progress=prog, info=used_mode, warns=warns)
            silent = opts['sound'] == 'generated' and used_mode.get('sound') == 'none'
            if silent: warns.append(f'{os.path.basename(f)}: the scene has no sound, rendered silent.'); opts['sound'] = 'none'
            exp = expected_size(preset, opts['vertical']); pr = probe(mp4)
            if pr is None:
                warns.append(f'{os.path.basename(mp4)}: could not check the output (ffprobe missing?).'); dur = None
            else:
                dur = fmt(pr[2]) if pr[2] >= 1 else f'{pr[2]:.1f}s'
                if (pr[0], pr[1]) != exp:
                    warns.append(f'{os.path.basename(mp4)} is {pr[0]}x{pr[1]}, expected {exp[0]}x{exp[1]} for {fmt_name}.')
                if opts['sound'] != 'none' and not pr[3]:
                    warns.append(f'{os.path.basename(mp4)} has no audio track.')
            log.info('done %s %s', mp4, pr)
            row = 'Done (silent)' if silent else 'Done'
            if not (self.cancel.is_set() or ev.is_set()):   # QC: only adds information, never changes Done or the file
                try:
                    res, qpath = quality_check(mp4, preset, opts['vertical'], used_mode, opts['sound'])
                    if res:
                        row += f" · QC {res['status']}"
                        if res['status'] != qc.OK:
                            warns.append(f"{os.path.basename(mp4)}: QC {res['status']} - {'; '.join(res['reasons'])}" + (f' (report: {qpath})' if qpath else ''))
                except Exception:
                    log.error('qc hook failed for %s\n%s', mp4, traceback.format_exc())
            done.append(mp4); self.q.put(('row', f, row, dur))
        except rc.Cancelled:
            log.info('cancelled %s', f); self.q.put(('row', f, 'Cancelled', None))
        except Exception as e:
            tb = traceback.format_exc()
            log.error('failed %s\n%s', f, tb)
            flog = write_failure_log(f, mp4, preset, opts, e, tb, log_dir=folder if os.path.isdir(folder) else None)
            first = (str(e).strip().splitlines() or [type(e).__name__])[0].replace('Page.evaluate: ', '')
            failed.append(f'{os.path.basename(f)}: {first}' + (f'\nLog: {flog}' if flog else '')); self.q.put(('row', f, 'Error', None))
        finally:
            if created:   # a folder this item made and left empty (cancelled before anything was written) is removed again
                try: os.rmdir(folder)   # only succeeds when empty
                except OSError: pass

    def poll(self):
        try:
            while True:
                m = self.q.get_nowait()
                if m[0] == 'p':
                    _, k, n, f, stage, a, b, eta = m
                    frac = {'load': 0.0, 'audio': 0.02, 'frames': 0.03 + 0.92 * a / max(b, 1), 'encode': 0.96, 'done': 1.0}[stage]   # order: load, audio, frames, encode
                    pct = 100 * (k + frac) / n
                    self.bar['value'] = pct
                    self.pct.config(text=f'{pct:.0f}%' + (f'  ·  {fmt(eta)} left' if eta and stage == 'frames' else ''))
                    label = {'load': 'Loading scene', 'frames': f'Frames {a}/{b}', 'audio': 'Rendering sound', 'encode': 'Encoding MP4', 'done': 'Checking output'}[stage]
                    self.set_row(f, label); self.status.config(text=f'[{k + 1}/{n}] {os.path.basename(f)}: {label}')
                    if stage == 'load': self._update_narr_btn()   # the selected row may have just started rendering
                elif m[0] == 'row':
                    self.set_row(m[1], m[2], m[3]); self._update_narr_btn()
                elif m[0] == 'update':
                    self.show_update(m[1], m[2])
                elif m[0] == 'update_none':
                    self.update_none(m[1], m[2])
                elif m[0] == 'update_p':
                    _, got, total = m
                    self.status.config(text=f'Downloading update: {got / 1e6:.1f}' + (f' of {total / 1e6:.1f} MB' if total else ' MB'))
                    if total: self.bar['value'] = 100 * got / total
                elif m[0] == 'update_ready':
                    self.status.config(text='Starting the installer...'); self.run_installer(m[1]); return   # the window is gone
                elif m[0] == 'update_fail':
                    self.update_failed(m[1])
                elif m[0] == 'end':
                    _, done, failed, warns, cancelled = m
                    self.go.config(state='normal'); self.stop.config(state='disabled'); self.stop_cur.config(state='disabled')
                    txt = ('Cancelled. ' if cancelled else '') + f'{len(done)} done' + (f', {len(failed)} failed' if failed else '')
                    self.status.config(text=txt); self.bar['value'] = 100 if done and not failed and not cancelled else self.bar['value']
                    if not cancelled and not failed: self.pct.config(text='100%')
                    if failed:
                        messagebox.showerror('Render failed', '\n\n'.join(failed) + f'\n\nDetails in {LOG}')
                    if warns:
                        messagebox.showwarning('Check the output', '\n\n'.join(warns))
                    if done and not cancelled:
                        try:
                            import winsound; winsound.MessageBeep()
                        except Exception: pass
                        if self.open_when_done.get(): self.open_out()
        except queue.Empty:
            pass
        self._poll_id = self.after(150, self.poll)

class NarrationDialog:
    """Small dialog for one queue row: narration WAV, words.json, style, captions; OK / Remove narration / Cancel.
    Plain ttk widgets, so it follows the app's sv-ttk dark/light theme; the title bar follows it too.
    ok() / remove() / cancel() are the button handlers (tests call them directly)."""
    def __init__(self, app, path):
        self.app, self.path = app, path
        cur = app.narr.get(path) or {}
        base = os.path.splitext(path)[0]
        guess = lambda ext: base + ext if os.path.isfile(base + ext) else ''
        self.wav = tk.StringVar(value=cur.get('wav') or guess('.wav'))
        w = cur.get('words'); self.words = tk.StringVar(value=w if isinstance(w, str) else guess('.words.json'))
        self.style = tk.StringVar(value=cur.get('style', 'pop'))
        self.captions = tk.BooleanVar(value=cur.get('captions', True))
        t = self.top = tk.Toplevel(app); t.title(f'Narration - {os.path.basename(path)}'); t.transient(app); t.resizable(False, False)
        f = ttk.Frame(t, padding=14); f.pack(fill='both', expand=True)
        for r, (label, var, pick) in enumerate((('Voice (WAV)', self.wav, self.pick_wav), ('Words (words.json)', self.words, self.pick_words))):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky='w', pady=4)
            ttk.Entry(f, textvariable=var, width=46).grid(row=r, column=1, sticky='we', padx=8, pady=4)
            ttk.Button(f, text='Browse...', command=pick).grid(row=r, column=2, pady=4)
        ttk.Label(f, text='Caption style').grid(row=2, column=0, sticky='w', pady=4)
        ttk.Combobox(f, textvariable=self.style, values=list(narrate_module().STYLES), state='readonly', width=12).grid(row=2, column=1, sticky='w', padx=8, pady=4)
        ttk.Checkbutton(f, text='Word captions (2D scenes; WebGL scenes get the voice only)', variable=self.captions).grid(row=3, column=1, columnspan=2, sticky='w', padx=8, pady=4)
        bt = ttk.Frame(f); bt.grid(row=4, column=0, columnspan=3, sticky='e', pady=(10, 0))
        ttk.Button(bt, text='OK', style='Accent.TButton', command=self.ok).pack(side='left')
        ttk.Button(bt, text='Remove narration', command=self.remove, state='normal' if path in app.narr else 'disabled').pack(side='left', padx=6)
        ttk.Button(bt, text='Cancel', command=self.cancel).pack(side='left')
        t.protocol('WM_DELETE_WINDOW', self.cancel); t.bind('<Escape>', lambda e: self.cancel())
        dark_titlebar(t, app.theme == 'dark')
        try: t.grab_set()
        except tk.TclError: pass   # the main window may be hidden (tests)
    def pick_wav(self):
        p = filedialog.askopenfilename(parent=self.top, filetypes=[('WAV', '*.wav'), ('Audio', '*.wav *.mp3 *.m4a *.flac *.ogg')])
        if p: self.wav.set(p)
    def pick_words(self):
        p = filedialog.askopenfilename(parent=self.top, filetypes=[('Word timings', '*.json')])
        if p: self.words.set(p)
    def ok(self):
        err = self.app.set_narration(self.path, self.wav.get().strip(), self.words.get().strip(), style=self.style.get(), captions=self.captions.get())
        if err:
            messagebox.showerror('Narration', err, parent=self.top); return err   # stays open to fix it
        self.app.status.config(text=f'{os.path.basename(self.path)}: narration set ({self.style.get()})'); self.close(); return None
    def remove(self):
        self.app.clear_narration(self.path); self.app.status.config(text=f'{os.path.basename(self.path)}: narration removed'); self.close()
    def cancel(self): self.close()
    def close(self):
        try: self.top.grab_release()
        except tk.TclError: pass
        self.top.destroy(); self.app._update_narr_btn()

def cli(argv):
    """Shadow.exe --render scene.html out.mp4 [--vertical] [--preset draft|good|best] [--silent] [--fast]
    Renders without the window (for scripts and for testing the built exe). Progress and errors go to shadow.log.
    Exit code 0 = done and the output size checked, 1 = failed."""
    import argparse
    ap = argparse.ArgumentParser(prog='Shadow'); ap.add_argument('--render', nargs=2, metavar=('SCENE', 'OUT'), required=True)
    ap.add_argument('--vertical', action='store_true'); ap.add_argument('--silent', action='store_true')
    ap.add_argument('--preset', default='best', choices=['draft', 'good', 'best'])
    ap.add_argument('--fast', action='store_true', help='Fast capture (JPEG frames, about 2x faster)')
    a = ap.parse_args(argv)
    preset = next(v for k, v in rc.PRESETS.items() if k.lower().startswith(a.preset))
    scene, out = (os.path.abspath(x) for x in a.render)
    log.info('cli render %s -> %s preset=%s vertical=%s', scene, out, a.preset, a.vertical)
    try:
        used = {}
        rc.render(scene, out, preset, vertical=a.vertical, sound='none' if a.silent else 'generated', info=used,
                  capture='jpeg' if a.fast else 'png')
        pr = probe(out); exp = expected_size(preset, a.vertical)
        log.info('cli done %s %s sound=%s expected=%s', out, pr, used.get('sound'), exp)
        try:   # QC is information only: the exit code below does not depend on it
            res, path = quality_check(out, preset, a.vertical, used, used.get('sound'))
            log.info('cli qc %s %s', res['status'] if res else 'not run', path or '')
        except Exception:
            log.error('cli qc hook failed\n%s', traceback.format_exc())
        return 0 if pr and (pr[0], pr[1]) == exp else 1
    except Exception:
        log.error('cli failed\n%s', traceback.format_exc()); return 1

def main():
    dpi_aware(); setup_log()
    if '--render' in sys.argv:
        sys.exit(cli(sys.argv[1:]))
    try:
        App().mainloop()
    except Exception:
        log.critical('crash\n%s', traceback.format_exc()); raise

if __name__ == '__main__':
    main()
