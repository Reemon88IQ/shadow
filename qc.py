"""Post-render quality check. Standard library only.
parse_* / classify are pure (no I/O). run_qc runs ffprobe + ffmpeg on a finished mp4; write_report writes <name>.qc.txt.
Neither run_qc nor write_report ever raises, and neither changes or deletes the mp4.

measured (from ffprobe + one ffmpeg pass with ebur128 / blackdetect / freezedetect):
    {'width': int, 'height': int, 'duration': float, 'has_audio': bool,
     'lufs': float|None, 'tp': float|None, 'black': [(start, end, dur)], 'freeze': [(start, end, dur)]}
expect:
    {'width': int, 'height': int, 'duration': float, 'silent': bool}   silent = no audio by design (skips audio checks)
"""
import datetime, json, os, re, shutil, subprocess

# ---------------------------------------------------------------- thresholds
DURATION_WARN = 0.3        # s   |actual - expected| above this -> Warn
DURATION_FAIL = 1.0        # s   above this -> Fail
LUFS_MIN, LUFS_MAX = -17.0, -13.0   # integrated loudness window (Shadow normalises to -15)
LUFS_TOL = 2.0   # with expect['lufs'] (the render's target: -14 songs, -15 the rest) the window is target +-2

def lufs_window(expect):
    t = (expect or {}).get('lufs')
    return (t - LUFS_TOL, t + LUFS_TOL) if isinstance(t, (int, float)) else (LUFS_MIN, LUFS_MAX)
TRUE_PEAK_MAX = -1.0       # dBTP
BLACK_MAX = 0.5            # s   a black stretch longer than this -> Warn
BLACK_IGNORE_START = 0.5   # s   black in the first half second (fade-in) does not count
FREEZE_MAX = 5.0           # s   frozen picture longer than this -> Warn

OK, WARN, FAIL, UNKNOWN = 'OK', 'Warn', 'Fail', 'Unknown'
TIMEOUT = 120             # s   per ffprobe / ffmpeg call
VF = 'scale=320:-2,blackdetect=d=0.5:pix_th=0.10,freezedetect=n=-60dB:d=5'   # small picture: detectors only need a decode
AF = 'ebur128=peak=true:framelog=quiet'
_NOWIN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
_NUM = r'(-?(?:\d+(?:\.\d*)?|\.\d+)|-?inf)'

_f = float   # handles '-15.2' and '-inf' (ebur128 prints -inf for digital silence)

def parse_ebur128(text):
    """{'lufs': I, 'tp': true peak} from the ebur128 'Summary:' block (the last one), or None values if there is none
    (no audio stream). Needs ebur128=peak=true for the true peak."""
    i = text.rfind('Summary:')
    if i < 0: return {'lufs': None, 'tp': None}
    s = text[i:]
    m = re.search(r'Integrated loudness:\s*I:\s*' + _NUM + r'\s*LUFS', s)
    p = re.search(r'True peak:\s*Peak:\s*' + _NUM + r'\s*dBFS', s)
    return {'lufs': _f(m.group(1)) if m else None, 'tp': _f(p.group(1)) if p else None}

def parse_blackdetect(text):
    """[(start, end, duration)] from blackdetect lines 'black_start:S black_end:E black_duration:D'."""
    return [(float(a), float(b), float(c)) for a, b, c in
            re.findall(r'black_start:\s*' + _NUM + r'\s+black_end:\s*' + _NUM + r'\s+black_duration:\s*' + _NUM, text)]

def parse_freezedetect(text, file_dur):
    """[(start, end, duration)] from freezedetect's lavfi.freezedetect.freeze_start / freeze_end lines.
    A freeze that is still going at the end of the file only has freeze_start: it runs to file_dur."""
    out, open_at = [], None
    for key, val in re.findall(r'lavfi\.freezedetect\.(freeze_start|freeze_end):\s*' + _NUM, text):
        v = float(val)
        if key == 'freeze_start':
            if open_at is not None: out.append((open_at, v, v - open_at))   # (should not happen) a start without an end
            open_at = v
        elif open_at is not None:
            out.append((open_at, v, v - open_at)); open_at = None
    if open_at is not None and file_dur is not None:
        out.append((open_at, float(file_dur), max(0.0, float(file_dur) - open_at)))
    return out

