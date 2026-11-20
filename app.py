import datetime, glob, importlib, json, logging, os, queue, re, shutil, subprocess, sys, threading, time, traceback, uuid, webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_core as rc
import blender_core as bc
import frame_check
import qc
import subtitles
import updater
import assistant
import youtube   # noqa: F401  (YouTubeDialog imports it again; listed here so PyInstaller bundles it)

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
SCENE_EXTS = ('.html', '.htm') + bc.EXTS   # HTML (three.js) scenes and Blender .blend scenes
BLENDER_ENGINES = list(bc.ENGINES.values())   # GUI labels: Scene setting / Eevee / Cycles (GPU) / Workbench (fast)
SOUNDS = ['Generated (built into the scene)', 'None (silent)', 'External file...', 'Auto (file next to scene)']
SONG_EXTS = ('.wav', '.mp3', '.m4a', '.flac', '.ogg', '.aac')   # Auto: <scene>.<ext> next to the .html, first one found wins
OUTDATED = 'This scene is outdated and does not support 9:16'
def scene_for_mp4(mp4, scenes):
    """The scene (of `scenes`, e.g. the queue) that renders this video, or None: <name>.mp4 / <name>_shorts.mp4 (also with a
    _2 / _3 suffix) belongs to the scene whose output name is <name> (short_x.html / x-short.html make x_shorts.mp4).
    A 9:16 file prefers the Short-named scene, a 16:9 file the other one. Pure."""
    raw = os.path.splitext(os.path.basename(mp4))[0]
    hits, short = [], False
    for stem in (raw, re.sub(r'_\d+$', '', raw)):   # exact name first (a scene may be called ocean_2), then without _2/_3
        short = stem.lower().endswith('_shorts')
        base = (stem[:-7] if short else stem).lower()
        hits = [f for f in scenes if strip_short_marker(scene_folder_name(f)).lower() == base]
        if hits: break
    if not hits: return None
    pref = [f for f in hits if (strip_short_marker(scene_folder_name(f)) != scene_folder_name(f)) == short]
    return (pref or hits)[0]

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

SKIPPED_SCENE_CHECK = 'Skipped (scene check)'   # Render: the scene misses window.__ready / seek / DURATION / canvas#c and the user chose No
FORMAT_AUTO = 'Auto (by file name)'   # per file: a name starting with "short_" -> 9:16, every other file -> 16:9
FORMAT_CHOICES = list(rc.FORMATS) + [FORMAT_AUTO]
# Capture: how each frame leaves the browser (render_core.render(capture=...)). PNG = lossless; JPEG = about 2x faster render,
# final video very slightly softer (measured SSIM 0.986 -> 0.983 on a 1080p scene) and ~1.7x larger file.
CAPTURES = {'Best quality (PNG)': 'png', 'Fast (JPEG, ~2x faster)': 'jpeg'}
SHORT_PREFIX = 'short_'
SHORT_SUFFIXES = tuple(sep + w for sep in ('-', '_', ' ') for w in ('short', 'shorts'))   # rain-short.html, rain_shorts.html ... -> 9:16 too

log = logging.getLogger('shadow')
def close_log():
    """Close and drop the log file handlers (tests: so their temp folder with shadow.log can be deleted on Windows)."""
    for h in list(log.handlers):
        try: h.close()
        except Exception: pass
        log.removeHandler(h)
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

def blender_engine(label):
    """Blender combobox label -> blender_core engine key ('scene' | 'eevee' | 'cycles' | 'workbench')."""
    return next((k for k, v in bc.ENGINES.items() if v == label), 'scene')

def scene_info(path):
    """What the scene source supports: 9:16 needs it to read the h URL param (get('h')). Sound is checked at render time.
    A .blend always 'supports' 9:16: it uses its 9:16 camera, or the 16:9 camera centre-cropped (warned at render time)."""
    if bc.is_blend(path): return {'vertical': True, 'blender': True}
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

def strip_short_marker(name):
    """'short_ocean' / 'ocean-short' / 'ocean_short' / 'ocean short' / '...shorts' -> 'ocean' (the Format Auto markers, any case);
    other names unchanged. Never returns an empty name."""
    low = name.lower()
    if low.startswith(SHORT_PREFIX): out = name[len(SHORT_PREFIX):]
    else:
        suf = next((s for s in sorted(SHORT_SUFFIXES, key=len, reverse=True) if low.endswith(s)), None)
        out = name[:-len(suf)] if suf else name
    out = out.strip(' ._-')
    return out or name

_META_FOLDER = re.compile(r"""<meta\s+name\s*=\s*["']shadow-folder["']\s+content\s*=\s*["']([^"'<>]*)["']""", re.I)

def scene_out_folder(scene_path):
    """The channel folder a scene asks for with <meta name="shadow-folder" content="Music"> (also "Music/2026"): the
    relative sub-path of Save to its folder goes in, '' when none or for a .blend. Windows-safe parts only; '..', drive
    letters and absolute paths are ignored."""
    if not scene_path.lower().endswith(('.html', '.htm')): return ''
    try:
        with open(scene_path, encoding='utf-8', errors='replace') as fh: head = fh.read(20000)
    except OSError: return ''
    m = _META_FOLDER.search(head)
    return safe_rel_folder(m.group(1)) if m else ''

_META_LUFS = re.compile(r"""<meta\s+name\s*=\s*["']shadow-lufs["']\s+content\s*=\s*["']\s*(-?\d+(?:\.\d+)?)\s*(?:lufs)?\s*["']""", re.I)

def target_lufs(scene_path, sound='generated', audio_file=None, narrated=False):
    """Loudness target of one render (render_core.render(lufs=...)): <meta name="shadow-lufs" content="-14"> in the scene
    wins (inside rc.LUFS_RANGE); else a song = rc.MUSIC_LUFS (-14, YouTube's playback level): a song file as the sound
    (External file, or Sound Auto finding <scene>.mp3 next to it) without narration, or a scene whose shadow-folder is
    Music; everything else (own generated sound, narration) rc.DEFAULT_LUFS (-15). Pure apart from reading the scene."""
    if scene_path.lower().endswith(('.html', '.htm')):
        try:
            with open(scene_path, encoding='utf-8', errors='replace') as fh: m = _META_LUFS.search(fh.read(20000))
        except OSError: m = None
        if m and rc.LUFS_RANGE[0] <= float(m.group(1)) <= rc.LUFS_RANGE[1]: return float(m.group(1))
    song = not narrated and (sound == 'file' and audio_file or sound == 'auto' and find_scene_audio(scene_path))
    if song or re.split(r'[\\/]', scene_out_folder(scene_path))[0].lower() == 'music': return rc.MUSIC_LUFS
    return rc.DEFAULT_LUFS

def safe_rel_folder(text):
    """'Music' / 'Music/2026' -> a Windows-safe relative path ('' if nothing is left): no '..', no : ? * etc., no drive,
    reserved device names prefixed with _."""
    parts = []
    for p in re.split(r'[\\/]+', text or ''):
        p = re.sub(r'[<>:"|?*\x00-\x1f]', '', p).strip(' .')
        if not p or p == '..': continue
        if p.split('.')[0].upper() in _WIN_RESERVED: p = '_' + p
        parts.append(p)
    return os.path.join(*parts) if parts else ''

# "Folder" option: which main folder under Save to a video goes into
FOLDER_AUTO = 'Auto (Music / Video)'
FOLDER_NONE = 'None (straight in Save to)'
DEFAULT_FOLDERS = ['Music', 'Video']

def category_folder(scene_path, folder_choice, sound='generated', audio_file=None, narrated=False):
    """The main folder (relative to Save to) for one scene. folder_choice: FOLDER_AUTO | FOLDER_NONE | a folder name
    ('Music', 'Video', one the user added) | None (= only the scene's own <meta name="shadow-folder">, the old behaviour).
    Auto: the scene's meta wins; else a song file as its sound (Sound Auto finds <scene>.mp3 next to it, or External file)
    and no narration -> 'Music'; everything else (own sound, narration, silent) -> 'Video'. Pure apart from reading the scene."""
    if folder_choice is None: return scene_out_folder(scene_path)
    if folder_choice == FOLDER_NONE: return ''
    if folder_choice == FOLDER_AUTO:
        meta = scene_out_folder(scene_path)
        if meta: return meta
        song = not narrated and (sound == 'file' and audio_file or sound == 'auto' and find_scene_audio(scene_path))
        return 'Music' if song else 'Video'
    return safe_rel_folder(folder_choice)

def output_place(scene_path, vertical, main=None):
    """(folder relative to Save to, file stem) of a scene's video: <main>\\<name>\\<name>.mp4. main: the main folder from
    category_folder() (None = the scene's own meta folder). A 9:16 render of a scene named like a Short (short_ocean.html)
    drops the marker, so the Short lands next to the 16:9 video: ocean\\ocean_shorts.mp4."""
    base = scene_folder_name(scene_path)
    if vertical: base = strip_short_marker(base)
    main = scene_out_folder(scene_path) if main is None else main
    return os.path.join(main, base), base + ('_shorts' if vertical else '')

def sound_mode(label):
    """Sound combobox label -> 'generated' | 'none' | 'file' | 'auto'."""
    return 'file' if label.startswith('External') else 'none' if label.startswith('None') else 'auto' if label.startswith('Auto') else 'generated'

def find_scene_audio(scene_path):
    """Auto mode: the audio file with the scene's base name next to it (.wav, .mp3, .m4a, .flac, .ogg, .aac, in that
    order), or None. <scene>.wav together with <scene>.words.json is a narration voice, not a song, so it is skipped."""
    base = os.path.splitext(scene_path)[0]
    voice = os.path.isfile(base + '.words.json') and not bc.is_blend(scene_path)   # a .blend has no captions: its <scene>.wav is just its sound
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

# ---------------------------------------------------------------- folders, up-to-date check, time estimate, notification
SKIPPED_UP_TO_DATE = 'Skipped (up to date)'

def collect_scenes(paths, recursive=False):
    """.html/.htm and .blend scenes from files and folders (a folder: its scenes, sorted; recursive=True also subfolders).
    Shadow's hidden .shadow_narr_* temp scenes and other dot-files are skipped. Returns (scenes, problems).
    Used by "Add folder...", dropping a folder on the table, and tools/mcp_server.py."""
    if isinstance(paths, str): paths = [paths]
    out, problems = [], []
    for p in paths or []:
        p = os.path.normpath(os.path.abspath(os.path.expanduser(p)))
        if os.path.isdir(p):
            pat = os.path.join(p, '**', '*') if recursive else os.path.join(p, '*')
            found = sorted(f for f in glob.glob(pat, recursive=recursive)
                           if f.lower().endswith(SCENE_EXTS) and os.path.isfile(f) and not os.path.basename(f).startswith('.'))
            if not found: problems.append(f'no .html or .blend scenes in folder: {p}')
            out += found
        elif os.path.isfile(p) and p.lower().endswith(SCENE_EXTS):
            out.append(p)
        else:
            problems.append(f'not a scene file or folder: {p}')
    seen, uniq = set(), []
    for f in out:
        k = os.path.normcase(f)
        if k not in seen: seen.add(k); uniq.append(f)
    return uniq, problems

def output_mp4(out, scene_path, vertical, main=None):
    """The first-choice output of a scene: <out>\\[main folder]\\<scene name>\\<scene>.mp4 (or <name>_shorts.mp4 for 9:16), see output_place."""
    sub, stem = output_place(scene_path, vertical, main)
    return os.path.join(out, sub, stem + '.mp4')

def up_to_date(scene_path, out, vertical, deps=(), expect=None, main=None):
    """The existing output mp4 if this scene does not need rendering again, else None: <out>\\<scene>\\<scene>.mp4 (or
    _shorts.mp4) exists, is not empty and is newer than the scene file and every file in deps (the song / narration voice
    / words.json it uses). expect=(w, h): the mp4 must also have that size (a Draft render does not count for Best);
    when the size cannot be read (no ffprobe) the age check alone decides. Good and Best have the same size, so switching
    between them is not detected: untick "Skip already rendered" to force a new render."""
    first = output_mp4(out, scene_path, vertical, main); folder = os.path.dirname(first); stem = os.path.splitext(os.path.basename(first))[0]
    pat = re.compile(re.escape(stem) + r'(_\d+)?\.mp4$', re.I)   # <scene>.mp4, <scene>_2.mp4 ... (a re-render after a change is _2)
    try:
        cands = [os.path.join(folder, n) for n in os.listdir(folder) if pat.match(n)] if os.path.isdir(folder) else []
        if not cands: return None
        mp4 = max(cands, key=os.path.getmtime)   # the newest render decides
        if os.path.getsize(mp4) == 0: return None
        t = os.path.getmtime(mp4)
        for d in (scene_path, *deps):
            if d and os.path.isfile(d) and os.path.getmtime(d) > t: return None
    except OSError:
        return None
    if expect:
        pr = probe(mp4)
        if pr is not None and (pr[0], pr[1]) != tuple(expect): return None
    return mp4

