"""Shadow's Blender engine: .blend -> MP4 with the same output as render_core (30 fps, x264 crf 16, AAC 192k, audio inside
the QC limits, .part.mp4 renamed on success). No GUI code here. render_core.py is not changed; its helpers are reused.

Blender runs headless (blender -b scene.blend --python tools/blender/shadow_blender_job.py -- job.json) with the user's own
Blender preferences and add-ons; the .blend is never saved. Every finished frame is piped into ffmpeg and deleted while the
next one renders (encoded while rendering, like the HTML path). Cancel kills Blender.

Scene conventions (all optional):
  - 9:16: a camera named like "Camera 9x16" / "Camera vertical" / "...short", or scene property shadow_camera_9x16 = camera name;
    without one the 16:9 camera is centre-cropped to portrait (warned).
  - Sound: the scene's own sound strips / speakers are mixed down (Sound = Generated); Auto / External file work as for HTML.
  - Thumbnail: scene property THUMBNAIL_T (seconds) or a timeline marker named "thumbnail".
  - Frame range = the scene's; other frame rates are re-timed to 30 fps (same real-time length)."""
import collections, glob, json, os, re, shutil, subprocess, sys, tempfile, threading, time
import render_core as rc

EXTS = ('.blend',)
ENGINES = {'scene': 'Scene setting', 'eevee': 'Eevee', 'cycles': 'Cycles (GPU)', 'workbench': 'Workbench (fast)'}
DEVICES = ('auto', 'gpu', 'cpu')
_LINE = re.compile(r'^SHADOW_(INFO|AUDIO|FRAME|DONE|ERROR)\b ?(.*)$')

def is_blend(path):
    return str(path).lower().endswith(EXTS)

def _base():
    return getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))

def job_script():
    return os.path.join(_base(), 'tools', 'blender', 'shadow_blender_job.py')

def _version_key(path):
    m = re.search(r'Blender\s*([\d.]+)', path, re.I)
    return tuple(int(x) for x in m.group(1).split('.') if x.isdigit()) if m else (0,)

def find_blender(configured=None):
    """blender.exe: SHADOW_BLENDER env / the configured path, then PATH, then the newest Program Files\\Blender Foundation\\Blender X.Y,
    Steam and the Microsoft Store locations. None if not found."""
    for p in (os.environ.get('SHADOW_BLENDER'), configured):
        if p and os.path.isfile(p): return p
    w = shutil.which('blender')
    if w: return w
    roots = [os.environ.get(k, '') for k in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA')] + ['C:\\Program Files']
    cands = []
    for r in filter(None, roots):
        cands += glob.glob(os.path.join(r, 'Blender Foundation', 'Blender*', 'blender.exe'))
        cands += glob.glob(os.path.join(r, 'Programs', 'Blender Foundation', 'Blender*', 'blender.exe'))
        cands += glob.glob(os.path.join(r, 'Steam', 'steamapps', 'common', 'Blender', 'blender.exe'))
    cands += glob.glob(os.path.join(os.environ.get('PROGRAMFILES', 'C:\\Program Files'), 'WindowsApps', 'BlenderFoundation.Blender*', 'Blender', 'blender.exe'))
    cands = sorted({c for c in cands if os.path.isfile(c)}, key=_version_key)
    return cands[-1] if cands else None

def blender_version(exe):
    try:
        r = subprocess.run([exe, '--version'], capture_output=True, text=True, timeout=60, creationflags=rc.NOWIN)
        m = re.search(r'Blender\s+([\d.]+)', r.stdout); return m.group(1) if m else None
    except Exception:
        return None

def _size(preset, vertical):
    w = preset['w']; return (round(w * 9 / 16), w) if vertical else (w, round(w * 9 / 16))

def _quality(preset):
    """Shadow preset -> Blender quality name (draft = 720p, good = 24 samples, best = 64 samples in the HTML presets)."""
    if preset.get('w', 1920) < 1920: return 'draft'
    return 'best' if preset.get('samples', 64) >= 64 else 'good'


class _Blender:
    """One headless Blender run; stdout parsed line by line in a thread (stderr merged, kept for error messages)."""
    def __init__(self, exe, blend, job, tmp, allow_scripts=False):
        jf = os.path.join(tmp, 'job.json')
        with open(jf, 'w', encoding='utf-8') as fh: json.dump(job, fh)
        cmd = [exe, '-b', os.path.abspath(blend), '-y' if allow_scripts else '-Y', '--python', job_script(), '--', jf]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     cwd=os.path.dirname(os.path.abspath(blend)), creationflags=rc.NOWIN)
        self.events = collections.deque(); self.tail = collections.deque(maxlen=40); self.lock = threading.Condition()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for raw in iter(self.proc.stdout.readline, b''):
            line = raw.decode('utf-8', 'replace').rstrip()
            m = _LINE.match(line)
            with self.lock:
                if m: self.events.append((m.group(1), m.group(2)))
                elif line.strip(): self.tail.append(line.strip())
                self.lock.notify_all()
        with self.lock: self.events.append(('EXIT', None)); self.lock.notify_all()

    def next(self, cancel, timeout=None):
        """The next SHADOW_* event (tag, payload); raises Cancelled when cancel is set (Blender is killed)."""
        end = time.time() + timeout if timeout else None
        with self.lock:
            while not self.events:
                if cancel is not None and cancel.is_set(): self.kill(); raise rc.Cancelled()
                if end and time.time() > end: self.kill(); raise RuntimeError('Blender did not answer in time. ' + self.why())
                self.lock.wait(0.2)
            return self.events.popleft()

    def why(self):
        """The most useful lines Blender printed (errors first) for an error message."""
        lines = list(self.tail)
        err = [l for l in lines if re.search(r'error|exception|traceback|failed|not found|cannot|unable', l, re.I)]
        return ' | '.join((err or lines)[-3:])[:600]

    def kill(self):
        if self.proc.poll() is None:
            try: self.proc.kill()
            except Exception: pass
        try: self.proc.wait(15)
        except Exception: pass


