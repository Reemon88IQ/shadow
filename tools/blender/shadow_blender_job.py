"""Runs INSIDE Blender (blender -b scene.blend --python shadow_blender_job.py -- job.json), started by blender_core.py.
Never run it by hand. It only reads the .blend and changes settings in memory - the .blend file is never saved.

job.json: {mode: 'render'|'preview', out_dir, w, h, vertical, quality: draft|good|best, engine: scene|eevee|cycles|workbench,
           device: auto|gpu|cpu, capture: png|jpeg, audio_wav (path or null: mix the scene's own sound into it),
           max_frames (int|null), preview_frames ([frame numbers] for mode 'preview'), scene (name or null)}
It prints machine-readable lines on stdout (everything else Blender prints is ignored by the caller):
  SHADOW_INFO {json}         settings actually used, frame count, camera, warnings, missing files, thumbnail time
  SHADOW_AUDIO {json}        {"wav": path} or {"wav": null} (no sound in the scene)
  SHADOW_FRAME i n path      frame i of n written (render), or one preview image
  SHADOW_DONE                all frames written
  SHADOW_ERROR message       a problem that stops the job (exit code 3)"""
import json, math, os, re, sys, traceback
from fractions import Fraction
import bpy

FPS = 30
VERTICAL_CAMERA = re.compile(r'(9\s*[x:_-]\s*16|vertical|portrait|short)', re.I)

def say(tag, payload=''):
    print(f'SHADOW_{tag} {payload}'.rstrip(), flush=True)

def engine_ids():
    """Engine identifiers this Blender accepts (the RNA enum lists only the registered ones; Cycles is an add-on)."""
    ids = {i.identifier for i in bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items}
    return ids | {'CYCLES'} if 'cycles' in bpy.context.preferences.addons else ids

def set_engine(scene, want):
    names = {'eevee': ['BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'], 'cycles': ['CYCLES'], 'workbench': ['BLENDER_WORKBENCH']}
    for n in names[want]:
        try: scene.render.engine = n; return n
        except TypeError: continue
    raise RuntimeError(f'engine {want} is not available in this Blender ({sorted(engine_ids())})')

def setup_cycles_device(scene, device):
    """GPU: OptiX, else CUDA, else HIP / oneAPI / Metal, every GPU of that type on (never the CPU next to it). Returns a label."""
    if device == 'cpu':
        scene.cycles.device = 'CPU'; return 'CPU'
    try:
        prefs = bpy.context.preferences.addons['cycles'].preferences
    except KeyError:
        return 'CPU (Cycles preferences not found)'
    for kind in ('OPTIX', 'CUDA', 'HIP', 'ONEAPI', 'METAL'):
        try: prefs.compute_device_type = kind
        except TypeError: continue
        try: prefs.refresh_devices()
        except Exception: pass
        gpus = [d for d in prefs.devices if d.type == kind]
        if gpus:
            for d in prefs.devices: d.use = d.type == kind
            scene.cycles.device = 'GPU'
            return f"{kind}: {', '.join(sorted({d.name for d in gpus}))}"
    scene.cycles.device = 'CPU'
    return 'CPU (no GPU found)'

def apply_quality(scene, quality, warns):
    """Samples per quality. 'best' never lowers the scene's own setting (the owner may have set more on purpose)."""
    eng = scene.render.engine; q = {'draft': 0, 'good': 1, 'best': 2}[quality]
    if eng == 'CYCLES':
        c = scene.cycles; want = (32, 128, 512)[q]
        c.samples = max(c.samples, want) if q == 2 else want
        c.use_denoising = True
        try: c.denoiser = 'OPTIX' if c.device == 'GPU' and bpy.context.preferences.addons['cycles'].preferences.compute_device_type == 'OPTIX' else 'OPENIMAGEDENOISE'
        except Exception: pass
        return f'{c.samples} samples, denoise {getattr(c, "denoiser", "?")}'
    if eng.startswith('BLENDER_EEVEE'):
        e = scene.eevee; want = (16, 64, 128)[q]
        e.taa_render_samples = max(e.taa_render_samples, want) if q == 2 else want
        return f'{e.taa_render_samples} samples'
    if eng == 'BLENDER_WORKBENCH':
        want = ('8', '16', '32')[q]
        try: scene.display.render_aa = want
        except Exception: pass
        return f'AA {scene.display.render_aa}'
    warns.append(f'unknown render engine {eng}: its own samples were kept')
    return 'scene setting'

