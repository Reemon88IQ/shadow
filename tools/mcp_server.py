"""Shadow MCP server: lets Claude (Claude Code, Claude Desktop, any MCP client) render Shadow scenes directly.

Local and free: stdio transport (optional HTTP on 127.0.0.1 only), no API keys. It does NOT need the Shadow window - it imports app.py /
render_core.py and renders with the same code as the GUI queue (app.render_scene -> render_item -> render_core.render,
resolve_audio, format_vertical, quality_check), so the files, folders, QC reports and warnings are the ones Shadow makes.

Run:   python tools/mcp_server.py            (an MCP client starts it; it talks MCP on stdin/stdout)
       python tools/mcp_server.py --http [--port 8765]   optional: streamable HTTP at http://127.0.0.1:8765/mcp, for clients
                                                         that take a URL. Localhost only, off unless started like this.
Test:  python tests/test_mcp_server.py
Tools: shadow_info, shadow_plan, shadow_render, shadow_progress, shadow_cancel, shadow_jobs, shadow_check_frames,
       shadow_qc, shadow_outputs; YouTube: shadow_youtube, shadow_upload_preview, shadow_upload, shadow_upload_progress,
       shadow_upload_cancel (youtube.py; preview first, upload after the owner's yes). Renders run one at a time in a background thread (jobs queue up); shadow_render
       returns at once with a job id, shadow_progress(wait_seconds=...) follows it.
Logs go to ~/shadow.log (the same log as the GUI). Nothing is ever printed to stdout (that is the MCP channel)."""
import asyncio, glob, json, os, subprocess, sys, threading, time, uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
import render_core as rc   # noqa: E402
import blender_core as bc  # noqa: E402
import app                 # noqa: E402  (no window is created by importing it)
import qc                  # noqa: E402

CHECK_FRAMES = os.path.join(ROOT, 'tools', 'check_frames.py')
QUALITY = {'draft': 'Draft (fast, 720p)', 'good': 'Good (1080p, 24 samples)', 'best': 'Best (1080p, 64 samples)',
           '1440p': '1440p (QHD, 64 samples)', '4k': '4K (UHD, 64 samples)'}
FORMAT = {'auto': app.FORMAT_AUTO, '16:9': '16:9 (YouTube)', '9:16': '9:16 (Shorts)'}
SOUND = ('auto', 'generated', 'none', 'file')
CAPTURE = ('png', 'jpeg')
ENGINE = tuple(bc.ENGINES)   # .blend only: scene | eevee | cycles | workbench
DEVICE = bc.DEVICES          # .blend + Cycles only: auto | gpu | cpu
STAGE_TEXT = {'load': 'Loading scene', 'audio': 'Rendering sound', 'frames': 'Frames', 'encode': 'Encoding MP4', 'done': 'Checking output'}
STAGE_FRAC = lambda stage, a, b: {'load': 0.0, 'audio': 0.02, 'frames': 0.03 + 0.92 * a / max(b, 1), 'encode': 0.96, 'done': 1.0}[stage]   # as the GUI bar
log = app.log


# ---------------------------------------------------------------- planning (pure apart from reading files)
def default_out_dir():
    """The GUI's "Save to" folder (~/.shadow_renderer.json 'out'), else ~/Videos - like Shadow's first start."""
    return app.load_cfg().get('out') or os.path.join(os.path.expanduser('~'), 'Videos')

collect_scenes = app.collect_scenes   # files + folders -> scenes (the GUI's "Add folder..." uses the same function)

def _norm(name, table, what):
    k = str(name or '').strip().lower()
    if k not in table: raise ValueError(f'{what} must be one of {", ".join(table)}, got {name!r}')
    return k

def folder_choice(folder):
    """MCP 'folder' -> the GUI's Folder value: auto -> FOLDER_AUTO, none -> FOLDER_NONE, a name -> that name (made safe)."""
    k = str(folder or 'auto').strip()
    if k.lower() == 'auto': return app.FOLDER_AUTO
    if k.lower() == 'none': return app.FOLDER_NONE
    name = app.safe_rel_folder(k)
    if not name: raise ValueError(f"folder must be 'auto', 'none' or a folder name, got {folder!r}")
    return name

def plan_scene(f, fmt_label, sound, audio_file=None, narration='auto', folder_choice=None):
    """What one scene will do, as the GUI table shows it: Format column, Audio column, [voice] / [voice embedded],
    the outdated-9:16 check. narration: 'auto' (attach <scene>.wav + <scene>.words.json next to it, like the GUI) or 'off'."""
    embedded = False; blend = bc.is_blend(f)
    try:
        if not blend:
            with open(f, encoding='utf-8', errors='replace') as fh: embedded = app.narrate_module().is_narrated(fh.read())
    except Exception: pass
    narr, narr_note = None, None
    if narration == 'auto' and not embedded and not blend:
        base = os.path.splitext(f)[0]; wav, words = base + '.wav', base + '.words.json'
        if os.path.isfile(wav) and os.path.isfile(words):
            err = app.narration_problem(f, wav, words, 'pop')
            if err is None: narr = dict(wav=wav, words=words, style='pop', yh=0.88, gain=1.0, captions=True)
            else: narr_note = f'narration files found but not used: {err}'
    vertical = app.format_vertical(f, fmt_label)
    narrated = bool(narr) or (embedded and sound in ('auto', 'generated'))
    ra = app.resolve_audio(f, sound, audio_file, narrated=narrated)
    outdated = vertical and not app.scene_info(f)['vertical']
    mark = f' {app.VOICE_EMBEDDED_MARK}' if embedded else f' {app.VOICE_MARK}' if narr else ''
    return dict(scene=f, name=os.path.basename(f) + mark, format=app.format_label(vertical), vertical=vertical,
                audio=ra['label'], audio_warn=ra['warn'], narration=narr, narration_note=narr_note, embedded=embedded,
                outdated=outdated, scene_check=[] if blend else rc.contract_problems(f),
                main=(main := app.category_folder(f, folder_choice, sound, audio_file, narrated=narrated)),
                folder=app.output_place(f, vertical, main)[0], kind='blender' if blend else 'html')

