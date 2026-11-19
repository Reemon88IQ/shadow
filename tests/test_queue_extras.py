"""Check frames button, time estimate, skip already rendered + Add folder, automatic .srt, end-of-queue notification.
Usage: python tests/test_queue_extras.py      exit 0 = every check PASS, 1 = any FAIL
Fixtures only (copied into a temp folder: the check writes <scene>.check.jpg next to the scene). Drives the real App with
its window hidden; message boxes are recorded, app.notify is recorded (no real toast pops up), logs/config go to the temp
folder; software WebGL (swiftshader) like smoke_test.py.
  X1 collect_scenes / Add folder (subfolders asked, hidden .shadow_narr_* skipped, no duplicates)   X2 up_to_date (pure)
  X3 estimate helpers   X4 subtitles.srt shared with make_words + write_srt   X5 toast_script (+ PowerShell builds it, Windows)
  X6 GUI: Time column + 'Estimate: ...' before, '... left' while rendering, 'took' after; queue ETA line
  X7 GUI: skip on -> second run 'Skipped (up to date)', no _2; scene changed -> rendered again (_2); skip off -> _2
  X8 GUI: narrated render -> <scene>.srt next to the mp4   X9 GUI: notification at the end (text, folder), off = none
  X10 GUI: Check frames: OK scene -> sheet + OK; broken scene -> problems; asks while a render runs
  X11 MCP: build_plan(skip_up_to_date=True) -> Skipped (up to date) + estimate_s   X12 Shadow.exe --check (cli_check)"""
import datetime, json, os, re, shutil, subprocess, sys, tempfile, threading, time, traceback
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, 'tools')); sys.path.insert(0, os.path.join(ROOT, 'tools', 'narrate'))
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import render_core as rc
import app
import subtitles

FIXT = os.path.join(ROOT, 'tests', 'fixtures')
SW = ['--use-angle=swiftshader', '--enable-unsafe-swiftshader']
TMP = tempfile.mkdtemp(prefix='shadow_extras_'); tempfile.tempdir = TMP
app.CFG = os.path.join(TMP, 'cfg.json'); app.LOG = os.path.join(TMP, 'shadow.log'); app.LOG_DIR = os.path.join(TMP, 'logs')
for h in list(app.log.handlers): app.log.removeHandler(h)
rc.PRESETS['Test (640 px)'] = dict(w=640, samples=2)
_render = rc.render
GATE = dict(name=None, reached=threading.Event(), release=threading.Event())
def render_wrapped(html, out, preset, **kw):
    if GATE['name'] == re.sub(r'^\.shadow_narr_[0-9a-f]+_', '', os.path.basename(html)):
        GATE['name'] = None; prog = kw.get('progress', lambda *_: None)
        def gated(stage, n, total, *rest):
            prog(stage, n, total, *rest)
            if stage == 'frames' and n == 5: GATE['reached'].set(); GATE['release'].wait(60)
        kw['progress'] = gated
    kw.setdefault('max_frames', 15); kw.setdefault('channel', None); kw.setdefault('extra_args', SW)
    return _render(html, out, preset, **kw)
rc.render = render_wrapped
msgs = []
for n in ('showwarning', 'showerror', 'showinfo'):
    setattr(app.messagebox, n, (lambda n: lambda title, text, **k: msgs.append((n, title, text)))(n))
asked_boxes = []   # every yes/no / folder / file dialog is replaced too: nothing may pop up on the owner's screen
for n in ('askyesno', 'askyesnocancel', 'askokcancel'):
    setattr(app.messagebox, n, (lambda n: lambda *x, **k: asked_boxes.append((n, x)) or False)(n))
app.filedialog.askdirectory = lambda **k: asked_boxes.append(('askdirectory', k)) or ''
app.filedialog.askopenfilename = lambda **k: ''
app.filedialog.askopenfilenames = lambda **k: ()
toasts = []
REAL_NOTIFY = app.notify
app.notify = lambda *a, **k: toasts.append(a)

