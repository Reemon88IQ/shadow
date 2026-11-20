"""Scene files -> names, output folders and paths; the Format / Sound / Folder choices. Pure helpers: no GUI, no rendering.
Split out of app.py (3.16). app.py imports every name back, so app.scene_folder_name(...) etc. keep working."""
import glob, os, re
import render_core as rc
import blender_core as bc

SCENE_EXTS = ('.html', '.htm') + bc.EXTS   # HTML (three.js) scenes and Blender .blend scenes

SONG_EXTS = ('.wav', '.mp3', '.m4a', '.flac', '.ogg', '.aac')   # Auto: <scene>.<ext> next to the .html, first one found wins

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

FORMAT_AUTO = 'Auto (by file name)'   # per file: a name starting with "short_" -> 9:16, every other file -> 16:9

FORMAT_CHOICES = list(rc.FORMATS) + [FORMAT_AUTO]

SHORT_PREFIX = 'short_'

SHORT_SUFFIXES = tuple(sep + w for sep in ('-', '_', ' ') for w in ('short', 'shorts'))   # rain-short.html, rain_shorts.html ... -> 9:16 too

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