def build_plan(paths, out_dir=None, quality='best', format='auto', sound='auto', audio_file=None, capture='png',
               narration='auto', outdated='skip', recursive=False, engine='scene', device='auto', skip_up_to_date=False, folder='auto'):
    """Check the settings and work out every scene's row. Raises ValueError for a bad setting. Pure apart from reading.
    skip_up_to_date: a scene whose <out>/<scene>/<scene>.mp4 is newer than the scene and its audio/narration (and has the
    preset's size) is not rendered again (status 'Skipped (up to date)', like the GUI option)."""
    q = _norm(quality, QUALITY, 'quality'); fm = _norm(format, FORMAT, 'format')
    snd = _norm(sound, SOUND, 'sound'); cap = _norm(capture, CAPTURE, 'capture')
    eng = _norm(engine, ENGINE, 'engine'); dev = _norm(device, DEVICE, 'device')
    if narration not in ('auto', 'off'): raise ValueError(f"narration must be 'auto' or 'off', got {narration!r}")
    if outdated not in ('skip', 'render'): raise ValueError(f"outdated must be 'skip' or 'render', got {outdated!r}")
    if snd == 'file':
        if not audio_file or not os.path.isfile(audio_file): raise ValueError(f'sound=file needs an existing audio_file, got {audio_file!r}')
        audio_file = os.path.abspath(audio_file)
    scenes, problems = collect_scenes(paths, recursive)
    out_dir = os.path.abspath(os.path.expanduser(out_dir)) if out_dir else default_out_dir()
    fc = folder_choice(folder)
    rows = [plan_scene(f, FORMAT[fm], snd, audio_file, narration, fc) for f in scenes]
    preset = rc.PRESETS[QUALITY[q]]
    for r in rows:
        r['skip'] = bool(r['outdated'] and outdated == 'skip')
        r['status'] = f'Skipped ({app.OUTDATED.lower()})' if r['skip'] else 'Queued'
        r['up_to_date'] = None
        if skip_up_to_date and not r['skip']:
            r['up_to_date'] = app.up_to_date(r['scene'], out_dir, r['vertical'], app.render_deps(r['scene'], dict(sound=snd, audio_file=audio_file), r['narration']),
                                             expect=app.expected_size(preset, r['vertical']), main=r['main'])
            if r['up_to_date']: r['skip'] = True; r['status'] = app.SKIPPED_UP_TO_DATE
        r['estimate_s'] = None if r['skip'] else app.estimate_seconds(app.scene_duration_guess(r['scene'], app.find_scene_audio(r['scene']) if snd == 'auto' else audio_file, r['narration']), preset, cap)
    warns = [r['audio_warn'] for r in rows if r['audio_warn'] and not r['skip']]
    warns += [f"{os.path.basename(r['scene'])}: {r['narration_note']}" for r in rows if r['narration_note']]
    warns += [f"{os.path.basename(r['scene'])} will probably fail: {'; '.join(r['scene_check'])}" for r in rows if r['scene_check'] and not r['skip']]
    if any(r['outdated'] for r in rows):
        warns.append(f"{app.OUTDATED}: " + ', '.join(os.path.basename(r['scene']) for r in rows if r['outdated'])
                     + (' (skipped; outdated="render" renders them anyway)' if outdated == 'skip' else ' (rendered anyway)'))
    opts = dict(vertical=app.format_vertical('', FORMAT[fm]), format=FORMAT[fm], sound=snd, audio_file=audio_file, capture=cap,
                engine=eng, device=dev, skip_done=bool(skip_up_to_date), folder=fc)   # engine/device: .blend scenes only (app.render_item drops them for HTML)
    known = [r['estimate_s'] for r in rows if not r['skip'] and r['estimate_s']]
    return dict(estimate_s=round(sum(known)) if known else None, not_estimated=sum(1 for r in rows if not r['skip'] and not r['estimate_s']),
                skip_up_to_date=bool(skip_up_to_date), out_dir=out_dir, quality=q, preset_name=QUALITY[q], preset=preset, format=fm, sound=snd, capture=cap,
                audio_file=audio_file, narration=narration, outdated=outdated, folder=fc, engine=eng, device=dev, opts=opts, rows=rows, problems=problems, warnings=warns)


# ---------------------------------------------------------------- jobs (one render at a time, like the GUI queue)
class Job:
    def __init__(self, plan):
        self.id = uuid.uuid4().hex[:8]; self.plan = plan; self.created = time.time()
        self.state = 'queued'   # queued -> running -> finished | cancelled
        self.cancel = threading.Event(); self.item_cancel = threading.Event(); self.lock = threading.Lock()
        self.items = [dict(scene=r['scene'], name=r['name'], format=r['format'], audio=r['audio'], status=r['status'],
                           skip=r['skip'], narration=r['narration'], embedded=r['embedded'], stage=None, frames=None,
                           eta=None, frac=0.0, result=None, estimate_s=r.get('estimate_s'), up_to_date=r.get('up_to_date')) for r in plan['rows']]
        self.warnings = list(plan['warnings']); self.started = self.ended = None; self.current = None; self.spf = None

    def run(self):
        with self.lock: self.state = 'running'; self.started = time.time()
        used = set()
        try:
            os.makedirs(self.plan['out_dir'], exist_ok=True)
        except OSError as e:
            with self.lock:
                self.warnings.append(f"Save to: {e}"); self.state = 'finished'; self.ended = time.time()
                for it in self.items:
                    if not it['skip']: it['status'] = 'Error'
            return
        for i, it in enumerate(self.items):
            if it['skip']: continue
            with self.lock:
                if self.cancel.is_set(): it['status'] = 'Cancelled'; continue
                self.current = i; ev = self.item_cancel = threading.Event()
            def prog(stage, a, b, eta=None, it=it):
                with self.lock:
                    it['stage'] = stage; it['frac'] = STAGE_FRAC(stage, a, b); it['eta'] = eta
                    if stage == 'frames':
                        it['frames'] = [a, b]
                        if eta is not None and b > a and a >= 3: self.spf = eta / (b - a)   # measured: corrects the estimate of the rest
                    it['status'] = STAGE_TEXT[stage] + (f' {a}/{b}' if stage == 'frames' else '')
            warns = []
            r = app.render_scene(it['scene'], self.plan['preset'], self.plan['out_dir'], self.plan['opts'], narr=it['narration'],
                                 cancel=ev, progress=prog, used=used, embedded=it['embedded'], warns=warns)
            with self.lock:
                it['status'] = r['row'] + (f" ({r['dur']})" if r['dur'] else ''); it['frac'] = 1.0
                it['result'] = {k: r[k] for k in ('mp4', 'dur', 'error', 'log', 'qc_report', 'thumb', 'srt', 'sound', 'vertical')}
                it['result']['result'] = r['status']   # done | cancelled | error ('status' stays the GUI's Status column text)
                it['result']['qc_status'] = (r['qc'] or {}).get('status'); it['result']['qc_reasons'] = (r['qc'] or {}).get('reasons')
                if r['status'] == 'cancelled': it['result']['mp4'] = None
                self.warnings += [w for w in warns if w not in self.warnings]; self.current = None   # the plan already holds the Auto-sound ones
        with self.lock:
            self.state = 'cancelled' if self.cancel.is_set() else 'finished'; self.ended = time.time()

    def snapshot(self):
        with self.lock:
            todo = [it for it in self.items if not it['skip']]
            pct = 100 * sum(it['frac'] for it in todo) / len(todo) if todo else 100.0
            cur = self.items[self.current] if self.current is not None else None
            items = []
            for it in self.items:
                d = {k: it[k] for k in ('scene', 'name', 'format', 'audio', 'status', 'frames')}
                if it['estimate_s'] and not it['result']: d['estimate_s'] = round(it['estimate_s'])
                if it['up_to_date']: d['mp4'] = it['up_to_date']
                if it['eta'] is not None and it['stage'] == 'frames' and it['result'] is None: d['eta_s'] = round(it['eta'])
                if it['result']: d.update(it['result'])
                items.append(d)
            # time left for the whole job: the running scene's own ETA + the queued scenes (estimated, with this job's measured speed)
            left, unknown = 0.0, 0
            for it in self.items:
                if it['skip'] or it['result']: continue
                if cur is it and it['eta'] is not None and it['stage'] == 'frames': left += it['eta'] + 8; continue
                est = it['estimate_s']
                if est and self.spf is not None and not bc.is_blend(it['scene']):
                    est = (est - app.ITEM_OVERHEAD_S) / (app.SEC_PER_FRAME.get(self.plan['capture'], app.SEC_PER_FRAME['png']) * (self.plan['preset']['w'] / 1920) ** 2) * self.spf + app.ITEM_OVERHEAD_S
                if est: left += est
                else: unknown += 1
            done = [it for it in self.items if it['result'] and it['result']['result'] == 'done']
            failed = [it for it in self.items if it['result'] and it['result']['result'] == 'error']
            skipped = [it for it in self.items if it['up_to_date'] or (it['result'] and it['result']['result'] == 'skipped')]
            summary = (('Cancelled. ' if self.state == 'cancelled' else '') + f'{len(done)} done' + (f', {len(failed)} failed' if failed else '')
                       + (f', {len(skipped)} skipped (up to date)' if skipped else '')
                       if self.state in ('finished', 'cancelled') else
                       f"[{(self.current or 0) + 1}/{len(self.items)}] {os.path.basename(cur['scene'])}: {cur['status']}" if cur else self.state)
            return dict(job_id=self.id, state=self.state, percent=round(pct, 1), summary=summary, out_dir=self.plan['out_dir'],
                        quality=self.plan['quality'], format=self.plan['format'], sound=self.plan['sound'], capture=self.plan['capture'],
                        elapsed_s=round((self.ended or time.time()) - self.started) if self.started else 0,
                        **(dict(eta_s=round(left), eta=app.eta_text(left, unknown=unknown)) if self.state in ('queued', 'running') else {}),
                        items=items, warnings=list(self.warnings))


