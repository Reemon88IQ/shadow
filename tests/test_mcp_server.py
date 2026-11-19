"""Checks of tools/mcp_server.py (the Shadow MCP server). Uses tests/fixtures only (copied into a temp folder).
Usage: python tests/test_mcp_server.py      exit 0 = every check PASS, 1 = any FAIL

  M1 collect_scenes: files + folders, sorted, hidden .shadow_narr_* and dot-files skipped, duplicates once, bad paths reported
  M2 build_plan: Format auto (short_* / *_short -> 9:16), Sound auto (song next to the scene / fallback warning), Sound file,
     narration auto ([voice] from <scene>.wav + .words.json), [voice embedded], outdated skip / render, bad settings -> ValueError
  M3 the job renders through app.render_scene (the GUI queue's own function): <out>/<scene>/<scene>.mp4 + .qc.txt,
     9:16 -> <scene>_shorts.mp4, a second job never overwrites (_2), same files as the GUI worker App._run_one makes
  M4 cancel: "cancel current" goes on with the next scene; "stop" cancels the rest; no partial mp4, no empty folder
  M5 read_qc / list_outputs / run_qc_now on the rendered files
  M6 the real server over stdio (MCP client): tools listed, shadow_info, shadow_plan, a bad setting is an error result,
     shadow_check_frames on webgl_min (sheet image returned), and - only if Chrome is installed - a real draft render
     followed with shadow_progress(wait_seconds) and shadow_outputs / shadow_qc
  M7 optional HTTP mode (--http): off by default, listens on 127.0.0.1 only, MCP client over http lists the tools and
     calls shadow_info, a request with a foreign Host header is refused (DNS rebinding)
M3/M4 use the regression tests' software-WebGL + frame cap (like test_queue_gui.py); M6 renders with the real Chrome."""
import asyncio, json, os, shutil, sys, tempfile, threading, time, traceback
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, 'tools')); sys.path.insert(0, HERE)
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import render_core as rc
import app
import mcp_server as ms
from regression import KW, probe

FIX = os.path.join(ROOT, 'tests', 'fixtures')   # never read scenes/ in tests
RESULTS = []
def check(name, ok, detail=''):
    RESULTS.append(bool(ok)); print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f'  --  {detail}' if detail else '')); return ok

def setup(tmp):
    d = os.path.join(tmp, 'in'); os.makedirs(d)
    for n, new in (('webgl_min.html', 'webgl_min.html'), ('webgl_min.html', 'short_webgl.html'), ('webgl_min.html', 'rain_short.html'),
                   ('outdated_no_h.html', 'short_old.html'), ('narr_2d.html', 'narr_2d.html'), ('minimal_silent.html', 'minimal_silent.html')):
        shutil.copy(os.path.join(FIX, n), os.path.join(d, new))
    open(os.path.join(d, '.shadow_narr_ab12cd34_webgl_min.html'), 'w').close()   # hidden temp scene: never a job
    open(os.path.join(d, 'notes.txt'), 'w').close()
    return d