def classify(measured, expect):
    """('OK'|'Warn'|'Fail', [reasons]). Fail: wrong resolution, expected audio missing, duration off by > DURATION_FAIL.
    Warn: duration off by > DURATION_WARN, loudness outside LUFS_MIN..LUFS_MAX, true peak > TRUE_PEAK_MAX,
    black > BLACK_MAX (after BLACK_IGNORE_START), freeze > FREEZE_MAX. silent=True skips every audio check."""
    fails, warns = [], []
    w, h = measured.get('width'), measured.get('height')
    if expect.get('width') is not None and (w, h) != (expect.get('width'), expect.get('height')):   # no expected size: skip
        fails.append(f"resolution {w}x{h}, expected {expect.get('width')}x{expect.get('height')}")

    d, ed = measured.get('duration'), expect.get('duration')
    if d is not None and ed is not None:
        off = abs(d - ed)
        if off > DURATION_FAIL: fails.append(f'duration {d:.2f} s, expected {ed:.2f} s (off by {off:.2f} s)')
        elif off > DURATION_WARN: warns.append(f'duration {d:.2f} s, expected {ed:.2f} s (off by {off:.2f} s)')
    elif ed is not None:
        warns.append('duration not measured')

    if not expect.get('silent'):
        if not measured.get('has_audio'):
            fails.append('no audio stream, but sound was expected')
        else:
            lufs, tp = measured.get('lufs'), measured.get('tp')
            if lufs is None: warns.append('loudness not measured')
            elif not (lufs_window(expect)[0] <= lufs <= lufs_window(expect)[1]):
                warns.append(f'loudness {lufs:.1f} LUFS, outside {lufs_window(expect)[0]:g}..{lufs_window(expect)[1]:g}')
            if tp is None: warns.append('true peak not measured')
            elif tp > TRUE_PEAK_MAX: warns.append(f'true peak {tp:.1f} dBTP, above {TRUE_PEAK_MAX:g}')

    for s, e, _ in measured.get('black') or []:
        dur = e - max(s, BLACK_IGNORE_START)
        if dur > BLACK_MAX: warns.append(f'black {max(s, BLACK_IGNORE_START):.2f}-{e:.2f} s ({dur:.2f} s)')
    for s, e, dur in measured.get('freeze') or []:
        if dur > FREEZE_MAX: warns.append(f'frozen picture {s:.2f}-{e:.2f} s ({dur:.2f} s)')

    return (FAIL if fails else WARN if warns else OK), fails + warns


# ---------------------------------------------------------------- runner (the only part that touches files / processes)
def _tools(ffmpeg=None, ffprobe=None):
    ffmpeg = ffmpeg or shutil.which('ffmpeg')
    if not ffprobe:
        ffprobe = shutil.which('ffprobe')
        if not ffprobe and ffmpeg:   # same folder as ffmpeg
            c = os.path.join(os.path.dirname(ffmpeg), 'ffprobe' + ('.exe' if os.name == 'nt' else ''))
            ffprobe = c if os.path.exists(c) else None
    return ffmpeg, ffprobe

