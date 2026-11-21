"""Unit checks for the queue's pure helpers in app.py (no window, no browser, no ffmpeg). Windows and Linux.
Usage: python tests/test_queue_logic.py      exit code 0 = all PASS, 1 = any FAIL"""
import os, shutil, sys, tempfile
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')   # Arabic names on a cp1252 console
except Exception: pass
import threading
os.environ['SHADOW_LANG'] = 'en'   # 3.20: the tests read the English texts
import app
from app import unique_path, scene_folder_name, find_scene_audio, resolve_audio, sound_mode, format_vertical   # importing app does not open a window

def main():
    d = tempfile.mkdtemp(prefix='shadow_queue_logic_'); results = []
    def check(got, want, label):
        ok = os.path.basename(got) == want; results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label} -> {os.path.basename(got)}" + ('' if ok else f'  (expected {want})'))
    def touch(name): open(os.path.join(d, name), 'w').close()
    try:
        p = os.path.join(d, 'scene.mp4')
        check(unique_path(p), 'scene.mp4', 'nothing exists')
        touch('scene.mp4')
        check(unique_path(p), 'scene_2.mp4', 'scene.mp4 on disk')
        touch('scene_2.mp4')
        check(unique_path(p), 'scene_3.mp4', 'scene.mp4 + scene_2.mp4 on disk')
        check(unique_path(p, {os.path.join(d, 'SCENE_3.MP4')}), 'scene_4.mp4', 'scene_3 used in batch (other case)')
        q = os.path.join(d, 'سين #1 shorts.mp4')
        check(unique_path(q), 'سين #1 shorts.mp4', 'arabic/space/# name free')
        check(unique_path(q, {q}), 'سين #1 shorts_2.mp4', 'arabic name used in batch')
        touch('سين #1 shorts.mp4')
        check(unique_path(q), 'سين #1 shorts_2.mp4', 'arabic name on disk')

        # ---- scene_folder_name: the per-scene output folder (original scene name, never the hidden narration temp file)
        def folder(path, want, label):
            got = scene_folder_name(path); ok = got == want; results.append(ok)
            print(f"{'PASS' if ok else 'FAIL'}  folder name: {label} -> {got!r}" + ('' if ok else f'  (expected {want!r})'))
        folder(os.path.join('C:\\', 'x', 'my_scene.html'), 'my_scene', 'plain name, directories dropped')
        folder('scene.htm', 'scene', '.htm extension')
        folder('سين #1 scene.html', 'سين #1 scene', 'arabic, space and #')
        folder('my.scene.v2.html', 'my.scene.v2', 'several dots keep all but the last extension')
        folder('name . .html', 'name', 'trailing spaces/dots (Windows would strip them) removed')
        folder('. .html', 'scene', 'nothing left after removing dots/spaces -> "scene"')
        folder('.html', 'html', 'a name that is only an extension (splitext sees no extension)')
        folder('con.html', '_con', 'reserved device name CON')
        folder('NUL.v2.html', '_NUL.v2', 'reserved device name NUL.anything')
        folder('com1.html', '_com1', 'reserved device name COM1')
        folder('console.html', 'console', 'a name that only starts with a reserved word is fine')
        folder(os.path.join('C:\\', 'x', '.shadow_narr_1a2b3c4d_my_scene.html'), 'my_scene', 'hidden narration temp file -> the original name')
        folder('.shadow_narr_zz_x.html', 'shadow_narr_zz_x', 'only a real hex id prefix counts as the narration temp file; leading dot removed')

        # ---- Auto sound: per-scene audio next to the .html (resolve_audio / find_scene_audio / the worker's routing)
        def same(got, want, label):
            ok = got == want; results.append(ok)
            print(f"{'PASS' if ok else 'FAIL'}  auto audio: {label} -> {got!r}" + ('' if ok else f'  (expected {want!r})'))
        sd = os.path.join(d, 'songs'); os.makedirs(sd)
        def put(name): open(os.path.join(sd, name), 'wb').close(); return os.path.join(sd, name)
        song = put('neon miles.html'); mp3 = put('neon miles.mp3')
        r = resolve_audio(song, 'auto')
        same((r['sound'], r['audio_file'], r['label'], r['warn']), ('file', mp3, 'neon miles.mp3', None), 'song mp3 beside html')
        put('neon miles.ogg'); wav = put('neon miles.wav')
        same(find_scene_audio(song), wav, 'order: .wav before .mp3/.ogg')
        same(sound_mode(app.SOUNDS[3]), 'auto', 'combobox label "Auto (file next to scene)" -> auto')
        same([sound_mode(x) for x in app.SOUNDS[:3]], ['generated', 'none', 'file'], 'old labels keep their modes')

        narr = put('talk.html'); voice = put('talk.wav'); put('talk.words.json')
        same(find_scene_audio(narr), None, 'wav + words.json is a voice, not a song')
        r = resolve_audio(narr, 'auto', narrated=True)
        same((r['sound'], r['audio_file'], r['label']), ('generated', None, 'narration'), 'wav + words.json (narrated) -> generated, narration kept')
        talk_mp3 = put('talk.mp3')
        same(resolve_audio(narr, 'auto', narrated=True)['sound'], 'generated', 'narrated scene never takes a song file')
        same(find_scene_audio(narr), talk_mp3, 'wav + words.json + mp3: the mp3 is the song candidate, wav skipped')

        lone = put('no audio.html')
        r = resolve_audio(lone, 'auto')
        same((r['sound'], r['audio_file'], r['label']), ('generated', None, 'scene sound (no file)'), 'missing audio -> scene sound')
        same(bool(r['warn']) and 'no audio.html' in r['warn'], True, 'missing audio -> warning names the scene')
        same(resolve_audio(lone, 'none')['sound'], 'none', 'Sound=None unchanged (no auto lookup)')
        same(resolve_audio(song, 'file', 'C:/x/global.mp3')['audio_file'], 'C:/x/global.mp3', 'External file unchanged (no auto lookup)')
        same(resolve_audio(song, 'generated')['sound'], 'generated', 'Generated unchanged (no auto lookup)')

        # two scenes with different audio in one queue, through the real worker (App._run_one) with the render stubbed
        a_html = put('song-a.html'); a_audio = put('song-a.flac')
        b_html = put('song-b.html'); b_audio = put('song-b.m4a')
        calls = []; PRESET = dict(app.rc.PRESETS[next(iter(app.rc.PRESETS))])
        def fake_render(scene, mp4, preset, opts, **kw):
            calls.append((scene, dict(opts))); open(mp4, 'wb').close()
        class Fake:
            def __init__(self): import queue; self.q = queue.Queue(); self.cancel = threading.Event(); self.embedded = set()
            opts = dict(vertical=False, sound='auto', audio_file=None)
        saved = app.render_item, app.probe, app.quality_check
        app.render_item, app.probe, app.quality_check = fake_render, (lambda m: (1920, 1080, 3.0, True)), (lambda *x: (None, None))
        out = os.path.join(d, 'out'); fk = Fake(); done, failed, warns, used = [], [], [], set()
        try:
            for f in (a_html, b_html, lone):
                app.App._run_one(fk, f, 0, 3, PRESET, out, '16:9 (YouTube)', done, failed, warns, used, threading.Event())
        finally:
            app.render_item, app.probe, app.quality_check = saved
        got = [(os.path.basename(s), o['sound'], o['audio_file'] and os.path.basename(o['audio_file'])) for s, o in calls]
        same(got, [('song-a.html', 'file', 'song-a.flac'), ('song-b.html', 'file', 'song-b.m4a'), ('no audio.html', 'generated', None)],
             'one queue, each scene its own audio (worker)')
        same(sorted(os.path.relpath(x, out).replace(os.sep, '/') for x in done), ['no audio/no audio.mp4', 'song-a/song-a.mp4', 'song-b/song-b.mp4'],
             'output names stay <name>/<name>.mp4')
        same([w for w in warns if 'no audio file' in w] == [r['warn']] and not failed, True, 'worker: one fallback warning, nothing failed')
        Fake.opts = dict(vertical=True, sound='auto', audio_file=None); done.clear()
        try:
            app.render_item, app.probe, app.quality_check = fake_render, (lambda m: (1080, 1920, 3.0, True)), (lambda *x: (None, None))
            app.App._run_one(Fake(), a_html, 0, 1, PRESET, out, '9:16 (Shorts)', done, failed, warns, used, threading.Event())
        finally:
            app.render_item, app.probe, app.quality_check = saved
        same([os.path.relpath(x, out).replace(os.sep, '/') for x in done], ['song-a/song-a_shorts.mp4'], '9:16 name stays <name>_shorts.mp4')

        # ---- Format "Auto (by file name)": short_*.html -> 9:16, every other file 16:9, decided per file in one queue
        def fm(got, want, label):
            ok = got == want; results.append(ok)
            print(f"{'PASS' if ok else 'FAIL'}  auto format: {label} -> {got!r}" + ('' if ok else f'  (expected {want!r})'))
        AUTO = app.FORMAT_AUTO
        fm(AUTO in app.FORMAT_CHOICES and app.FORMAT_CHOICES[:2] == list(app.rc.FORMATS), True, 'combobox: the 2 old formats + Auto (by file name)')
        fm([format_vertical(x, AUTO) for x in ('short_rain.html', os.path.join('C:' + os.sep, 'x y', 'Short_Fox.HTML'), 'SHORT_a.htm')], [True] * 3, 'short_ prefix (any case, any folder) -> 9:16')
        fm([format_vertical(x, AUTO) for x in ('rain.html', 'shorts_rain.html', 'my_short_rain.html', 'short.html', 'short-rain.html', os.path.join('short_dir', 'rain.html'))],
           [False] * 6, 'no short_ prefix on the FILE name -> 16:9 (shorts_, _short_, short., short-, folder named short_)')
        fm([format_vertical(x, AUTO) for x in ('only-if-you-stay-short.html', 'rain_short.HTML', 'rain-shorts.html', 'rain short.htm')], [True] * 4,
           'short at the END of the name (-short, _short, " short", ...shorts; any case) -> 9:16')
        fm([format_vertical(x, AUTO) for x in ('shortcake.html', 'rain-shortcut.html', 'rainshort.html')], [False] * 3, 'words that only contain "short" stay 16:9')
        fm((format_vertical('short_x.html', '16:9 (YouTube)'), format_vertical('x.html', '9:16 (Shorts)'), format_vertical('x.html', 'bogus')), (False, True, False),
           'fixed formats ignore the file name; unknown label -> 16:9')
        fm((app.format_label(True), app.format_label(False)), ('9:16', '16:9'), 'Format column text')
        s_html = put('short_song-a.html'); put('short_song-a.mp3')
        calls.clear(); done.clear(); failed.clear(); warns.clear()
        sizes = {}
        def fake_probe(m): return sizes[m]
        def fake_render2(scene, mp4, preset, opts, **kw):
            calls.append((os.path.basename(scene), dict(opts))); open(mp4, 'wb').close()
            w = PRESET['w']; sizes[mp4] = (round(w * 9 / 16), w, 3.0, True) if opts['vertical'] else (w, round(w * 9 / 16), 3.0, True)
        Fake.opts = dict(vertical=False, format=AUTO, sound='auto', audio_file=None)
        out2 = os.path.join(d, 'out_auto'); used2 = set()
        try:
            app.render_item, app.probe, app.quality_check = fake_render2, fake_probe, (lambda *x: (None, None))
            for f in (s_html, a_html):
                app.App._run_one(Fake(), f, 0, 2, PRESET, out2, AUTO, done, failed, warns, used2, threading.Event())
        finally:
            app.render_item, app.probe, app.quality_check = saved
        fm([(n, o['vertical'], 'format' in o, o['audio_file'] and os.path.basename(o['audio_file'])) for n, o in calls],
           [('short_song-a.html', True, False, 'short_song-a.mp3'), ('song-a.html', False, False, 'song-a.flac')],
           'worker: one queue, short_ -> 9:16 and the other 16:9 (own audio each; "format" not passed to the renderer)')
        fm(sorted(os.path.relpath(x, out2).replace(os.sep, '/') for x in done), ['song-a/song-a.mp4', 'song-a/song-a_shorts.mp4'],
           'output names: the Short short_song-a.html lands next to the video: song-a/song-a_shorts.mp4 and song-a/song-a.mp4')
        # ---- strip_short_marker / output_place: the Short's folder = the video's folder; <meta name="shadow-folder"> = channel folder
        fm([app.strip_short_marker(x) for x in ('short_rain', 'Rain-Short', 'rain_shorts', 'rain short', 'rain', 'short_', 'shortcake')],
           ['rain', 'Rain', 'rain', 'rain', 'rain', 'short_', 'shortcake'], 'strip_short_marker (prefix, suffixes, any case; nothing left -> unchanged)')
        mus = put('neon.html'); open(mus, 'w', encoding='utf-8').write('<head><meta name="shadow-folder" content="Music"></head>')
        msh = put('short_neon.html'); open(msh, 'w', encoding='utf-8').write("<meta name='shadow-folder' content='Music'>")
        fm((app.output_place(mus, False), app.output_place(msh, True), app.output_place(msh, False)),
           ((os.path.join('Music', 'neon'), 'neon'), (os.path.join('Music', 'neon'), 'neon_shorts'), (os.path.join('Music', 'short_neon'), 'short_neon')),
           'meta shadow-folder: Music\\neon\\neon.mp4 + neon_shorts.mp4; a short_ file rendered 16:9 keeps its name')
        bad = put('bad.html'); open(bad, 'w', encoding='utf-8').write('<meta name="shadow-folder" content="../../Win:dows/ con /x?">')
        fm(app.scene_out_folder(bad), os.path.join('Windows', '_con', 'x'), 'meta folder made safe: no .., no : ? , reserved names prefixed')
        fm((app.scene_out_folder(a_html), app.scene_out_folder(os.path.join(d, 'missing.html'))), ('', ''), 'no meta / missing file -> no channel folder')
        fm(app.output_mp4('O', mus, True), os.path.join('O', 'Music', 'neon', 'neon_shorts.mp4'), 'output_mp4 uses output_place')
        fm((failed, [w for w in warns if 'expected' in w]), ([], []), 'no failure, no size warning (expected size per file)')
        fm(Fake.opts['format'], AUTO, 'the shared queue options are not changed by the worker')
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print(f'{sum(results)}/{len(results)} passed')
    return 0 if all(results) else 1

if __name__ == '__main__':
    sys.exit(main())
