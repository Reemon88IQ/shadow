"""Shadow rendering engine: HTML scene -> frames -> audio -> MP4. No GUI code here."""
VERSION = "3.6"
import base64, collections, functools, urllib.parse, http.server, os, shutil, subprocess, sys, tempfile, threading, time, wave, array

NOWIN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
FORMATS = {'16:9 (YouTube)': False, '9:16 (Shorts)': True}
PRESETS = {
    'Draft (fast, 720p)': dict(w=1280, samples=8),
    'Good (1080p, 24 samples)': dict(w=1920, samples=24),
    'Best (1080p, 64 samples)': dict(w=1920, samples=64),
}

# External audio (Sound = External file / Auto song): loudness-match to -15 LUFS, then a true-peak limiter so the track
# never goes over -1 dBTP (ceiling -1.5). The limiter runs 4x oversampled (192 kHz) so it catches inter-sample peaks;
# latency=1 removes its 5 ms look-ahead delay (needs ffmpeg 5.1+). level=0: no auto make-up gain (it would undo the ceiling).
LOUDNORM = 'loudnorm=I=-15:TP=-1.5:LRA=11'
TP_CEILING_DB = -1.5
TP_LIMITER = (f'aresample=192000,alimiter=limit={10 ** (TP_CEILING_DB / 20):.4f}:attack=5:release=50:level=0:latency=1,'
              'aresample=48000')
EXT_AUDIO_AF = f'{LOUDNORM},{TP_LIMITER},apad'   # apad: silence after a track that is shorter than the video

def x264_args():
    """Video encoder settings (crf 16 = visually lossless for these scenes; preset medium: about 2x faster than slow)."""
    return ['-c:v', 'libx264', '-preset', 'medium', '-crf', '16', '-pix_fmt', 'yuv420p']

def find_ffmpeg():
    return shutil.which('ffmpeg')