def render_deps(scene_path, opts, narr=None):
    """Files besides the scene whose change means a new render: the audio file it will use and the narration files."""
    deps = []
    mode = opts.get('sound')
    if mode == 'auto' and not narr: deps.append(find_scene_audio(scene_path))
    elif mode == 'file' and not narr: deps.append(opts.get('audio_file'))
    if narr:
        deps.append(narr.get('wav'))
        if isinstance(narr.get('words'), str): deps.append(narr['words'])
    return [d for d in deps if d]

# Speed before the first frame is measured: 1080p Best on the owner's PC (RTX 5070 Ti), seconds per frame for the capture
# mode; other sizes scale with the pixel count. Plus a fixed part per scene (Chrome start, sound, encode tail, QC).
SEC_PER_FRAME = {'png': 0.025, 'jpeg': 0.019}   # 3.15 parallel capture, measured end to end at 1080p (was 0.087 / 0.036)
ITEM_OVERHEAD_S = 15
_DUR_RE = re.compile(r'(?<![\w.])(?:window\.)?DURATION\s*=\s*(\d+(?:\.\d+)?)\s*(?:[;,)\n]|$)')

def media_seconds(path):
    """Length of an audio file in seconds (wav via the wave module, others via ffprobe), or None."""
    try:
        if path.lower().endswith('.wav'):
            import wave
            with wave.open(path) as wf: return wf.getnframes() / float(wf.getframerate())
    except Exception: pass
    ff = rc.find_ffmpeg()
    exe = shutil.which('ffprobe') or (os.path.join(os.path.dirname(ff), 'ffprobe' + ('.exe' if os.name == 'nt' else '')) if ff else None)
    if not exe or not os.path.exists(exe): return None
    try:
        r = subprocess.run([exe, '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                           capture_output=True, text=True, timeout=15, creationflags=NOWIN)
        return float(r.stdout.strip())
    except Exception: return None

def scene_duration_guess(scene_path, audio_file=None, narr=None):
    """The scene's length in seconds WITHOUT opening it in Chrome, for the time estimate: a literal `DURATION = 12.5` in the
    HTML, else the narration (words.json "duration" / last word, or the voice WAV), else the song it will use. None when it
    cannot be known (a .blend, or DURATION computed in code without an audio file)."""
    if bc.is_blend(scene_path): return None
    try:
        with open(scene_path, encoding='utf-8', errors='replace') as fh: m = _DUR_RE.search(fh.read())
        if m and float(m.group(1)) > 0: return float(m.group(1))
    except OSError: pass
    if narr:
        try:
            wj = narr.get('words')
            if isinstance(wj, str):
                with open(wj, encoding='utf-8') as fh: wj = json.load(fh)
            if isinstance(wj, dict) and wj.get('duration'): return float(wj['duration'])
        except Exception: pass
        if narr.get('wav'):
            s = media_seconds(narr['wav'])
            if s: return s
    if audio_file and os.path.isfile(audio_file):
        return media_seconds(audio_file)
    return None

def estimate_seconds(scene_seconds, preset, capture='png', sec_per_frame=None, fps=30):
    """Render time of one scene: frames x seconds per frame (measured in this batch, else SEC_PER_FRAME scaled from 1080p
    by the pixel count) + ITEM_OVERHEAD_S. None when the length is unknown."""
    if not scene_seconds: return None
    if sec_per_frame is None:
        w = preset.get('w', 1920) if preset else 1920
        sec_per_frame = SEC_PER_FRAME.get(capture, SEC_PER_FRAME['png']) * (w / 1920.0) ** 2
    return round(scene_seconds * fps) * sec_per_frame + ITEM_OVERHEAD_S

def span_text(seconds):
    """'under 1 min' / 'about 12 min' / 'about 1 h 05 min'."""
    m = int(round(seconds / 60.0))
    if seconds < 60: return 'under 1 min'
    return f'about {m // 60} h {m % 60:02d} min' if m >= 60 else f'about {m} min'

def eta_text(seconds, now=None, unknown=0):
    """'about 12 min · done ~15:40' (+ how many scenes could not be estimated). now: a datetime (tests), default now."""
    if seconds is None or seconds <= 0:
        return f'{unknown} scene(s) not estimated yet' if unknown else ''
    end = (now or datetime.datetime.now()) + datetime.timedelta(seconds=seconds)
    return f'{span_text(seconds)} · done ~{end:%H:%M}' + (f' (+ {unknown} not estimated)' if unknown else '')

def _ps_quote(s):
    return "'" + str(s).replace("'", "''") + "'"

def toast_script(title, text, folder=None, show=True):
    """PowerShell (Windows PowerShell 5.1, built into Windows 10/11) that shows a Windows notification through WinRT, with
    no extra package. Clicking it opens `folder` (a file: link; protocol activation). show=False builds everything but does
    not show it (tests). The app id is Windows PowerShell's own, so no shortcut/registration is needed."""
    from xml.sax.saxutils import escape, quoteattr
    launch = ''
    if folder:
        launch = f' activationType="protocol" launch={quoteattr("file:///" + os.path.abspath(folder).replace(os.sep, "/"))}'
    xml = (f'<toast{launch}><visual><binding template="ToastGeneric"><text>{escape(title)}</text><text>{escape(text)}</text>'
           '</binding></visual></toast>')
    return '\n'.join([
        "$ErrorActionPreference = 'Stop'",
        '[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]',
        '[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]',
        '$x = New-Object Windows.Data.Xml.Dom.XmlDocument',
        f'$x.LoadXml({_ps_quote(xml)})',
        '$t = [Windows.UI.Notifications.ToastNotification]::new($x)',
        "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'",
        '$n = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app)',
        '$n.Show($t)' if show else "Write-Output 'toast ready'"])

def notify(title, text, folder=None, show=True, wait=False):
    """Show a Windows notification (fire and forget, hidden PowerShell, CREATE_NO_WINDOW). Never raises; returns the
    Popen (or the CompletedProcess when wait=True), None when it cannot run (not Windows / no powershell.exe)."""
    if os.name != 'nt': return None
    try:
        import base64
        exe = shutil.which('powershell') or os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
        if not os.path.exists(exe): return None
        enc = base64.b64encode(toast_script(title, text, folder, show).encode('utf-16-le')).decode('ascii')
        cmd = [exe, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-EncodedCommand', enc]
        if wait: return subprocess.run(cmd, capture_output=True, text=True, timeout=60, creationflags=NOWIN)
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NOWIN)
    except Exception:
        log.exception('notification'); return None

def write_srt(mp4, words):
    """<mp4 name>.srt next to the video from the narration's word timings (subtitles.srt). Returns the path or None
    (never raises: a missing subtitle file must not fail the render)."""
    try:
        path = os.path.splitext(mp4)[0] + '.srt'
        with open(path, 'w', encoding='utf-8') as fh: fh.write(subtitles.srt(subtitles.words_list(words)))
        log.info('subtitles %s', path); return path
    except Exception:
        log.error('srt failed for %s\n%s', mp4, traceback.format_exc()); return None

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
    opts = dict(opts); bl = {k: opts.pop(k) for k in ('engine', 'device', 'allow_scripts') if k in opts}   # Blender-only settings
    if opts.get('lufs') is None:   # loudness per channel: songs -14, the rest -15, or the scene's own shadow-lufs
        opts['lufs'] = target_lufs(scene, opts.get('sound', 'generated'), opts.get('audio_file'), narrated=bool(narr))
    if bc.is_blend(scene):   # Blender scene: same contract as rc.render (folders, audio rules, QC all the same)
        if narr: raise ValueError('narration with word captions works for HTML scenes only; for a .blend put the voice in its sound')
        return bc.render(scene, mp4, preset, progress=progress, cancel=cancel, info=info, **opts, **bl)
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

def render_scene(f, preset, out, opts, narr=None, cancel=None, progress=lambda *_: None, used=None, embedded=False,
                 fmt_name=None, warns=None):
    """Render ONE queue item exactly as the GUI queue does (App._run_one and tools/mcp_server.py both call this):
    <out>\\<scene name>\\<scene>.mp4 (or _shorts.mp4), never overwriting; Format Auto and Sound Auto per file; narration
    forces generated sound; ffprobe size/audio check; QC report; thumbnail; failure log in the scene folder; an empty folder
    left by a cancelled item is removed. Never raises (cancel / errors come back in the result).
    f: the ORIGINAL scene; opts: dict(vertical, format, sound, audio_file, capture) like App.opts; used: set of mp4 paths
    already given out in this batch; embedded: the scene already contains narration; warns: list the warnings are added to.
    opts['skip_done'] = True: a scene whose <scene>.mp4 is up to date (up_to_date()) is not rendered again ('skipped').
    A narrated render also gets <video>.srt (from the words.json used) next to the mp4.
    Returns dict(status='done'|'cancelled'|'error'|'skipped', row (the Status column text), mp4, dur, error (one line + log
    path), log, qc (qc result or None), qc_report, thumb, srt, sound (mode actually used), vertical, warns)."""
    warns = [] if warns is None else warns; used = set() if used is None else used
    opts = dict(opts); used_mode = {}; skip_done = bool(opts.pop('skip_done', False))
    fmt_name = fmt_name or opts.get('format') or '16:9 (YouTube)'
    if opts.get('format') == FORMAT_AUTO:   # Format = Auto (by file name): 9:16 for short_*.html, 16:9 for the rest
        opts['vertical'] = format_vertical(f, FORMAT_AUTO)
        fmt_name = '9:16 (Shorts)' if opts['vertical'] else '16:9 (YouTube)'
    opts.pop('format', None)   # render_item / render_core do not take it
    opts['vertical'] = bool(opts.get('vertical'))
    main = category_folder(f, opts.pop('folder', None), opts.get('sound', 'generated'), opts.get('audio_file'), narrated=bool(narr or embedded))
    sub, name = output_place(f, opts['vertical'], main); folder = os.path.join(out, sub)
    if skip_done:   # already rendered and nothing it uses changed since: keep that file, render nothing
        old = up_to_date(f, out, opts['vertical'], render_deps(f, opts, narr), expect=expected_size(preset, opts['vertical']), main=main)
        if old:
            log.info('skipped %s: %s is up to date', f, old)
            return dict(status='skipped', row=SKIPPED_UP_TO_DATE, mp4=old, dur=None, error=None, log=None, qc=None, qc_report=None,
                        thumb=None, srt=None, sound=None, vertical=opts['vertical'], warns=warns)
    mp4 = unique_path(os.path.join(folder, name + '.mp4'), used)   # never overwrite: not a file on disk, not one from this batch
    used.add(mp4)
    res = dict(status='error', row='Error', mp4=mp4, dur=None, error=None, log=None, qc=None, qc_report=None, thumb=None,
               srt=None, sound=None, vertical=opts['vertical'], warns=warns)
    created = False
    if narr and opts['sound'] not in ('generated', 'auto'):   # the voice lives in the page's own sound: External file / None would drop it
        warns.append(f'{os.path.basename(f)}: narration needs generated sound, used Generated for this file.')
    if narr:
        opts['sound'] = 'generated'; opts['audio_file'] = None
    elif opts['sound'] == 'auto':   # per scene: <scene>.mp3/.wav/... next to it, else the scene's own sound (warned)
        ra = resolve_audio(f, 'auto', narrated=embedded)
        opts['sound'], opts['audio_file'] = ra['sound'], ra['audio_file']
        if ra['warn']: warns.append(ra['warn'])
        log.info('auto audio for %s: %s', f, ra['audio_file'] or ra['label'])
    stopped = lambda: cancel is not None and cancel.is_set()
    try:
        created = not os.path.isdir(folder)
        os.makedirs(folder, exist_ok=True)
        log.info('render %s -> %s%s', f, mp4, ' (with narration)' if narr else '')
        render_item(f, mp4, preset, opts, narr=narr, cancel=cancel, progress=progress, info=used_mode, warns=warns)
        warns.extend(f'{os.path.basename(f)}: {w}' for w in used_mode.get('warnings', []))   # Blender: missing files, no 9:16 camera...
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
        if not stopped():   # QC: only adds information, never changes Done or the file
            try:
                qres, qpath = quality_check(mp4, preset, opts['vertical'], used_mode, opts['sound'])
                res.update(qc=qres, qc_report=qpath)
                if qres:
                    row += f" · QC {qres['status']}"
                    if qres['status'] != qc.OK:
                        warns.append(f"{os.path.basename(mp4)}: QC {qres['status']} - {'; '.join(qres['reasons'])}" + (f' (report: {qpath})' if qpath else ''))
            except Exception:
                log.error('qc hook failed for %s\n%s', mp4, traceback.format_exc())
            if used_mode.get('thumbnail_t') is not None:   # the scene asked for a thumbnail (window.THUMBNAIL_T)
                th = save_thumbnail(mp4, used_mode['thumbnail_t'], opts['vertical'])
                if th: row += ' · thumb'; res['thumb'] = th
            if narr and narr.get('words'):   # subtitles for YouTube from the same word timings the captions use
                res['srt'] = write_srt(mp4, narr['words'])
        res.update(status='done', row=row, dur=dur, sound=opts['sound'])
    except rc.Cancelled:
        log.info('cancelled %s', f); res.update(status='cancelled', row='Cancelled')
    except Exception as e:
        tb = traceback.format_exc()
        log.error('failed %s\n%s', f, tb)
        flog = write_failure_log(f, mp4, preset, opts, e, tb, log_dir=folder if os.path.isdir(folder) else None)
        first = (str(e).strip().splitlines() or [type(e).__name__])[0].replace('Page.evaluate: ', '')
        res.update(status='error', row='Error', log=flog, error=f'{os.path.basename(f)}: {first}' + (f'\nLog: {flog}' if flog else ''))
    finally:
        if created:   # a folder this item made and left empty (cancelled before anything was written) is removed again
            try: os.rmdir(folder)   # only succeeds when empty
            except OSError: pass
    return res

def narration_problem(scene, wav, words, style='pop'):
    """Why this narration cannot be attached to scene (an error string), or None. The checks App.set_narration uses
    (wav and words.json readable and well-formed, style, the scene not narrated already, a Shadow scene with SOUND/seek)."""
    if bc.is_blend(scene): return 'narration with word captions works for HTML scenes only (a .blend uses <scene>.wav as its sound)'
    try:
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
        with open(scene, encoding='utf-8') as fh: html = fh.read()
        if mod.is_narrated(html): return f'{os.path.basename(scene)} already has narration built in'
        if 'SOUND' not in html or 'seek' not in html: return f'{os.path.basename(scene)} is not a Shadow scene with a SOUND kit'
        return None
    except Exception as e:
        log.exception('narration_problem'); return f'{type(e).__name__}: {e}'

def save_thumbnail(mp4, t, vertical):
    """<name>_thumb.jpg next to the mp4: the frame at t seconds, 1280x720 (720x1280 for 9:16), as YouTube wants.
    Only for scenes that set window.THUMBNAIL_T. Never raises (a failed thumbnail must not fail the render); returns the path or None."""
    try:
        out = unique_path(os.path.splitext(mp4)[0] + '_thumb.jpg'); size = '720:1280' if vertical else '1280:720'
        r = subprocess.run([rc.find_ffmpeg(), '-v', 'error', '-y', '-ss', f'{t:.3f}', '-i', mp4, '-frames:v', '1', '-vf', f'scale={size}', '-q:v', '2', out],
                           capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if r.returncode == 0 and os.path.exists(out): log.info('thumbnail %s at %.2f s', out, t); return out
        log.error('thumbnail failed for %s: %s', mp4, r.stderr.decode('utf-8', 'replace')[-300:])
    except Exception:
        log.error('thumbnail failed for %s\n%s', mp4, traceback.format_exc())
    return None

def quality_check(mp4, preset, vertical, info, sound):
    """Post-render QC (qc.run_qc + qc.write_report) for a finished mp4. Returns (result, report_path), or (None, None) if
    the QC code itself broke - that is only logged. Never raises, never touches the mp4.
    info: render_core's info dict (duration = what was rendered); sound: the mode actually used ('none' = silent by design)."""
    try:
        w, h = expected_size(preset, vertical)
        res = qc.run_qc(mp4, dict(width=w, height=h, duration=(info or {}).get('duration'), silent=(sound == 'none'),
                                      lufs=(info or {}).get('lufs')))
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
        self.project_path = None   # the .shadow file opened / saved last (Project > Save project)
        self.narr = {}   # path -> narration settings for that file (see narrate_for_render); set by the UI later
        self.out_mp4 = {}   # path -> the mp4s this session rendered (or found up to date) for that row: the YouTube dialog uploads them
        self.embedded = set()   # files whose HTML already carries narration (shown as [voice embedded], never narrated again)
        self.theme_choice = cfg.get('theme') if cfg.get('theme') in ('dark', 'light') else None   # None = follow Windows
        self.theme = self.theme_choice or system_theme()
        self.out_dir = tk.StringVar(value=cfg.get('out', os.path.join(os.path.expanduser('~'), 'Videos')))
        q = cfg.get('preset'); self.preset = tk.StringVar(value=q if q in rc.PRESETS else 'Best (1080p, 64 samples)')
        self.open_when_done = tk.BooleanVar(value=cfg.get('open', True))
        self.skip_done = tk.BooleanVar(value=cfg.get('skip_done', True))   # don't render a scene again whose mp4 is up to date
        self.notify = tk.BooleanVar(value=cfg.get('notify', True))         # Windows notification when the queue ends
        self._dur_cache = {}   # path -> (key, seconds or None): scene lengths for the time estimate
        self._spf = {}         # measured seconds per frame in this batch: {'html': s, 'blend': s}
        self._run_t0 = {}      # path -> time its render started (the Time column shows how long it took)
        self._cur_left = None  # (path, seconds left) of the file rendering now
        self.check_thread = None; self.check_results = []; self.check_kw = {}   # Check frames (check_kw: tests pass Chrome options)
        f = cfg.get('format'); self.fmt = tk.StringVar(value=f if f in FORMAT_CHOICES else '16:9 (YouTube)')
        c = cfg.get('capture'); self.capture = tk.StringVar(value=c if c in CAPTURES else next(iter(CAPTURES)))
        self.folders = [safe_rel_folder(x) for x in cfg.get('folders', DEFAULT_FOLDERS) if safe_rel_folder(x)] or list(DEFAULT_FOLDERS)
        fc = cfg.get('folder', FOLDER_AUTO); self.folder_choice = tk.StringVar(value=fc if fc in self.folder_values() else FOLDER_AUTO)
        e = cfg.get('blender_engine'); self.bl_engine = tk.StringVar(value=e if e in BLENDER_ENGINES else BLENDER_ENGINES[0])
        s = cfg.get('sound'); self.snd = tk.StringVar(value=s if s in SOUNDS else SOUNDS[0]); self.snd_file = tk.StringVar(value=cfg.get('sound_file', ''))
        # Assistant panel (assistant.py: Claude Code CLI headless, the owner's subscription). ai_kw: tests pass a fake CLI
        self.ai = None; self.ai_kw = {}; self.ai_dir_choice = cfg.get('assistant_dir', ''); self.ai_open = bool(cfg.get('assistant_open', False))
        self.ai_pending = None; self.ai_t0 = None; self.ai_last = None
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
        self.ai_btn = ttk.Button(top, text='Assistant', command=self.toggle_assistant); self.ai_btn.pack(side='right', padx=(0, 8))
        self.proj_btn = ttk.Button(top, text='Project', command=self.project_menu); self.proj_btn.pack(side='right', padx=(0, 8))
        self._top = top
        self._theme_text()

        # compact setup status bar
        env = ttk.Frame(self); env.pack(fill='x', padx=P)
        self.env_lbls = {}
        for k in ('Chrome', 'ffmpeg', 'playwright', 'Blender'):
            dot = ttk.Label(env, text='●'); dot.pack(side='left'); txt = ttk.Label(env, text=k); txt.pack(side='left', padx=(3, 14))
            self.env_lbls[k] = (dot, txt)
        ttk.Button(env, text='Install missing', command=self.install_missing).pack(side='right')
        self.selftest_btn = ttk.Button(env, text='Self-test', command=self.run_selftest); self.selftest_btn.pack(side='right')
        ttk.Button(env, text='Re-check', command=self.check).pack(side='right', padx=6)

        # drop area + table
        card = ttk.LabelFrame(self, text='Scenes', padding=10); card.pack(fill='both', expand=True, padx=P, pady=10)
        dz = ttk.Frame(card, style='Card.TFrame', padding=14, cursor='hand2'); dz.pack(fill='x')
        self.drop = ttk.Label(dz, anchor='center', cursor='hand2', font='SunValleyBodyLargeFont',
                              text=('⤓   Drop .html or .blend scenes here, or click to browse' if HAS_DND else 'Click to add .html or .blend scenes'))
        self.drop.pack(fill='x')
        for w in (dz, self.drop): w.bind('<Button-1>', lambda e: self.add())
        self.dz = dz
        tf = ttk.Frame(card); tf.pack(fill='both', expand=True, pady=(8, 8))
        # values: name, status, dur, audio, fmt, time (new columns go last so older code/tests keep their indices)
        # time: the estimate before the render (~3 min), the time left while it renders, how long it took when finished
        self.tree = ttk.Treeview(tf, columns=('name', 'status', 'dur', 'audio', 'fmt', 'time'), displaycolumns=('name', 'status', 'dur', 'time', 'fmt', 'audio'),
                                 show='headings', height=6, selectmode='extended')
        k = self.winfo_fpixels('1i') / 96
        for c, t, w, st in (('name', 'Name', 300, True), ('status', 'Status', 200, True), ('dur', 'Duration', 80, False),
                            ('audio', 'Audio', 170, True), ('fmt', 'Format', 70, False), ('time', 'Time', 90, False)):
            self.tree.heading(c, text=t, anchor='w'); self.tree.column(c, width=round(w * k), minwidth=round(60 * k), stretch=st, anchor='w')
        sb = ttk.Scrollbar(tf, orient='vertical', command=self.tree.yview); self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True); sb.pack(side='right', fill='y')
        self.tree.bind('<Double-1>', lambda e: self.preview()); self.tree.bind('<Delete>', lambda e: self.remove())
        self.tree.bind('<<TreeviewSelect>>', lambda e: self._update_narr_btn())
        if HAS_DND:
            for w in (self.dz, self.drop, self.tree):
                w.drop_target_register(DND_FILES); w.dnd_bind('<<Drop>>', self.on_drop)
        bt = ttk.Frame(card); bt.pack(fill='x')
        btns = {}
        for i, (t, c) in enumerate((('Add...', self.add), ('Add folder...', self.add_folder), ('Remove', self.remove), ('Clear', self.clear),
                                    ('Preview', self.preview), ('Check frames', self.check_frames), ('Narration...', self.open_narration_dialog))):
            b = btns[t] = ttk.Button(bt, text=t, command=c); b.grid(row=0, column=i, sticky='we', padx=(0 if i == 0 else 6, 0))
            bt.columnconfigure(i, weight=1, uniform='b')
        self.check_btn = btns['Check frames']
        self.narr_btn = btns['Narration...']; self.narr_btn.config(state='disabled')   # enabled when one row is selected that can take narration

        # options
        op = ttk.LabelFrame(self, text='Options', padding=10); op.pack(fill='x', padx=P)
        rows = (('Quality', self.preset, list(rc.PRESETS)), ('Format', self.fmt, FORMAT_CHOICES), ('Sound', self.snd, SOUNDS),
                ('Capture', self.capture, list(CAPTURES)), ('Blender', self.bl_engine, BLENDER_ENGINES))
        for r, (lbl, var, vals) in enumerate(rows):
            ttk.Label(op, text=lbl).grid(row=r, column=0, sticky='w', pady=3)
            ttk.Combobox(op, textvariable=var, values=vals, state='readonly', width=32).grid(row=r, column=1, sticky='w', padx=10, pady=3)
        self.snd.trace_add('write', self.on_sound)
        self.fmt.trace_add('write', lambda *_: self.refresh_audio())   # the Format column follows the Format setting
        ttk.Label(op, textvariable=self.snd_file, foreground='#888').grid(row=2, column=2, columnspan=2, sticky='w')
        ttk.Label(op, text='.blend scenes only (GPU: OptiX/CUDA when found)', foreground='#888').grid(row=4, column=2, columnspan=2, sticky='w')
        ttk.Label(op, text='Save to').grid(row=5, column=0, sticky='w', pady=3)
        ttk.Entry(op, textvariable=self.out_dir).grid(row=5, column=1, columnspan=2, sticky='we', padx=10, pady=3)
        ttk.Button(op, text='Browse...', command=self.pick_out).grid(row=5, column=3, sticky='e')
        ttk.Label(op, text='Folder').grid(row=6, column=0, sticky='w', pady=3)
        self.folder_box = ttk.Combobox(op, textvariable=self.folder_choice, values=self.folder_values(), state='readonly', width=32)
        self.folder_box.grid(row=6, column=1, sticky='w', padx=10, pady=3)
        ttk.Label(op, text='Auto: songs -> Music, the rest -> Video (Short + video share one folder)', foreground='#888').grid(row=6, column=2, sticky='w')
        ttk.Button(op, text='New folder...', command=self.add_main_folder).grid(row=6, column=3, sticky='e')
        op.columnconfigure(2, weight=1)

        # progress
        pr = ttk.Frame(self); pr.pack(fill='x', padx=P, pady=(12, 0))
        self.bar = ttk.Progressbar(pr, maximum=100); self.bar.pack(side='left', fill='x', expand=True)
        self.pct = ttk.Label(pr, text='0%', width=16, anchor='e'); self.pct.pack(side='right', padx=(10, 0))
        sl = ttk.Frame(self); sl.pack(fill='x', padx=P, pady=(4, 0))
        self.eta_lbl = ttk.Label(sl, text='', foreground='#888'); self.eta_lbl.pack(side='right')   # whole queue: about 12 min · done ~15:40
        self.status = ttk.Label(sl, text='Ready', foreground='#888'); self.status.pack(side='left')

        # actions
        act = ttk.Frame(self); act.pack(fill='x', padx=P, pady=P)
        self.go = ttk.Button(act, text='Render', style='Accent.TButton', command=self.start)
        self.stop_cur = ttk.Button(act, text='Cancel current', command=self.do_cancel_current, state='disabled')
        self.stop = ttk.Button(act, text='Stop queue', command=self.do_cancel, state='disabled')
        self.yt_btn = ttk.Button(act, text='YouTube...', command=self.open_youtube_dialog)
        for i, w in enumerate((self.go, self.stop_cur, self.stop, ttk.Button(act, text='Open folder', command=self.open_out), ttk.Button(act, text='Open log', command=self.open_log), self.yt_btn)):
            w.grid(row=0, column=i, sticky='we', padx=(0 if i == 0 else 6, 0)); act.columnconfigure(i, weight=1, uniform='a')
        chk = ttk.Frame(act); chk.grid(row=1, column=0, columnspan=6, sticky='w', pady=(8, 0))
        for t, v in (('Open folder when done', self.open_when_done), ('Skip scenes already rendered (up to date)', self.skip_done),
                     ('Notify me when the queue ends', self.notify)):
            ttk.Checkbutton(chk, text=t, variable=v).pack(side='left', padx=(0, 18))
        self.skip_done.trace_add('write', lambda *_: self.update_eta())
        for var in (self.preset, self.capture):
            var.trace_add('write', lambda *_: self.update_eta())
        self.build_assistant()

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
        self.apply_theme(); self._theme_text(); self.ai_colors(); self.save()

    # ------------------------------------------------------------------ setup
    def check(self):
        e = rc.check_env(); self.env = e; self.blender = bc.find_blender()
        for k, ok in (('Chrome', e['chrome']), ('ffmpeg', e['ffmpeg']), ('playwright', e['playwright']), ('Blender', bool(self.blender))):
            dot, txt = self.env_lbls[k]
            dot.config(foreground='#3fb950' if ok else '#f85149' if k != 'Blender' else '#888'); txt.config(text=k if ok else f'{k} (missing)' if k != 'Blender' else 'Blender (not found, only for .blend)')
        if e['chrome'] and e.get('browser') != 'Chrome': self.env_lbls['Chrome'][1].config(text=f"{e['browser']} (Chrome not found)")
    def run_selftest(self, wait=False, **kw):
        """Self-test button: selftest() in a thread, the result in a message box (tests: wait=True runs here, returns it)."""
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo('Self-test', 'A render is running. Run the self-test when the queue is done.'); return None
        self.selftest_btn.config(state='disabled'); self.status.config(text='Self-test: rendering a 2-second test scene...')
        out = {}
        def work():
            out['res'] = selftest(**kw)
            self.q.put(('selftest', out['res']))
        if wait: work(); self.on_selftest(out['res']); return out['res']
        threading.Thread(target=work, daemon=True).start()
    def on_selftest(self, res):
        if not self.selftest_btn.winfo_exists(): return
        self.selftest_btn.config(state='normal')
        self.status.config(text=('Self-test passed' if res['ok'] else 'Self-test FAILED') + f" ({res.get('seconds', 0)} s, {res.get('browser') or 'no browser'})")
        if getattr(self, 'selftest_box_off', False): return   # tests
        (messagebox.showinfo if res['ok'] else messagebox.showwarning)('Self-test', selftest_text(res))
    def install_missing(self):
        cmds = []
        frozen = getattr(sys, 'frozen', False)  # in Shadow.exe the python packages are bundled; sys.executable is the exe
        if not frozen and (not self.env['playwright'] or not HAS_DND or not sv_ttk): cmds.append(f'"{sys.executable}" -m pip install playwright tkinterdnd2 sv-ttk darkdetect')
        if not self.env['ffmpeg']: cmds.append('winget install -e --id Gyan.FFmpeg')
        if not self.env['chrome']: cmds.append('winget install -e --id Google.Chrome')
        if not getattr(self, 'blender', None) and any(bc.is_blend(f) for f in self.files): cmds.append('winget install -e --id BlenderFoundation.Blender')
        if not cmds:
            messagebox.showinfo('Setup', 'Everything is installed.'); return
        subprocess.Popen('cmd /c "' + ' && '.join(cmds) + ' && echo. && echo Done. Close this window and restart the app. && pause"', creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
        messagebox.showinfo('Setup', 'Installing in a new window. When it finishes, close this app and open it again (ffmpeg needs a restart to be found).')

    # ------------------------------------------------------------------ file list
    def on_drop(self, ev):
        for f in self.tk.splitlist(ev.data):
            if os.path.isdir(f): self.add_folder(f)   # a dropped folder adds its scenes, like "Add folder..."
            elif f.lower().endswith(PROJECT_EXT): self.open_project(f)
            else: self._add(f)

    # ------------------------------------------------------------------ project files (.shadow)
    def project_menu(self):
        m = tk.Menu(self, tearoff=0)
        m.add_command(label='Open project...', command=self.open_project)
        m.add_command(label='Save project', command=lambda: self.save_project(self.project_path))
        m.add_command(label='Save project as...', command=self.save_project)
        try: m.tk_popup(self.proj_btn.winfo_rootx(), self.proj_btn.winfo_rooty() + self.proj_btn.winfo_height())
        finally: m.grab_release()
    def project_data(self):
        """The queue (scenes in order + each row's narration) and the render options, as saved in a .shadow file."""
        return dict(files=list(self.files), narration={f: dict(v) for f, v in self.narr.items() if f in self.files},
                    options=dict(quality=self.preset.get(), format=self.fmt.get(), sound=self.snd.get(), sound_file=self.snd_file.get(),
                                 capture=self.capture.get(), blender=self.bl_engine.get(), folder=self.folder_choice.get(),
                                 save_to=self.out_dir.get(), skip_done=bool(self.skip_done.get())))
    def save_project(self, path=None):
        """Save project (as): the queue and its options in a .shadow file. Returns the path, or None (cancelled / failed)."""
        if not path:
            path = filedialog.asksaveasfilename(defaultextension=PROJECT_EXT, filetypes=[('Shadow project', '*' + PROJECT_EXT)],
                                                initialdir=os.path.dirname(self.files[0]) if self.files else None, initialfile='project' + PROJECT_EXT)
            if not path: return None
        try: write_project(path, self.project_data())
        except OSError as e: messagebox.showerror('Project', f'Could not save {path}:\n{e}'); return None
        self.project_path = path; self.title(f'Shadow - {os.path.splitext(os.path.basename(path))[0]}')
        self.status.config(text=f'Project saved: {path}'); log.info('project saved %s (%d scenes)', path, len(self.files))
        return path
    def open_project(self, path=None, ask=messagebox.askyesnocancel):
        """Open project: scenes (in order) + their narration + the options. With scenes already in the list it asks:
        Yes = replace the list, No = add to it, Cancel. Missing files are listed, the rest is opened. Returns the scenes added."""
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo('Project', 'A render is running. Open the project when the queue is done.'); return []
        if not path:
            path = filedialog.askopenfilename(filetypes=[('Shadow project', '*' + PROJECT_EXT)])
            if not path: return []
        try: data = read_project(path)
        except (OSError, ValueError) as e: messagebox.showerror('Project', f'Could not open {path}:\n{e}'); return []
        if self.files:
            a = ask('Project', f'Replace the {len(self.files)} scene(s) in the list with the project?\n\nYes = replace   No = add to the list   Cancel = do nothing')
            if a is None: return []
            if a: self.clear()
        o = data['options']
        for var, key, ok in ((self.preset, 'quality', rc.PRESETS), (self.fmt, 'format', FORMAT_CHOICES), (self.snd, 'sound', SOUNDS),
                             (self.capture, 'capture', CAPTURES), (self.bl_engine, 'blender', BLENDER_ENGINES)):
            if o.get(key) in ok: var.set(o[key])
        if o.get('sound_file') is not None: self.snd_file.set(o['sound_file'])
        if o.get('save_to'): self.out_dir.set(o['save_to'])
        if 'skip_done' in o: self.skip_done.set(bool(o['skip_done']))
        fc = o.get('folder')
        if fc and fc not in self.folder_values() and safe_rel_folder(fc): self.folders.append(safe_rel_folder(fc)); self.folder_box.config(values=self.folder_values())
        if fc in self.folder_values(): self.folder_choice.set(fc)
        added, missing, narr_err = [], [f for f in data['files'] if not os.path.isfile(f)], []
        for f in data['files']:
            if f in missing or os.path.normpath(f) in self.files: continue
            self._add(f); added.append(os.path.normpath(f))
            n = data['narration'].get(f)
            if n and os.path.normpath(f) not in self.embedded:
                err = self.set_narration(f, n.get('wav', ''), n.get('words', ''), style=n.get('style', 'pop'), yh=n.get('yh', 0.88),
                                         gain=n.get('gain', 1.0), captions=n.get('captions', True))
                if err: narr_err.append(f'{os.path.basename(f)}: {err}')
        self.refresh_audio(); self.project_path = path; self.title(f'Shadow - {os.path.splitext(os.path.basename(path))[0]}')
        self.status.config(text=f'Project opened: {len(added)} scene(s)' + (f', {len(missing)} missing' if missing else ''))
        log.info('project opened %s: %d added, %d missing', path, len(added), len(missing))
        if missing or narr_err:
            messagebox.showwarning('Project', ('Not found (not added):\n' + '\n'.join(missing) if missing else '')
                                   + ('\n\n' if missing and narr_err else '') + ('Narration not attached:\n' + '\n'.join(narr_err) if narr_err else ''))
        return added
    def add_folder(self, d=None, ask=None):
        """Add every .html / .blend scene in a folder (sorted; hidden .shadow_narr_* temp scenes skipped). When its subfolders
        hold scenes too, ask whether to add those as well. Returns the scenes added (tests)."""
        if d is None:
            d = filedialog.askdirectory(initialdir=os.path.dirname(self.files[-1]) if self.files else None)
            if not d: return []
        top, _ = collect_scenes([d]); deep, _ = collect_scenes([d], recursive=True)
        found = top
        if len(deep) > len(top):
            more = len(deep) - len(top)
            if (ask or messagebox.askyesno)('Add folder', f'{os.path.basename(os.path.normpath(d)) or d}: {len(top)} scene(s) in the folder, '
                                            f'{more} more in its subfolders.\n\nAdd the subfolders too?'):
                found = deep
        before = set(self.files)
        for f in found: self._add(f)
        added = [f for f in self.files if f not in before]
        self.status.config(text=f'{len(added)} scene(s) added from {d}' if added else f'No new .html or .blend scenes in {d}')
        return added
    def add(self):
        for f in filedialog.askopenfilenames(filetypes=[('Scene', '*.html *.htm *.blend'), ('HTML scene', '*.html *.htm'), ('Blender scene', '*.blend')]): self._add(f)
    def _add(self, f):
        f = os.path.normpath(f)
        if f.lower().endswith(SCENE_EXTS) and f not in self.files:
            with self.lock:   # while a batch runs, a new file joins it and renders after the ones before it
                self.files.append(f); self.state[f] = 'queued'
            try:   # narration already built into the scene (the tool's own check): marked, never narrated twice
                if bc.is_blend(f): raise StopIteration   # binary .blend: no HTML narration
                with open(f, encoding='utf-8', errors='replace') as fh:
                    if narrate_module().is_narrated(fh.read()): self.embedded.add(f)
            except Exception: pass
            self.rows[f] = self.tree.insert('', 'end', values=(self._row_name(f), 'Queued', '—', '', '', ''))
            self._auto_narration(f)
            self._refresh_name(f)
            if sound_mode(self.snd.get()) == 'auto':
                w = self._audio(f)['warn']
                if w: self.status.config(text=w)
            self.update_eta()
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
            v = list(self.tree.item(iid, 'values')); v += [''] * (6 - len(v))
            v[0] = self._row_name(f); v[3] = self._audio(f)['label']; v[4] = format_label(format_vertical(f, self.fmt.get()))
            self.tree.item(iid, values=v)
    def refresh_audio(self):
        """Update every row's Audio and Format columns; in Auto sound mode the status line names the scenes without an audio file."""
        for f in list(self.files): self._refresh_name(f)
        self.update_eta()
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
            err = narration_problem(f, wav, words, style)
            if err: return err
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
        self.update_eta()
        if kept: self.status.config(text="The file that is rendering can't be removed. Press Cancel to stop it.")
        return kept
    def remove(self):
        self._drop([k for iid in self.tree.selection() for k, v in self.rows.items() if v == iid])
    def clear(self):
        self._drop(list(self.files))
    def preview(self):
        sel = self.tree.selection()
        f = next((k for k, v in self.rows.items() if sel and v == sel[0]), self.files[0] if self.files else None)
        if f and bc.is_blend(f) and getattr(self, 'blender', None):   # open a .blend in the Blender Shadow renders with
            subprocess.Popen([self.blender, f], creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0)); return
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
    def youtube_items(self):
        """(mp4, scene) pairs for the YouTube dialog: the videos this session made for the selected rows (all rows if none
        is selected), newest render of each output last."""
        sel = set(self.tree.selection())
        rows = [f for f in self.files if not sel or self.rows.get(f) in sel]
        return [(m, f) for f in rows for m in self.out_mp4.get(f, []) if os.path.isfile(m)]
    def open_youtube_dialog(self):
        try: return YouTubeDialog(self, self.youtube_items())
        except Exception as e:
            log.exception('youtube dialog'); messagebox.showerror('YouTube', str(e)); return None
    def folder_values(self):
        """The Folder combobox: Auto, the main folders (Music, Video, the ones added), None."""
        return [FOLDER_AUTO, *self.folders, FOLDER_NONE]
    def add_main_folder(self, name=None):
        """New folder...: add a main folder name (e.g. 'Kids') to the Folder list and select it. Saved in the settings."""
        from tkinter import simpledialog
        name = safe_rel_folder(name if name is not None else simpledialog.askstring('New folder', 'Folder name (inside Save to), e.g. Kids:', parent=self) or '')
        if not name: return
        if name.lower() not in (x.lower() for x in self.folders): self.folders.append(name)
        self.folder_box.config(values=self.folder_values()); self.folder_choice.set(next(x for x in self.folders if x.lower() == name.lower()))
        self.save()
    def open_out(self):
        d = self.out_dir.get(); os.makedirs(d, exist_ok=True); open_path(d)
    def open_log(self):
        if not os.path.exists(LOG): open(LOG, 'a').close()
        open_path(LOG)
    def save(self):
        save_cfg({'out': self.out_dir.get(), 'preset': self.preset.get(), 'open': self.open_when_done.get(), 'format': self.fmt.get(),
                  'skip_done': self.skip_done.get(), 'notify': self.notify.get(),
                  'folder': self.folder_choice.get(), 'folders': self.folders,
                  'assistant_open': self.ai_open, 'assistant_dir': self.ai_dir_choice,
                  'capture': self.capture.get(), 'blender_engine': self.bl_engine.get(), 'sound': self.snd.get(), 'sound_file': self.snd_file.get(), **({'theme': self.theme_choice} if self.theme_choice else {})})
    def on_close(self):
        self.do_cancel(); self.save()
        if self.ai: self.ai.close()   # ends the assistant's claude process (and a render it started)
        try: self.after_cancel(self._poll_id)
        except Exception: pass
        self.destroy()

    # ------------------------------------------------------------------ render
    def start(self, ask=messagebox.askyesnocancel):
        if not self.files: messagebox.showwarning('Render', 'Add at least one .html or .blend scene.'); return
        self.check()
        need = {'ffmpeg'} | ({'chrome', 'playwright'} if any(not bc.is_blend(f) for f in self.files) else set())   # a .blend-only queue needs no Chrome
        miss = [k for k, v in self.env.items() if not v and k in need]
        if any(bc.is_blend(f) for f in self.files) and not self.blender: miss.append('Blender (for the .blend scenes)')
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
        broken = {f: p for f, p in ((f, rc.contract_problems(f)) for f, _ in jobs if not bc.is_blend(f)) if p}
        if broken:   # explained before the render instead of an error (or a 3-minute wait) in the middle of the queue
            a = ask('Scene check', 'These scenes miss something Shadow needs and will probably fail:\n\n' +
                    '\n\n'.join(f'{os.path.basename(f)}:\n' + '\n'.join(f'  • {x}' for x in p) for f, p in broken.items()) +
                    '\n\nRender them anyway?\nYes = render all   No = skip these   Cancel = stop')
            if a is None: return
            if a is False:
                for f in broken: self.set_row(f, SKIPPED_SCENE_CHECK)
                jobs = [j for j in jobs if j[0] not in broken]
                if not jobs: return
        for f, _ in jobs: self.set_row(f, 'Queued', '—')
        self.refresh_audio()   # Audio column + the Auto fallback warning on the status line
        with self.lock:
            keep = {f for f, _ in jobs}
            for f in self.files: self.state[f] = 'queued' if f in keep else 'skipped'
        self.opts = dict(vertical=vertical, format=fmt_name, sound=mode, audio_file=self.snd_file.get() or None,
                         capture=CAPTURES.get(self.capture.get(), 'png'), engine=blender_engine(self.bl_engine.get()), device='auto',
                         skip_done=bool(self.skip_done.get()), folder=self.folder_choice.get())
        self._spf = {}; self._run_t0 = {}; self._cur_left = None   # time estimate: measured again in every batch
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
        done, failed, warns, used, skipped = [], [], [], set(), []
        k = 0
        while True:
            f, left, ev, narr = self.next_queued()   # live list: files added while rendering are picked up, removed ones are not
            if f is None: break
            n = k + 1 + left
            try:
                self._run_one(f, k, n, preset, out, fmt_name, done, failed, warns, used, ev, narr=narr, skipped=skipped)
            finally:
                with self.lock: self.state[f] = 'finished'
                k += 1
        for w in warns: log.warning(w)
        self.q.put(('end', done, failed, warns, self.cancel.is_set(), skipped))

    def _run_one(self, f, k, n, preset, out, fmt_name, done, failed, warns, used, ev, narr=None, skipped=None):
        """One file in the worker thread. narr: the copy of this file's narration settings from next_queued (or None).
        The work itself is render_scene() (shared with tools/mcp_server.py); this only feeds the UI queue."""
        if self.cancel.is_set() or ev.is_set():
            self.q.put(('row', f, 'Cancelled', None)); return
        def prog(stage, a, b, eta=None, k=k, f=f):
            self.q.put(('p', k, n, f, stage, a, b, eta))
        r = render_scene(f, preset, out, self.opts, narr=narr, cancel=ev, progress=prog, used=used,
                         embedded=f in getattr(self, 'embedded', ()), fmt_name=fmt_name, warns=warns)
        if r['status'] == 'done': done.append(r['mp4'])
        elif r['status'] == 'error': failed.append(r['error'])
        elif r['status'] == 'skipped' and skipped is not None: skipped.append(r['mp4'])
        store = getattr(self, 'out_mp4', None)   # the YouTube dialog's list (tests call _run_one on stand-ins without it)
        if store is not None and r['status'] in ('done', 'skipped') and r.get('mp4'):
            lst = store.setdefault(f, [])
            if r['mp4'] not in lst: lst.append(r['mp4'])
        self.q.put(('row', f, r['row'], r['dur']))

    # ------------------------------------------------------------------ time estimate (Time column + the line under the bar)
    def _scene_seconds(self, f):
        """scene_duration_guess for one row, cached until the scene / its audio / its narration changes."""
        ra = self._audio(f); narr = self.narr.get(f)
        deps = [f, ra.get('audio_file')] + ([narr.get('wav'), narr.get('words') if isinstance(narr.get('words'), str) else None] if narr else [])
        try: key = tuple((d, os.path.getmtime(d)) for d in deps if d and os.path.isfile(d))
        except OSError: key = None
        hit = self._dur_cache.get(f)
        if hit and hit[0] == key: return hit[1]
        sec = scene_duration_guess(f, ra.get('audio_file'), narr)
        self._dur_cache[f] = (key, sec); return sec
    def _row_estimate(self, f, preset, capture):
        """Seconds this row will take, or None (unknown length, or skipped because it is up to date)."""
        blend = bc.is_blend(f); spf = self._spf.get('blend' if blend else 'html')
        if blend and spf is None: return None   # a .blend's speed is only known once its first frames are rendered
        return estimate_seconds(self._scene_seconds(f), preset, capture, spf)
    def update_eta(self):
        """Time column of the queued rows + 'about 12 min · done ~15:40' for the whole queue (before and while rendering)."""
        try:
            preset = rc.PRESETS.get(self.preset.get()) or next(iter(rc.PRESETS.values()))
            capture = CAPTURES.get(self.capture.get(), 'png'); busy = self.worker is not None and self.worker.is_alive()
            total, unknown = 0.0, 0
            with self.lock: todo = [f for f in self.files if self.state.get(f) == 'queued']
            for f in todo:
                est = self._row_estimate(f, preset, capture)
                self._set_time(f, f'~{fmt(est)}' if est else '?')
                if est: total += est
                else: unknown += 1
            if busy and self._cur_left: total += self._cur_left[1]
            txt = eta_text(total, unknown=unknown) if (todo or busy) else ''
            self.eta_lbl.config(text=(('Queue: ' if busy else 'Estimate: ') + txt) if txt else '')
        except Exception:
            log.exception('update_eta')
    def _set_time(self, f, text):
        iid = self.rows.get(f)
        if iid and self.tree.exists(iid):
            v = list(self.tree.item(iid, 'values')); v += [''] * (6 - len(v))
            if v[5] != text: v[5] = text; self.tree.item(iid, values=v)
    def _eta_progress(self, f, stage, a, b, eta):
        """Feed one progress message into the estimate: measured seconds per frame and this file's time left."""
        now = time.time()
        if stage == 'load': self._run_t0[f] = now
        left = None
        if stage == 'frames' and eta is not None and b > a and a >= 3:
            self._spf['blend' if bc.is_blend(f) else 'html'] = eta / (b - a)   # = elapsed / frames done (render_core's own ETA)
        if stage == 'frames' and eta is not None: left = eta + 8   # + encode tail and QC
        elif stage in ('load', 'audio'):
            preset = rc.PRESETS.get(self.preset.get()) or {}
            left = self._row_estimate(f, preset, (getattr(self, 'opts', None) or {}).get('capture', 'png'))
        elif stage in ('encode', 'done'): left = 5
        self._cur_left = (f, left or 0)
        self._set_time(f, f'{fmt(left)} left' if left else '…')

    def poll(self):
        eta_dirty = False
        try:
            while True:
                m = self.q.get_nowait()
                if m[0] == 'p':
                    _, k, n, f, stage, a, b, eta = m
                    self._eta_progress(f, stage, a, b, eta); eta_dirty = True
                    frac = {'load': 0.0, 'audio': 0.02, 'frames': 0.03 + 0.92 * a / max(b, 1), 'encode': 0.96, 'done': 1.0}[stage]   # order: load, audio, frames, encode
                    pct = 100 * (k + frac) / n
                    self.bar['value'] = pct
                    self.pct.config(text=f'{pct:.0f}%' + (f'  ·  {fmt(eta)} left' if eta and stage == 'frames' else ''))
                    label = {'load': 'Loading scene', 'frames': f'Frames {a}/{b}', 'audio': 'Rendering sound', 'encode': 'Encoding MP4', 'done': 'Checking output'}[stage]
                    self.set_row(f, label); self.status.config(text=f'[{k + 1}/{n}] {os.path.basename(f)}: {label}')
                    if stage == 'load': self._update_narr_btn()   # the selected row may have just started rendering
                elif m[0] == 'row':
                    self.set_row(m[1], m[2], m[3]); self._update_narr_btn()
                    t0 = self._run_t0.pop(m[1], None)
                    self._set_time(m[1], f'took {fmt(time.time() - t0)}' if t0 and m[2].startswith('Done') else '')
                    if self._cur_left and self._cur_left[0] == m[1]: self._cur_left = None
                    eta_dirty = True
                elif m[0] == 'ai':
                    self.ai_event(*m[1:])
                elif m[0] == 'check':
                    self.show_check(m[1], m[2])
                elif m[0] == 'check_status':
                    self.status.config(text=m[1])
                elif m[0] == 'check_end':
                    self.check_btn.config(state='normal')
                elif m[0] == 'update':
                    self.show_update(m[1], m[2])
                elif m[0] == 'update_none':
                    self.update_none(m[1], m[2])
                elif m[0] == 'selftest':
                    self.on_selftest(m[1])
                elif m[0] == 'update_p':
                    _, got, total = m
                    self.status.config(text=f'Downloading update: {got / 1e6:.1f}' + (f' of {total / 1e6:.1f} MB' if total else ' MB'))
                    if total: self.bar['value'] = 100 * got / total
                elif m[0] == 'update_ready':
                    self.status.config(text='Starting the installer...'); self.run_installer(m[1]); return   # the window is gone
                elif m[0] == 'update_fail':
                    self.update_failed(m[1])
                elif m[0] == 'end':
                    _, done, failed, warns, cancelled, skipped = m
                    self.go.config(state='normal'); self.stop.config(state='disabled'); self.stop_cur.config(state='disabled')
                    txt = (('Cancelled. ' if cancelled else '') + f'{len(done)} done' + (f', {len(failed)} failed' if failed else '')
                           + (f', {len(skipped)} skipped (up to date)' if skipped else ''))
                    self._cur_left = None; self.update_eta(); eta_dirty = False
                    if self.notify.get() and not cancelled and (done or failed):   # Windows notification; a click opens the folder
                        folders = {os.path.dirname(p) for p in done}
                        self.last_notify = (f'Shadow: queue finished' if not failed else 'Shadow: queue finished with errors', txt,
                                            folders.pop() if len(folders) == 1 else self.out_dir.get())
                        notify(*self.last_notify)
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
        if eta_dirty: self.update_eta()
        self._poll_id = self.after(150, self.poll)

    # ------------------------------------------------------------------ Assistant panel (assistant.py, Claude Code headless)
    def build_assistant(self):
        """Collapsible chat on the right: Claude (through the installed Claude Code CLI, the owner's subscription) with Shadow's
        MCP tools. Hidden until the top-bar 'Assistant' button; the chat runs in assistant.Chat's threads (UI never waits)."""
        k = self.winfo_fpixels('1i') / 96
        p = self.ai_frame = ttk.LabelFrame(self, text='Assistant (Claude Code)', padding=8)
        hd = ttk.Frame(p); hd.pack(fill='x')
        self.ai_dir_lbl = ttk.Label(hd, text='', foreground='#888'); self.ai_dir_lbl.pack(side='left', fill='x', expand=True)
        ttk.Button(hd, text='New chat', command=self.ai_new).pack(side='right')
        ttk.Button(hd, text='Folder...', command=self.ai_pick_dir).pack(side='right', padx=(0, 6))
        tf = ttk.Frame(p); tf.pack(fill='both', expand=True, pady=(6, 6))
        self.ai_text = tk.Text(tf, wrap='word', width=48, height=18, state='disabled', relief='flat', padx=8, pady=6,
                               font='SunValleyBodyFont', cursor='arrow')
        sb = ttk.Scrollbar(tf, orient='vertical', command=self.ai_text.yview); self.ai_text.configure(yscrollcommand=sb.set)
        self.ai_text.pack(side='left', fill='both', expand=True); sb.pack(side='right', fill='y')
        self.ai_allow_bar = ttk.Frame(p)   # shown when Claude was blocked from a tool that may be allowed once
        self.ai_allow_lbl = ttk.Label(self.ai_allow_bar, text='', wraplength=round(330 * k)); self.ai_allow_lbl.pack(side='left', fill='x', expand=True)
        ttk.Button(self.ai_allow_bar, text='No', command=self.ai_deny).pack(side='right')
        ttk.Button(self.ai_allow_bar, text='Allow once', style='Accent.TButton', command=self.ai_allow).pack(side='right', padx=(0, 6))
        self.ai_in = tk.Text(p, wrap='word', height=3, width=48, relief='flat', padx=8, pady=6, font='SunValleyBodyFont', undo=True)
        self.ai_in.pack(fill='x')
        self.ai_in.bind('<Return>', self.ai_on_return)   # Enter sends, Shift+Enter = new line
        bt = ttk.Frame(p); bt.pack(fill='x', pady=(6, 0))
        self.ai_send_btn = ttk.Button(bt, text='Send', style='Accent.TButton', command=self.ai_send)
        self.ai_stop_btn = ttk.Button(bt, text='Stop', command=self.ai_stop, state='disabled')
        self.ai_send_btn.pack(side='left', fill='x', expand=True); self.ai_stop_btn.pack(side='left', fill='x', expand=True, padx=(6, 0))
        self.ai_status = ttk.Label(p, text='Ask for a render, a frame check or a QC summary. Uses your Claude subscription via Claude Code.',
                                   foreground='#888', wraplength=round(380 * k)); self.ai_status.pack(fill='x', pady=(6, 0))
        self.ai_colors(); self._ai_dir_label()
        if self.ai_open: self.ai_open = False; self.after_idle(self.toggle_assistant)

    def ai_colors(self):
        if not hasattr(self, 'ai_text'): return
        dark = self.theme == 'dark'
        bg, fg = ('#2b2b2b', '#e6e6e6') if dark else ('#ececec', '#1b1b1b')
        for w in (self.ai_text, self.ai_in):
            w.configure(background=bg, foreground=fg, insertbackground=fg, selectbackground='#2f6fb5', highlightthickness=0)
        t = self.ai_text
        t.tag_configure('you', foreground='#58a6ff' if dark else '#0b57d0', spacing1=8)
        t.tag_configure('claude', spacing1=2)
        t.tag_configure('tool', foreground='#8b949e' if dark else '#5f6368')
        t.tag_configure('err', foreground='#ff7b72' if dark else '#b3261e')

    def toggle_assistant(self):
        self.ai_open = not self.ai_open
        self.update_idletasks(); w0 = self.winfo_width()
        if self.ai_open:
            self._ai_dir_label()
            self.ai_frame.pack(side='right', fill='both', padx=(0, 14), pady=14, before=self._top)
            self.update_idletasks(); add = self.ai_frame.winfo_reqwidth() + 14
            self.geometry(f'{w0 + add}x{self.winfo_height()}'); self.ai_in.focus_set()
        else:
            add = self.ai_frame.winfo_width() + 14
            self.ai_frame.pack_forget()
            self.geometry(f'{max(w0 - add, self.winfo_reqwidth())}x{self.winfo_height()}')
        self.ai_btn.config(style='Accent.TButton' if self.ai_open else 'TButton')
        self.save()

    def ai_folder(self):
        """The assistant's working folder: the one picked with Folder..., else the first scene's folder, else Save to."""
        if self.ai_dir_choice and os.path.isdir(self.ai_dir_choice): return self.ai_dir_choice
        sel = self._selected_path() if hasattr(self, 'tree') else None
        for f in ([sel] if sel else []) + list(self.files):
            if f and os.path.isdir(os.path.dirname(f)): return os.path.dirname(f)
        return self.out_dir.get() or os.path.expanduser('~')

    def _ai_dir_label(self):
        d = self.ai.cwd if self.ai else self.ai_folder()
        how = 'chosen' if self.ai_dir_choice else ('this chat' if self.ai else 'auto: scenes\' folder')
        self.ai_dir_lbl.config(text=f'Folder ({how}): {d if len(d) < 48 else "..." + d[-45:]}')

    def ai_pick_dir(self):
        d = filedialog.askdirectory(title='Assistant folder (Claude may read files here)', initialdir=self.ai_folder())
        if not d: return
        self.ai_dir_choice = os.path.normpath(d); self.save()
        if self.ai and os.path.normcase(self.ai.cwd) != os.path.normcase(self.ai_dir_choice):
            self.ai_new(note=f'New folder: {self.ai_dir_choice} - a new conversation starts with the next message.')
        self._ai_dir_label()

    def ai_append(self, text, tag='claude'):
        t = self.ai_text; t.config(state='normal')
        at_end = t.yview()[1] > 0.98
        if tag != self.ai_last or tag in ('you', 'tool', 'err'):
            if t.index('end-1c') != '1.0': t.insert('end', '\n')
            if tag == 'you': t.insert('end', 'You: ', 'you')
            elif tag == 'claude' and self.ai_last != 'claude': t.insert('end', 'Claude: ', 'you')
        t.insert('end', text, tag); self.ai_last = tag
        t.config(state='disabled')
        if at_end: t.see('end')

    def ai_context(self):
        """The [Shadow app] block put in front of each message: the queue, Save to, settings, whether the GUI is rendering."""
        busy = self.worker is not None and self.worker.is_alive()
        lines = [f'[Shadow app] Save to: {self.out_dir.get()} | Quality: {self.preset.get()} | Format: {self.fmt.get()} | '
                 f'Sound: {self.snd.get()} | Capture: {self.capture.get()} | GUI queue rendering now: {"yes" if busy else "no"}']
        rows = self.files[:30]
        lines.append(f'Queue ({len(self.files)} scene(s))' + (':' if rows else ': empty'))
        for f in rows:
            v = self.tree.item(self.rows[f], 'values') if f in self.rows else ()
            lines.append(f'- {f} | {v[1] if len(v) > 1 else ""} | {v[4] if len(v) > 4 else ""} | audio: {v[3] if len(v) > 3 else ""}')
        if len(self.files) > 30: lines.append(f'... and {len(self.files) - 30} more')
        sel = self._selected_path()
        if sel: lines.append(f'Selected: {sel}')
        return '\n'.join(lines)

    def ai_on_return(self, e=None):
        if e is not None and getattr(e, 'state', 0) & 1: return None   # Shift+Enter: the Text widget adds a new line
        self.ai_send(); return 'break'

    def ai_send(self, text=None, show=True):
        msg = (self.ai_in.get('1.0', 'end') if text is None else text).strip()
        if not msg: return
        if self.ai and self.ai.busy: return
        if text is None: self.ai_in.delete('1.0', 'end')
        if show: self.ai_append(msg, 'you')
        if self.ai is None:
            q = self.q
            self.ai = assistant.Chat(lambda *ev: q.put(('ai',) + ev), self.ai_folder(),
                                     add_dirs=[d for d in {self.out_dir.get()} if d], **self.ai_kw)
            self._ai_dir_label()
        self.ai_allow_bar.pack_forget(); self.ai_pending = None
        self.ai_send_btn.config(state='disabled'); self.ai_stop_btn.config(state='normal'); self.ai_t0 = time.time()
        self.ai.send(self.ai_context() + '\n\n' + msg)

    def ai_stop(self):
        if not self.ai: return
        self.ai.stop()
        self.ai_append('(stopped - a render the assistant started was stopped too; the GUI queue is not affected)', 'tool')
        self._ai_idle('Stopped. The next message continues the same conversation.')

    def ai_new(self, note=None):
        if self.ai: self.ai.close()
        self.ai = None; self.ai_pending = None; self.ai_allow_bar.pack_forget(); self.ai_last = None
        t = self.ai_text; t.config(state='normal'); t.delete('1.0', 'end'); t.config(state='disabled')
        if note: self.ai_append(note, 'tool')
        self._ai_idle('New conversation.'); self._ai_dir_label()

    def ai_allow(self):
        tool = self.ai_pending
        self.ai_allow_bar.pack_forget(); self.ai_pending = None
        if not (tool and self.ai and self.ai.allow_once(tool)): return
        name = assistant.short_tool(tool)
        self.ai_append(f'(you allowed {name} once)', 'tool')
        self.ai_send(f'The owner clicked "Allow once" for {name}. It is allowed for this message only: do the step that was blocked now.', show=False)

    def ai_deny(self):
        tool = self.ai_pending; self.ai_allow_bar.pack_forget(); self.ai_pending = None
        if tool: self.ai_append(f'(not allowed: {assistant.short_tool(tool)})', 'tool')

    def _ai_idle(self, text):
        self.ai_send_btn.config(state='normal'); self.ai_stop_btn.config(state='disabled'); self.ai_status.config(text=text)

    def ai_event(self, kind, *d):
        if kind == 'text':
            self.ai_append(d[0], 'claude')
        elif kind == 'tool':
            self.ai_append('  > ' + d[0], 'tool')
        elif kind == 'status':
            self.ai_status.config(text=d[0])
        elif kind == 'denied':
            tool, summary, allowable = d
            self.ai_append(f'  blocked: {summary}', 'err')
            if allowable:
                self.ai_pending = tool
                self.ai_allow_lbl.config(text=f'Claude wants to use {summary}. Allow it once?')
                self.ai_allow_bar.pack(fill='x', pady=(0, 6), before=self.ai_in)
        elif kind == 'turn_end':
            took = f' · {time.time() - self.ai_t0:.0f} s' if self.ai_t0 else ''
            self._ai_idle(('Done' if not d[0].get('is_error') else 'Finished with an error') + took)
            if self.ai_pending: self.ai_status.config(text='Waiting for you: Allow once or No')
        elif kind == 'error':
            self.ai_append(d[0], 'err'); self._ai_idle('Error - see the message above.')
            if self.ai and not self.ai.ready_ok: self.ai = None   # not installed / not signed in: check again on the next Send
        elif kind == 'exit':
            if not (self.ai and self.ai.busy): self.ai_stop_btn.config(state='disabled'); self.ai_send_btn.config(state='normal')

    # ------------------------------------------------------------------ Check frames (frame_check.check, in a thread)
    def check_frames(self, paths=None, ask=None):
        """The 'Check frames' button: contact sheet + problems for the selected rows (frame_check, like tools/check_frames.py),
        in a background thread; each result opens a small window. HTML scenes that read get('h') are checked in 16:9 and
        9:16, others in their row's format only. While a render runs it asks first (a second Chrome/Blender slows it).
        Returns the thread, or None with the reason on the status line."""
        if paths is None:
            sel = set(self.tree.selection()); paths = [f for f in self.files if self.rows.get(f) in sel]
        if not paths:
            self.status.config(text='Select one or more scenes, then press Check frames.'); return None
        if self.check_thread and self.check_thread.is_alive():
            self.status.config(text='A frame check is already running.'); return None
        if self.worker and self.worker.is_alive():
            if not (ask or messagebox.askyesno)('Check frames', 'A render is running. Checking frames now starts a second Chrome '
                                                '(or Blender) and slows the render down.\n\nCheck anyway?'):
                return None
        jobs = []
        for f in paths:
            vertical = format_vertical(f, self.fmt.get())
            only = None if bc.is_blend(f) or scene_info(f)['vertical'] else format_label(vertical)
            jobs.append((f, only))
        engine = blender_engine(self.bl_engine.get()); kw = dict(self.check_kw)
        def run():
            for i, (f, only) in enumerate(jobs, 1):
                self.q.put(('check_status', f'Checking frames {i}/{len(jobs)}: {os.path.basename(f)}...'))
                try: res = frame_check.check(f, only=only, engine=engine, **({} if bc.is_blend(f) else kw))
                except Exception as e:
                    log.exception('check frames'); res = dict(ok=False, sheet=None, problems=[f'{type(e).__name__}: {e}'], notes=[])
                log.info('check frames %s: %s', f, 'OK' if res['ok'] else '; '.join(res['problems']))
                self.q.put(('check', f, res))
            self.q.put(('check_end',))
        self.check_btn.config(state='disabled')
        self.check_thread = threading.Thread(target=run, daemon=True); self.check_thread.start()
        return self.check_thread
    def show_check(self, f, res):
        self.check_results.append((f, res))
        name = os.path.basename(f)
        self.status.config(text=f'{name}: frames OK' if res['ok'] else f'{name}: {len(res["problems"])} problem(s) - see the check window')
        if getattr(self, 'check_window_off', False): return None   # tests
        try: return CheckWindow(self, f, res)
        except Exception: log.exception('check window'); return None

class YouTubeDialog:
    """YouTube...: upload rendered videos to a signed-in channel (youtube.py, YouTube Data API, your own OAuth client).
    Titles / descriptions / tags / Made for Kids come from metadata.txt next to the video or next to its scene ('=== SHORT ===' section
    for *_shorts.mp4). Privacy defaults to Private. Until the Google Cloud project passes YouTube's audit, YouTube keeps every
    API upload Private whatever is chosen. upload() runs in a thread; tests call add()/start() with youtube.py patched."""
    def __init__(self, app, items):
        import youtube as ytm
        self.app, self.ytm, self.items, self.cancel, self.busy, self.last = app, ytm, [], threading.Event(), False, None
        t = self.top = tk.Toplevel(app); t.title('Upload to YouTube'); t.transient(app); t.minsize(640, 360)
        f = ttk.Frame(t, padding=14); f.pack(fill='both', expand=True)
        ttk.Label(f, text='Channel').grid(row=0, column=0, sticky='w')
        self.channel = tk.StringVar(); self.ch_box = ttk.Combobox(f, textvariable=self.channel, state='readonly', width=34)
        self.ch_box.grid(row=0, column=1, sticky='w', padx=8)
        ttk.Button(f, text='Sign in...', command=self.sign_in).grid(row=0, column=2, sticky='w')
        self.tree = ttk.Treeview(f, columns=('title', 'when', 'status'), height=6)
        for c, w, txt in (('#0', 200, 'Video'), ('title', 240, 'Title (metadata.txt)'), ('when', 120, 'Publish'), ('status', 170, 'Status')):
            self.tree.heading(c, text=txt); self.tree.column(c, width=w, stretch=c != 'status')
        self.tree.grid(row=1, column=0, columnspan=4, sticky='nsew', pady=10)
        self.tree.bind('<Double-1>', lambda e: self.open_selected())
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.show_source())
        self.ask = lambda title, text: messagebox.askyesno(title, text, parent=self.top)   # tests replace it
        ttk.Label(f, text='Privacy').grid(row=2, column=0, sticky='w')
        self.privacy = tk.StringVar(value='private')
        ttk.Combobox(f, textvariable=self.privacy, values=list(ytm.PRIVACY), state='readonly', width=12).grid(row=2, column=1, sticky='w', padx=8)
        self.kids = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text='Made for kids', variable=self.kids).grid(row=2, column=2, sticky='w')
        self.ai, self.thumb, self.sched = tk.BooleanVar(value=False), tk.BooleanVar(value=True), tk.BooleanVar(value=True)
        opt = ttk.Frame(f); opt.grid(row=3, column=0, columnspan=4, sticky='w', pady=(6, 0))
        ttk.Checkbutton(opt, text='Altered or synthetic (AI) content', variable=self.ai).pack(side='left')
        ttk.Checkbutton(opt, text='Set thumbnail (*_thumb.jpg)', variable=self.thumb).pack(side='left', padx=12)
        ttk.Checkbutton(opt, text='Schedule (PUBLISH: time)', variable=self.sched).pack(side='left')
        ttk.Label(f, text='Until Google approves the app (YouTube API audit), YouTube keeps API uploads Private.',
                  foreground='#888').grid(row=4, column=0, columnspan=4, sticky='w', pady=(6, 0))
        self.bar = ttk.Progressbar(f, maximum=100); self.bar.grid(row=5, column=0, columnspan=4, sticky='we', pady=(10, 0))
        self.msg = ttk.Label(f, text='', foreground='#888'); self.msg.grid(row=6, column=0, columnspan=4, sticky='w')
        bt = ttk.Frame(f); bt.grid(row=7, column=0, columnspan=4, sticky='we', pady=(10, 0))
        ttk.Button(bt, text='Add video...', command=self.pick).pack(side='left')
        ttk.Button(bt, text='Remove', command=self.remove).pack(side='left', padx=6)
        self.studio_btn = ttk.Button(bt, text='Open in Studio', command=self.open_selected, state='disabled'); self.studio_btn.pack(side='left')
        ttk.Button(bt, text='Close', command=self.close).pack(side='right')
        self.up_btn = ttk.Button(bt, text='Upload', style='Accent.TButton', command=self.start); self.up_btn.pack(side='right', padx=6)
        f.columnconfigure(3, weight=1); f.rowconfigure(1, weight=1)
        t.protocol('WM_DELETE_WINDOW', self.close); t.bind('<Escape>', lambda e: self.close())
        dark_titlebar(t, app.theme == 'dark')
        self.refresh_channels()
        for mp4, scene in items: self.add(mp4, scene)
        if not self.items: self.msg.config(text='Render first, or "Add video..." to pick mp4 files.')

    def refresh_channels(self, select=None):
        ch = self.ytm.channels(); names = [c['title'] for c in ch]
        self.ch_box.config(values=names)
        if select: self.channel.set(select)
        elif self.channel.get() not in names: self.channel.set(names[0] if len(names) == 1 else '')
        try: self.ytm.load_client()
        except self.ytm.YouTubeError as e: self.msg.config(text=str(e))

    def add(self, mp4, scene=None):
        """One video row. Its metadata.txt is looked for next to the video, then next to its scene: the row's scene, or for
        "Add video..." the queued scene that makes this file (scene_for_mp4), so a picked mp4 still gets its metadata."""
        mp4 = os.path.normpath(mp4)   # the file picker gives C:/... - the same file must not be listed twice
        if any(os.path.normcase(i['mp4']) == os.path.normcase(mp4) for i in self.items): return
        scene = scene or scene_for_mp4(mp4, getattr(self.app, 'files', []))
        it = dict(mp4=mp4, scene=scene, cands=[os.path.dirname(scene)] if scene else [], meta=None, src=None, result=None)
        it['iid'] = self.tree.insert('', 'end', text=os.path.basename(mp4), values=('', '', 'Ready'))
        self.items.append(it); self.load_meta(it)
        meta = it['meta']
        if meta and meta.get('kids') is not None and len(self.items) == 1: self.kids.set(bool(meta['kids']))
        if meta and meta.get('ai') is not None and len(self.items) == 1: self.ai.set(bool(meta['ai']))

    def load_meta(self, it):
        """(Re)read the row's metadata.txt from disk (also right before Upload: a file written after the dialog opened counts)."""
        it['meta'], it['src'] = self.ytm.metadata_for(it['mp4'], it['cands'])
        m = it['meta'] or {}
        self.tree.set(it['iid'], 'title', m.get('title') or '(no metadata.txt: file name)'); self.tree.set(it['iid'], 'when', self.when_text(m))

    def show_source(self):
        """Status line for the selected row: which metadata.txt its upload will use."""
        sel = set(self.tree.selection()); it = next((i for i in self.items if i['iid'] in sel), None)
        if it: self.msg.config(text=f"metadata: {it['src']}" if it['src'] else 'no metadata.txt next to this video'
                               + (f" or its scene ({os.path.basename(it['scene'])})" if it['scene'] else ' (and no queued scene makes it)'))

    @staticmethod
    def when_text(meta):
        """Publish column: the PUBLISH: time in this PC's zone, '' without PUBLISH:, '(not a date)' when it has no date + time."""
        m = meta or {}
        if m.get('publish_at'):
            import datetime
            t = datetime.datetime.strptime(m['publish_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc).astimezone()
            return t.strftime('%a %d %b %H:%M')
        return '(not a date)' if m.get('publish') else ''

    def pick(self):
        for p in filedialog.askopenfilenames(parent=self.top, filetypes=[('MP4 video', '*.mp4')], initialdir=self.app.out_dir.get()): self.add(p)

    def remove(self):
        if self.busy: return
        sel = set(self.tree.selection())
        for it in [i for i in self.items if i['iid'] in sel]: self.tree.delete(it['iid']); self.items.remove(it)

    def sign_in(self):
        if self.busy: return
        self.msg.config(text='Sign in on the Google page that opened in your browser...')
        def work():
            try: ch = self.ytm.sign_in(); self.top.after(0, lambda: (self.refresh_channels(ch['title']), self.msg.config(text=f"Signed in: {ch['title']}")))
            except Exception as e: self.top.after(0, lambda e=e: self.msg.config(text=f'Sign-in failed: {e}'))
        threading.Thread(target=work, daemon=True).start()

    def start(self, wait=False):
        """Upload every video that is not uploaded yet. wait=True (tests) runs in this thread."""
        if self.busy:
            self.cancel.set(); self.msg.config(text='Stopping after the current chunk...'); return
        todo = [i for i in self.items if not i['result']]
        if not todo: self.msg.config(text='Nothing to upload.'); return
        if not self.channel.get(): self.msg.config(text='Choose a channel (or Sign in...).'); return
        for it in todo: self.load_meta(it)   # what is on disk now, not what was there when the dialog opened
        bare = [os.path.basename(i['mp4']) for i in todo if not i['meta']]
        if bare and not self.ask('YouTube', 'No metadata.txt found for:\n\n' + '\n'.join(bare) +
                                 '\n\nThey would be uploaded with the file name as title and no description, tags or schedule. Upload them anyway?'):
            self.msg.config(text='Put metadata.txt next to the video or its scene, then press Upload.'); return
        self.busy = True; self.cancel.clear(); self.up_btn.config(text='Stop'); ch = self.channel.get()
        privacy, kids, ai, thumb, sched = self.privacy.get(), self.kids.get(), self.ai.get(), self.thumb.get(), self.sched.get()
        bad_thumb = []
        def set_row(it, status): self.top.after(0, lambda: self.tree.set(it['iid'], 'status', status))
        def work():
            ok = 0
            for n, it in enumerate(todo):
                set_row(it, 'Uploading 0%')
                def prog(d, t, it=it, n=n):
                    pct = d * 100 // max(t, 1); set_row(it, f'Uploading {pct}%')
                    self.top.after(0, lambda: self.bar.config(value=(n + d / max(t, 1)) * 100 / len(todo)))
                try:
                    r = self.ytm.upload_video(it['mp4'], ch, it['meta'] or {}, privacy, progress=prog, cancel=self.cancel,
                                              overrides=dict(kids=kids, privacy=privacy, ai=ai), meta_path=it['src'],
                                              thumbnail=thumb, schedule=sched)
                    it['result'] = r; self.last = r; ok += 1
                    extra = (['scheduled'] if r.get('publish_at') else []) + (['thumbnail'] if r.get('thumb') == 'set' else [])
                    if str(r.get('thumb') or '').startswith('error'): extra.append('no thumbnail'); bad_thumb.append(r['thumb'][7:])
                    set_row(it, f"Done ({', '.join([r.get('privacy') or privacy] + extra)})")
                    log.info('youtube upload %s -> %s %s thumb=%s publish=%s', it['mp4'], r['channel'], r['url'], r.get('thumb'), r.get('publish_at'))
                except Exception as e:
                    set_row(it, 'Stopped' if self.cancel.is_set() else 'Error'); log.warning('youtube upload %s: %s', it['mp4'], e)
                    self.top.after(0, lambda e=e: self.msg.config(text=str(e)))
                    if self.cancel.is_set(): break
            def end():
                self.busy = False; self.up_btn.config(text='Upload')
                if ok:
                    self.studio_btn.config(state='normal')
                    self.msg.config(text=f'{ok} uploaded to {ch}. Double-click a row to open it in YouTube Studio.'
                                    + (f' Thumbnail not set: {bad_thumb[-1]}' if bad_thumb else ''))
            self.top.after(0, end)
        if wait: work()
        else: threading.Thread(target=work, daemon=True).start()

    def open_selected(self):
        sel = set(self.tree.selection())
        it = next((i for i in self.items if i['iid'] in sel and i['result']), None) or (self.last and {'result': self.last})
        if it: webbrowser.open(it['result']['studio'])

    def close(self):
        if self.busy and not messagebox.askyesno('YouTube', 'An upload is running. Stop it and close?', parent=self.top): return
        self.cancel.set(); self.top.destroy()

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

class CheckWindow:
    """Result of 'Check frames' for one scene: the contact sheet (scaled to fit), OK or the problems (page errors, impure
    seek(t), never ready...), notes, and Open image / Close. Follows the app's theme."""
    def __init__(self, app, path, res):
        self.app, self.path, self.res = app, path, res
        t = self.top = tk.Toplevel(app); t.title(f'Frame check - {os.path.basename(path)}'); t.transient(app)
        f = ttk.Frame(t, padding=12); f.pack(fill='both', expand=True)
        self.photo = None
        if res.get('sheet') and os.path.isfile(res['sheet']):
            try:
                import base64, io
                from PIL import Image
                with Image.open(res['sheet']) as f0: im = f0.copy()   # closed at once: an open file would stay locked on Windows
                k = app.winfo_fpixels('1i') / 96
                im.thumbnail((round(1100 * k), round(560 * k)))
                buf = io.BytesIO(); im.save(buf, 'PNG')   # Tk reads PNG itself: no PIL.ImageTk needed
                self.photo = tk.PhotoImage(master=t, data=base64.b64encode(buf.getvalue()).decode('ascii'), format='png')
                ttk.Label(f, image=self.photo).pack(anchor='w')
            except Exception:
                log.exception('check window image')
        ok = res.get('ok')
        lines = (['OK: no page errors, seek(t) is pure.' if not bc.is_blend(path) else 'OK: no problems.'] if ok
                 else ['Problems:'] + [f'  • {p}' for p in res.get('problems') or []])
        lines += [f'Note: {n}' for n in res.get('notes') or []]
        if res.get('sheet'): lines.append(f'Sheet: {res["sheet"]}')
        ttk.Label(f, text='\n'.join(lines), justify='left', wraplength=round(1100 * app.winfo_fpixels('1i') / 96),
                  foreground='#3fb950' if ok else '#f85149').pack(anchor='w', pady=(8, 0))
        bt = ttk.Frame(f); bt.pack(anchor='e', pady=(10, 0))
        ttk.Button(bt, text='Open image', command=self.open_image, state='normal' if res.get('sheet') else 'disabled').pack(side='left')
        ttk.Button(bt, text='Close', style='Accent.TButton', command=self.close).pack(side='left', padx=(6, 0))
        t.protocol('WM_DELETE_WINDOW', self.close); t.bind('<Escape>', lambda e: self.close())
        dark_titlebar(t, app.theme == 'dark')
    def open_image(self):
        if self.res.get('sheet'): open_path(self.res['sheet'])
    def close(self): self.top.destroy()

def cli_check(argv):
    """Shadow.exe --check scene.html|scene.blend [--only 16:9|9:16] [--times 3,25%,end-2] [--out sheet.jpg]
    tools/check_frames.py inside the exe: prints the sheet path and OK / PROBLEMS; exit 0 = OK, 1 = problems."""
    import argparse
    ap = argparse.ArgumentParser(prog='Shadow'); ap.add_argument('--check', required=True, metavar='SCENE')
    ap.add_argument('--only', choices=['16:9', '9:16']); ap.add_argument('--times'); ap.add_argument('--out')
    ap.add_argument('--engine', default='scene', choices=list(bc.ENGINES)); a = ap.parse_args(argv)
    res = frame_check.check(a.check, times=a.times, out=a.out, only=a.only, engine=a.engine)
    lines = [f'note: {n}' for n in res['notes']] + ([f'wrote {res["sheet"]}'] if res['sheet'] else [])
    lines.append('OK' if res['ok'] else 'PROBLEMS:\n  ' + '\n  '.join(res['problems']))
    log.info('cli check %s: %s', a.check, ' | '.join(lines))
    try: print('\n'.join(lines))   # a --noconsole exe has no stdout: the log has it too
    except Exception: pass
    return 0 if res['ok'] else 1

SELFTEST_SCENE = """<!doctype html><meta charset=utf-8><title>Shadow self-test</title><body style="margin:0;background:#000">
<canvas id=c></canvas><script>
const q = new URLSearchParams(location.search), get = k => q.get(k), W = +get('w') || 1280, H = +get('h') || Math.round(W * 9 / 16);
const c = document.getElementById('c'); c.width = W; c.height = H; const g = c.getContext('2d');
window.DURATION = 2;
window.seek = (t) => { g.fillStyle = '#0b1d33'; g.fillRect(0, 0, W, H); const x = W * (0.1 + 0.8 * t / 2);
  g.fillStyle = '#f5a623'; g.beginPath(); g.arc(x, H / 2, H / 6, 0, 7); g.fill();
  g.fillStyle = '#fff'; g.font = `${Math.round(H / 14)}px sans-serif`; g.textAlign = 'center'; g.fillText('Shadow self-test  t=' + t.toFixed(2), W / 2, H * 0.88); };
window.seek(0); window.__ready = true;
</script>"""

def selftest(progress=lambda msg: None, channel='auto', extra_args=(), keep=False):
    """Self-test: renders a built-in 2-second scene with a test tone through the real pipeline (browser -> frames -> ffmpeg ->
    audio loudness -> QC) in a temp folder. -> dict(ok, steps=[(name, ok, detail)], seconds, browser, mp4 (only with keep)).
    Never raises; the first failing step says what to fix."""
    t0 = time.time(); steps = []; tmp = tempfile_dir('shadow_selftest_'); res = dict(ok=False, steps=steps, browser=None)
    def step(name, ok, detail=''):
        steps.append((name, bool(ok), detail)); progress(f'{name}: {"OK" if ok else "FAILED"}'); return ok
    try:
        env = rc.check_env()
        ch = rc.resolve_channel(channel) if channel == 'auto' else channel; res['browser'] = rc.browser_name(ch) if ch else 'bundled Chromium'
        if not step('Browser', env['chrome'] or channel != 'auto', res['browser'] if env['chrome'] or channel != 'auto'
                    else 'neither Chrome nor Edge found: winget install -e --id Google.Chrome'): return res
        if not step('ffmpeg', env['ffmpeg'], rc.find_ffmpeg() or 'not on PATH: winget install -e --id Gyan.FFmpeg, then sign out and in'): return res
        if not step('playwright', env['playwright'], 'ok' if env['playwright'] else 'pip install playwright'): return res
        scene = os.path.join(tmp, 'selftest.html')
        with open(scene, 'w', encoding='utf-8') as f: f.write(SELFTEST_SCENE)
        tone = os.path.join(tmp, 'selftest.wav')
        r = subprocess.run([rc.find_ffmpeg(), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2:sample_rate=48000',
                            '-ac', '2', tone], capture_output=True, creationflags=NOWIN)
        if not step('Test tone (ffmpeg)', r.returncode == 0 and os.path.isfile(tone), rc.ffmpeg_message(r.stderr.decode('utf-8', 'replace').splitlines(), r.returncode) if r.returncode else 'ok'): return res
        mp4 = os.path.join(tmp, 'selftest.mp4'); info = {}; preset = rc.PRESETS['Draft (fast, 720p)']; t1 = time.time()
        try:
            rc.render(scene, mp4, preset, sound='file', audio_file=tone, capture='jpeg', info=info, channel=channel, extra_args=extra_args)
            step('Render (browser + frames + encode)', True, f'{time.time() - t1:.1f} s with {info.get("browser")}')
        except Exception as e:
            step('Render (browser + frames + encode)', False, (str(e).strip().splitlines() or [type(e).__name__])[0]); return res
        pr = probe(mp4)
        if not step('Output file', pr and (pr[0], pr[1]) == (1280, 720) and pr[3], f'{pr[0]}x{pr[1]}, {pr[2]:.1f} s, audio {"yes" if pr and pr[3] else "no"}' if pr else 'ffprobe could not read it'): return res
        qres, _ = quality_check(mp4, preset, False, info, 'file')
        step('Quality check', qres and qres['status'] == qc.OK, (qres or {}).get('status', 'not run') + (': ' + '; '.join(qres['reasons']) if qres and qres.get('reasons') else ''))
        res['ok'] = all(s[1] for s in steps)
        if keep: res['mp4'] = mp4
        return res
    except Exception as e:
        log.exception('self-test'); step('Self-test', False, str(e)); return res
    finally:
        res['seconds'] = round(time.time() - t0, 1)
        log.info('self-test: %s in %s s, %s', 'OK' if res['ok'] else 'FAILED', res['seconds'], '; '.join(f'{n}={"ok" if o else d}' for n, o, d in steps))
        if not keep: shutil.rmtree(tmp, ignore_errors=True)

def tempfile_dir(prefix):
    import tempfile
    return tempfile.mkdtemp(prefix=prefix)

def selftest_text(res):
    lines = [('OK   ' if ok else 'FAIL ') + f'{n}: {d}' for n, ok, d in res['steps']]
    return ('Self-test passed' if res['ok'] else 'Self-test FAILED') + f' ({res.get("seconds", 0)} s)\n\n' + '\n'.join(lines)

def clean_old_updates(dest=None):
    """Delete update setups in %TEMP%\\Shadow-update that are this version or older (installed already). -> bytes freed."""
    dest = dest or os.path.join(os.environ.get('TEMP') or os.path.expanduser('~'), 'Shadow-update'); freed = 0
    for p in glob.glob(os.path.join(dest, 'Shadow-*-Setup.exe')):
        m = re.match(r'Shadow-([\d.]+)-Setup\.exe$', os.path.basename(p), re.I)
        try:
            if m and updater.parse_version(m.group(1)) <= updater.parse_version(rc.VERSION):
                n = os.path.getsize(p); os.remove(p); freed += n
        except (OSError, ValueError): pass
    return freed

def startup_cleanup():
    """At start (in a thread): leftover render temp folders of crashed renders + installed update setups."""
    try:
        n, freed = rc.clean_stale_temp(); freed += clean_old_updates()
        if n or freed: log.info('cleanup: %d stale temp folder(s), %.1f MB freed', n, freed / 1048576)
    except Exception: log.exception('startup cleanup')

def cli_selftest(argv):
    """Shadow.exe --selftest: render the built-in test scene with a tone and check it; exit 0 = OK."""
    res = selftest()
    try: print(selftest_text(res))
    except Exception: pass
    return 0 if res['ok'] else 1

def cli(argv):
    """Shadow.exe --render scene.html out.mp4 [--vertical] [--preset draft|good|best] [--silent] [--fast]
    Renders without the window (for scripts and for testing the built exe). Progress and errors go to shadow.log.
    Exit code 0 = done and the output size checked, 1 = failed."""
    import argparse
    ap = argparse.ArgumentParser(prog='Shadow'); ap.add_argument('--render', nargs=2, metavar=('SCENE', 'OUT'), required=True)
    ap.add_argument('--vertical', action='store_true'); ap.add_argument('--silent', action='store_true')
    ap.add_argument('--preset', default='best', choices=['draft', 'good', 'best', '1440p', '4k'])
    ap.add_argument('--fast', action='store_true', help='Fast capture (JPEG frames, about 2x faster)')
    ap.add_argument('--engine', default='scene', choices=list(bc.ENGINES), help='.blend only: render engine (default: the scene setting)')
    a = ap.parse_args(argv)
    preset = next(v for k, v in rc.PRESETS.items() if k.lower().startswith(a.preset))
    scene, out = (os.path.abspath(x) for x in a.render)
    log.info('cli render %s -> %s preset=%s vertical=%s', scene, out, a.preset, a.vertical)
    try:
        used = {}
        opts = dict(vertical=a.vertical, sound='none' if a.silent else 'generated', capture='jpeg' if a.fast else 'png')
        opts['lufs'] = target_lufs(scene, opts['sound'])   # the scene's shadow-lufs / shadow-folder Music, else -15
        if bc.is_blend(scene): bc.render(scene, out, preset, info=used, engine=a.engine, **opts)
        else: rc.render(scene, out, preset, info=used, **opts)
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
    if '--check' in sys.argv:
        sys.exit(cli_check(sys.argv[1:]))
    if '--selftest' in sys.argv:
        sys.exit(cli_selftest(sys.argv[1:]))
    threading.Thread(target=startup_cleanup, daemon=True, name='startup-cleanup').start()
    try:
        a = App()
        proj = next((x for x in sys.argv[1:] if x.lower().endswith(PROJECT_EXT) and os.path.isfile(x)), None)
        if proj: a.after(300, lambda: a.open_project(proj))   # Shadow.exe my.shadow (or a double-clicked .shadow file)
        a.mainloop()
    except Exception:
        log.critical('crash\n%s', traceback.format_exc()); raise

if __name__ == '__main__':
    main()