def run_qc(mp4, expect, ffmpeg=None, ffprobe=None, timeout=TIMEOUT):
    """Measure a finished mp4 with ONE ffprobe and ONE ffmpeg pass, then classify it.
    Returns {'file', 'status': OK|Warn|Fail|Unknown, 'reasons': [...], 'measured': {...}|None, 'expect': {...}, 'seconds'}.
    Never raises: a timeout, a missing tool, an unreadable file or output it cannot parse gives status 'Unknown'."""
    t0 = datetime.datetime.now()
    res = {'file': mp4, 'status': UNKNOWN, 'reasons': [], 'measured': None, 'expect': dict(expect or {}), 'seconds': 0.0}
    def unknown(why):
        res['reasons'] = [why]; res['status'] = UNKNOWN; return res
    try:
        if not mp4 or not os.path.isfile(mp4): return unknown(f'file not found: {mp4}')
        ffmpeg, ffprobe = _tools(ffmpeg, ffprobe)
        if not ffprobe or not os.path.exists(ffprobe) and not shutil.which(ffprobe): return unknown('ffprobe not found')
        if not ffmpeg or not os.path.exists(ffmpeg) and not shutil.which(ffmpeg): return unknown('ffmpeg not found')
        r = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'stream=codec_type,width,height:format=duration', '-of', 'json', mp4],
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, creationflags=_NOWIN)
        if r.returncode != 0: return unknown('ffprobe could not read the file: ' + ((r.stderr.strip().splitlines() or ['?'])[-1]))
        info = json.loads(r.stdout or '{}'); streams = info.get('streams', [])
        v = next((x for x in streams if x.get('codec_type') == 'video'), None)
        if not v: return unknown('no video stream')
        has_audio = any(x.get('codec_type') == 'audio' for x in streams)
        dur = float((info.get('format') or {}).get('duration') or 0) or None
        graph = f'[0:v]{VF}[v]' + (f';[0:a]{AF}[a]' if has_audio else '')
        maps = ['-map', '[v]'] + (['-map', '[a]'] if has_audio else [])
        r = subprocess.run([ffmpeg, '-hide_banner', '-nostats', '-i', mp4, '-filter_complex', graph, *maps, '-f', 'null', '-'],
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, creationflags=_NOWIN)
        if r.returncode != 0: return unknown('ffmpeg could not decode the file: ' + ((r.stderr.strip().splitlines() or ['?'])[-1]))
        e = parse_ebur128(r.stderr)
        if has_audio and e['lufs'] is None and 'Summary:' not in r.stderr: return unknown('could not parse the loudness summary')
        m = dict(width=int(v.get('width') or 0), height=int(v.get('height') or 0), duration=dur, has_audio=has_audio,
                 lufs=e['lufs'], tp=e['tp'], black=parse_blackdetect(r.stderr), freeze=parse_freezedetect(r.stderr, dur))
        res['measured'] = m
        res['status'], res['reasons'] = classify(m, res['expect'])
        return res
    except subprocess.TimeoutExpired:
        return unknown(f'timed out after {timeout} s')
    except Exception as ex:   # anything else: report it, never raise
        return unknown(f'{type(ex).__name__}: {ex}')
    finally:
        res['seconds'] = round((datetime.datetime.now() - t0).total_seconds(), 2)

def write_report(mp4, result):
    """Write <name>.qc.txt next to the mp4 and return its path; None if it cannot be written. Never raises."""
    try:
        path = os.path.splitext(mp4)[0] + '.qc.txt'
        m, e = result.get('measured') or {}, result.get('expect') or {}
        fmt = lambda x, unit='': '-' if x is None else (f'{x:.2f}{unit}' if isinstance(x, float) else f'{x}{unit}')
        segs = lambda xs: ', '.join(f'{a:.2f}-{b:.2f} s ({c:.2f} s)' for a, b, c in xs) or 'none'
        lines = [f"QC {result.get('status', UNKNOWN)}  {os.path.basename(mp4)}",
                 f"checked {datetime.datetime.now():%Y-%m-%d %H:%M:%S} in {result.get('seconds', 0)} s", '',
                 'reasons:', *([f'  - {r}' for r in result.get('reasons') or []] or ['  none']), '',
                 'measured:',
                 f"  resolution  {m.get('width', '-')}x{m.get('height', '-')}   (expected {e.get('width', '-')}x{e.get('height', '-')})",
                 f"  duration    {fmt(m.get('duration'), ' s')}   (expected {fmt(e.get('duration'), ' s')})",
                 f"  audio       {'yes' if m.get('has_audio') else 'no'}   (silent by design: {'yes' if e.get('silent') else 'no'})",
                 f"  loudness    {fmt(m.get('lufs'), ' LUFS')}   (allowed {lufs_window(e)[0]:g}..{lufs_window(e)[1]:g}"
                 + (f", target {e['lufs']:g})" if isinstance(e.get('lufs'), (int, float)) else ')'),
                 f"  true peak   {fmt(m.get('tp'), ' dBTP')}   (max {TRUE_PEAK_MAX:g})",
                 f"  black       {segs(m.get('black') or [])}   (max {BLACK_MAX:g} s, first {BLACK_IGNORE_START:g} s ignored)",
                 f"  frozen      {segs(m.get('freeze') or [])}   (max {FREEZE_MAX:g} s)"]
        with open(path, 'w', encoding='utf-8') as fh: fh.write('\n'.join(lines) + '\n')
        return path
    except Exception:
        return None
