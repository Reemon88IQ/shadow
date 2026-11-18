#!/usr/bin/env python3
"""make_narrated_scene.py  -  add embedded narration + word captions to a Shadow 2D scene.
Does NOT modify Shadow. Input scene stays untouched; a new HTML is written.

  python make_narrated_scene.py scene.html narr.wav words.json [-o out.html] [--style pop|karaoke] [--yh 0.88] [--gain 1.0] [--no-captions]

narr.wav : mono or stereo WAV (converted to 16-bit mono 24 kHz via ffmpeg if needed)
words.json: {"words":[{"w":"Hello","s":0.2,"e":0.5},...]}  (from align_words.py)
Requires the scene to use the Shadow SOUND kit (window.SOUND) and window.seek(t).

As a library (used by Shadow):  build_narrated_html(scene_html, wav_path, words, ...) -> html string; raises ValueError.
"""
import argparse, base64, json, os, re, subprocess, sys, tempfile, time, wave

MARK = "<!--NARRATED-BY-make_narrated_scene-->"
FFMPEG = 'ffmpeg'
STYLES = ('pop', 'karaoke')
_NOWIN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

def _here():
    """Folder holding captions.js / wrap_tail.js: next to this file, or tools/narrate inside a PyInstaller bundle."""
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        d = os.path.join(meipass, 'tools', 'narrate')
        if os.path.isfile(os.path.join(d, 'captions.js')): return d
    return os.path.dirname(os.path.abspath(__file__))
HERE = _here()

def _remove(path, tries=3, delay=0.3):
    """Delete a temp file; retry a few times (OneDrive / antivirus can hold it for a moment). Never raises."""
    for i in range(tries):
        try:
            if os.path.exists(path): os.remove(path)
            return True
        except OSError:
            if i < tries - 1: time.sleep(delay)
    return False

def load_wav_b64(path):
    """(base64 of a 16-bit mono WAV, duration in s). Other WAV/audio formats are converted with ffmpeg into a temp file
    that is always deleted. Raises ValueError if the file cannot be read or converted."""
    if not path or not os.path.isfile(path):
        raise ValueError(f'narration file not found: {path}')
    ok = False
    try:
        with wave.open(path, 'rb') as w:
            ok = (w.getnchannels() == 1 and w.getsampwidth() == 2)
    except Exception:
        ok = False
    tmp = None
    try:
        if not ok:
            fd, tmp = tempfile.mkstemp(prefix='shadow_narr_', suffix='.wav'); os.close(fd)
            try:
                r = subprocess.run([FFMPEG, '-y', '-loglevel', 'error', '-i', path, '-ac', '1', '-ar', '24000',
                                    '-c:a', 'pcm_s16le', tmp], capture_output=True, text=True, errors='replace', creationflags=_NOWIN)
            except OSError as e:
                raise ValueError(f'ffmpeg not found ({e})')
            if r.returncode != 0:
                raise ValueError('ffmpeg could not convert the narration: ' + ((r.stderr.strip().splitlines() or ['?'])[-1]))
            path = tmp
        try:
            with open(path, 'rb') as f:
                data = f.read()
            with wave.open(path, 'rb') as w:
                dur = w.getnframes() / w.getframerate()
        except Exception as e:
            raise ValueError(f'narration is not a readable WAV: {e}')
        return base64.b64encode(data).decode(), dur
    finally:
        if tmp: _remove(tmp)

CAPTIONS_WEBGL = 'captions not supported (WebGL): the voice is added, the word captions are not drawn.'

def is_narrated(scene_html):
    """True if the scene already carries embedded narration: this tool's marker, or an older/hand-made copy of the kit."""
    return MARK in scene_html or 'window.NARR=' in scene_html or 'NARR.b64' in scene_html

def is_webgl(scene_html):
    """True if the scene draws canvas#c with WebGL (three.js): the 2D caption overlay cannot draw on it."""
    return bool(re.search(r"WebGLRenderer|getContext\(\s*['\"](?:webgl2?|experimental-webgl)['\"]", scene_html))