def render(blend_path, out_mp4, preset, fps=30, progress=lambda *_: None, cancel=None, max_frames=None, vertical=False,
           sound='generated', audio_file=None, info=None, capture='png', engine='scene', device='auto', blender=None,
           allow_scripts=False, **_ignored):
    """Same signature and contract as render_core.render, for .blend files (extra: engine, device, blender exe path,
    allow_scripts = run Python drivers/scripts stored in the .blend; off by default). progress stages: load, audio, frames,
    encode, done. info gets sound / scene_duration / duration / capture / thumbnail_t like render_core, plus 'blender'
    (engine, device, samples, camera, frames...) and 'warnings' (missing files, no 9:16 camera, ...)."""
    if capture not in rc.CAPTURE_JS: raise ValueError(f'capture must be one of {list(rc.CAPTURE_JS)}')
    if engine not in ENGINES: raise ValueError(f'engine must be one of {list(ENGINES)}')
    if device not in DEVICES: raise ValueError(f'device must be one of {list(DEVICES)}')
    if fps != 30: raise ValueError('Blender scenes render at 30 fps')
    if not os.path.isfile(blend_path): raise RuntimeError(f'Blender scene not found: {blend_path}')
    exe = find_blender(blender)
    if not exe: raise RuntimeError('Blender not found. Install it (winget install BlenderFoundation.Blender) or set its path.')
    ffmpeg = rc.find_ffmpeg()
    if not ffmpeg: raise RuntimeError('ffmpeg not found')
    cw, ch = _size(preset, vertical)
    tmp = tempfile.mkdtemp(prefix='shadow_blend_'); frames_dir = os.path.join(tmp, 'frames'); os.makedirs(frames_dir)
    part = rc._part_path(out_mp4); proc = None; bl = None
    job = dict(mode='render', out_dir=frames_dir, w=cw, h=ch, vertical=bool(vertical), quality=_quality(preset), engine=engine,
               device=device, capture=capture, audio_wav=os.path.join(tmp, 'scene_sound.wav') if sound == 'generated' else None,
               max_frames=max_frames)
    try:
        progress('load', 0, 1)
        bl = _Blender(exe, blend_path, job, tmp, allow_scripts)
        meta = None; scene_wav = None
        while True:   # SHADOW_INFO, then SHADOW_AUDIO, before any frame
            tag, val = bl.next(cancel, timeout=900)
            if tag == 'INFO': meta = json.loads(val)
            elif tag == 'AUDIO': scene_wav = json.loads(val).get('wav'); break
            elif tag == 'ERROR': raise RuntimeError(f'Blender: {val}')
            elif tag == 'EXIT': raise RuntimeError('Blender stopped before rendering. ' + bl.why())
        n = int(meta['frames'])
        if sound == 'generated' and not scene_wav: sound = 'none'   # no sound strips / speakers: silent, like an HTML scene without SOUND
        if info is not None:
            info.update(sound=sound, capture=capture, scene_duration=meta['duration'], duration=n / fps, blender=meta,
                        warnings=list(meta.get('warnings') or []))
            th = meta.get('thumbnail_t')
            if th is not None: info['thumbnail_t'] = max(0.0, min(float(th), n / fps - 1 / fps))
        # 1) sound (Blender is already rendering frames meanwhile; they wait on disk)
        aud = os.path.join(tmp, 'audio.wav')
        if sound == 'generated':
            progress('audio', 0, 1)
            rc._ff([ffmpeg, '-y', '-i', scene_wav, '-af', rc.LOUDNORM + ',apad', '-ar', '48000', '-t', str(n / fps), aud])
        elif sound == 'file':
            if not audio_file or not os.path.exists(audio_file): raise RuntimeError('Audio file not found')
            progress('audio', 0, 1)
            rc._ff([ffmpeg, '-y', '-i', audio_file, '-af', rc.EXT_AUDIO_AF, '-ar', '48000', '-t', str(n / fps), aud])
        if sound != 'none': aud = rc.finalize_audio(ffmpeg, aud, tmp)
        # 2) frames -> ffmpeg, the same encode as render_core
        cmd = [ffmpeg, '-y', '-hide_banner', '-nostats', '-loglevel', 'error',
               '-f', 'image2pipe', '-c:v', rc.CAPTURE_CODEC[capture], '-framerate', str(fps), '-i', '-']
        if sound != 'none': cmd += ['-i', aud]
        cmd += ['-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', *rc.x264_args()]
        if sound != 'none': cmd += ['-c:a', 'aac', '-b:a', '192k', '-shortest']
        cmd += ['-movflags', '+faststart', '-f', 'mp4', part]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=rc.NOWIN)
        err = collections.deque(maxlen=20)
        def drain():
            for line in iter(proc.stderr.readline, b''):
                t = line.decode('utf-8', 'replace').strip()
                if t: err.append(t)
        reader = threading.Thread(target=drain, daemon=True); reader.start()
        ff_err = lambda ret: RuntimeError('ffmpeg failed: ' + (err[-1] if err else f'exit code {ret}'))
        got = 0; t0 = time.time()
        while got < n:
            tag, val = bl.next(cancel)
            if tag == 'FRAME':
                i, _, path = val.split(' ', 2)
                with open(path, 'rb') as fh: data = fh.read()
                try: proc.stdin.write(data)
                except OSError: ret = proc.wait(); reader.join(5); raise ff_err(ret)
                try: os.remove(path)
                except OSError: pass
                got += 1
                progress('frames', got, n, (time.time() - t0) / got * (n - got))
            elif tag == 'ERROR': raise RuntimeError(f'Blender: {val}')
            elif tag == 'EXIT': raise RuntimeError(f'Blender stopped after {got} of {n} frames. ' + bl.why())
        while True:   # SHADOW_DONE, then Blender exits
            tag, val = bl.next(cancel, timeout=120)
            if tag in ('DONE', 'EXIT'): break
            if tag == 'ERROR': raise RuntimeError(f'Blender: {val}')
        bl.kill(); bl = None
        progress('encode', 0, 1)
        try: proc.stdin.close()
        except OSError: pass
        ret = proc.wait(); reader.join(5)
        if ret != 0: raise ff_err(ret)
        proc = None
        os.replace(part, out_mp4)
        progress('done', 1, 1)
        return out_mp4
    finally:
        if bl is not None: bl.kill()
        if proc is not None:
            try: proc.kill()
            except Exception: pass
            try: proc.wait(10)
            except Exception: pass
        if os.path.exists(part):
            try: os.remove(part)
            except OSError: pass
        shutil.rmtree(tmp, ignore_errors=True)