res = []
def check(name, ok, info=''):
    res.append(bool(ok)); print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else f'  ({str(info)[:400]})'))
def tone_wav(path, sec=1.0):
    import wave, array, math
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes(array.array('h', (int(8000 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(int(48000 * sec)))).tobytes())
def touch(p, t): os.utime(p, (t, t))

S = os.path.join(TMP, 'scenes'); os.makedirs(os.path.join(S, 'sub')); os.makedirs(os.path.join(S, 'sub2', 'deep'))
for src, dst in (('webgl_min.html', 'a_scene.html'), ('webgl_min.html', 'b_scene.html'), ('webgl_min.html', 'sub/c_scene.html'),
                 ('webgl_min.html', 'sub2/deep/d_scene.html'), ('webgl_min.html', '.shadow_narr_1234abcd_a_scene.html'),
                 ('broken_no_seek.html', 'broken.html'), ('narr_2d.html', 'talk.html')):
    shutil.copy(os.path.join(FIXT, src), os.path.join(S, dst))
open(os.path.join(S, 'notes.txt'), 'w').write('not a scene')
A, B, BROKEN, TALK = (os.path.join(S, x) for x in ('a_scene.html', 'b_scene.html', 'broken.html', 'talk.html'))
tone_wav(os.path.join(S, 'talk.wav'), 1.5)
WORDS = [{'w': 'Hello', 's': 0.1, 'e': 0.4}, {'w': 'world.', 's': 0.45, 'e': 0.9}, {'w': 'Again', 's': 1.0, 'e': 1.3}]
json.dump({'duration': 1.5, 'words': WORDS}, open(os.path.join(S, 'talk.words.json'), 'w'))

try:
    # ---------------------------------------------------------------- X1..X5 pure helpers
    top, _ = app.collect_scenes([S]); deep, _ = app.collect_scenes([S], recursive=True)
    names = lambda xs: [os.path.relpath(x, S).replace(os.sep, '/') for x in xs]
    check('X1 collect_scenes: top folder sorted, hidden temp scene + .txt skipped', names(top) == ['a_scene.html', 'b_scene.html', 'broken.html', 'talk.html'], names(top))
    check('X1 collect_scenes recursive: subfolders too', sorted(names(deep)) == sorted(names(top) + ['sub/c_scene.html', 'sub2/deep/d_scene.html']), names(deep))

    out = os.path.join(TMP, 'out_x2'); mp4 = app.output_mp4(out, A, False); os.makedirs(os.path.dirname(mp4))
    check('X2 output_mp4: <out>/<scene>/<scene>.mp4 and _shorts.mp4', mp4 == os.path.join(out, 'a_scene', 'a_scene.mp4')
          and app.output_mp4(out, A, True).endswith(os.path.join('a_scene', 'a_scene_shorts.mp4')), mp4)
    check('X2 up_to_date: no output -> None', app.up_to_date(A, out, False) is None)
    open(mp4, 'wb').write(b'x' * 100); now = time.time(); touch(A, now - 100); touch(mp4, now - 50)
    song = os.path.join(TMP, 'song.wav'); tone_wav(song, 0.2); touch(song, now - 200)
    check('X2 up_to_date: mp4 newer than scene and song -> that mp4', app.up_to_date(A, out, False, [song]) == mp4)
    touch(song, now - 10)
    check('X2 up_to_date: the song changed after the render -> None', app.up_to_date(A, out, False, [song]) is None)
    touch(song, now - 200); touch(A, now - 10)
    check('X2 up_to_date: the scene changed -> None', app.up_to_date(A, out, False, [song]) is None)
    m2 = os.path.join(out, 'a_scene', 'a_scene_2.mp4'); open(m2, 'wb').write(b'y' * 100); touch(m2, now - 5)
    check('X2 up_to_date: a newer _2 render counts (newest file decides)', app.up_to_date(A, out, False, [song]) == m2)
    check('X2 up_to_date: 9:16 looks for _shorts.mp4 only', app.up_to_date(A, out, True) is None)
    check('X2 render_deps: Auto song / External file / narration files', app.render_deps(A, dict(sound='file', audio_file=song)) == [song]
          and app.render_deps(TALK, dict(sound='auto'), dict(wav='v.wav', words='w.json')) == ['v.wav', 'w.json'])

    check('X3 scene_duration_guess: literal DURATION = 4 in the HTML', app.scene_duration_guess(A) == 4.0, app.scene_duration_guess(A))
    nd = os.path.join(TMP, 'nodur.html'); open(nd, 'w').write('<script>window.DURATION = AUDIO.duration;</script>')
    check('X3 scene_duration_guess: computed DURATION -> the song length, else None',
          abs((app.scene_duration_guess(nd, song) or 0) - 0.2) < 0.01 and app.scene_duration_guess(nd) is None)
    check('X3 scene_duration_guess: narration words.json duration', app.scene_duration_guess(nd, None, dict(words=os.path.join(S, 'talk.words.json'))) == 1.5)
    best = rc.PRESETS['Best (1080p, 64 samples)']; draft = rc.PRESETS['Draft (fast, 720p)']
    e_png, e_jpg = app.estimate_seconds(100, best, 'png'), app.estimate_seconds(100, best, 'jpeg')
    check('X3 estimate_seconds: 100 s Best PNG = 3000 x 87 ms + 15 s; JPEG faster; Draft faster; measured speed wins',
          abs(e_png - (3000 * 0.087 + 15)) < 0.01 and e_jpg < e_png and app.estimate_seconds(100, draft, 'png') < e_png
          and app.estimate_seconds(100, best, 'png', sec_per_frame=0.01) == 3000 * 0.01 + 15 and app.estimate_seconds(None, best) is None, (e_png, e_jpg))
    t0 = datetime.datetime(2026, 11, 19, 15, 28)
    check('X3 eta_text: "about 12 min · done ~15:40", hours, unknown scenes',
          app.eta_text(725, now=t0) == 'about 12 min · done ~15:40' and app.eta_text(3900, now=t0).startswith('about 1 h 05 min')
          and app.eta_text(30, now=t0, unknown=2).endswith('(+ 2 not estimated)') and app.eta_text(0, unknown=1) == '1 scene(s) not estimated yet',
          app.eta_text(725, now=t0))

    import make_words
    check('X4 make_words.srt is the shared subtitles.srt', make_words.srt is subtitles.srt)
    s = subtitles.srt(subtitles.words_list({'words': WORDS}))
    check('X4 srt from words.json content', s == '1\n00:00:00,100 --> 00:00:00,900\nHello world.\n\n2\n00:00:01,000 --> 00:00:01,300\nAgain\n\n', s)
    p = app.write_srt(os.path.join(TMP, 'clip.mp4'), os.path.join(S, 'talk.words.json'))
    check('X4 write_srt: <mp4 name>.srt from a words.json path', p == os.path.join(TMP, 'clip.srt') and open(p, encoding='utf-8').read() == s, p)
    check('X4 write_srt: bad words -> None, no exception', app.write_srt(os.path.join(TMP, 'bad.mp4'), [{'x': 1}]) is None)

    sc = app.toast_script("Shadow: Bob's & <done>", '2 done, 1 failed', r'C:\Videos\my scene')
    check('X5 toast_script: XML escaped, PowerShell quotes doubled, click opens the folder (file:/// link)',
          "Bob''s &amp; &lt;done&gt;" in sc and 'launch="file:///C:/Videos/my scene"' in sc and 'activationType="protocol"' in sc and '$n.Show($t)' in sc, sc)
    if os.name == 'nt':
        r = REAL_NOTIFY('Shadow test', 'not shown', TMP, show=False, wait=True)
        check('X5 Windows PowerShell builds the toast (WinRT types load, XML parses; not shown)', r is not None and r.returncode == 0 and 'toast ready' in r.stdout,
              (r.returncode, r.stderr[-300:]) if r else 'no powershell')

    # ---------------------------------------------------------------- GUI
    a = app.App(); a.withdraw()
    a.open_when_done.set(False); a.preset.set('Test (640 px)'); a.fmt.set('16:9 (YouTube)'); a.snd.set(app.SOUNDS[0])
    a.check_kw = dict(channel=None, extra_args=SW); a.check_window_off = True
    G = {}
    def busy(): return a.worker is not None and a.worker.is_alive()
    def after_idle(fn):
        def w():
            if busy(): a.after(150, w)
            else: a.after(500, fn)
        a.after(150, w)
    def rows(): return {os.path.basename(f): a.tree.item(a.rows[f], 'values') for f in a.files}
    steps = []
    def nxt():
        if steps:
            fn = steps.pop(0)
            try: fn()
            except Exception: check('GUI step crashed', False, traceback.format_exc()); a.after(10, nxt)
        else: a.quit()

    def x1_add_folder():
        asked = []
        added = a.add_folder(S, ask=lambda *x: asked.append(x) or False)
        check('X1 Add folder: the folder\'s scenes added, subfolders asked about (No = not added)',
              [os.path.basename(f) for f in added] == ['a_scene.html', 'b_scene.html', 'broken.html', 'talk.html'] and len(asked) == 1 and '2 more' in asked[0][1],
              ([os.path.basename(f) for f in added], asked))
        again = a.add_folder(S, ask=lambda *x: True)
        check('X1 Add folder again with subfolders: only the new ones, no duplicates', sorted(os.path.basename(f) for f in again) == ['c_scene.html', 'd_scene.html']
              and len(a.files) == 6, [os.path.basename(f) for f in again])
        ev = type('E', (), {'data': '{' + os.path.join(S, 'sub') + '}'})()
        a.clear(); a.on_drop(ev)
        check('X1 dropping a folder onto the table adds its scenes', [os.path.basename(f) for f in a.files] == ['c_scene.html'], a.files)
        a.clear(); nxt()

    def x6_estimate():
        a._add(A); a._add(B); a.update_eta()
        v = rows()
        G['est_before'] = a.eta_lbl.cget('text')
        check('X6 before rendering: Time column ~m:ss per row and "Estimate: about ... · done ~HH:MM"',
              v['a_scene.html'][5].startswith('~') and G['est_before'].startswith('Estimate: ') and ' · done ~' in G['est_before'], (v, G['est_before']))
        out = os.path.join(TMP, 'out_gui'); a.out_dir.set(out); a.skip_done.set(True)
        GATE['reached'].clear(); GATE['release'].clear(); GATE['name'] = 'a_scene.html'
        a.start()
        def wait():
            if GATE['reached'].is_set(): a.after(700, held); return
            if not busy(): held(); return
            a.after(50, wait)
        def held():
            v = rows(); G['during'] = (v['a_scene.html'][5], a.eta_lbl.cget('text'), v['b_scene.html'][5])
            GATE['release'].set(); after_idle(done)
        def done():
            v = rows()
            check('X6 while rendering: the running row shows "m:ss left", the queue line "Queue: about ... · done ~"',
                  G['during'][0].endswith(' left') and G['during'][1].startswith('Queue: ') and ' · done ~' in G['during'][1], G['during'])
            check('X6 after: finished rows show how long they took', v['a_scene.html'][5].startswith('took ') and v['b_scene.html'][5].startswith('took '), v)
            check('X9 notification at the end: "1 done"-style text, click opens the output', toasts and toasts[-1][1] == '2 done' and toasts[-1][2] == out, toasts)
            G['out'] = out; nxt()
        a.after(50, wait)

    def x7_skip():
        n = len(toasts); a.start()
        def done():
            v = rows(); files = sorted(os.listdir(os.path.join(G['out'], 'a_scene')))
            check('X7 skip on: second run -> "Skipped (up to date)", no _2', v['a_scene.html'][1] == app.SKIPPED_UP_TO_DATE
                  and v['b_scene.html'][1] == app.SKIPPED_UP_TO_DATE and not any('_2' in f for f in files), (v, files))
            check('X7 status line counts the skipped ones', 'skipped (up to date)' in a.status.cget('text'), a.status.cget('text'))
            check('X9 nothing rendered (all skipped) -> no notification', len(toasts) == n, toasts[n:])
            touch(A, time.time() + 5)   # the scene changed after its render
            a.start(); after_idle(changed)
        def changed():
            v = rows(); files = sorted(os.listdir(os.path.join(G['out'], 'a_scene')))
            check('X7 the scene changed -> rendered again (a_scene_2.mp4), the unchanged one still skipped',
                  v['a_scene.html'][1].startswith('Done') and 'a_scene_2.mp4' in files and v['b_scene.html'][1] == app.SKIPPED_UP_TO_DATE, (v, files))
            a.skip_done.set(False); a.notify.set(False); n2 = len(toasts); a.start()
            def off():
                files = sorted(os.listdir(os.path.join(G['out'], 'b_scene')))
                check('X7 skip off -> renders again (_2), as before', 'b_scene_2.mp4' in files, files)
                check('X9 notification off -> none', len(toasts) == n2, toasts[n2:])
                a.notify.set(True); nxt()
            after_idle(off)
        after_idle(done)

    def x8_srt():
        a.clear(); a._add(TALK)
        check('X8 talk.html gets its narration attached ([voice])', app.VOICE_MARK in rows()['talk.html'][0], rows())
        a.start()
        def done():
            d = os.path.join(G['out'], 'talk'); files = sorted(os.listdir(d))
            text = open(os.path.join(d, 'talk.srt'), encoding='utf-8').read() if 'talk.srt' in files else ''
            check('X8 narrated render: talk.mp4 + talk.srt from its words.json', 'talk.mp4' in files and text.startswith('1\n00:00:00,100 --> 00:00:00,900\nHello world.'), (files, text[:80]))
            nxt()
        after_idle(done)

    def x10_check():
        a.clear()
        for f in (A, BROKEN): a._add(f)
        a.tree.selection_set([a.rows[A], a.rows[BROKEN]])
        a.check_results.clear(); th = a.check_frames()
        def wait():
            if th and th.is_alive(): a.after(200, wait); return
            a.after(400, done)
        def done():
            r = dict((os.path.basename(f), x) for f, x in a.check_results)
            ok = r.get('a_scene.html') or {}; bad = r.get('broken.html') or {}
            check('X10 Check frames: OK scene -> ok, a_scene.check.jpg next to it (both formats: it reads h)', ok.get('ok') and ok.get('sheet') == os.path.join(S, 'a_scene.check.jpg')
                  and os.path.isfile(ok['sheet']), ok)
            try:
                from PIL import Image
                hgt = Image.open(ok['sheet']).height
            except Exception as e: hgt = repr(e)
            check('X10 the sheet has two rows (16:9 + 9:16)', hgt == 2 * (260 + 30), hgt)
            check('X10 broken scene (no seek) -> problems reported', bad and not bad['ok'] and any('seek' in p for p in bad['problems']), bad)
            check('X10 button enabled again after the check', str(a.check_btn.cget('state')) == 'normal', a.check_btn.cget('state'))
            # a render "running": it asks first; No = nothing starts
            asked = []; real_worker = a.worker
            a.worker = threading.Thread(target=lambda: time.sleep(2)); a.worker.start()
            r2 = a.check_frames([A], ask=lambda *x: asked.append(x) or False)
            a.worker.join(); a.worker = real_worker
            check('X10 while a render runs it asks first; No = no check', r2 is None and len(asked) == 1 and 'slows the render' in asked[0][1], asked)
            a.tree.selection_set([]); r3 = a.check_frames()
            check('X10 nothing selected -> a hint on the status line', r3 is None and 'Select' in a.status.cget('text'), a.status.cget('text'))
            nxt()
        a.after(200, wait)

    steps[:] = [x1_add_folder, x6_estimate, x7_skip, x8_srt, x10_check]
    a.after(100, nxt)
    killer = threading.Timer(900, lambda: os._exit(3)); killer.daemon = True; killer.start()
    a.mainloop()
    a.after_cancel(a._poll_id); a.destroy()
    check('no error dialogs from the GUI runs', not [m for m in msgs if m[0] == 'showerror'], msgs)

    # ---------------------------------------------------------------- X11 MCP, X12 CLI
    import mcp_server as ms
    plan = ms.build_plan([A, B, TALK], out_dir=G['out'], quality='draft', format='16:9', sound='generated', skip_up_to_date=True)
    st = {os.path.basename(r['scene']): r['status'] for r in plan['rows']}
    check('X11 MCP plan: Draft size differs from the 640 px test renders -> nothing skipped', all(v == 'Queued' for v in st.values()), st)
    rc.PRESETS['Draft (fast, 720p)'] = dict(w=640, samples=2)   # same size as the test renders
    plan = ms.build_plan([A, B, TALK], out_dir=G['out'], quality='draft', format='16:9', sound='generated', skip_up_to_date=True)
    st = {os.path.basename(r['scene']): r['status'] for r in plan['rows']}
    check('X11 MCP plan skip_up_to_date: rendered scenes "Skipped (up to date)", a_scene (changed after its last render) Queued, opts carry skip_done',
          st == {'a_scene.html': 'Queued', 'b_scene.html': app.SKIPPED_UP_TO_DATE, 'talk.html': app.SKIPPED_UP_TO_DATE} and plan['opts']['skip_done'] is True, st)
    plan = ms.build_plan([A], out_dir=os.path.join(TMP, 'fresh'), quality='best', format='16:9', sound='generated', capture='jpeg')
    check('X11 MCP plan: estimate_s per row and in total (4 s scene, Best JPEG)', plan['rows'][0]['estimate_s'] and abs(plan['estimate_s'] - (120 * 0.036 + 15)) < 1, plan['estimate_s'])
    rc.PRESETS['Draft (fast, 720p)'] = dict(w=1280, samples=8)
    import frame_check, io, contextlib
    c = os.path.join(TMP, 'cli'); os.makedirs(c); shutil.copy(os.path.join(FIXT, 'webgl_min.html'), os.path.join(c, 'cli scene.html'))
    real_check = frame_check.check
    frame_check.check = lambda *x, **k: real_check(*x, **dict(k, channel=None, extra_args=SW))   # software WebGL, as everywhere in the tests
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf): code = app.cli_check(['--check', os.path.join(c, 'cli scene.html'), '--only', '16:9'])
        with contextlib.redirect_stdout(io.StringIO()): code_bad = app.cli_check(['--check', BROKEN, '--only', '9:16'])
    finally: frame_check.check = real_check
    check('X12 Shadow.exe --check (cli_check): exit 0, prints the sheet path + OK, writes it; a broken scene exits 1',
          code == 0 and 'wrote' in buf.getvalue() and buf.getvalue().strip().endswith('OK') and os.path.isfile(os.path.join(c, 'cli scene.check.jpg')) and code_bad == 1,
          (code, code_bad, buf.getvalue()))
except Exception:
    check('test crashed', False, traceback.format_exc())
finally:
    shutil.rmtree(TMP, ignore_errors=True)
print(f'\n{sum(res)}/{len(res)} passed'); sys.exit(0 if all(res) else 1)
