"""A finished video + a song -> one music video (3.18). Free and offline: ffmpeg for everything, librosa only when it is
installed (source runs / the MCP server; the exe falls back to a loudness search).

Level 1 - Shadow looks at the video itself (look()): it decodes it small (48x27 grey, 10 fps) and measures motion over
time, its peak, hard cuts, black frames and the best LOOP point (the frame most like the first one). With the song's
beats and chorus (song_info()) plan() arranges them:
  - 9:16 video, or a video of 60 s or less  -> 'chorus': the chorus part of the song, placed so the video's strongest
                                                motion lands on the chorus start, starting on a beat, faded out at the end
  - a short 16:9 video (over 60 s songs)    -> 'loop':  the video repeats to the song's length, each seam crossfaded
  - a video longer than the song            -> 'trim':  the video is cut to the song, faded out
  - about the same length (within 1 s)      -> 'as_is'
Level 2 - Claude looks (tools/mcp_server.py shadow_video_look / shadow_video_song): the contact sheet + these numbers +
the automatic plan; Claude may override mode / song_start / loop_end.

render() writes the mp4 (BT.709 tags, x264 like the rest of Shadow) with the song loudness-matched to -14 LUFS and
true-peak limited exactly like a song render (render_core.ext_audio_af + finalize_audio)."""
import json, os, re, shutil, subprocess, sys, tempfile, time
import render_core as rc

VIDEO_EXTS = ('.mp4', '.mov', '.m4v', '.webm', '.mkv')
NOWIN = rc.NOWIN
LOOK_W, LOOK_H, LOOK_FPS = 48, 27, 10   # what level 1 decodes: tiny, fast, enough for motion / cuts / loop points
SHORT_MAX_S = 60.0                      # a video this long or shorter is treated as a clip for the chorus (when not looping)
SAME_S = 1.0                            # video and song within this many seconds: join them as they are
FADE_OUT_S = 1.5
XFADE_S = 0.4                           # loop seam crossfade


def is_video(path):
    return isinstance(path, str) and path.lower().endswith(VIDEO_EXTS)


# ---------------------------------------------------------------- probing
def probe(path):
    """-> dict(w, h, fps, dur, has_audio, color_space). Raises RuntimeError when it is not a readable video."""
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,width,height,r_frame_rate,color_space:format=duration',
                        '-of', 'json', path], capture_output=True, text=True, creationflags=NOWIN)
    try: d = json.loads(r.stdout or '{}')
    except ValueError: d = {}
    v = next((s for s in d.get('streams', []) if s.get('codec_type') == 'video'), None)
    if not v: raise RuntimeError(f'not a video file ffmpeg can read: {os.path.basename(path)}')
    num, _, den = (v.get('r_frame_rate') or '30/1').partition('/')
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    return dict(w=int(v['width']), h=int(v['height']), fps=round(fps, 3), dur=float(d.get('format', {}).get('duration') or 0),
                has_audio=any(s.get('codec_type') == 'audio' for s in d.get('streams', [])), color_space=v.get('color_space') or '')


def media_duration(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', path],
                       capture_output=True, text=True, creationflags=NOWIN)
    try: return float(r.stdout.strip())
    except ValueError: raise RuntimeError(f'cannot read the length of {os.path.basename(path)}') from None


# ---------------------------------------------------------------- level 1: look at the video
def _tiny_frames(path, dur):
    raw = subprocess.run([rc.find_ffmpeg() or 'ffmpeg', '-v', 'error', '-i', path, '-vf', f'fps={LOOK_FPS},scale={LOOK_W}:{LOOK_H}:flags=area,format=gray',
                          '-f', 'rawvideo', '-'], capture_output=True, creationflags=NOWIN).stdout
    n = LOOK_W * LOOK_H
    return [raw[i:i + n] for i in range(0, len(raw) - n + 1, n)]

def _diff(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)