class Runner:
    """Jobs run one after another on one background thread (two renders at once would only fight over the GPU)."""
    def __init__(self):
        self.jobs = {}; self.order = []; self.lock = threading.Lock(); self.wake = threading.Condition(self.lock)
        self.thread = threading.Thread(target=self._loop, daemon=True, name='shadow-mcp-runner'); self.thread.start()

    def submit(self, plan):
        job = Job(plan)
        with self.wake:
            self.jobs[job.id] = job; self.order.append(job.id); self.wake.notify()
        log.info('mcp job %s: %d scene(s) -> %s', job.id, len(job.items), plan['out_dir'])
        return job

    def get(self, job_id=None):
        with self.lock:
            if job_id: return self.jobs.get(job_id)
            return self.jobs[self.order[-1]] if self.order else None

    def _loop(self):
        while True:
            with self.wake:
                job = next((self.jobs[i] for i in self.order if self.jobs[i].state == 'queued'), None)
                if job is None: self.wake.wait(); continue
            try: job.run()
            except Exception:
                log.exception('mcp job %s crashed', job.id)
                with job.lock: job.state = 'finished'; job.ended = time.time(); job.warnings.append('internal error, see ~/shadow.log')

    def cancel(self, job_id, current_only=False):
        job = self.get(job_id)
        if not job: return None
        with job.lock:
            if current_only: job.item_cancel.set()
            else:
                job.cancel.set(); job.item_cancel.set()
                if job.state == 'queued': job.state = 'cancelled'; job.ended = time.time()
                for it in job.items:
                    if it['status'] == 'Queued': it['status'] = 'Cancelled'
        return job

    def wait(self, job, seconds):
        """Block up to `seconds` until the job ends (so a client does not poll every second)."""
        end = time.time() + max(0.0, min(float(seconds or 0), 55.0))
        while time.time() < end and job.state in ('queued', 'running'): time.sleep(0.25)


# ---------------------------------------------------------------- YouTube uploads (youtube.py; one at a time, background thread)
import youtube as yt   # noqa: E402

def upload_settings(video, channel='', privacy='private', ai='auto', thumbnail='auto', publish='auto', meta='', scene=''):
    """The MCP choices -> youtube.prepare() arguments, then prepare(). -> dict(view for the client, args for upload_video).
    ai auto|yes|no; thumbnail auto|none|<path>; publish auto (PUBLISH: in metadata.txt) | none | a date+time text;
    meta: a metadata.txt path (default: next to the video, else next to `scene`). Raises ValueError / yt.YouTubeError."""
    video = os.path.abspath(os.path.expanduser(video or ''))
    if not video.lower().endswith('.mp4'): raise ValueError(f'video must be an .mp4 file, got {video!r}')
    if privacy not in yt.PRIVACY: raise ValueError(f'privacy must be one of {", ".join(yt.PRIVACY)}')
    ai = (ai or 'auto').lower(); thumbnail = thumbnail or 'auto'; publish = (publish or 'auto').strip()
    if ai not in ('auto', 'yes', 'no'): raise ValueError("ai must be 'auto', 'yes' or 'no'")
    m = mp = None
    if meta:
        mp = os.path.abspath(os.path.expanduser(meta))
        if not os.path.isfile(mp): raise ValueError(f'metadata file not found: {mp}')
        with open(mp, encoding='utf-8-sig') as f: d = yt.parse_metadata(f.read())
        m = d.get('short' if video.lower().endswith('_shorts.mp4') else 'full') or d.get('full')
    ov = dict(ai={'yes': True, 'no': False}.get(ai))
    schedule = publish.lower() != 'none'
    if publish.lower() not in ('auto', 'none'):
        ov['publish_at'] = yt.parse_publish(publish)
        if not ov['publish_at']: raise ValueError(f'publish: no date and time in {publish!r} (e.g. "2026-11-25 05:00 Sydney")')
    thumb = False if thumbnail.lower() == 'none' else True if thumbnail.lower() == 'auto' else os.path.abspath(os.path.expanduser(thumbnail))
    cands = [os.path.dirname(os.path.abspath(scene))] if scene else []
    if m is None and not meta: m, mp = yt.metadata_for(video, cands)
    ch, body, img, notes = yt.prepare(video, channel or None, m if m is not None else {}, privacy, (), ov, mp, thumb, schedule)
    s, st = body['snippet'], body['status']
    view = dict(video=video, size_mb=round(os.path.getsize(video) / 1048576, 1), channel=ch['title'], metadata=mp, title=s['title'],
                description=s['description'][:300] + ('...' if len(s['description']) > 300 else ''), tags=s['tags'], category=s['categoryId'],
                privacy=st['privacyStatus'], made_for_kids=st['selfDeclaredMadeForKids'], ai_label=st.get('containsSyntheticMedia'),
                publish_at_utc=st.get('publishAt'), thumbnail=img, notes=notes)
    args = dict(mp4=video, channel=ch['id'], meta=m if m is not None else {}, privacy=privacy, overrides=ov, meta_path=mp,
                thumbnail=img or False, schedule=schedule)
    return dict(view=view, args=args)

