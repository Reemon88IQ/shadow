"""Smart Shadow (3.19): ai_tasks.py + the AI... window. Never calls Claude: Task.ask runs tests/fixtures/fake_claude.py, the
fix / metadata jobs get a stand-in Task that edits the file itself. Scenes are copied into a temp folder (never scenes/).
  I1 Task.args: subscription only (no MCP servers, dontAsk), Read/Glob/Grep + Edit/Write ONLY for the named file
  I2 Task.ask with the fake CLI: text and session id come back; no ANTHROPIC_API_KEY reaches it; a not-signed-in CLI -> error
  I3 fix_scene: works on a hidden copy next to the scene (relative files load), the original is untouched; a problem left
     after round 1 goes back to Claude (round 2); ok only when Shadow's own check is clean and the copy changed
  I4 apply_fix keeps the original in .shadow_backup (never collected as a scene), discard removes the copy
  I5 write_metadata: metadata.txt next to the mp4, parsed by youtube.py; one there already -> metadata_2.txt, or replaced
     with the old one in .shadow_backup; the contact sheet it shows Claude is removed again
  I7 new_video: a new folder per idea (never reusing one); voiced: Claude writes the script, Shadow makes the voice, then
     Claude may only change the scene; DURATION = voice + 1.5 s; the scene's problems go back to Claude; no voice -> music only
  I8 New video window: the result goes into the queue with its voice attached (Audio column), settings remembered
  I6 window: AI... opens on one selected row with the problems Shadow saw; Fix -> Apply replaces the scene, row says
     'Fixed by AI - render again'; a failed render's problems (error + log) reach the window; Check window offers Fix
Usage: python tests/test_ai_tasks.py     exit 0 = all PASS"""
import json, os, shutil, subprocess, sys, tempfile, time
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(__file__))
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import ai_tasks as ai, assistant as asst, scene_paths, youtube

fails = []
def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f'  ({info})' if info and not ok else ''))
    if not ok: fails.append(name)

TMP = tempfile.mkdtemp(prefix='shadow_ai_test_')
FIXT = os.path.join(ROOT, 'tests', 'fixtures')
FAKE = [sys.executable, os.path.join(FIXT, 'fake_claude.py')]
LOGF = os.path.join(TMP, 'fake_argv.jsonl'); os.environ['FAKE_LOG'] = LOGF
os.environ['ANTHROPIC_API_KEY'] = 'sk-test-must-not-reach-claude'
READY = lambda exe: (True, 'ok')
def app_resolve(scene):
    """<scene>.wav + <scene>.words.json: a narration voice (not taken as a song), which App._add attaches by itself."""
    os.environ['SHADOW_LANG'] = 'en'   # 3.20: the tests read the English texts
    import app
    b = os.path.splitext(scene)[0]
    return app.find_scene_audio(scene) is None and os.path.isfile(b + '.wav') and os.path.isfile(b + '.words.json')

class StandIn:
    """A Task that edits like Claude would: edits(path, round) changes the copy; never starts a process."""
    def __init__(self, edits, text='fixed it'):
        self.edits, self.text, self.rounds, self.prompts = edits, text, 0, []
        self.cwd = None; self.edit_files = []; self.add_dirs = []; self.stopped = False
    def ask(self, prompt):
        self.rounds += 1; self.prompts.append(prompt)
        target = os.path.join(self.cwd, self.edit_files[0])
        self.edits(target, self.rounds)
        return dict(ok=True, text=self.text, error=None, session_id='s1', denials=[])
    def stop(self): self.stopped = True