def look(video, sheet=None, tiles=12):
    """Level 1. -> dict(dur, w, h, vertical, motion=[(t, 0..100)] per 0.5 s, peak_t, cuts=[t], black=[t], still,
    loop_end, loop_match (0..100, 100 = identical to the first frame), sheet). sheet: write a contact sheet jpg there."""
    p = probe(video); frames = _tiny_frames(video, p['dur']); n = len(frames)
    if n < 2: raise RuntimeError(f'{os.path.basename(video)}: too short or unreadable')
    diffs = [0.0] + [_diff(frames[i], frames[i - 1]) for i in range(1, n)]
    luma = [sum(f) / len(f) for f in frames]
    srt = sorted(diffs[1:]); med = srt[len(srt) // 2] if srt else 0
    cuts = [round(i / LOOK_FPS, 2) for i in range(1, n) if diffs[i] > max(18.0, 6 * med + 4)]
    half = max(1, LOOK_FPS // 2)                       # motion per 0.5 s, cuts left out (a cut is not motion)
    cut_set = {int(c * LOOK_FPS) for c in cuts}
    motion = []
    for s in range(0, n, half):
        vals = [diffs[i] for i in range(s, min(n, s + half)) if i not in cut_set and i > 0]
        motion.append((round(s / LOOK_FPS, 2), round(min(100.0, (sum(vals) / len(vals)) * 4) if vals else 0.0, 1)))
    win = max(1, LOOK_FPS)                             # peak = the strongest 1 s of motion
    smooth = [sum(diffs[j] for j in range(i, min(n, i + win)) if j not in cut_set) for i in range(n)]
    peak_i = max(range(n), key=lambda i: smooth[i]) if any(smooth) else 0
    still = max(diffs[1:] or [0]) < 1.0
    first = frames[0]; lo = max(int(1.0 * LOOK_FPS), n // 2)
    cand = [(i, _diff(first, frames[i])) for i in range(lo, n)]
    best_i, best_d = min(cand, key=lambda c: c[1]) if cand else (n - 1, _diff(first, frames[-1]))
    out = dict(dur=round(p['dur'], 3), w=p['w'], h=p['h'], vertical=p['h'] > p['w'], fps=p['fps'], has_audio=p['has_audio'],
               motion=motion, peak_t=round((peak_i + win / 2) / LOOK_FPS, 2), cuts=cuts,
               black=[round(i / LOOK_FPS, 2) for i, l in enumerate(luma) if l < 16], still=still,
               loop_end=round(best_i / LOOK_FPS, 2), loop_match=round(max(0.0, 100 - best_d * 2), 1), sheet=None)
    if sheet:
        try:
            rate = tiles / max(p['dur'], 0.1); cols = 4 if not out['vertical'] else 6; rows = (tiles + cols - 1) // cols
            tw = 320 if not out['vertical'] else 180
            subprocess.run([rc.find_ffmpeg() or 'ffmpeg', '-v', 'error', '-y', '-i', video, '-vf',
                            f'fps={rate:.5f},scale={tw}:-2,tile={cols}x{rows}:padding=4:color=black', '-frames:v', '1', '-q:v', '3', sheet],
                           check=True, capture_output=True, creationflags=NOWIN)
            out['sheet'] = sheet
        except Exception: pass
    return out


# ---------------------------------------------------------------- the song
def _loudest_window(song, length, dur):
    """Fallback without librosa: start of the loudest `length` seconds (ffmpeg ebur128 momentary loudness every 0.1 s)."""
    r = subprocess.run([rc.find_ffmpeg() or 'ffmpeg', '-hide_banner', '-nostats', '-i', song, '-af', 'ebur128', '-f', 'null', '-'],
                       capture_output=True, text=True, creationflags=NOWIN).stderr
    m = [float(x) for x in re.findall(r'\bM:\s*(-?[\d.]+)', r)]
    if not m: return 0.0
    k = max(1, int(round(length * 10)))
    lin = [10 ** (v / 20) for v in m]
    best, best_s, acc = -1.0, 0, sum(lin[:k])
    for s in range(0, max(1, len(lin) - k)):
        if acc > best: best, best_s = acc, s
        acc += (lin[s + k] if s + k < len(lin) else 0) - lin[s]
    return min(max(0.0, best_s / 10.0), max(0.0, dur - length))

def song_info(song, clip_len=None):
    """-> dict(dur, beats=[t] or None, bpm or None, chorus=(t, end) or None, source='librosa'|'loudness')."""
    dur = media_duration(song)
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        for p in (os.path.join(here, 'tools', 'moonshift'), os.path.join(getattr(sys, '_MEIPASS', here), 'tools', 'moonshift')):
            if os.path.isdir(p) and p not in sys.path: sys.path.insert(0, p)
        import song_structure as ss   # librosa + numpy: present in source runs, not in the exe
        y = ss.load(song); b = ss.beats(y); segs, _ = ss.structure(y, b['times'])
        named = ss.guess_chapters(segs)
        ends = {s['t']: s['end'] for s in segs}
        ch = next(((t, ends.get(t, dur)) for t, nm in named if nm in ('Chorus', 'Final Chorus')), None)   # not 'Pre-Chorus'
        return dict(dur=round(dur, 3), beats=b['times'], bpm=b['bpm'], chorus=ch and (round(ch[0], 3), round(ch[1], 3)), source='librosa')
    except Exception:
        start = _loudest_window(song, clip_len or 15.0, dur)
        return dict(dur=round(dur, 3), beats=None, bpm=None, chorus=(round(start, 2), round(min(dur, start + (clip_len or 15.0)), 2)), source='loudness')


# ---------------------------------------------------------------- plan
MODES = ('auto', 'chorus', 'loop', 'trim', 'as_is')

def _snap(t, beats):
    return min(beats, key=lambda b: abs(b - t)) if beats else round(t, 2)

def plan(v, s, mode='auto', song_start=None, loop_end=None):
    """v = look(), s = song_info(). -> dict(mode, song_start, out_dur, loop_end, fade_out, reason). Pure."""
    d, S = v['dur'], s['dur']
    if mode not in MODES: raise ValueError(f"mode must be one of {', '.join(MODES)}")
    if mode == 'auto':
        if abs(S - d) <= SAME_S: mode = 'as_is'
        elif d > S: mode = 'trim'
        elif v['vertical'] or S <= SHORT_MAX_S: mode = 'chorus'
        else: mode = 'loop'
        why = {'as_is': f'video {d:.1f} s and song {S:.1f} s are about the same length',
               'trim': f'video {d:.1f} s is longer than the song {S:.1f} s: cut to the song',
               'chorus': (f'{"9:16 " if v["vertical"] else ""}clip of {d:.1f} s: the chorus part of the song, the video\'s strongest motion '
                          f'({v["peak_t"]:.1f} s) on the chorus start'),
               'loop': f'video {d:.1f} s, song {S:.1f} s: the video loops (seam at the frame most like the first, {v["loop_match"]:.0f}% alike)'}[mode]
    else: why = f'mode {mode} chosen'
    out = dict(mode=mode, song_start=0.0, out_dur=round(min(d, S), 3), loop_end=None, fade_out=0.0, reason=why)
    if mode == 'chorus':
        ch = (s.get('chorus') or (0.0, S))[0]
        start = song_start if song_start is not None else _snap(max(0.0, ch - v['peak_t']), s.get('beats'))
        if song_start is None and start > ch - v['peak_t'] + 0.05 and s.get('beats'):   # snapped late: take the beat before
            earlier = [b for b in s['beats'] if b <= ch - v['peak_t']]
            if earlier: start = earlier[-1]
        out.update(song_start=round(min(max(0.0, start), max(0.0, S - d)), 3), out_dur=round(d, 3), fade_out=min(FADE_OUT_S, d / 4))
    elif mode == 'loop':
        le = loop_end if loop_end is not None else v['loop_end']
        out.update(loop_end=round(max(1.0, min(le, d)), 3), out_dur=round(S, 3), fade_out=FADE_OUT_S, song_start=0.0)
    elif mode == 'trim':
        out.update(out_dur=round(S, 3), fade_out=FADE_OUT_S)
    elif mode == 'as_is':
        out.update(out_dur=round(min(d, S), 3))
    if song_start is not None and mode != 'chorus': out['song_start'] = round(max(0.0, min(song_start, S - 1)), 3)
    return out


# ---------------------------------------------------------------- render
def _video_vf(p, fade_out, out_dur, extra=''):
    """Even size, yuv420p tagged BT.709. A clip already in BT.709 (or untagged HD, which is BT.709 in practice) keeps its pixel
    values; a clip tagged BT.601 is converted."""
    conv = 'scale=out_color_matrix=bt709:out_range=tv,' if p.get('color_space') in ('bt470bg', 'smpte170m') else ''
    vf = f'{extra}pad=ceil(iw/2)*2:ceil(ih/2)*2,{conv}format=yuv420p,setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv'
    if fade_out: vf += f',fade=t=out:st={max(0.0, out_dur - fade_out):.3f}:d={fade_out:.3f}'
    return vf

def render(video, song, out_mp4, pl=None, mode='auto', song_start=None, loop_end=None, progress=lambda *_: None, cancel=None, info=None):
    """Write out_mp4 (via <name>.part.mp4, renamed on success). pl: a plan() result; None = look + song_info + plan here.
    info gets: sound='file', lufs, duration, scene_duration, plan, look, song (for QC and the app)."""
    ff = rc.find_ffmpeg()
    if not ff: raise RuntimeError('ffmpeg not found')
    if not song or not os.path.isfile(song): raise RuntimeError(f'no song for {os.path.basename(video)}: put {os.path.splitext(os.path.basename(video))[0]}.mp3 next to it, or choose Sound = External file')
    progress('load', 0, 1)
    p = probe(video); v = look(video); s = song_info(song, clip_len=v['dur'])
    pl = pl or plan(v, s, mode, song_start, loop_end)
    if info is not None:
        info.update(sound='file', lufs=rc.MUSIC_LUFS, duration=pl['out_dur'], scene_duration=pl['out_dur'], plan=pl, look=v,
                    size=(p['w'] + p['w'] % 2, p['h'] + p['h'] % 2),                      # QC: the video's own size (made even)
                    thumbnail_t=round(min(v['peak_t'], max(0.0, pl['out_dur'] - 0.1)), 2),   # the strongest moment
                    song=dict(dur=s['dur'], bpm=s['bpm'], chorus=s['chorus'], source=s['source']))
    if cancel is not None and cancel.is_set(): raise rc.Cancelled()
    tmp = tempfile.mkdtemp(prefix=rc.TEMP_PREFIXES[0]); rc.mark_temp_owner(tmp); part = rc._part_path(out_mp4)
    try:
        progress('audio', 0, 1)
        aud = os.path.join(tmp, 'song.wav'); L = pl['out_dur']
        af = rc.ext_audio_af(rc.MUSIC_LUFS)
        fades = (f',afade=t=in:d=0.15' if pl['song_start'] > 0 else '') + (f',afade=t=out:st={max(0.0, L - pl["fade_out"]):.3f}:d={pl["fade_out"]:.3f}' if pl['fade_out'] else '')
        # fades first, then the loudness chain: a short faded clip still lands on -14 LUFS (measured: fades after it gave -15.7)
        rc._ff([ff, '-y', '-ss', f'{pl["song_start"]:.3f}', '-t', f'{L:.3f}', '-i', song, '-af', (fades.lstrip(',') + ',' if fades else '') + af, '-ar', '48000', '-t', f'{L:.3f}', aud])
        aud = rc.finalize_audio(ff, aud, tmp, target=rc.MUSIC_LUFS)
        if cancel is not None and cancel.is_set(): raise rc.Cancelled()
        progress('frames', 0, 1)
        if pl['mode'] == 'loop':
            le = pl['loop_end']; X = min(XFADE_S, le / 4)
            unit = os.path.join(tmp, 'unit.mp4')
            if le - X >= 0.5:   # seamless unit: [X, le] crossfaded into [0, X] -> its end flows into its own start
                fc = (f'[0:v]split[a][b];[a]trim=start={X:.3f}:end={le:.3f},setpts=PTS-STARTPTS[m];[b]trim=start=0:end={X:.3f},setpts=PTS-STARTPTS[h];'
                      f'[m][h]xfade=transition=fade:duration={X:.3f}:offset={max(0.0, le - 2 * X):.3f}[u]')
                rc._ff([ff, '-y', '-i', video, '-filter_complex', fc, '-map', '[u]', '-an', '-c:v', 'libx264', '-preset', 'medium', '-crf', '12', '-pix_fmt', 'yuv420p', unit])
            else:
                rc._ff([ff, '-y', '-i', video, '-t', f'{le:.3f}', '-an', '-c:v', 'libx264', '-preset', 'medium', '-crf', '12', '-pix_fmt', 'yuv420p', unit])
            src = ['-stream_loop', '-1', '-i', unit]
        else:
            src = ['-i', video]
        cmd = [ff, '-y', '-hide_banner', '-nostats', '-loglevel', 'error', *src, '-i', aud, '-map', '0:v:0', '-map', '1:a:0',
               '-t', f'{L:.3f}', '-vf', _video_vf(p, pl['fade_out'] if pl['mode'] in ('loop', 'trim') else 0.0, L),
               *rc.x264_args(), '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', '-f', 'mp4', part]
        rc._ff(cmd)
        if cancel is not None and cancel.is_set(): raise rc.Cancelled()
        os.replace(part, out_mp4); progress('done', 1, 1)
        return out_mp4
    finally:
        if os.path.exists(part):
            try: os.remove(part)
            except OSError: pass
        shutil.rmtree(tmp, ignore_errors=True)


def describe(v, s, pl):
    """Plain lines for the Check window / MCP: what Shadow saw and what it will do."""
    m = ', '.join(f'{t:g}s {val:.0f}' for t, val in v['motion'][:16]) + (' ...' if len(v['motion']) > 16 else '')
    lines = [f'Video: {v["w"]}x{v["h"]}, {v["dur"]:.1f} s, {"9:16" if v["vertical"] else "16:9"}{", still picture" if v["still"] else ""}',
             f'Motion (0-100 every 0.5 s): {m}', f'Strongest motion at {v["peak_t"]:.1f} s; cuts: {", ".join(f"{c:g}s" for c in v["cuts"]) or "none"}',
             f'Best loop point: {v["loop_end"]:.1f} s ({v["loop_match"]:.0f}% like the first frame)',
             f'Song: {s["dur"]:.1f} s' + (f', {s["bpm"]:g} BPM' if s.get('bpm') else '') + (f', chorus {s["chorus"][0]:.1f}-{s["chorus"][1]:.1f} s' if s.get('chorus') else '')
             + f' ({"beats and sections by librosa" if s["source"] == "librosa" else "loudest part; librosa not installed"})',
             f'Plan: {pl["mode"]} - {pl["reason"]}; song from {pl["song_start"]:.2f} s, video {pl["out_dur"]:.1f} s'
             + (f', loops at {pl["loop_end"]:.2f} s' if pl.get('loop_end') else '') + (f', fade-out {pl["fade_out"]:.1f} s' if pl['fade_out'] else '')]
    if v['black']: lines.append(f'Dark frames at: {", ".join(f"{t:g}s" for t in v["black"][:8])}')
    return lines