def pick_camera(scene, vertical, warns):
    """9:16: a camera whose name says 9x16 / 9:16 / vertical / portrait / short, or the scene property shadow_camera_9x16.
    Without one the 16:9 camera is used and framed for portrait by Blender (warned)."""
    cams = [o for o in scene.objects if o.type == 'CAMERA']
    if not cams and scene.camera is None:
        raise RuntimeError('the scene has no camera')
    if vertical:
        want = scene.get('shadow_camera_9x16')
        cam = scene.objects.get(want) if isinstance(want, str) else None
        cam = cam or next((o for o in cams if VERTICAL_CAMERA.search(o.name)), None)
        if cam is not None:
            scene.camera = cam
            if any(m.camera for m in scene.timeline_markers): warns.append('timeline markers switch cameras: they may override the 9:16 camera')
            return cam.name, True
        warns.append('no 9:16 camera (name it e.g. "Camera 9x16"): the 16:9 camera was framed for portrait')
    elif scene.camera is not None and VERTICAL_CAMERA.search(scene.camera.name):
        other = next((o for o in cams if not VERTICAL_CAMERA.search(o.name)), None)
        if other is not None: scene.camera = other   # a 16:9 render must not use the Shorts camera
    if scene.camera is None: scene.camera = cams[0]
    return scene.camera.name, False