class Upload:
    def __init__(self, view, args):
        self.id = uuid.uuid4().hex[:8]; self.view = view; self.args = args; self.state = 'running'
        self.done = 0; self.total = os.path.getsize(args['mp4']); self.result = None; self.error = None
        self.cancel = threading.Event(); self.started = time.time(); self.ended = None
        self.thread = threading.Thread(target=self._run, daemon=True, name=f'shadow-upload-{self.id}'); self.thread.start()

    def _run(self):
        def prog(d, t): self.done, self.total = d, t
        try:
            self.result = yt.upload_video(progress=prog, cancel=self.cancel, **self.args)
            self.state = 'done'; log.info('mcp upload %s -> %s %s', self.args['mp4'], self.result.get('channel'), self.result.get('url'))
        except Exception as e:
            self.state = 'cancelled' if self.cancel.is_set() else 'error'; self.error = str(e)
            log.warning('mcp upload %s: %s', self.args['mp4'], e)
        self.ended = time.time()

    def snapshot(self):
        out = dict(upload_id=self.id, state=self.state, percent=round(self.done * 100 / max(self.total, 1)), video=self.view['video'],
                   channel=self.view['channel'], title=self.view['title'], seconds=round((self.ended or time.time()) - self.started))
        if self.result:
            r = self.result
            out.update(url=r['url'], studio=r['studio'], privacy=r.get('privacy'), scheduled_utc=r.get('publish_at'), thumbnail=r.get('thumb'))
        if self.error: out['error'] = self.error
        return out

UPLOADS = {}; UPLOAD_ORDER = []
def get_upload(upload_id=''):
    if upload_id: return UPLOADS.get(upload_id)
    return UPLOADS[UPLOAD_ORDER[-1]] if UPLOAD_ORDER else None


# ---------------------------------------------------------------- video + song (3.18): level 2 = Claude looks, then decides
import video_song as vsong   # noqa: E402

def video_song_inputs(video, song=''):
    """-> (video, song) as absolute paths; song '' = <video>.mp3/.wav/... next to it (Sound Auto). Raises ValueError."""
    video = os.path.abspath(os.path.expanduser(video or ''))
    if not vsong.is_video(video) or not os.path.isfile(video): raise ValueError(f'not a video file: {video!r} ({", ".join(vsong.VIDEO_EXTS)})')
    song = os.path.abspath(os.path.expanduser(song)) if song else app.find_scene_audio(video)
    if not song or not os.path.isfile(song):
        raise ValueError(f'no song: put {os.path.splitext(os.path.basename(video))[0]}.mp3 next to the video, or pass song=<file>')
    return video, song

class VideoSongJob:
    """One video + song render in a background thread, through app.render_scene (folders, QC, thumbnail as in the queue)."""
    def __init__(self, video, song, out_dir, folder, overrides):
        self.id = uuid.uuid4().hex[:8]; self.video, self.song, self.out_dir = video, song, out_dir
        self.state = 'running'; self.stage = 'load'; self.result = None; self.started = time.time(); self.ended = None
        self.cancel = threading.Event(); self.warns = []
        opts = dict(vertical=False, format=app.FORMAT_AUTO, sound='file', audio_file=song, capture='png', folder=folder, **overrides)
        self.thread = threading.Thread(target=self._run, args=(opts,), daemon=True); self.thread.start()
    def _run(self, opts):
        def prog(stage, *a): self.stage = stage
        try:
            self.result = app.render_scene(self.video, rc.PRESETS[QUALITY['best']], self.out_dir, opts, cancel=self.cancel, progress=prog, warns=self.warns)
            self.state = {'done': 'finished', 'cancelled': 'cancelled'}.get(self.result['status'], 'error')
        except Exception as e:
            self.result = dict(status='error', error=str(e)); self.state = 'error'
        self.ended = time.time()
    def snapshot(self):
        r = self.result or {}
        out = dict(job_id=self.id, state=self.state, stage=self.stage, video=self.video, song=self.song,
                   seconds=round((self.ended or time.time()) - self.started, 1))
        if r: out.update(status=r.get('row'), mp4=r.get('mp4'), qc=(r.get('qc') or {}).get('status'), qc_report=r.get('qc_report'),
                         thumbnail=r.get('thumb'), error=r.get('error'), warnings=self.warns)
        return out
VIDEO_JOBS = {}; VIDEO_ORDER = []


RUNNER = None
def runner():
    global RUNNER
    if RUNNER is None: RUNNER = Runner()
    return RUNNER


# ---------------------------------------------------------------- checks, QC, outputs
def check_frames(scene, only=None, times=None, out=None, timeout=600, engine='scene'):
    """tools/check_frames.py as a subprocess (its own Chrome): contact sheet + page errors + seek(t) purity."""
    scene = os.path.abspath(os.path.expanduser(scene))
    if not os.path.isfile(scene): raise ValueError(f'scene not found: {scene}')
    if only not in (None, '', '16:9', '9:16'): raise ValueError("only must be '16:9', '9:16' or empty")
    cmd = [sys.executable, CHECK_FRAMES, scene] + (['--only', only] if only else []) + (['--times', times] if times else []) + (['--out', out] if out else []) + (['--engine', engine] if engine and engine != 'scene' else [])
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, creationflags=app.NOWIN)
    text = (r.stdout + ('\n' + r.stderr if r.stderr.strip() else '')).strip()
    sheet = next((l[6:].strip() for l in r.stdout.splitlines() if l.startswith('wrote ')), None)
    problems = []; notes = [l[6:].strip() for l in r.stdout.splitlines() if l.startswith('note: ')]
    if 'PROBLEMS:' in r.stdout:
        problems = [l.strip() for l in r.stdout.split('PROBLEMS:', 1)[1].splitlines() if l.strip()]
    elif r.returncode != 0:
        problems = [(r.stderr.strip().splitlines() or [f'exit code {r.returncode}'])[-1]]
    return dict(ok=r.returncode == 0, scene=scene, sheet=sheet, problems=problems, notes=notes, seconds=round(time.time() - t0, 1), output=text[-3000:])