def preview(blend_path, frames=None, out=None, size=640, engine='scene', device='auto', blender=None, allow_scripts=False,
            only=None, timeout=900):
    """check_frames for a .blend: a few frames in 16:9 and 9:16 at `size` px wide (draft samples) -> one contact sheet.
    frames: list of times like check_frames ('3', '25%', 'end-2'); default 3,25%,50%,75%,end-2.
    Returns dict(ok, sheet, problems, warnings, info{16:9, 9:16})."""
    from PIL import Image, ImageDraw
    exe = find_blender(blender)
    if not exe: return dict(ok=False, sheet=None, problems=['Blender not found'], warnings=[], info={})
    spec = frames or ['3', '25%', '50%', '75%', 'end-2']
    sizes = [s for s in (('16:9', False), ('9:16', True)) if not only or s[0] == only]
    rows, problems, warns, infos = [], [], [], {}
    for name, vertical in sizes:
        tmp = tempfile.mkdtemp(prefix='shadow_blendpv_')
        try:
            # first run: only to learn the frame range (cheap: max_frames 1, nothing rendered with mode preview + 0 frames)
            w = size if not vertical else round(size * 9 / 16); h = round(size * 9 / 16) if not vertical else size
            job = dict(mode='preview', out_dir=tmp, w=w, h=h, vertical=vertical, quality='draft', engine=engine, device=device,
                       capture='jpeg', audio_wav=None, preview_frames=None, frames_spec=spec)
            job['preview_frames'] = _frames_from_spec(exe, blend_path, spec, tmp, allow_scripts)
            bl = _Blender(exe, blend_path, job, tmp, allow_scripts); imgs = []; meta = {}
            while True:
                tag, val = bl.next(None, timeout=timeout)
                if tag == 'INFO': meta = json.loads(val)
                elif tag == 'FRAME':
                    _, _, p = val.split(' ', 2); imgs.append(Image.open(p).convert('RGB')); imgs[-1].load()
                elif tag == 'ERROR': problems.append(f'{name}: Blender: {val}'); break
                elif tag in ('DONE', 'EXIT'):
                    if tag == 'EXIT' and not imgs: problems.append(f'{name}: Blender stopped. {bl.why()}')
                    break
            bl.kill(); infos[name] = meta
            warns += [f'{name}: {x}' for x in meta.get('warnings') or [] if f'{name}: {x}' not in warns]
            if imgs:
                times = [(f - meta.get('first', 0)) / 30 for f in job['preview_frames']]
                rows.append((name, list(zip(times, imgs))))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    # missing textures etc. are real problems before a long render
    for name, meta in infos.items():
        for m in meta.get('missing') or []:
            p = f'missing {m}'
            if p not in problems: problems.append(p)
    sheet = None
    if rows:
        th = 260; strips = []
        for name, frames_ in rows:
            tiles = [im.resize((round(th * im.width / im.height), th)) for _, im in frames_]
            row = Image.new('RGB', (sum(t.width for t in tiles) + 8 * len(tiles), th + 30), (20, 20, 24)); x = 0; dr = ImageDraw.Draw(row)
            for (t, _), tile in zip(frames_, tiles):
                row.paste(tile, (x, 30)); dr.text((x + 4, 8), f'{name}  t={t:.1f}s', fill=(230, 230, 230)); x += tile.width + 8
            strips.append(row)
        W = max(r.width for r in strips); img = Image.new('RGB', (W, sum(r.height for r in strips)), (20, 20, 24)); y = 0
        for r in strips: img.paste(r, (0, y)); y += r.height
        sheet = out or os.path.splitext(blend_path)[0] + '.check.jpg'; img.save(sheet, quality=88)
    return dict(ok=not problems and bool(rows), sheet=sheet, problems=problems, warnings=warns, info=infos)