try:
    # ---------------------------------------------------------------- I1
    t = ai.Task(TMP, [os.path.join(TMP, '.shadow_fix_ab.html')], exe=FAKE, lang='Arabic')
    a = t.args(); allowed = a[a.index('--allowedTools') + 1].split(',')
    check('I1 dontAsk, no MCP servers, tools Read/Glob/Grep/Edit/Write only', a[a.index('--permission-mode') + 1] == 'dontAsk'
          and '--strict-mcp-config' in a and '--mcp-config' not in a and a[a.index('--tools') + 1] == 'Read,Glob,Grep,Edit,Write', a)
    check('I1 edits allowed only for the named file', allowed == ['Read', 'Glob', 'Grep', 'Edit(./.shadow_fix_ab.html)', 'Write(./.shadow_fix_ab.html)'], allowed)
    a2 = ai.Task(TMP, [], exe=FAKE).args()
    check('I1 a job without a file cannot edit at all', a2[a2.index('--tools') + 1] == 'Read,Glob,Grep' and 'Edit' not in ','.join(a2))
    check('I1 the summary language goes into the system prompt', 'in Arabic' in a[a.index('--append-system-prompt') + 1])

    # ---------------------------------------------------------------- I2
    evs = []
    t = ai.Task(TMP, [], exe=FAKE, ready=READY, on_event=lambda *e: evs.append(e))
    r = t.ask('say hello')
    check('I2 ask (fake CLI): text + session id back', r['ok'] and 'Echo 1: say hello' in r['text'] and r['session_id'], r)
    log = [json.loads(l) for l in open(LOGF, encoding='utf-8')]
    check('I2 no API key in Claude\'s environment', not log[-1]['api_key'])
    r2 = t.ask('again')
    log = [json.loads(l) for l in open(LOGF, encoding='utf-8')]
    check('I2 a second round resumes the same session', '--resume' in log[-1]['argv'] and log[-1]['argv'][log[-1]['argv'].index('--resume') + 1] == r['session_id'])
    os.environ['FAKE_AUTH'] = 'api_key'
    r3 = ai.Task(TMP, [], exe=FAKE).ask('x')
    os.environ.pop('FAKE_AUTH')
    check('I2 an API-key sign-in is refused (subscription only)', not r3['ok'] and 'subscription' in (r3['error'] or ''), r3)
    r4 = ai.Task(TMP, [], exe=FAKE, ready=READY).ask('please CRASH')
    check('I2 a crash comes back as an error, not an exception', not r4['ok'] and 'exit' in (r4['error'] or ''), r4)

    # ---------------------------------------------------------------- I3
    d = os.path.join(TMP, 'ep 1'); os.makedirs(d)
    scene = os.path.join(d, 'my scene.html'); shutil.copy(os.path.join(FIXT, 'broken_no_seek.html'), scene)
    orig = open(scene, encoding='utf-8').read()
    checks = []
    def fake_check(p, cancel=None):
        checks.append(p); src = open(p, encoding='utf-8').read()
        return dict(ok='window.seek' in src and 'BAD' not in src, sheet=None, problems=[] if 'window.seek' in src and 'BAD' not in src else ['window.seek(t) is missing'], notes=[])
    def edits(p, n):   # round 1 fixes seek but leaves a mark, round 2 removes it
        s = open(p, encoding='utf-8').read()
        s = s.replace('window.not_seek', 'window.seek') + ('<!-- BAD -->' if n == 1 else '')
        if n == 2: s = s.replace('<!-- BAD -->', '')
        open(p, 'w', encoding='utf-8').write(s)
    st = StandIn(edits)
    r = ai.fix_scene(scene, ['window.seek(t) is missing'], task=st, check=fake_check)
    check('I3 works on a hidden .shadow_fix_ copy in the scene\'s folder', os.path.dirname(r['work']) == d and os.path.basename(r['work']).startswith('.shadow_fix_')
          and st.cwd == d and st.edit_files == [os.path.basename(r['work'])], r['work'])
    check('I3 the original is untouched', open(scene, encoding='utf-8').read() == orig)
    check('I3 a problem left after round 1 goes back to Claude; ok after round 2', r['ok'] and r['rounds'] == 2 and 'Still wrong' in st.prompts[1]
          and checks and all(c == r['work'] for c in checks), (r['rounds'], r['problems'], r['error']))
    check('I3 the prompt carries the problems, the copy\'s name and the scene contract', 'window.seek(t) is missing' in st.prompts[0]
          and os.path.basename(r['work']) in st.prompts[0] and 'window.__ready' in st.prompts[0])
    check('I3 folder scans never pick up the copy', scene_paths.collect_scenes([d])[0] == [scene])
    r_no = ai.fix_scene(scene, ['x'], task=StandIn(lambda p, n: None), check=lambda p, cancel=None: dict(ok=True, sheet=None, problems=[], notes=[]))
    check('I3 Claude changed nothing -> not ok', not r_no['ok'] and 'did not change' in (r_no['error'] or ''), r_no)
    ai.discard(r_no['work'])
    r_bad = ai.fix_scene(scene, ['x'], task=StandIn(lambda p, n: None), check=fake_check, rounds=2)
    check('I3 still broken after the last round -> not ok, problems listed', not r_bad['ok'] and r_bad['rounds'] == 2 and r_bad['problems'], r_bad)
    ai.discard(r_bad['work'])

    # ---------------------------------------------------------------- I4
    bak = ai.apply_fix(scene, r['work'])
    check('I4 apply: the scene now has the fix, the original is in .shadow_backup', 'window.seek' in open(scene, encoding='utf-8').read()
          and open(bak, encoding='utf-8').read() == orig and os.path.basename(os.path.dirname(bak)) == '.shadow_backup', bak)
    check('I4 the copy is gone, the backup is never collected as a scene', not os.path.exists(r['work'])
          and scene_paths.collect_scenes([d], recursive=True)[0] == [scene])
    old = os.path.join(d, ai.FIX_PREFIX + 'old.html'); open(old, 'w').write('x'); os.utime(old, (time.time() - 7 * 3600,) * 2)
    ai.clean_stale_fixes(d)
    check('I4 copies left by a crash (> 6 h) are removed', not os.path.exists(old))

    # ---------------------------------------------------------------- I5
    vd = os.path.join(TMP, 'out', 'my scene'); os.makedirs(vd); mp4 = os.path.join(vd, 'my scene.mp4')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=s=320x180:r=30:d=2', '-pix_fmt', 'yuv420p', mp4], check=True)
    seen = {}
    def write_meta(p, n):
        seen['files'] = sorted(os.listdir(os.path.dirname(p)))
        open(p, 'w', encoding='utf-8').write('TITLE: Why is the sky blue?\nDESCRIPTION:\nLight scatters.\nTAGS: sky, light\nAUDIENCE: no\nAI: yes\n')
    st = StandIn(write_meta)
    m = ai.write_metadata(mp4, scene=scene, task=st)
    check('I5 metadata.txt next to the mp4, parsed by youtube.py', m['ok'] and m['path'] == os.path.join(vd, 'metadata.txt')
          and youtube.parse_metadata(m['text'])['full']['title'] == 'Why is the sky blue?' and st.edit_files == ['metadata.txt'], m)
    check('I5 Claude saw a contact sheet; it is removed afterwards', any(f.startswith('.shadow_sheet_') for f in seen['files'])
          and not any(f.startswith('.shadow_sheet_') for f in os.listdir(vd)) and '.shadow_sheet_' in st.prompts[0], seen)
    check('I5 the scene\'s folder is readable for Claude', st.add_dirs == [d])
    m2 = ai.write_metadata(mp4, task=StandIn(write_meta))
    check('I5 one there already -> metadata_2.txt (never overwritten)', m2['ok'] and os.path.basename(m2['path']) == 'metadata_2.txt'
          and os.path.isfile(os.path.join(vd, 'metadata.txt')))
    m3 = ai.write_metadata(mp4, replace=True, task=StandIn(write_meta))
    check('I5 replace=True: the old one goes to .shadow_backup', m3['ok'] and os.path.basename(m3['path']) == 'metadata.txt'
          and len(os.listdir(os.path.join(vd, '.shadow_backup'))) == 1)
    m4 = ai.write_metadata(mp4, replace=True, task=StandIn(lambda p, n: open(p, 'w').write('hello')))
    check('I5 a file without TITLE is reported', not m4['ok'] and 'TITLE' in (m4['error'] or ''), m4)
    check('I5 scene_text finds the scene\'s words', isinstance(ai.scene_text(scene), str))


    # ---------------------------------------------------------------- I7 new_video
    import wave, struct
    def fake_voice(py, script, wav, words):
        with wave.open(wav, 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(struct.pack('<h', 0) * 24000 * 10)
        json.dump({'words': [{'w': 'Why', 's': 0.3, 'e': 0.5}, {'w': 'blue', 's': 9.0, 'e': 9.6}]}, open(words, 'w'))
    def nv_edits(p, n):
        if p.endswith('.script.txt'): open(p, 'w', encoding='utf-8').write('Why is the sky blue? Because of tiny molecules.')
        else:
            src = open(p, encoding='utf-8').read().replace('window.DURATION = 20;', 'window.DURATION = 11.5;')
            open(p, 'w', encoding='utf-8').write(src)
    root = os.path.join(TMP, 'new videos'); st = StandIn(nv_edits)
    good = lambda p, cancel=None: dict(ok='BAD' not in open(p, encoding='utf-8').read(), sheet=None,
                                       problems=[] if 'BAD' not in open(p, encoding='utf-8').read() else ['text cut off'], notes=[])
    nv = ai.new_video('Why is the sky blue?', root, style='Explainer (science, narrated)', seconds=30, voice=True, voice_py='py',
                      voice_maker=fake_voice, task=st, check=good)
    base = os.path.join(root, 'why_is_the_sky_blue')
    check('I7 folder + files named after the idea', nv['ok'] and nv['folder'] == base and all(os.path.isfile(os.path.join(base, 'why_is_the_sky_blue' + e))
          for e in ('.html', '.script.txt', '.wav', '.words.json')), (nv.get('error'), nv.get('problems'), os.listdir(base) if os.path.isdir(base) else None))
    check('I7 job 1 may write only the script, job 2 only the scene', st.edit_files == ['why_is_the_sky_blue.html'] and 'script.txt' in st.prompts[0]
          and 'NARRATION' in st.prompts[0] and 'PICTURE' in st.prompts[1])
    check('I7 DURATION asked = the voice length + 1.5 s, words.json named for timing', nv['duration'] == 11.5 and 'window.DURATION = 11.5' in st.prompts[1]
          and 'why_is_the_sky_blue.words.json' in st.prompts[1])
    check('I7 Shadow attaches that narration by itself (Sound Auto)', app_resolve(nv['scene']))
    def bad_then_good(p, n):
        src = open(p, encoding='utf-8').read().replace('<!-- BAD -->', '')
        open(p, 'w', encoding='utf-8').write(src + ('<!-- BAD -->' if n == 1 else ''))
    st2 = StandIn(bad_then_good)
    nv2 = ai.new_video('Why is the sky blue?', root, voice=False, task=st2, check=good)
    check('I7 the same idea again -> a new folder (_2), never overwritten', nv2['folder'] == base + '_2' and os.path.isfile(os.path.join(base, 'why_is_the_sky_blue.wav')))
    check('I7 no voice: one job, music only, problems sent back (round 2 ok)', nv2['ok'] and nv2['rounds'] == 2 and len(st2.prompts) == 2
          and 'There is no voice' in st2.prompts[0] and 'text cut off' in st2.prompts[1] and not nv2['wav'], (nv2['rounds'], nv2['problems']))
    nv3 = ai.new_video('x', root, voice=True, voice_py='py', voice_maker=lambda *a: (_ for _ in ()).throw(RuntimeError('kokoro broke')),
                       task=StandIn(nv_edits), check=good)
    check('I7 a voice failure stops with the reason', not nv3['ok'] and 'kokoro broke' in nv3['error'], nv3.get('error'))
    check('I7 slug: Arabic idea -> a dated name', ai.slug('لماذا السماء زرقاء').startswith('video_'))
    check('I7 the template passes Shadow own scene check', ai.template_path() and not __import__('render_core').contract_problems(ai.template_path()))

    # ---------------------------------------------------------------- I6 window
    import app, no_dialogs
    app.LOG = os.path.join(TMP, 'shadow.log'); app.LOG_DIR = os.path.join(TMP, 'logs'); app.close_log()
    no_dialogs.block(app)
    w = app.App(); w.withdraw(); w.check_window_off = True
    try:
        scene2 = os.path.join(d, 'second.html'); shutil.copy(os.path.join(FIXT, 'broken_no_seek.html'), scene2)
        w._add(scene2); w.update()
        w.tree.selection_set(()); w.update()
        check('I6 AI... with nothing selected -> a hint, no window', w.open_ai() is None and 'Select one scene' in w.status.cget('text'))
        w.row_problems[scene2] = app.render_problems(dict(status='error', error='scene never became ready', log=os.path.join(d, 'second_1.log'),
                                                          qc=None))
        w.tree.selection_set(w.rows[scene2]); w.update()
        real_task = ai.Task
        def fix_seek(p, n):
            s = open(p, encoding='utf-8').read(); open(p, 'w', encoding='utf-8').write(s.replace('window.not_seek', 'window.seek'))
        ai.Task = lambda cwd, files=(), **kw: StandIn(fix_seek)
        w.ai_check = lambda p, cancel=None: dict(ok=True, sheet=None, problems=[], notes=[])
        win = w.open_ai()
        note = win.note.get('1.0', 'end')
        check('I6 the window starts from the failed render (error + its log)', 'scene never became ready' in note and 'second_1.log' in note, note)
        check('I6 YouTube text is off without a rendered video', str(win.meta_btn.cget('state')) == 'disabled')
        win.note.insert('end', '\n- the title is cut at the bottom')
        th = win.start_fix(); th.join(30)
        for _ in range(20): w.update(); time.sleep(0.05)
        check("I6 Fix -> OK, Apply enabled, the diff is shown", str(win.apply_btn.cget("state")) == "normal" and "window.seek" in win.out.get("1.0", "end"),
              (str(win.apply_btn.cget("state")), win.fix and win.fix.get("work"), win.out.get("1.0", "end")[-400:]))
        bak = win.apply()
        check('I6 Apply: scene replaced, backup kept, row says render again', bak and os.path.isfile(bak) and 'window.seek' in open(scene2, encoding='utf-8').read()
              and w.tree.item(w.rows[scene2], 'values')[1] == 'Fixed by AI - render again' and scene2 not in w.row_problems and w.state[scene2] == 'queued')
        win.close()
        w.out_mp4[scene2] = [mp4]
        win2 = w.open_ai([scene2], [])
        check('I6 with a rendered video: YouTube text is on', str(win2.meta_btn.cget('state')) == 'normal')
        win2.close()
        ai.Task = real_task
        bad = os.path.join(d, 'third.html'); shutil.copy(os.path.join(FIXT, 'broken_no_seek.html'), bad)
        w.show_check(bad, dict(ok=False, sheet=None, problems=['9:16: window.seek(t) is missing'], notes=[]))
        check('I6 a failed Check frames is remembered for the AI window', w.row_problems.get(bad) == ['9:16: window.seek(t) is missing'])
        cw = app.CheckWindow(w, bad, dict(ok=False, sheet=None, problems=['x'], notes=[]))
        labels = [c.cget('text') for c in cw.top.winfo_children()[0].winfo_children()[-1].winfo_children()]
        check('I6 the Check window offers "Fix with AI..."', 'Fix with AI...' in labels, labels)
        cw.close()
        # I8 New video window
        nvd_root = os.path.join(TMP, 'nv window')
        w.ai_voice = 'py'; w.ai_voice_maker = fake_voice; w.ai_check = good
        ai.Task = lambda cwd, files=(), **kw: StandIn(nv_edits)
        dlg = w.open_new_video(); w.update()
        dlg.idea.insert('1.0', 'How does a rainbow form?'); dlg.root.set(nvd_root); dlg.length.set('30 seconds')
        for _ in range(5): w.update(); time.sleep(0.05)
        th = dlg.start(); th.join(30)
        for _ in range(20): w.update(); time.sleep(0.05)
        sc = os.path.join(nvd_root, 'how_does_a_rainbow_form', 'how_does_a_rainbow_form.html')
        check('I8 the new scene is in the queue', sc in w.files and 'Ready' in dlg.status.cget('text'), (dlg.status.cget('text'), w.files[-1:]))
        audio_col = w.tree.item(w.rows[sc], 'values')[3] if sc in w.rows else ''
        check('I8 its voice is attached (Audio column)', 'narr' in str(audio_col).lower() or 'voice' in str(audio_col).lower(), audio_col)
        check('I8 settings remembered for next time', w.cfg_ai.get('length') == '30 seconds' and w.cfg_ai.get('dir') == nvd_root)
        dlg.close(); ai.Task = real_task
        check('I6 no real dialog opened', not [c for c in no_dialogs.CALLS if c.startswith('ask')], no_dialogs.CALLS)
    finally:
        w.destroy()
finally:
    try:
        import app as _a; _a.close_log()
    except Exception: pass
    shutil.rmtree(TMP, ignore_errors=True)

print(f'\n{"ALL PASS" if not fails else f"{len(fails)} FAIL: " + ", ".join(fails)}')
sys.exit(1 if fails else 0)