def find_qc_reports(path):
    """.qc.txt files for an mp4, a .qc.txt, or a folder (its scene folders too)."""
    p = os.path.abspath(os.path.expanduser(path))
    if p.lower().endswith('.qc.txt'): return [p] if os.path.isfile(p) else []
    if p.lower().endswith('.mp4'):
        q = os.path.splitext(p)[0] + '.qc.txt'; return [q] if os.path.isfile(q) else []
    if os.path.isdir(p): return sorted(glob.glob(os.path.join(p, '*.qc.txt')) + glob.glob(os.path.join(p, '*', '*.qc.txt')) + glob.glob(os.path.join(p, '*', '*', '*.qc.txt')))   # Save to \ Music \ song \ x.qc.txt
    return []

def read_qc(path):
    reps = find_qc_reports(path)
    out = []
    for q in reps:
        try:
            with open(q, encoding='utf-8') as fh: txt = fh.read()
        except OSError as e: txt = f'(cannot read: {e})'
        first = txt.splitlines()[0] if txt else ''
        status = first.split()[1] if first.startswith('QC ') and len(first.split()) > 1 else None
        out.append(dict(report=q, status=status, text=txt))
    return out

def run_qc_now(mp4, quality='best'):
    """QC an existing mp4 again (app.quality_check, same thresholds). Size expectation from quality + the file's own shape."""
    mp4 = os.path.abspath(os.path.expanduser(mp4))
    if not os.path.isfile(mp4): raise ValueError(f'mp4 not found: {mp4}')
    pr = app.probe(mp4); vertical = bool(pr and pr[1] > pr[0])
    res, path = app.quality_check(mp4, rc.PRESETS[QUALITY[_norm(quality, QUALITY, 'quality')]], vertical, {'duration': pr[2] if pr else None},
                                  'generated' if (pr and pr[3]) else 'none')
    return dict(mp4=mp4, status=(res or {}).get('status'), reasons=(res or {}).get('reasons'), report=path)

OUT_EXTS = ('.mp4', '.qc.txt', '.log', '_thumb.jpg', '.jpg')
def list_outputs(path):
    """Shadow's output files under a folder (the Save to folder or one scene folder): mp4s with size/duration/QC status,
    QC reports, failure logs, thumbnails. Skips unfinished .part.mp4 files."""
    p = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(p): raise ValueError(f'folder not found: {p}')
    files = sorted(set(glob.glob(os.path.join(p, '*')) + glob.glob(os.path.join(p, '*', '*')) + glob.glob(os.path.join(p, '*', '*', '*'))))   # also Save to \ Music \ song \
    out = []
    for f in files:
        if not os.path.isfile(f) or f.lower().endswith('.part.mp4') or not f.lower().endswith(OUT_EXTS): continue
        d = dict(path=f, kind='video' if f.lower().endswith('.mp4') else 'qc' if f.lower().endswith('.qc.txt') else
                 'log' if f.lower().endswith('.log') else 'image', bytes=os.path.getsize(f),
                 modified=time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(f))))
        if d['kind'] == 'video':
            pr = app.probe(f)
            if pr: d.update(width=pr[0], height=pr[1], seconds=round(pr[2], 2), audio=pr[3])
            q = read_qc(f); d['qc'] = q[0]['status'] if q else None
        out.append(d)
    return out