_RANGE_PY = ("import bpy,json,sys;s=bpy.context.scene;f=s.render.fps/(s.render.fps_base or 1);"
             "print('SHADOW_RANGE '+json.dumps([s.frame_start,s.frame_end,f]),flush=True)")

def _frames_from_spec(exe, blend, spec, tmp, allow_scripts=False):
    """Times ('3', '25%', 'end-2', seconds) -> frame numbers on the 30 fps timeline the job script makes."""
    r = subprocess.run([exe, '-b', os.path.abspath(blend), '-y' if allow_scripts else '-Y', '--python-expr', _RANGE_PY],
                       capture_output=True, text=True, errors='replace', timeout=300, creationflags=rc.NOWIN)
    m = re.search(r'SHADOW_RANGE (\[.*\])', r.stdout)
    if not m: raise RuntimeError('Blender could not open the scene. ' + ' | '.join(r.stdout.strip().splitlines()[-3:])[:400])
    start, end, src = json.loads(m.group(1))
    if abs(src - 30) > 1e-3:   # same re-timing as the job script (approximately: frame_map rounding)
        ratio = 30 / src; n = max(1, round((end - start + 1) * ratio)); start = round(start * ratio); end = start + n - 1
    dur = (end - start + 1) / 30; out = []
    for s in spec:
        s = str(s).strip()
        t = dur * float(s[:-1]) / 100 if s.endswith('%') else dur - float(s[4:]) if s.startswith('end-') else float(s)
        t = min(max(t, 0.0), max(dur - 1 / 30, 0)); out.append(start + int(round(t * 30)))
    return out