def build_narrated_html(scene_html, wav_path, words, style='pop', yh=0.88, gain=1.0, captions=True, info=None):
    """Return the scene HTML with the narration embedded (mixed into SOUND.renderOffline in voiceover mode) and, unless
    captions=False, word captions drawn after every seek(t). words: list of {"w","s","e"} or {"words": [...]}.
    info: optional dict, filled with narration_duration, words and warnings. Raises ValueError on bad input."""
    if is_narrated(scene_html):
        raise ValueError('scene already narrated by this tool. Use the original scene.')
    if 'SOUND' not in scene_html or 'seek' not in scene_html:
        raise ValueError('scene does not look like a Shadow scene (no SOUND / seek).')
    if style not in STYLES:
        raise ValueError(f'style must be one of {", ".join(STYLES)}, got {style!r}')
    words = words['words'] if isinstance(words, dict) and 'words' in words else words
    if not isinstance(words, list) or not all(isinstance(x, dict) and {'w', 's', 'e'} <= set(x) for x in words):
        raise ValueError('words must be a list of {"w","s","e"} (or {"words": [...]})')
    b64, adur = load_wav_b64(wav_path)
    warnings = []
    if captions and is_webgl(scene_html):
        warnings.append(CAPTIONS_WEBGL)
    m = re.search(r'DURATION\s*=\s*([0-9.]+)', scene_html)
    if m and adur > float(m.group(1)) + 0.05:
        warnings.append(f'narration {adur:.2f}s is longer than scene DURATION {m.group(1)}s - the tail will be cut.')
    if info is not None:
        info.update(narration_duration=adur, words=len(words), warnings=warnings)

    try:
        caps = open(os.path.join(HERE, 'captions.js'), encoding='utf8').read()
        tail = open(os.path.join(HERE, 'wrap_tail.js'), encoding='utf8').read()
    except OSError as e:
        raise ValueError(f'narration kit files missing: {e}')
    nj = json.dumps({'words': words, 'gain': gain, 'style': style, 'yh': yh, 'captions': bool(captions)})
    hook = r"""
window.NARR=Object.assign(%s,{b64:"%s"});
(function(){ // caption overlay: wrap window.seek (assigned by the scene later) - draws after the scene frame
  if(!NARR.captions) return; let _s; const prev=window.seek;   // a scene may have set seek before this block ran
  Object.defineProperty(window,'seek',{configurable:true,get(){return _s;},set(f){
    _s=function(t){ const r=f.apply(this,arguments); try{ const c=document.getElementById('c'); if(c&&window.Captions){
      const x=c.getContext('2d'); x.save(); x.setTransform(1,0,0,1,0,0); x.globalAlpha=1;
      Captions.draw(x,c.width,c.height,t,NARR.words,{style:NARR.style,yh:NARR.yh,vertical:c.width<c.height}); x.restore(); } }catch(e){} return r; };
  }});
  if(typeof prev==='function') window.seek=prev;   // re-assign through the setter so it gets wrapped too
})();
""" % (nj, b64)
    block = MARK + '\n<script>\n' + caps + '\n' + hook + '\n' + tail + '\n</script>\n'
    i = scene_html.rfind('</body>')
    return scene_html[:i] + block + scene_html[i:] if i >= 0 else scene_html + block

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('scene'); ap.add_argument('wav'); ap.add_argument('words')
    ap.add_argument('-o', '--out'); ap.add_argument('--style', default='pop', choices=['pop', 'karaoke'])
    ap.add_argument('--yh', type=float, default=0.88); ap.add_argument('--gain', type=float, default=1.0)
    ap.add_argument('--no-captions', action='store_true')
    a = ap.parse_args()

    html = open(a.scene, encoding='utf8').read()
    words = json.load(open(a.words, encoding='utf8'))
    info = {}
    try:
        html = build_narrated_html(html, a.wav, words, style=a.style, yh=a.yh, gain=a.gain, captions=not a.no_captions, info=info)
    except ValueError as e:
        sys.exit(f'ERROR: {e}')
    for w in info['warnings']:
        print(f'WARNING: {w}')
    out = a.out or os.path.splitext(a.scene)[0] + '_narrated.html'
    open(out, 'w', encoding='utf8').write(html)
    print(f"OK -> {out}  ({os.path.getsize(out)} bytes, narration {info['narration_duration']:.2f}s, {info['words']} words)")

if __name__ == '__main__':
    main()