# ---------------------------------------------------------------- MCP tools
def build_server():
    from mcp.server.mcpserver import MCPServer, Image
    mcp = MCPServer('shadow', version=rc.VERSION, instructions=(
        'Seekframe renders self-contained three.js HTML scenes and Blender .blend scenes to MP4 (1080p 30 fps with sound) for the owner\'s YouTube channels, '
        'with the same code as the Seekframe app. Typical flow: shadow_check_frames(scene) -> look at the sheet -> shadow_plan -> '
        'shadow_render -> shadow_progress(wait_seconds=50) until finished -> shadow_qc / shadow_outputs. '
        'Output: <out_dir>/<scene name>/<scene>.mp4 (9:16: <scene>_shorts.mp4) + .qc.txt; files are never overwritten. '
        'YouTube: shadow_upload_preview(video) -> show it to the owner -> only after his yes shadow_upload(same arguments) -> '
        'shadow_upload_progress(wait_seconds=50). Uploads are Private by default.'))

    def plan_view(plan):
        return dict(out_dir=plan['out_dir'], quality=plan['preset_name'], format=plan['format'], sound=plan['sound'],
                    capture=plan['capture'], audio_file=plan['audio_file'], problems=plan['problems'], warnings=plan['warnings'],
                    engine=plan['engine'], device=plan['device'], skip_up_to_date=plan['skip_up_to_date'], folder=plan['folder'],
                    estimate_s=plan['estimate_s'], estimate=app.eta_text(plan['estimate_s'] or 0, unknown=plan['not_estimated']),
                    rows=[dict(scene=r['scene'], name=r['name'], kind=r['kind'], format=r['format'], audio=r['audio'], status=r['status'], estimate_s=r['estimate_s'] and round(r['estimate_s']),
                               output=app.output_mp4(plan['out_dir'], r['scene'], r['vertical'], r['main']))
                          for r in plan['rows']])

    @mcp.tool()
    def shadow_info() -> dict:
        """Shadow version, whether Chrome / ffmpeg / playwright are found, the default output folder, the settings choices,
        and the current / last render job."""
        env = rc.check_env(); job = runner().get(); be = bc.find_blender()
        return dict(version=rc.VERSION, env=env, ready=all(env.values()), default_out_dir=default_out_dir(),
                    blender=dict(path=be, version=bc.blender_version(be) if be else None),
                    quality=list(QUALITY), format=list(FORMAT), sound=list(SOUND), capture=list(CAPTURE), engine=list(ENGINE), device=list(DEVICE),
                    last_job=job.snapshot()['summary'] if job else None)

    @mcp.tool()
    def shadow_plan(paths: list[str], out_dir: str = '', quality: str = 'best', format: str = 'auto', sound: str = 'auto',
                    audio_file: str = '', capture: str = 'png', narration: str = 'auto', outdated: str = 'skip',
                    recursive: bool = False, engine: str = 'scene', device: str = 'auto', skip_up_to_date: bool = False, folder: str = 'auto') -> dict:
        """Dry run: what shadow_render would do with these settings, without rendering (the GUI table: Format, Audio,
        narration marks, output path, outdated 9:16 scenes, Auto-sound fallbacks, up-to-date scenes, estimated time).
        Same parameters as shadow_render."""
        try:
            return plan_view(build_plan(paths, out_dir or None, quality, format, sound, audio_file or None, capture, narration, outdated, recursive, engine, device, skip_up_to_date, folder))
        except ValueError as e:
            return dict(error=str(e))

    @mcp.tool()
    def shadow_render(paths: list[str], out_dir: str = '', quality: str = 'best', format: str = 'auto', sound: str = 'auto',
                      audio_file: str = '', capture: str = 'png', narration: str = 'auto', outdated: str = 'skip',
                      recursive: bool = False, engine: str = 'scene', device: str = 'auto', skip_up_to_date: bool = False, folder: str = 'auto') -> dict:
        """Start rendering scene .html files and/or whole folders of scenes; returns at once with a job_id.
        out_dir: the "Save to" folder (default: the GUI's). Each scene gets <out_dir>/<main folder>/<scene name>/<scene>.mp4;
        a Short named short_x.html goes next to its video: <main folder>/x/x_shorts.mp4.
        folder: auto (the scene's <meta name="shadow-folder">, else a song file as its sound -> Music, the rest -> Video) |
        none (straight in out_dir) | a folder name (Music, Video, Kids...).
        quality: draft (720p) | good (1080p, 24 samples) | best (1080p, 64 samples).
        format: auto (short_*/..._short files -> 9:16, others 16:9) | 16:9 | 9:16.
        sound: auto (<scene>.mp3/.wav/... next to the scene, else the scene's own sound) | generated | none | file (+ audio_file).
        capture: png (lossless) | jpeg (about 2x faster, very slightly softer).
        narration: auto (attach <scene>.wav + <scene>.words.json next to the scene, like the GUI) | off.
        outdated: skip | render - scenes without get('h') in a 9:16 render (the GUI asks; here you choose).
        engine (.blend only): scene (its own setting) | eevee | cycles | workbench; device (.blend + Cycles): auto (GPU: OptiX,
        CUDA...) | gpu | cpu. A .blend uses its 9:16 camera ("Camera 9x16") for Shorts and its own sound strips for sound=generated.
        skip_up_to_date: don't render a scene again whose <scene>.mp4 is newer than the scene and its audio/narration files
        (the GUI's "Skip scenes already rendered" option; default off here: a render you ask for is a new file, _2, _3...).
        A narrated scene also gets <video>.srt subtitles next to its mp4 (result 'srt').
        Follow it with shadow_progress(job_id, wait_seconds=50)."""
        try:
            plan = build_plan(paths, out_dir or None, quality, format, sound, audio_file or None, capture, narration, outdated, recursive, engine, device, skip_up_to_date, folder)
        except ValueError as e:
            return dict(error=str(e))
        env = rc.check_env(); kinds = {r['kind'] for r in plan['rows'] if not r['skip']}
        need = {'ffmpeg'} | ({'chrome', 'playwright'} if 'html' in kinds else set())   # a .blend-only job needs no Chrome
        miss = [k for k, v in env.items() if not v and k in need]
        if 'blender' in kinds and not bc.find_blender(): miss.append('Blender (winget install -e --id BlenderFoundation.Blender)')
        if miss: return dict(error='Missing: ' + ', '.join(miss) + '. Install them (Seekframe\'s "Install missing" button does it).')
        if not plan['rows']: return dict(error='no scenes to render', problems=plan['problems'])
        if all(r['skip'] for r in plan['rows']): return dict(error='every scene was skipped (' + ', '.join(sorted({r['status'] for r in plan['rows']})) + ')', plan=plan_view(plan))
        job = runner().submit(plan)
        return dict(job_id=job.id, queued_behind=sum(1 for j in runner().jobs.values() if j.state in ('queued', 'running') and j is not job),
                    plan=plan_view(plan))

    @mcp.tool()
    async def shadow_progress(job_id: str = '', wait_seconds: float = 0) -> dict:
        """Progress of a render job (default: the latest): state, percent, each scene's status ('Frames 120/900',
        'Done · QC OK', ...), ETA, outputs, QC status, warnings. wait_seconds (max 55) blocks until the job ends or the time
        is up - use 30-50 instead of calling it every few seconds."""
        job = runner().get(job_id or None)
        if not job: return dict(error=f'no such job: {job_id}' if job_id else 'no render job yet')
        if wait_seconds: await asyncio.to_thread(runner().wait, job, wait_seconds)
        return job.snapshot()

    @mcp.tool()
    def shadow_cancel(job_id: str = '', current_only: bool = False) -> dict:
        """Cancel a render job (default: the latest). current_only=True = the GUI's "Cancel current" (the queue goes on with
        the next scene); otherwise "Stop queue" (the running scene and every queued one). A partly written file is removed."""
        job = runner().cancel(job_id or None, current_only)
        if not job: return dict(error='no such job')
        return dict(job_id=job.id, cancelled='current scene' if current_only else 'whole job', state=job.state)

    @mcp.tool()
    def shadow_youtube() -> dict:
        """YouTube upload setup: the signed-in channels (title, id) and whether the OAuth client file is there.
        Signing in a channel needs the owner in his browser: Shadow window -> YouTube... -> Sign in... (or python youtube.py signin)."""
        try: yt.load_client(); client = None
        except yt.YouTubeError as e: client = str(e)
        return dict(channels=yt.channels(), client_ok=client is None, client_problem=client,
                    note='Until the Google project passes YouTube\'s API audit, every API upload stays Private (also after a scheduled time).')

    @mcp.tool()
    def shadow_upload_preview(video: str, channel: str = '', privacy: str = 'private', ai: str = 'auto', thumbnail: str = 'auto',
                              publish: str = 'auto', meta: str = '', scene: str = '') -> dict:
        """What shadow_upload WOULD send to YouTube for this mp4, without sending anything: channel, title, description,
        tags, privacy, Made for Kids, AI label, publish time (UTC), thumbnail and notes (missing metadata, past time...).
        Always call this first, show it to the owner and upload only after his yes (same arguments to shadow_upload).
        Metadata: metadata.txt next to the video (or `meta`, or next to `scene`): TITLE, DESCRIPTION, TAGS, AUDIENCE, AI,
        THUMBNAIL, PUBLISH; the '=== SHORT ===' section for *_shorts.mp4. channel: title or id of a signed-in channel
        (shadow_youtube), optional when only one is signed in. privacy private|unlisted|public (default private).
        ai auto|yes|no (auto = AI: line). thumbnail auto (THUMBNAIL: or <video>_thumb.jpg) | none | an image path.
        publish auto (PUBLISH: line) | none | a date and time like "2026-11-25 05:00 Sydney" (scheduled = Private until then)."""
        try: s = upload_settings(video, channel, privacy, ai, thumbnail, publish, meta, scene)
        except (ValueError, yt.YouTubeError) as e: return dict(error=str(e))
        return dict(preview=s['view'], uploaded=False)

    @mcp.tool()
    def shadow_upload(video: str, channel: str = '', privacy: str = 'private', ai: str = 'auto', thumbnail: str = 'auto',
                      publish: str = 'auto', meta: str = '', scene: str = '') -> dict:
        """Upload a finished mp4 to the owner's YouTube channel - ONLY after he approved the shadow_upload_preview of the
        same arguments. Same code as the app's YouTube... button. Starts in the background and returns an upload_id;
        follow it with shadow_upload_progress(wait_seconds=50). Arguments: see shadow_upload_preview."""
        try: s = upload_settings(video, channel, privacy, ai, thumbnail, publish, meta, scene)
        except (ValueError, yt.YouTubeError) as e: return dict(error=str(e))
        if any(u.state == 'running' for u in UPLOADS.values()): return dict(error='another upload is running; wait for it (shadow_upload_progress)')
        u = Upload(s['view'], s['args']); UPLOADS[u.id] = u; UPLOAD_ORDER.append(u.id)
        log.info('mcp upload %s started: %s -> %s', u.id, s['view']['video'], s['view']['channel'])
        return dict(upload_id=u.id, started=True, preview=s['view'])

    @mcp.tool()
    async def shadow_upload_progress(upload_id: str = '', wait_seconds: float = 0) -> dict:
        """Progress of a YouTube upload (default: the latest): state running|done|error|cancelled, percent, and when done the
        video link, the YouTube Studio link, privacy, scheduled time (UTC) and thumbnail ('set' / 'error: ...').
        wait_seconds (max 55) blocks until it ends or the time is up."""
        u = get_upload(upload_id)
        if not u: return dict(error=f'no such upload: {upload_id}' if upload_id else 'no upload yet')
        end = time.time() + max(0.0, min(float(wait_seconds or 0), 55.0))
        while time.time() < end and u.state == 'running': await asyncio.sleep(0.25)
        return u.snapshot()

    @mcp.tool()
    def shadow_upload_cancel(upload_id: str = '') -> dict:
        """Stop a running YouTube upload (default: the latest) after the current 8 MB chunk. Nothing is published."""
        u = get_upload(upload_id)
        if not u: return dict(error='no such upload')
        u.cancel.set(); return dict(upload_id=u.id, cancelling=u.state == 'running', state=u.state)

    @mcp.tool()
    async def shadow_video_look(video: str, song: str = '', show_sheet: bool = True):
        """Look at a finished video + a song before joining them (Shadow 3.18): Shadow's own measurements of the video
        (motion every 0.5 s and its peak, hard cuts, dark frames, the best loop point and how alike it is to the first frame),
        the song's beats / BPM / chorus (librosa) and the arrangement Shadow would make on its own (mode chorus | loop | trim |
        as_is, song_start, loop_end). With show_sheet the contact sheet comes back as an image: LOOK at it and decide whether
        the automatic plan is right (e.g. the moment that should meet the chorus, a loop point that hides the jump).
        song '' = <video>.mp3/.wav next to the video. Writes <video>.check.jpg next to the video; renders nothing."""
        try:
            video, song = video_song_inputs(video, song)
            sheet = os.path.splitext(video)[0] + '.check.jpg'
            v = await asyncio.to_thread(vsong.look, video, sheet)
            s = await asyncio.to_thread(vsong.song_info, song, v['dur'])
            pl = vsong.plan(v, s)
        except (ValueError, RuntimeError) as e: return dict(error=str(e))
        res = dict(video=video, song=song, look={k: v[k] for k in v if k != 'sheet'}, song_info={k: s[k] for k in ('dur', 'bpm', 'chorus', 'source')},
                   beats_first=(s['beats'] or [])[:16], plan=pl, summary=vsong.describe(v, s, pl), sheet=v['sheet'],
                   next='shadow_video_song(video, song, mode=..., song_start=..., loop_end=...) renders it; leave them out to use this plan.')
        if show_sheet and v['sheet'] and os.path.isfile(v['sheet']): return [json.dumps(res, ensure_ascii=False, indent=1), Image(path=v['sheet'])]
        return res

    @mcp.tool()
    def shadow_video_song(video: str, song: str = '', mode: str = 'auto', song_start: float = -1, loop_end: float = -1,
                          out_dir: str = '', folder: str = 'auto') -> dict:
        """Join a finished video and a song into one music video (Shadow 3.18), like dropping the video in the Shadow queue:
        <out_dir>/<folder>/<video name>/<video name>.mp4 (9:16 -> _shorts.mp4), the song at -14 LUFS, BT.709, QC report,
        thumbnail at the strongest moment, never overwriting. Look first with shadow_video_look. mode auto | chorus | loop |
        trim | as_is; song_start (seconds, -1 = the plan's) where the song starts (chorus mode: put a strong beat where the
        video's key moment is); loop_end (-1 = the plan's) where a looping video jumps back. Runs in the background: follow
        it with shadow_video_song_progress(job_id, wait_seconds=50)."""
        try:
            video, song = video_song_inputs(video, song)
            if mode not in vsong.MODES: raise ValueError(f"mode must be one of {', '.join(vsong.MODES)}")
        except ValueError as e: return dict(error=str(e))
        ov = dict(video_mode=mode, **({'song_start': float(song_start)} if song_start is not None and song_start >= 0 else {}),
                  **({'loop_end': float(loop_end)} if loop_end is not None and loop_end >= 0 else {}))
        j = VideoSongJob(video, song, os.path.abspath(os.path.expanduser(out_dir)) if out_dir else default_out_dir(), folder_choice(folder), ov)
        VIDEO_JOBS[j.id] = j; VIDEO_ORDER.append(j.id)
        return dict(job_id=j.id, started=True, video=video, song=song, mode=mode)

    @mcp.tool()
    async def shadow_video_song_progress(job_id: str = '', wait_seconds: float = 0) -> dict:
        """A video + song job (default: the latest): state running | finished | error | cancelled, stage, and when finished the
        mp4, QC status, QC report and thumbnail. wait_seconds (max 55) blocks until it ends or the time is up."""
        j = VIDEO_JOBS.get(job_id) if job_id else (VIDEO_JOBS[VIDEO_ORDER[-1]] if VIDEO_ORDER else None)
        if not j: return dict(error=f'no such job: {job_id}' if job_id else 'no video + song job yet')
        end = time.time() + max(0.0, min(float(wait_seconds or 0), 55.0))
        while time.time() < end and j.state == 'running': await asyncio.sleep(0.25)
        return j.snapshot()

    @mcp.tool()
    def shadow_jobs() -> list[dict]:
        """All render jobs of this server session (newest last): id, state, percent, summary."""
        r = runner()
        return [{k: s[k] for k in ('job_id', 'state', 'percent', 'summary', 'out_dir')} for s in (r.jobs[i].snapshot() for i in r.order)]

    @mcp.tool()
    async def shadow_check_frames(scene: str, only: str = '', times: str = '', show_sheet: bool = True, engine: str = 'scene'):
        """Run tools/check_frames.py BEFORE a full render (seconds, not minutes): a contact sheet of frames in 16:9 and 9:16,
        page errors, and whether seek(t) is pure. only: '16:9' | '9:16' | '' (both). times: e.g. '3,25%,50%,75%,end-2'.
        Writes <scene>.check.jpg next to the scene; show_sheet=True also returns the image itself.
        A .blend: Blender renders those frames at 640 px with draft samples (engine: scene|eevee|cycles|workbench); missing
        textures / linked files are problems, "no 9:16 camera" is a note."""
        try:
            res = await asyncio.to_thread(check_frames, scene, only or None, times or None, None, 900 if bc.is_blend(scene) else 600, engine)
        except (ValueError, subprocess.TimeoutExpired) as e:
            return dict(error=str(e))
        if show_sheet and res['sheet'] and os.path.isfile(res['sheet']):
            return [json.dumps(res, ensure_ascii=False, indent=1), Image(path=res['sheet'])]
        return res

    @mcp.tool()
    def shadow_qc(path: str, rerun: bool = False, quality: str = 'best') -> dict:
        """Read Shadow's quality report(s): path = an mp4, its .qc.txt, a scene folder or the whole Save to folder.
        rerun=True runs the QC again on that mp4 (same checks as after a render; quality sets the expected size)."""
        try:
            if rerun: return run_qc_now(path, quality)
        except ValueError as e:
            return dict(error=str(e))
        reps = read_qc(path)
        return dict(reports=reps) if reps else dict(error=f'no .qc.txt found for {path}')

    @mcp.tool()
    def shadow_outputs(path: str = '', job_id: str = '') -> dict:
        """List what Shadow produced: give a folder (Save to or one scene folder), or a job_id (default: the latest job's
        out_dir, only its scene folders). Videos come with size, duration, audio yes/no and QC status."""
        try:
            if path: return dict(files=list_outputs(path))
            job = runner().get(job_id or None)
            if not job: return dict(error='give a folder or a job_id')
            files = []
            for r in job.plan['rows']:
                d = os.path.join(job.plan['out_dir'], r['folder'])
                if os.path.isdir(d): files += list_outputs(d)
            return dict(job_id=job.id, files=files)
        except ValueError as e:
            return dict(error=str(e))

    return mcp