def retime_to_30(scene):
    """Shadow videos are 30 fps. Another scene rate is re-timed with frame mapping, so the video keeps its real-time length.
    Returns (first frame, number of frames, source fps)."""
    src = scene.render.fps / (scene.render.fps_base or 1.0)
    start, end = scene.frame_start, scene.frame_end
    n_src = end - start + 1
    if abs(src - FPS) < 1e-3:
        return start, n_src, src
    ratio = Fraction(FPS / src).limit_denominator(900)   # frame_map_old / frame_map_new are 1..900
    while ratio.numerator > 900 or ratio.denominator > 900: ratio = Fraction(ratio.numerator // 2 or 1, ratio.denominator // 2 or 1)
    scene.render.frame_map_old, scene.render.frame_map_new = ratio.denominator, ratio.numerator
    n = max(1, round(n_src * FPS / src))
    scene.render.fps, scene.render.fps_base = FPS, 1.0
    first = round(start * ratio)
    scene.frame_start, scene.frame_end = first, first + n - 1
    return first, n, src

def has_sound(scene):
    se = scene.sequence_editor
    strips = []
    if se:
        for attr in ('strips_all', 'sequences_all'):
            if hasattr(se, attr): strips = list(getattr(se, attr)); break
    if any(s.type == 'SOUND' and not s.mute for s in strips): return True
    return any(o.type == 'SPEAKER' and o.data.sound is not None and not o.data.muted for o in scene.objects)

def missing_files():
    out = []
    for coll, label in ((bpy.data.images, 'image'), (bpy.data.sounds, 'sound'), (bpy.data.movieclips, 'movie'),
                        (bpy.data.libraries, 'linked library'), (getattr(bpy.data, 'fonts', []), 'font'), (getattr(bpy.data, 'cache_files', []), 'cache')):
        for d in coll:
            fp = getattr(d, 'filepath', '') or ''
            if not fp or fp.startswith('<') or getattr(d, 'packed_file', None) is not None: continue
            if getattr(d, 'source', 'FILE') in ('GENERATED', 'VIEWER'): continue
            p = bpy.path.abspath(fp, library=getattr(d, 'library', None))
            if '#' in p or '<UDIM>' in p.upper(): continue   # sequences / UDIM tiles: checked by Blender itself
            if not os.path.exists(p): out.append(f'{label} {d.name}: {p}')
    return out

def thumbnail_time(scene):
    """THUMBNAIL_T as a scene custom property (seconds), or a timeline marker named 'thumbnail' / 'thumb' (read BEFORE
    re-timing: the marker sits on the scene's own frame rate)."""
    for k in ('THUMBNAIL_T', 'shadow_thumbnail_t'):
        v = scene.get(k)
        if isinstance(v, (int, float)): return float(v)
    m = next((m for m in scene.timeline_markers if m.name.lower() in ('thumbnail', 'thumb')), None)
    src = scene.render.fps / (scene.render.fps_base or 1.0)
    return (m.frame - scene.frame_start) / src if m else None

def main():
    job = json.load(open(sys.argv[sys.argv.index('--') + 1], encoding='utf-8'))
    scene = bpy.data.scenes.get(job.get('scene') or '') or bpy.context.scene
    if bpy.context.window is not None: bpy.context.window.scene = scene
    warns = []
    # 1) the scene's own sound, mixed down BEFORE re-timing (re-timing changes the frame rate the strips are placed on)
    wav = None
    if job.get('audio_wav') and has_sound(scene):
        try:
            bpy.ops.sound.mixdown(filepath=job['audio_wav'], check_existing=False, relative_path=False, container='WAV', codec='PCM',
                                  format='S16', mixrate=48000, accuracy=1024)
            wav = job['audio_wav'] if os.path.isfile(job['audio_wav']) else None
            if wav is None: warns.append('the scene sound could not be mixed down')
        except Exception as e:
            warns.append(f'the scene sound could not be mixed down: {e}')
    thumb = thumbnail_time(scene)
    first, n, src_fps = retime_to_30(scene)
    if job.get('max_frames'): n = min(n, int(job['max_frames'])); scene.frame_end = first + n - 1
    # 2) engine, device, samples, camera, size, output format
    eng = job.get('engine') or 'scene'
    if eng != 'scene': set_engine(scene, eng)
    device = None
    if scene.render.engine == 'CYCLES': device = setup_cycles_device(scene, job.get('device') or 'auto')
    samples = apply_quality(scene, job.get('quality') or 'best', warns)
    camera, vcam = pick_camera(scene, bool(job.get('vertical')), warns)
    r = scene.render
    r.resolution_x, r.resolution_y, r.resolution_percentage = int(job['w']), int(job['h']), 100
    r.pixel_aspect_x = r.pixel_aspect_y = 1.0
    r.use_border = False
    if job.get('vertical') and not vcam and scene.camera is not None and scene.camera.data.type != 'ORTHO':
        # centre crop like a Shorts crop of the 16:9 shot: keep its HEIGHT of view (in 16:9 with sensor fit Auto the sensor
        # width spans the width, so the height of view is sensor_width * 9/16) and cut the sides
        cd = scene.camera.data
        if cd.sensor_fit in ('AUTO', 'HORIZONTAL'): cd.sensor_height = cd.sensor_width * 9 / 16
        cd.sensor_fit = 'VERTICAL'
    elif job.get('vertical') and not vcam and scene.camera is not None:
        cd = scene.camera.data; cd.ortho_scale = cd.ortho_scale * 9 / 16   # ortho: the scale spans the larger side (now the height)
    try: r.use_file_extension = True
    except Exception: pass
    im = r.image_settings
    if job.get('capture') == 'jpeg':
        im.file_format = 'JPEG'; im.color_mode = 'RGB'; im.quality = 95
    else:
        im.file_format = 'PNG'; im.color_mode = 'RGB'; im.color_depth = '8'; im.compression = 15
    r.use_overwrite = True; r.use_placeholder = False
    if hasattr(r, 'use_lock_interface'): r.use_lock_interface = True
    miss = missing_files()
    info = dict(blender=bpy.app.version_string, scene=scene.name, engine=r.engine, device=device, samples=samples, camera=camera,
                vertical_camera=vcam, frames=n, first=first, src_fps=round(src_fps, 3), duration=n / FPS, width=r.resolution_x,
                height=r.resolution_y, thumbnail_t=thumb, missing=miss, warnings=warns + [f'missing {m}' for m in miss[:10]])
    say('INFO', json.dumps(info))
    say('AUDIO', json.dumps({'wav': wav}))
    out = job['out_dir']; ext = '.jpg' if job.get('capture') == 'jpeg' else '.png'
    if job.get('mode') == 'preview':
        for k, f in enumerate(job.get('preview_frames') or [first]):
            scene.frame_set(int(f)); r.filepath = os.path.join(out, f'p{k:03d}')
            bpy.ops.render.render(write_still=True, scene=scene.name)
            say('FRAME', f'{k + 1} {len(job["preview_frames"])} {r.filepath}{ext}')
        say('DONE'); return
    r.filepath = os.path.join(out, 'f_######')
    state = {'i': 0}
    def written(sc, *_):
        state['i'] += 1
        say('FRAME', f"{state['i']} {n} {sc.render.frame_path(frame=sc.frame_current)}")
    bpy.app.handlers.render_write.append(written)
    bpy.ops.render.render(animation=True, scene=scene.name)
    if state['i'] < n: raise RuntimeError(f"Blender stopped after {state['i']} of {n} frames")
    say('DONE')

try:
    main()
except Exception as e:
    say('ERROR', f'{type(e).__name__}: {e}'.replace('\n', ' '))
    traceback.print_exc()
    sys.stdout.flush(); os._exit(3)
