"""Checks tools/narrate/make_narrated_scene.py as a library (build_narrated_html) and as a CLI. No render, no browser.
Usage: python tests/test_narrate_build.py      exit 0 = all PASS, 1 = any FAIL
Needs ffmpeg (makes tiny WAVs with lavfi). Everything goes into a temp folder that is deleted at the end."""
import base64, importlib, io, json, os, re, shutil, subprocess, sys, tempfile, wave
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
TOOL_DIR = os.path.join(ROOT, 'tools', 'narrate')
sys.path.insert(0, TOOL_DIR)
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import make_narrated_scene as mns

AI = os.path.join(ROOT, 'tests', 'fixtures', 'narr_2d.html')              # a 2D Shadow scene with a SOUND kit, no narration
WEBGL = os.path.join(ROOT, 'tests', 'fixtures', 'webgl_min.html')         # WebGL canvas, no three.js
# 'already narrated' scenes are made from AI inside main(): an old-kit copy (window.NARR=, no marker) and one built by this tool
SILENT = os.path.join(ROOT, 'tests', 'fixtures', 'minimal_silent.html')   # no SOUND kit
WORDS = [{'w': 'Hello', 's': 0.2, 'e': 0.5}, {'w': 'world.', 's': 0.55, 'e': 0.9}]
results = []

def check(label, ok, seen=''):
    results.append(bool(ok)); print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f': {seen}' if seen else ''))
def raises(fn, text):
    try: fn(); return False, 'no error'
    except ValueError as e: return text in str(e), f'ValueError: {e}'
    except BaseException as e: return False, f'{type(e).__name__}: {e}'
def read(p):
    with open(p, encoding='utf-8') as fh: return fh.read()
def leftovers(d): return sorted(f for f in os.listdir(d) if f.startswith('shadow_narr_'))
def narr_json(html):
    m = re.search(r'window\.NARR=Object\.assign\((\{.*?\}),\{b64:"([A-Za-z0-9+/=]+)"\}\);', html)
    return (json.loads(m.group(1)), m.group(2)) if m else (None, None)