def fill_windows_env():
    """MCP clients start the server with a cut-down environment (the mcp SDK passes only a few variables, no PROGRAMFILES /
    LOCALAPPDATA). Without them render_core.check_env and Playwright's channel='chrome' cannot find the installed Chrome
    (measured: "Missing: chrome"). Put back the standard Windows values when they are missing. Returns what was added."""
    if os.name != 'nt': return {}
    home = os.environ.get('USERPROFILE') or os.path.expanduser('~'); drive = os.environ.get('SystemDrive', 'C:')
    want = {'PROGRAMFILES': drive + '\\Program Files', 'PROGRAMFILES(X86)': drive + '\\Program Files (x86)',
            'LOCALAPPDATA': os.path.join(home, 'AppData', 'Local'), 'APPDATA': os.path.join(home, 'AppData', 'Roaming'),
            'TEMP': os.path.join(home, 'AppData', 'Local', 'Temp'), 'TMP': os.path.join(home, 'AppData', 'Local', 'Temp'),
            'SystemRoot': drive + '\\Windows', 'ProgramData': drive + '\\ProgramData'}
    added = {k: v for k, v in want.items() if not os.environ.get(k)}
    os.environ.update(added)
    return added

HTTP_HOST = '127.0.0.1'   # the HTTP mode never listens on another address (no LAN, no internet)
HTTP_PORT = 8765