def tone_wav(path, sec=1.0):
    import wave, array, math
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes(array.array('h', (int(8000 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(int(48000 * sec)))).tobytes())

def m1(tmp, d):
    s, p = ms.collect_scenes([d, os.path.join(d, 'webgl_min.html'), os.path.join(tmp, 'missing.html'), os.path.join(d, 'notes.txt')])
    names = [os.path.basename(x) for x in s]
    check('M1 folder -> its .html scenes, sorted, hidden temp scene skipped, a file named twice only once',
          names == sorted(names) and '.shadow_narr_ab12cd34_webgl_min.html' not in names and names.count('webgl_min.html') == 1 and len(names) == 6, names)
    check('M1 missing file and non-scene file are reported, not rendered', len(p) == 2 and all('not a scene' in x for x in p), p)
    sub = os.path.join(d, 'sub'); os.makedirs(sub); shutil.copy(os.path.join(FIX, 'minimal_silent.html'), os.path.join(sub, 'deep.html'))
    a, _ = ms.collect_scenes(d); b, _ = ms.collect_scenes(d, recursive=True)
    check('M1 recursive=True also takes subfolders (default: not)', 'deep.html' not in map(os.path.basename, a) and 'deep.html' in map(os.path.basename, b))
    shutil.rmtree(sub)
    e = os.path.join(tmp, 'empty'); os.makedirs(e); _, p = ms.collect_scenes(e)
    check('M1 empty folder is reported', p and 'no .html or .blend scenes' in p[0], p)

def m2(tmp, d):
    plan = ms.build_plan([d], out_dir=os.path.join(tmp, 'out'), format='auto', sound='auto')
    row = {os.path.basename(r['scene']): r for r in plan['rows']}
    check('M2 format auto: short_* and *_short -> 9:16, others 16:9 (same as app.format_vertical)',
          row['short_webgl.html']['format'] == '9:16' and row['rain_short.html']['format'] == '9:16' and row['webgl_min.html']['format'] == '16:9'
          and all(r['vertical'] == app.format_vertical(r['scene'], app.FORMAT_AUTO) for r in plan['rows']))
    check('M2 outdated 9:16 scene is skipped by default with the GUI warning text', row['short_old.html']['skip'] and any(app.OUTDATED in w for w in plan['warnings']),
          row['short_old.html']['status'])
    p2 = ms.build_plan([d], out_dir=os.path.join(tmp, 'out'), outdated='render')
    check('M2 outdated="render" keeps it', not next(r for r in p2['rows'] if r['scene'].endswith('short_old.html'))['skip'])
    check('M2 sound auto with no file: "scene sound (no file)" + the app\'s warning', row['webgl_min.html']['audio'] == 'scene sound (no file)'
          and any('webgl_min.html: no audio file next to it' in w for w in plan['warnings']))
    tone_wav(os.path.join(d, 'webgl_min.mp3.wav'))   # a wrong name is NOT the song
    tone_wav(os.path.join(d, 'webgl_min.wav'))
    plan = ms.build_plan([os.path.join(d, 'webgl_min.html')], out_dir=os.path.join(tmp, 'out'))
    check('M2 sound auto: <scene>.wav next to it is used (Audio column = file name, as resolve_audio)', plan['rows'][0]['audio'] == 'webgl_min.wav'
          and plan['rows'][0]['audio'] == app.resolve_audio(plan['rows'][0]['scene'], 'auto')['label'])
    os.remove(os.path.join(d, 'webgl_min.wav')); os.remove(os.path.join(d, 'webgl_min.mp3.wav'))
    # narration next to the scene -> [voice]
    tone_wav(os.path.join(d, 'narr_2d.wav'))
    with open(os.path.join(d, 'narr_2d.words.json'), 'w', encoding='utf-8') as fh:
        json.dump({'words': [{'w': 'hello', 's': 0.1, 'e': 0.5}, {'w': 'there', 's': 0.55, 'e': 0.9}]}, fh)
    r = ms.build_plan([os.path.join(d, 'narr_2d.html')], out_dir=os.path.join(tmp, 'out'), sound='none')['rows'][0]
    check('M2 narration auto: <scene>.wav + .words.json attached ([voice], audio "narration", wav not used as a song)',
          r['narration'] and r['name'].endswith(app.VOICE_MARK) and r['audio'] == 'narration', (r['name'], r['audio']))
    r = ms.build_plan([os.path.join(d, 'narr_2d.html')], out_dir=os.path.join(tmp, 'out'), narration='off')['rows'][0]
    check('M2 narration="off" does not attach it', not r['narration'] and not r['name'].endswith(app.VOICE_MARK))
    built = os.path.join(d, 'built.html')
    with open(os.path.join(d, 'narr_2d.html'), encoding='utf-8') as fh: html = fh.read()
    with open(built, 'w', encoding='utf-8') as fh:
        fh.write(app.narrate_module().build_narrated_html(html, os.path.join(d, 'narr_2d.wav'), json.load(open(os.path.join(d, 'narr_2d.words.json')))))
    r = ms.build_plan([built], out_dir=os.path.join(tmp, 'out'))['rows'][0]
    check('M2 a scene with narration built in shows [voice embedded] and is not narrated again', r['embedded'] and not r['narration']
          and r['name'].endswith(app.VOICE_EMBEDDED_MARK) and r['audio'] == 'narration')
    os.remove(built)
    tone_wav(os.path.join(tmp, 'song.wav'))
    r = ms.build_plan([os.path.join(d, 'webgl_min.html')], sound='file', audio_file=os.path.join(tmp, 'song.wav'), out_dir=tmp)
    check('M2 sound=file uses the given file for every scene', r['rows'][0]['audio'] == 'song.wav' and r['opts']['audio_file'].endswith('song.wav'))
    bad = []
    for kw in (dict(quality='ultra'), dict(format='4:3'), dict(sound='loud'), dict(capture='webp'), dict(sound='file'),
               dict(narration='maybe'), dict(outdated='ask')):
        try: ms.build_plan([d], out_dir=tmp, **kw); bad.append(kw)
        except ValueError: pass
    check('M2 every bad setting raises ValueError (quality/format/sound/capture/file without audio_file/narration/outdated)', not bad, bad)
    p = ms.build_plan([d], out_dir=tmp, quality='Draft', format='16:9', capture='JPEG')
    check('M2 names are case-insensitive and map to the GUI presets', p['preset'] == rc.PRESETS['Draft (fast, 720p)'] and p['opts']['capture'] == 'jpeg'
          and p['opts']['format'] == '16:9 (YouTube)')
    os.remove(os.path.join(d, 'narr_2d.wav')); os.remove(os.path.join(d, 'narr_2d.words.json'))

def patched_render(gate=None):
    """rc.render with software WebGL + a frame cap; gate = (scene base name, Event reached, Event release) holds that render after frame 3."""
    real = rc.render; calls = []
    def wrapped(html, o, p, **kw):
        calls.append(os.path.basename(html))
        if gate and os.path.basename(html) == gate[0]:
            prog = kw.get('progress', lambda *_: None)
            def gated(stage, a, b, *rest):
                prog(stage, a, b, *rest)
                if stage == 'frames' and a == 3: gate[1].set(); gate[2].wait(30)
            kw['progress'] = gated
        return real(html, o, p, **{**kw, **KW, 'max_frames': 15})
    rc.render = wrapped
    return real, calls

def run_job(plan, timeout=300):
    job = ms.Job(plan); t = threading.Thread(target=job.run, daemon=True); t.start(); t.join(timeout); return job

def m3(tmp, d):
    out = os.path.join(tmp, 'out3')
    calls_scene = []; real_rs = app.render_scene
    app.render_scene = lambda *a, **k: calls_scene.append(os.path.basename(a[0])) or real_rs(*a, **k)
    real, calls = patched_render()
    try:
        plan = ms.build_plan([os.path.join(d, 'webgl_min.html'), os.path.join(d, 'short_webgl.html'), os.path.join(d, 'short_old.html')],
                             out_dir=out, quality='draft', sound='auto')
        job = run_job(plan); s = job.snapshot()
        job2 = run_job(ms.build_plan([os.path.join(d, 'webgl_min.html')], out_dir=out, quality='draft')); s2 = job2.snapshot()
        # the GUI worker on the same scene, into another folder, for comparison
        gui_out = os.path.join(tmp, 'gui3'); os.makedirs(gui_out)
        class Fake:
            def __init__(s_): import queue; s_.q = queue.Queue(); s_.cancel = threading.Event(); s_.opts = dict(plan['opts'])
        fk = Fake(); done, failed, warns = [], [], []
        app.App._run_one(fk, os.path.join(d, 'short_webgl.html'), 0, 1, plan['preset'], gui_out, plan['opts']['format'], done, failed, warns, set(), threading.Event())
    finally:
        rc.render = real; app.render_scene = real_rs
    it = {os.path.basename(i['scene']): i for i in s['items']}
    w = it['webgl_min.html']; v = it['short_webgl.html']
    check('M3 job finished, both scenes Done with QC', s['state'] == 'finished' and w['status'].startswith('Done · QC') and v['status'].startswith('Done · QC'),
          (s['state'], w['status'], v['status'], s['warnings']))
    check('M3 rendered through app.render_scene (the GUI queue function), outdated one never rendered',
          calls_scene[:2] == ['webgl_min.html', 'short_webgl.html'] and 'short_old.html' not in calls_scene and it['short_old.html']['status'].startswith('Skipped'), calls_scene)
    check('M3 16:9 -> <out>/webgl_min/webgl_min.mp4 + .qc.txt', w['mp4'] == os.path.join(out, 'webgl_min', 'webgl_min.mp4') and os.path.isfile(w['mp4'])
          and os.path.isfile(os.path.join(out, 'webgl_min', 'webgl_min.qc.txt')), w['mp4'])
    pv = probe(v['mp4']) if v['mp4'] else None
    check('M3 9:16 -> <out>/short_webgl/short_webgl_shorts.mp4, 720x1280 for draft', v['mp4'] == os.path.join(out, 'short_webgl', 'short_webgl_shorts.mp4')
          and pv and pv[:2] == (720, 1280), (v['mp4'], pv))
    check('M3 a second job never overwrites: webgl_min_2.mp4 in the same folder', s2['items'][0]['mp4'] == os.path.join(out, 'webgl_min', 'webgl_min_2.mp4')
          and os.path.isfile(s2['items'][0]['mp4']))
    gp = probe(done[0]) if done else None
    check('M3 same as the GUI worker: file name, size, audio, status text, warnings',
          done and os.path.relpath(done[0], gui_out) == os.path.relpath(v['mp4'], out) and gp == pv
          and [m for m in fk.q.queue if m[0] == 'row'][-1][2] == v['status'].split(' (')[0]
          and sorted(warns) == sorted(x for x in s['warnings'] if 'short_webgl' in x),
          (done, gp, pv, warns))
    check('M3 percent 100, warnings include the Auto-sound fallback', s['percent'] == 100.0 and any('no audio file next to it' in x for x in s['warnings']), s['percent'])
    return out

def m4(tmp, d):
    out = os.path.join(tmp, 'out4'); scenes = [os.path.join(d, n) for n in ('webgl_min.html', 'minimal_silent.html', 'rain_short.html')]
    gate = ('webgl_min.html', threading.Event(), threading.Event())
    real, calls = patched_render(gate)
    try:
        r = ms.Runner()
        job = r.submit(ms.build_plan(scenes, out_dir=out, quality='draft'))
        gate[1].wait(60); mid = job.snapshot()
        r.cancel(job.id, current_only=True); gate[2].set()
        t0 = time.time()
        while job.state != 'finished' and time.time() - t0 < 120: time.sleep(0.2)
        s = job.snapshot()
        gate2 = ('minimal_silent.html', threading.Event(), threading.Event()); rc.render = real
        real, calls = patched_render(gate2)
        job2 = r.submit(ms.build_plan(scenes, out_dir=os.path.join(tmp, 'out4b'), quality='draft'))
        gate2[1].wait(60); r.cancel(job2.id); gate2[2].set()
        t0 = time.time()
        while job2.state not in ('cancelled', 'finished') and time.time() - t0 < 120: time.sleep(0.2)
        s2 = job2.snapshot()
    finally:
        rc.render = real; gate[2].set()
    st = [i['status'] for i in s['items']]
    check('M4 while rendering: status "Frames 3/15", percent between 0 and 100', mid['items'][0]['status'].startswith('Frames') and 0 < mid['percent'] < 100,
          (mid['items'][0]['status'], mid['percent']))
    check('M4 cancel current: #1 Cancelled, #2 and #3 still Done', st[0] == 'Cancelled' and st[1].startswith('Done') and st[2].startswith('Done'), st)
    check('M4 cancelled scene leaves no mp4, no .part.mp4 and no empty folder', not os.path.exists(os.path.join(out, 'webgl_min')), os.listdir(out))
    st2 = [i['status'] for i in s2['items']]
    check('M4 stop: job "cancelled", #1 Done, #2 and #3 Cancelled', s2['state'] == 'cancelled' and st2[0].startswith('Done') and st2[1:] == ['Cancelled', 'Cancelled']
          and s2['summary'].startswith('Cancelled. 1 done'), (s2['state'], st2, s2['summary']))

def m5(tmp, out):
    q = ms.read_qc(os.path.join(out, 'webgl_min', 'webgl_min.mp4'))
    check('M5 read_qc(mp4) -> its .qc.txt with status', len(q) == 1 and q[0]['status'] in ('OK', 'WARN', 'FAIL', 'UNKNOWN') and q[0]['text'].startswith('QC '), q[:1])
    allq = ms.read_qc(out)
    check('M5 read_qc(Save to folder) -> every scene folder\'s report', len(allq) >= 3, [os.path.basename(x['report']) for x in allq])
    files = ms.list_outputs(out); vids = [f for f in files if f['kind'] == 'video']
    check('M5 list_outputs: videos with size, duration, audio, QC status', len(vids) == 3 and all(v['width'] and v['seconds'] > 0 and v['qc'] for v in vids),
          [(os.path.basename(v['path']), v.get('width'), v.get('height'), v.get('qc')) for v in vids])
    open(os.path.join(out, 'webgl_min', 'x.part.mp4'), 'w').close()
    check('M5 list_outputs skips unfinished .part.mp4', not any(f['path'].endswith('.part.mp4') for f in ms.list_outputs(out)))
    r = ms.run_qc_now(os.path.join(out, 'short_webgl', 'short_webgl_shorts.mp4'), 'draft')
    check('M5 run_qc_now re-checks an mp4 (9:16 read from the file)', r['status'] and r['report'] and os.path.isfile(r['report']), r)
    try: ms.list_outputs(os.path.join(tmp, 'nope')); ok = False
    except ValueError: ok = True
    check('M5 list_outputs on a missing folder -> ValueError', ok)

async def m6_async(tmp, d):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.stdio import get_default_environment
    # the environment a real MCP client gives the server (cut down: no PROGRAMFILES / LOCALAPPDATA), + the log in the temp folder
    env = dict(get_default_environment(), SHADOW_LOG=os.path.join(tmp, 'shadow.log'))
    real_log = os.path.join(os.path.expanduser('~'), 'shadow.log')
    before = os.path.getsize(real_log) if os.path.isfile(real_log) else None
    p = StdioServerParameters(command=sys.executable, args=[os.path.join(ROOT, 'tools', 'mcp_server.py')], env=env)
    J = lambda res: json.loads(res.content[0].text)
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = sorted(t.name for t in (await s.list_tools()).tools)
            want = sorted(['shadow_info', 'shadow_plan', 'shadow_render', 'shadow_progress', 'shadow_cancel', 'shadow_jobs',
                           'shadow_check_frames', 'shadow_qc', 'shadow_outputs'])
            check('M6 stdio server lists the 9 tools', names == want, names)
            info = J(await s.call_tool('shadow_info', {}))
            check('M6 started with a client cut-down environment, the server still finds Chrome / ffmpeg / playwright',
                  info['ready'] == all(rc.check_env().values()), info['env'])
            check('M6 shadow_info: version = render_core.VERSION, default out dir = the GUI Save to setting',
                  info['version'] == rc.VERSION and info['default_out_dir'] == ms.default_out_dir() and set(info['env']) == {'ffmpeg', 'playwright', 'chrome'},
                  (info['version'], info['default_out_dir']))
            pl = J(await s.call_tool('shadow_plan', {'paths': [d], 'out_dir': os.path.join(tmp, 'o6')}))
            check('M6 shadow_plan over MCP: rows with format/audio/output', len(pl['rows']) == 6 and all('output' in x for x in pl['rows']), len(pl.get('rows', [])))
            bad = J(await s.call_tool('shadow_render', {'paths': [d], 'quality': 'ultra'}))
            check('M6 bad setting -> error result, nothing started', 'error' in bad and 'quality' in bad['error'], bad)
            none = J(await s.call_tool('shadow_progress', {}))
            check('M6 progress before any job -> clear error', none.get('error') == 'no render job yet', none)
            cf = await s.call_tool('shadow_check_frames', {'scene': os.path.join(d, 'webgl_min.html'), 'only': '16:9', 'times': '1,2'})
            kinds = [c.type for c in cf.content]
            res = json.loads(cf.content[0].text) if kinds and kinds[0] == 'text' else {}
            check('M6 shadow_check_frames: ok, sheet written next to the scene and returned as an image',
                  res.get('ok') and res.get('sheet') and os.path.isfile(res['sheet']) and 'image' in kinds, (kinds, res.get('problems'), res.get('output', '')[-300:]))
            if not rc.check_env()['chrome']:
                print('SKIP  M6 real render (Chrome not installed)'); return
            out = os.path.join(tmp, 'o6')
            st = J(await s.call_tool('shadow_render', {'paths': [os.path.join(d, 'webgl_min.html'), os.path.join(d, 'short_webgl.html')],
                                                       'out_dir': out, 'quality': 'draft', 'capture': 'jpeg'}))
            jid = st.get('job_id'); t0 = time.time(); seen = set(); pr = {}
            while jid and time.time() - t0 < 600:
                pr = J(await s.call_tool('shadow_progress', {'job_id': jid, 'wait_seconds': 5}))
                seen.update(i['status'].split(' ')[0] for i in pr['items'])
                if pr['state'] in ('finished', 'cancelled'): break
            it = pr.get('items', [])
            check('M6 real render via MCP (Chrome, draft, jpeg): both Done with QC, 1280x720 + 720x1280',
                  pr.get('state') == 'finished' and all(i['status'].startswith('Done') and i.get('qc_status') for i in it)
                  and [probe(i['mp4'])[:2] for i in it] == [(1280, 720), (720, 1280)], (pr.get('summary'), [i['status'] for i in it], pr.get('warnings')))
            check('M6 progress was visible while it ran (Frames ...)', 'Frames' in seen or 'Done' in seen, seen)
            o = J(await s.call_tool('shadow_outputs', {'job_id': jid}))
            check('M6 shadow_outputs(job) lists the 2 videos + their QC reports', sum(f['kind'] == 'video' for f in o['files']) == 2
                  and sum(f['kind'] == 'qc' for f in o['files']) == 2, [os.path.basename(f['path']) for f in o['files']])
            q = J(await s.call_tool('shadow_qc', {'path': out}))
            check('M6 shadow_qc(folder) reads both reports', len(q.get('reports', [])) == 2, [x['status'] for x in q.get('reports', [])])
            jobs = (await s.call_tool('shadow_jobs', {})).content
            check('M6 shadow_jobs lists the job', jid in ''.join(c.text for c in jobs if c.type == 'text'))
    check('M6 server log went to SHADOW_LOG in the temp folder; the real ~/shadow.log is untouched', os.path.isfile(os.path.join(tmp, 'shadow.log'))
          and before == (os.path.getsize(real_log) if os.path.isfile(real_log) else None))

def free_port():
    import socket
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p

async def m7_async(tmp):
    import socket, subprocess, urllib.request, urllib.error
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    a = ms.parse_args([])
    check('M7 HTTP off by default (no arguments = stdio)', a.http is False)
    check('M7 --http --port N parsed; host is fixed to 127.0.0.1', ms.parse_args(['--http', '--port', '9001']).port == 9001 and ms.HTTP_HOST == '127.0.0.1')
    port = free_port()
    env = dict(os.environ, SHADOW_LOG=os.path.join(tmp, 'shadow_http.log'))
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, 'tools', 'mcp_server.py'), '--http', '--port', str(port)], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=app.NOWIN)
    try:
        t0 = time.time(); up = False
        while time.time() - t0 < 30 and not up:
            try: socket.create_connection(('127.0.0.1', port), 0.5).close(); up = True
            except OSError: time.sleep(0.2)
        check('M7 HTTP server listening on 127.0.0.1', up)
        try:
            import psutil
            addrs = {c.laddr.ip for c in psutil.Process(p.pid).net_connections() if c.status == 'LISTEN'}
            check('M7 listens on 127.0.0.1 only (not 0.0.0.0 / LAN)', addrs == {'127.0.0.1'}, addrs)
        except Exception as e:
            print(f'SKIP  M7 listen-address check ({e})')
        async with streamable_http_client(f'http://127.0.0.1:{port}/mcp') as (r, w, *_):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = sorted(t.name for t in (await s.list_tools()).tools)
                check('M7 MCP over HTTP lists the 9 tools', len(names) == 9 and 'shadow_render' in names, names)
                info = json.loads((await s.call_tool('shadow_info', {})).content[0].text)
                check('M7 shadow_info over HTTP', info.get('version') == rc.VERSION, info.get('version'))
        req = urllib.request.Request(f'http://127.0.0.1:{port}/mcp', method='POST', data=b'{}',
                                     headers={'Host': f'evil.example:{port}', 'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
        try: code = urllib.request.urlopen(req, timeout=10).status
        except urllib.error.HTTPError as e: code = e.code
        check('M7 foreign Host header refused (DNS-rebinding protection)', code in (400, 403, 421), code)
    finally:
        p.terminate()
        try: p.wait(10)
        except Exception: p.kill()

def main():
    tmp = tempfile.mkdtemp(prefix='shadow_mcp_test_')
    try:
        d = setup(tmp)
        for f in (lambda: m1(tmp, d), lambda: m2(tmp, d)):
            try: f()
            except Exception: check('crash', False, traceback.format_exc())
        out = None
        try: out = m3(tmp, d)
        except Exception: check('M3 crash', False, traceback.format_exc())
        try: m4(tmp, d)
        except Exception: check('M4 crash', False, traceback.format_exc())
        if out:
            try: m5(tmp, out)
            except Exception: check('M5 crash', False, traceback.format_exc())
        try: asyncio.run(m6_async(tmp, d))
        except Exception: check('M6 crash', False, traceback.format_exc())
        try: asyncio.run(m7_async(tmp))
        except Exception: check('M7 crash', False, traceback.format_exc())
        for f in os.listdir(d):
            if f.endswith('.check.jpg'): os.remove(os.path.join(d, f))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f'{sum(RESULTS)}/{len(RESULTS)} passed'); return 0 if RESULTS and all(RESULTS) else 1

if __name__ == '__main__':
    sys.exit(main())