def main():
    tmp = tempfile.mkdtemp(prefix='shadow_narrate_test_')
    old_tempdir = tempfile.tempdir; tempfile.tempdir = tmp      # the tool's mkstemp lands here, so leftovers are visible
    try:
        j = lambda n: os.path.join(tmp, n)
        def tone(path, extra, d=1.5):
            r = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'sine=f=300:d={d}', *extra, path], capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
            return path
        stereo = tone(j('voice stereo 48k.wav'), ['-ac', '2', '-ar', '48000'])            # needs conversion
        mono = tone(j('voice mono 24k.wav'), ['-ac', '1', '-ar', '24000', '-c:a', 'pcm_s16le'])  # used as is
        long = tone(j('long.wav'), ['-ac', '1', '-c:a', 'pcm_s16le'], d=2)
        garbage = j('not audio.wav'); open(garbage, 'wb').write(b'this is not a wav file' * 50)
        scene = read(AI)
        ai_narrated = j('old kit narrated.html'); open(ai_narrated, 'w', encoding='utf-8').write(scene + '<script>window.NARR={}</script>')   # narrated by an older kit copy, without the marker
        narrated = j('narrated by this tool.html'); open(narrated, 'w', encoding='utf-8').write(mns.build_narrated_html(scene, mono, WORDS))        # already narrated by this tool

        # ------------------------------------------------ library: the happy path (stereo -> converted)
        info = {}
        html = mns.build_narrated_html(scene, stereo, {'words': WORDS}, style='karaoke', info=info)
        cfg, b64 = narr_json(html)
        check('returns the scene with the narration block', isinstance(html, str) and mns.MARK in html and html.startswith(scene[:200]))
        check('NARR config (words, style, yh, gain, captions)', cfg == {'words': WORDS, 'gain': 1.0, 'style': 'karaoke', 'yh': 0.88, 'captions': True}, str(cfg)[:80])
        w = wave.open(io.BytesIO(base64.b64decode(b64 or ''))) if b64 else None
        check('embedded audio is 16-bit mono WAV', w and (w.getnchannels(), w.getsampwidth()) == (1, 2), w and f'{w.getnchannels()} ch, {8 * w.getsampwidth()} bit, {w.getframerate()} Hz')
        check('captions get vertical for 9:16', 'vertical:c.width<c.height' in html)
        check('info: duration and words', abs(info.get('narration_duration', 0) - 1.5) < 0.05 and info.get('words') == 2 and info.get('warnings') == [], str(info))
        check('converted temp WAV deleted', leftovers(tmp) == [], str(leftovers(tmp)) or 'none left')
        check('scene file itself unchanged', read(AI) == scene)

        # mono 16-bit -> used as is (no temp file at all)
        html2 = mns.build_narrated_html(scene, mono, WORDS, captions=False)
        cfg2, _ = narr_json(html2)
        check('mono 16-bit WAV used as is, list of words, captions off', cfg2 and cfg2['captions'] is False and leftovers(tmp) == [])

        # narration longer than the scene's DURATION -> warning, not an error
        info3 = {}
        mns.build_narrated_html('<html><script>window.SOUND={};window.DURATION=1.0;window.seek=1</script></body></html>', long, WORDS, info=info3)
        check('longer than DURATION -> warning', len(info3.get('warnings', [])) == 1 and 'longer than scene DURATION' in info3['warnings'][0], str(info3.get('warnings')))

        # ------------------------------------------------ WebGL scene: voice yes, captions reported as unsupported
        infw = {}; mns.build_narrated_html(read(WEBGL), mono, WORDS, info=infw)
        check('WebGL scene -> "captions not supported (WebGL)" warning', any('captions not supported (WebGL)' in w for w in infw['warnings']), str(infw['warnings']))
        infn = {}; mns.build_narrated_html(read(WEBGL), mono, WORDS, captions=False, info=infn)
        check('WebGL scene with captions off -> no warning', infn['warnings'] == [], str(infn['warnings']))
        check('2D scene -> no WebGL warning', info.get('warnings') == [])

        # ------------------------------------------------ library: errors are ValueError (never SystemExit)
        for label, fn, text in [
            ('already narrated scene', lambda: mns.build_narrated_html(read(narrated), stereo, WORDS), 'already narrated'),
            ('narrated without the marker (old kit copy, window.NARR=)', lambda: mns.build_narrated_html(read(ai_narrated), stereo, WORDS), 'already narrated'),
            ('scene containing window.NARR=', lambda: mns.build_narrated_html(scene + '<script>window.NARR={}</script>', stereo, WORDS), 'already narrated'),
            ('scene containing NARR.b64', lambda: mns.build_narrated_html(scene + '<script>x=NARR.b64</script>', stereo, WORDS), 'already narrated'),
            ('our own output again', lambda: mns.build_narrated_html(mns.build_narrated_html(scene, mono, WORDS), mono, WORDS), 'already narrated'),
            ('scene without SOUND kit', lambda: mns.build_narrated_html(read(SILENT), stereo, WORDS), 'does not look like a Shadow scene'),
            ('bad style', lambda: mns.build_narrated_html(scene, stereo, WORDS, style='bounce'), 'style must be'),
            ('bad words', lambda: mns.build_narrated_html(scene, stereo, [{'w': 'x'}]), 'words must be'),
            ('missing WAV', lambda: mns.build_narrated_html(scene, j('nope.wav'), WORDS), 'not found'),
            ('file that is not audio', lambda: mns.build_narrated_html(scene, garbage, WORDS), 'could not convert'),
        ]:
            ok, seen = raises(fn, text); check(f'{label} -> ValueError', ok, seen)
        mns.FFMPEG = j('no_ffmpeg.exe')
        ok, seen = raises(lambda: mns.build_narrated_html(scene, stereo, WORDS), 'ffmpeg not found'); mns.FFMPEG = 'ffmpeg'
        check('ffmpeg missing -> ValueError', ok, seen)
        check('no temp WAV left after the errors', leftovers(tmp) == [], str(leftovers(tmp)) or 'none left')

        # ------------------------------------------------ _remove retries a locked file (Windows: an open handle blocks delete)
        locked = j('shadow_narr_locked.wav'); fh = open(locked, 'wb'); fh.write(b'x')
        if os.name == 'nt':
            first = mns._remove(locked, tries=3, delay=0.05)
            fh.close(); second = mns._remove(locked)
            check('_remove: locked -> False after 3 tries, then True', first is False and second is True and not os.path.exists(locked), f'{first}, {second}')
        else:
            fh.close(); check('_remove deletes (lock test is Windows-only)', mns._remove(locked) and not os.path.exists(locked))

        # ------------------------------------------------ _MEIPASS: kit files are found inside a PyInstaller bundle
        bundle = j('bundle'); os.makedirs(os.path.join(bundle, 'tools', 'narrate'))
        for f in ('captions.js', 'wrap_tail.js'): shutil.copy(os.path.join(TOOL_DIR, f), os.path.join(bundle, 'tools', 'narrate', f))
        sys._MEIPASS = bundle; importlib.reload(mns); in_bundle = mns.HERE
        sys._MEIPASS = j('empty bundle'); importlib.reload(mns); fallback = mns.HERE
        del sys._MEIPASS; importlib.reload(mns)
        check('_MEIPASS lookup', in_bundle == os.path.join(bundle, 'tools', 'narrate') and fallback == TOOL_DIR and mns.HERE == TOOL_DIR,
              f'bundle -> {os.path.relpath(in_bundle, tmp)}, empty bundle -> next to the tool')

        # ------------------------------------------------ CLI: same arguments, same messages
        env = dict(os.environ, TMP=tmp, TEMP=tmp, TMPDIR=tmp)   # the CLI's temp WAV must land where we can see it
        words_file = j('words.json'); json.dump({'words': WORDS}, open(words_file, 'w'))
        out = j('out narrated.html')
        r = subprocess.run([sys.executable, os.path.join(TOOL_DIR, 'make_narrated_scene.py'), AI, stereo, words_file, '-o', out, '--style', 'pop'],
                           capture_output=True, text=True, encoding='utf-8', errors='replace', env=env)
        check('CLI writes the file and prints OK', r.returncode == 0 and os.path.isfile(out) and re.match(r'OK -> .+  \(\d+ bytes, narration 1\.50s, 2 words\)', r.stdout.strip()),
              r.stdout.strip().replace(tmp, '<tmp>') or r.stderr.strip()[-120:])
        check('CLI output equals the library output', os.path.isfile(out) and read(out) == mns.build_narrated_html(scene, stereo, WORDS, style='pop'))
        r = subprocess.run([sys.executable, os.path.join(TOOL_DIR, 'make_narrated_scene.py'), narrated, stereo, words_file, '-o', j('x.html')],
                           capture_output=True, text=True, encoding='utf-8', errors='replace', env=env)
        check('CLI error: exit 1 with the same message', r.returncode == 1 and r.stderr.strip() == 'ERROR: scene already narrated by this tool. Use the original scene.' and not os.path.exists(j('x.html')),
              f'exit {r.returncode}: {r.stderr.strip()}')
        check('no temp WAV left after the CLI runs', leftovers(tmp) == [], str(leftovers(tmp)) or 'none left')

        # ------------------------------------------------ packaging: the frozen exe (simulated _MEIPASS)
        # In Shadow.exe the .py is inside the PYZ archive (--hidden-import) and only the two .js files are real files
        # under _MEIPASS/tools/narrate (--add-data). Fake that: a bundle folder with the .js only (captions.js marked),
        # the real tools/narrate at the END of sys.path standing in for the PYZ archive.
        if ROOT not in sys.path: sys.path.insert(0, ROOT)
        import app
        fake = j('fake _MEIPASS'); kit = os.path.join(fake, 'tools', 'narrate'); os.makedirs(kit)
        shutil.copy(os.path.join(TOOL_DIR, 'wrap_tail.js'), kit)
        with open(os.path.join(TOOL_DIR, 'captions.js'), encoding='utf-8') as fh: open(os.path.join(kit, 'captions.js'), 'w', encoding='utf-8').write('// FROM-THE-BUNDLE\n' + fh.read())
        saved_path, saved_mod = list(sys.path), sys.modules.pop('make_narrated_scene', None)
        sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p)) != os.path.normcase(TOOL_DIR)] + [TOOL_DIR]
        sys._MEIPASS = fake
        try:
            mod = app.narrate_module()
            frozen_here = mod.HERE
            out_html = mod.build_narrated_html(scene, mono, WORDS)
            on_path = os.path.normcase(kit) in [os.path.normcase(p) for p in sys.path]
        finally:
            del sys._MEIPASS; sys.path[:] = saved_path; sys.modules.pop('make_narrated_scene', None)
            if saved_mod: sys.modules['make_narrated_scene'] = saved_mod
        check('frozen: narrate_module() puts _MEIPASS/tools/narrate on sys.path', on_path)
        check('frozen: the tool reads its .js from _MEIPASS/tools/narrate', frozen_here == kit and '// FROM-THE-BUNDLE' in out_html,
              f"HERE -> {os.path.relpath(frozen_here, tmp)}; bundle captions.js used: {'// FROM-THE-BUNDLE' in out_html}")
        # the build lines ship exactly those files (both scripts, same arguments)
        need = ['--add-data "tools\\narrate\\captions.js;tools\\narrate"', '--add-data "tools\\narrate\\wrap_tail.js;tools\\narrate"',
                '--paths tools\\narrate', '--hidden-import make_narrated_scene']
        for bat in ('build_exe.bat', 'install.bat'):
            line = next((l for l in open(os.path.join(ROOT, bat), encoding='ascii').read().splitlines() if 'PyInstaller' in l), '')
            missing = [n for n in need if n not in line]
            check(f'{bat}: PyInstaller line bundles the narration kit', not missing, 'all present' if not missing else f'missing {missing}')
        check('the bundled files exist in the repo', all(os.path.isfile(os.path.join(TOOL_DIR, f)) for f in ('captions.js', 'wrap_tail.js', 'make_narrated_scene.py')))
    finally:
        tempfile.tempdir = old_tempdir
        shutil.rmtree(tmp, ignore_errors=True)
    print(f'{sum(results)}/{len(results)} passed')
    return 0 if results and all(results) else 1

if __name__ == '__main__':
    sys.exit(main())