def parse_args(argv):
    """[] -> stdio (default). --http [--port N] -> local streamable HTTP at http://127.0.0.1:N/mcp (off unless asked)."""
    import argparse
    ap = argparse.ArgumentParser(prog='mcp_server.py', description='Seekframe MCP server (stdio by default).')
    ap.add_argument('--http', action='store_true', help=f'serve MCP over streamable HTTP on {HTTP_HOST} only (for clients that want a URL)')
    ap.add_argument('--port', type=int, default=HTTP_PORT, help=f'HTTP port (default {HTTP_PORT})')
    a = ap.parse_args(argv)
    if not 1 <= a.port <= 65535: ap.error('port must be 1-65535')
    return a


def http_security(port):
    """Only requests addressed to this machine by name/IP are served (blocks DNS-rebinding from web pages)."""
    from mcp.server.transport_security import TransportSecuritySettings
    hosts = [f'127.0.0.1:{port}', f'localhost:{port}']
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                                     allowed_origins=[f'http://{h}' for h in hosts])


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    added = fill_windows_env()
    if os.environ.get('SHADOW_LOG'): app.LOG = os.environ['SHADOW_LOG']   # tests: keep the real ~/shadow.log untouched
    app.setup_log(); log.propagate = False   # ~/shadow.log only; MCPServer's console handler on stderr would repeat every line
    log.info('mcp server start (Seekframe v%s, pid %d)%s', rc.VERSION, os.getpid(), f' - added env {sorted(added)}' if added else '')
    threading.Thread(target=app.startup_cleanup, daemon=True).start()   # leftovers of crashed renders, installed update setups
    runner()
    if args.http:
        log.info('mcp server: HTTP mode on http://%s:%d/mcp (localhost only)', HTTP_HOST, args.port)
        build_server().run('streamable-http', host=HTTP_HOST, port=args.port, transport_security=http_security(args.port))
    else:
        build_server().run('stdio')


if __name__ == '__main__':
    main()