def check_env():
    """Returns a dict of what is available."""
    out = {'ffmpeg': bool(find_ffmpeg()), 'playwright': False, 'chrome': False}
    try:
        import playwright  # noqa
        out['playwright'] = True
    except Exception:
        pass
    paths = [os.environ.get(k, '') + p for k in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA') for p in ('\\Google\\Chrome\\Application\\chrome.exe',)]
    out['chrome'] = any(os.path.exists(p) for p in paths) or bool(shutil.which('google-chrome') or shutil.which('chrome'))
    return out

class Cancelled(Exception):
    pass

def _ff(cmd):
    """Run ffmpeg; on failure raise with ffmpeg's own last error line instead of the whole command."""
    r = subprocess.run(cmd, capture_output=True, creationflags=NOWIN)
    if r.returncode != 0:
        lines = [l for l in r.stderr.decode('utf-8', 'replace').splitlines() if l.strip()]
        raise RuntimeError('ffmpeg failed: ' + (lines[-1].strip() if lines else f'exit code {r.returncode}'))

# Frame capture from the canvas. PNG = lossless (default). JPEG 0.98 = "Fast capture": measured on a 1080p scene
# (RTX 5070 Ti, 2026-11-18) 82 -> 31 ms per frame (whole render about 2x faster), final video SSIM 0.986 -> 0.983, file ~1.7x larger.
CAPTURE_JS = {'png': '"image/png"', 'jpeg': '"image/jpeg", 0.98'}   # toDataURL arguments
CAPTURE_CODEC = {'png': 'png', 'jpeg': 'mjpeg'}                         # ffmpeg decoder for the piped frames

AUDIO_TARGET_LUFS, AUDIO_TP_MAX = -15.0, -1.2   # final mp4 audio: QC allows -17..-13 LUFS and true peak <= -1 dBTP

def _loud(ffmpeg, path):
    """(integrated LUFS, true peak dBTP) of an audio/video file (ffmpeg ebur128, 4x oversampled true peak)."""
    import re
    r = subprocess.run([ffmpeg, '-hide_banner', '-nostats', '-i', path, '-af', 'ebur128=peak=true', '-f', 'null', '-'],
                       capture_output=True, creationflags=NOWIN).stderr.decode('utf-8', 'replace')
    i = re.findall(r'I:\s*(-?[\d.]+) LUFS', r); tp = re.search(r'True peak:\s*Peak:\s*(-?[\d.]+|-inf)', r)
    return (float(i[-1]) if i else None), (float(tp.group(1)) if tp and tp.group(1) != '-inf' else None)

def finalize_audio(ffmpeg, wav, tmp, aac=('-c:a', 'aac', '-b:a', '192k')):
    """Make the mp4's audio land inside the QC limits AFTER the AAC encode: encode a trial AAC, measure it, and turn the
    wav down by exactly what is needed (never up) - loudness to AUDIO_TARGET_LUFS when it is louder, true peak to
    AUDIO_TP_MAX. Measured on a loud song: the limiter gives -1.4 dBTP, but AAC 192k added +1.4 dB (0.0 dBTP) and
    single-pass loudnorm left -13.6 LUFS. Returns the wav to mux (a new file when it was turned down)."""
    cur = wav
    for k in range(3):
        trial = os.path.join(tmp, f'trial{k}.m4a')
        if subprocess.run([ffmpeg, '-v', 'error', '-y', '-i', cur, *aac, trial], capture_output=True, creationflags=NOWIN).returncode: return cur
        lufs, tp = _loud(ffmpeg, trial)
        if lufs is None or tp is None: return cur
        gain = min(0.0, AUDIO_TARGET_LUFS - lufs if lufs > AUDIO_TARGET_LUFS + 0.3 else 0.0, AUDIO_TP_MAX - tp if tp > AUDIO_TP_MAX else 0.0)
        if gain > -0.05: return cur
        nxt = os.path.join(tmp, f'final{k}.wav')
        _ff([ffmpeg, '-y', '-i', cur, '-af', f'volume={gain:.2f}dB', '-c:a', 'pcm_s16le', nxt]); cur = nxt
    return cur

def _part_path(out_mp4):
    """The file ffmpeg writes while rendering; renamed to out_mp4 only when everything succeeded."""
    root, ext = os.path.splitext(os.path.abspath(out_mp4))
    return f'{root}.part{ext or ".mp4"}'

def render(html_path, out_mp4, preset, fps=30, progress=lambda *_: None, cancel=None, max_frames=None, channel='chrome', extra_args=(), vertical=False, sound='generated', audio_file=None, info=None, capture='png'):
    """progress(stage, done, total[, eta]); stages in order: load, audio, frames, encode, done. cancel: threading.Event.
    sound: 'generated' | 'none' | 'file' (audio_file). capture: 'png' (lossless, default) | 'jpeg' (Fast capture).
    info: optional dict, filled with {'sound': the mode actually used, 'scene_duration', 'duration', 'capture'}.
    Frames are piped straight into ffmpeg (no frame files on disk) and encoded while the scene renders; the mp4 is written
    as <name>.part.mp4 and renamed to out_mp4 only on success, so a cancelled or failed render leaves no file behind."""
    from playwright.sync_api import sync_playwright
    if capture not in CAPTURE_JS: raise ValueError(f'capture must be one of {list(CAPTURE_JS)}')
    src = os.path.abspath(html_path); page = os.path.basename(src); d = os.path.dirname(src)
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError('ffmpeg not found')
    w, samples = preset['w'], preset['samples']
    cw, ch = (round(w * 9 / 16), w) if vertical else (w, round(w * 9 / 16))
    tmp = tempfile.mkdtemp(prefix='bq_render_')
    part = _part_path(out_mp4); proc = None
    class Q(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a, **k): pass
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Q, directory=d))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        with sync_playwright() as p:
            kw = dict(args=['--ignore-gpu-blocklist', '--enable-gpu-rasterization', *extra_args])
            if channel: kw['channel'] = channel
            b = p.chromium.launch(**kw)
            pg = b.new_page(viewport={'width': 1280, 'height': 720})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            progress('load', 0, 1)
            pg.goto(f'http://127.0.0.1:{port}/{urllib.parse.quote(page)}?render=1&s={samples}&w={cw}&h={ch}')
            try:
                pg.wait_for_function('window.__ready===true', timeout=180000)
            except Exception:
                raise RuntimeError('The scene did not load. ' + ' | '.join(errs[:2]))
            if sound == 'generated' and not pg.evaluate("typeof window.STYLE_SOUND==='function' && typeof window.SOUND==='object' && typeof window.SOUND.renderOffline==='function'"):
                sound = 'none'   # the scene has no generated score: render it silent instead of failing after all the frames
            if info is not None: info['sound'] = sound; info['capture'] = capture
            dur = pg.evaluate('window.DURATION'); n = round(dur * fps)
            if max_frames: n = min(n, max_frames)
            if info is not None:
                info['scene_duration'] = dur; info['duration'] = n / fps   # scene length / length actually rendered
                th = pg.evaluate('typeof window.THUMBNAIL_T === "number" ? window.THUMBNAIL_T : null')   # optional: thumbnail moment (s)
                if th is not None: info['thumbnail_t'] = max(0.0, min(float(th), n / fps - 1 / fps))
            if cancel is not None and cancel.is_set(): raise Cancelled()

            # 1) sound first: ffmpeg needs the finished audio file as its second input before the frames start flowing
            aud = os.path.join(tmp, 'audio.wav')
            if sound == 'generated':
                progress('audio', 0, 1)
                b64 = pg.evaluate('''async()=>{const o=await SOUND.renderOffline((k)=>window.STYLE_SOUND(k), window.DURATION, 48000, {voiceover:false});
                  const u=new Uint8Array(o.inter.buffer); let s=''; for(let i=0;i<u.length;i+=32768) s+=String.fromCharCode.apply(null,u.subarray(i,i+32768)); return btoa(s)}''')
                f32 = array.array('f'); f32.frombytes(base64.b64decode(b64))
                i16 = array.array('h', (int(max(-1.0, min(1.0, v)) * 32767) for v in f32))
                raw = os.path.join(tmp, 'raw.wav')
                with wave.open(raw, 'wb') as wf:
                    wf.setnchannels(2); wf.setsampwidth(2); wf.setframerate(48000); wf.writeframes(i16.tobytes())
                _ff([ffmpeg, '-y', '-i', raw, '-af', LOUDNORM, '-ar', '48000', aud])
            elif sound == 'file':
                if not audio_file or not os.path.exists(audio_file): raise RuntimeError('Audio file not found')
                progress('audio', 0, 1)
                # loudness-match the external track, true-peak limit it, pad with silence if it is shorter than the video
                _ff([ffmpeg, '-y', '-i', audio_file, '-af', EXT_AUDIO_AF, '-ar', '48000', '-t', str(n / fps), aud])
            if sound != 'none':   # measured after a trial AAC encode: turn down until the mp4 will pass QC (true peak, loudness)
                aud = finalize_audio(ffmpeg, aud, tmp)

            # 2) frames -> ffmpeg's stdin, encoded while the next frames render. -nostats + a reader thread on stderr:
            # ffmpeg can never block on a full stderr pipe (that would hang a long render).
            cmd = [ffmpeg, '-y', '-hide_banner', '-nostats', '-loglevel', 'error',
                   '-f', 'image2pipe', '-c:v', CAPTURE_CODEC[capture], '-framerate', str(fps), '-i', '-']
            if sound != 'none': cmd += ['-i', aud]
            cmd += ['-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', *x264_args()]   # yuv420p needs even sizes (an outdated scene can give odd ones)
            if sound != 'none': cmd += ['-c:a', 'aac', '-b:a', '192k', '-shortest']
            cmd += ['-movflags', '+faststart', '-f', 'mp4', part]
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=NOWIN)
            err_lines = collections.deque(maxlen=20)
            def drain():
                for line in iter(proc.stderr.readline, b''):
                    t = line.decode('utf-8', 'replace').strip()
                    if t: err_lines.append(t)
            reader = threading.Thread(target=drain, daemon=True); reader.start()
            def ffmpeg_error(ret):
                return RuntimeError('ffmpeg failed: ' + (err_lines[-1] if err_lines else f'exit code {ret}'))
            js = CAPTURE_JS[capture]
            t0 = time.time()
            for i in range(n):
                if cancel is not None and cancel.is_set(): raise Cancelled()
                d64 = pg.evaluate(f'(()=>{{window.seek({i}/{fps});return document.getElementById("c").toDataURL({js})}})()')
                try:
                    proc.stdin.write(base64.b64decode(d64.split(',')[1]))
                except OSError:   # ffmpeg stopped (bad frame, disk full...): report its own message
                    ret = proc.wait(); reader.join(5); raise ffmpeg_error(ret)
                progress('frames', i + 1, n, (time.time() - t0) / (i + 1) * (n - i - 1))
            b.close()
        progress('encode', 0, 1)   # only the last frames are left to encode
        try: proc.stdin.close()
        except OSError: pass   # ffmpeg already stopped: its exit code below says why
        ret = proc.wait(); reader.join(5)
        if ret != 0: raise ffmpeg_error(ret)
        proc = None
        os.replace(part, out_mp4)
        progress('done', 1, 1)
        return out_mp4
    finally:
        if proc is not None:   # cancelled / failed while ffmpeg runs: stop it and wait, so Windows releases the .part file
            try: proc.kill()
            except Exception: pass
            try: proc.wait(10)
            except Exception: pass
        if os.path.exists(part):
            try: os.remove(part)
            except OSError: pass
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
