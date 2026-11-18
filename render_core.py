"""Shadow rendering engine: HTML scene -> frames -> audio -> MP4. No GUI code here."""
VERSION = "3.3"
import base64, functools, urllib.parse, http.server, os, shutil, subprocess, sys, tempfile, threading, time, wave, array

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

def render(html_path, out_mp4, preset, fps=30, progress=lambda *_: None, cancel=None, max_frames=None, channel='chrome', extra_args=(), vertical=False, sound='generated', audio_file=None, info=None):
    """progress(stage, done, total). cancel: threading.Event. sound: 'generated' | 'none' | 'file' (audio_file).
    info: optional dict, filled with {'sound': the mode actually used} (a scene without SOUND/STYLE_SOUND renders silent)."""
    from playwright.sync_api import sync_playwright
    src = os.path.abspath(html_path); page = os.path.basename(src); d = os.path.dirname(src)
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError('ffmpeg not found')
    w, samples = preset['w'], preset['samples']
    cw, ch = (round(w * 9 / 16), w) if vertical else (w, round(w * 9 / 16))
    tmp = tempfile.mkdtemp(prefix='bq_render_')
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
            if info is not None: info['sound'] = sound
            dur = pg.evaluate('window.DURATION'); n = round(dur * fps)
            if max_frames: n = min(n, max_frames)
            if info is not None: info['scene_duration'] = dur; info['duration'] = n / fps   # scene length / length actually rendered
            t0 = time.time()
            for i in range(n):
                if cancel is not None and cancel.is_set(): raise Cancelled()
                d64 = pg.evaluate(f'(()=>{{window.seek({i}/{fps});return document.getElementById("c").toDataURL("image/png")}})()')
                with open(os.path.join(tmp, f'f{i:04d}.png'), 'wb') as fh: fh.write(base64.b64decode(d64.split(',')[1]))
                progress('frames', i + 1, n, (time.time() - t0) / (i + 1) * (n - i - 1))
            b64 = None
            if sound == 'generated':
                progress('audio', 0, 1)
                b64 = pg.evaluate('''async()=>{const o=await SOUND.renderOffline((k)=>window.STYLE_SOUND(k), window.DURATION, 48000, {voiceover:false});
                  const u=new Uint8Array(o.inter.buffer); let s=''; for(let i=0;i<u.length;i+=32768) s+=String.fromCharCode.apply(null,u.subarray(i,i+32768)); return btoa(s)}''')
            b.close()
        aud = os.path.join(tmp, 'audio.wav')
        if sound == 'generated':
            f32 = array.array('f'); f32.frombytes(base64.b64decode(b64))
            i16 = array.array('h', (int(max(-1.0, min(1.0, v)) * 32767) for v in f32))
            raw = os.path.join(tmp, 'raw.wav')
            with wave.open(raw, 'wb') as wf:
                wf.setnchannels(2); wf.setsampwidth(2); wf.setframerate(48000); wf.writeframes(i16.tobytes())
            _ff([ffmpeg, '-y', '-i', raw, '-af', 'loudnorm=I=-15:TP=-1.5:LRA=11', '-ar', '48000', aud])
        elif sound == 'file':
            if not audio_file or not os.path.exists(audio_file): raise RuntimeError('Audio file not found')
            # loudness-match the external track, true-peak limit it, pad with silence if it is shorter than the video
            _ff([ffmpeg, '-y', '-i', audio_file, '-af', EXT_AUDIO_AF, '-ar', '48000', '-t', str(n / fps), aud])
        progress('encode', 0, 1)
        cmd = [ffmpeg, '-y', '-framerate', str(fps), '-i', os.path.join(tmp, 'f%04d.png')]
        if sound != 'none': cmd += ['-i', aud]
        cmd += ['-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', *x264_args()]   # yuv420p needs even sizes (an outdated scene can give odd ones)
        if sound != 'none': cmd += ['-c:a', 'aac', '-b:a', '192k', '-shortest']
        cmd += ['-movflags', '+faststart', out_mp4]
        _ff(cmd)
        progress('done', 1, 1)
        return out_mp4
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
