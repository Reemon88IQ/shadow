"""Video + song (3.18): a finished video and a song -> one music video (video_song.py, the queue, MCP).
Test videos and a test song are generated with ffmpeg in a temp folder (never scenes/).
  S1 look(): a hard cut is found at 3 s, a video that repeats every 2 s gets its loop point at 2 s, a contact sheet is written
  S2 plan() (pure): auto picks as_is / trim / chorus / loop by length and shape; chorus starts on a beat before the chorus
     so the video's strongest motion lands on it; overrides (mode, song_start, loop_end) win; a bad mode -> ValueError
  S3 song_info(): BPM and the loud 'chorus' part of the test song (librosa), or the loudest part without librosa
  S4 render(): a 9:16 clip -> the chorus part of the song, the clip's length and size, BT.709, about -14 LUFS, <= -1 dBTP
  S5 render(): a short 16:9 clip + a 70 s song -> loops to 70 s
  S6 app: format/scene_info/_audio rules for a video; render_scene puts it in <out>/Music/<video>/<video>.mp4 with QC OK;
     no song -> a clear error; narration refused; render_item never opens a browser for a video
  S7 MCP: video_song_inputs finds <video>.mp3 next to it, refuses a missing song / a non-video; a job renders it
Usage: python tests/test_video_song.py     exit 0 = all PASS"""
import json, os, shutil, subprocess, sys, tempfile, threading, time
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, 'tools'))
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
os.environ['SHADOW_LANG'] = 'en'   # 3.20: the tests read the English texts
import render_core as rc, video_song as vs, app

fails = []
def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f'  ({info})' if info and not ok else ''))
    if not ok: fails.append(name)

tmp = tempfile.mkdtemp(prefix='shadow_vsong_')
app.LOG = os.path.join(tmp, 'shadow.log'); app.LOG_DIR = os.path.join(tmp, 'logs')
FF = rc.find_ffmpeg() or 'ffmpeg'
def ff(*a): subprocess.run([FF, '-v', 'error', '-y', *a], check=True, creationflags=rc.NOWIN)
def probe(p):
    r = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,width,height,color_space,color_primaries:format=duration',
                                   '-of', 'json', p], capture_output=True, text=True, creationflags=rc.NOWIN).stdout)
    v = next(s for s in r['streams'] if s['codec_type'] == 'video')
    return dict(w=v['width'], h=v['height'], cs=v.get('color_space'), prim=v.get('color_primaries'), dur=float(r['format']['duration']),
                audio=any(s['codec_type'] == 'audio' for s in r['streams']))

try:
    cut = os.path.join(tmp, 'cut.mp4')   # 3 s moving pattern, hard cut to 3 s of colour bars
    ff('-f', 'lavfi', '-i', 'testsrc2=s=640x360:r=30:d=3', '-f', 'lavfi', '-i', 'smptebars=s=640x360:r=30:d=3',
       '-filter_complex', '[0][1]concat=n=2:v=1[v]', '-map', '[v]', '-pix_fmt', 'yuv420p', cut)
    base = os.path.join(tmp, 'base.mp4'); ff('-f', 'lavfi', '-i', 'testsrc2=s=640x360:r=30:d=2', '-pix_fmt', 'yuv420p', base)
    per = os.path.join(tmp, 'periodic.mp4'); ff('-stream_loop', '1', '-i', base, '-c', 'copy', per)   # 4 s, t=2 looks like t=0
    tall = os.path.join(tmp, 'tall.mp4'); ff('-f', 'lavfi', '-i', 'testsrc2=s=360x640:r=30:d=6', '-pix_fmt', 'yuv420p', tall)
    song = os.path.join(tmp, 'song.wav')   # 70 s: 120 BPM clicks; 30-45 s and 55-65 s a loud extra layer (the 'chorus')
    ff('-f', 'lavfi', '-i', "aevalsrc='0.25*sin(2*PI*110*t)*exp(-30*mod(t,0.5)) + if(between(t,30,45)+between(t,55,65),"
       "0.35*sin(2*PI*440*t)*(0.6+0.4*sin(2*PI*2*t)),0.03*sin(2*PI*220*t))':s=48000:d=70", '-ac', '2', song)

    # ---------------------------------------------------------------- S1
    v_cut = vs.look(cut, sheet=os.path.join(tmp, 'cut.jpg'))
    check('S1 look: the hard cut is found at 3 s (and only there)', len(v_cut['cuts']) == 1 and abs(v_cut['cuts'][0] - 3.0) <= 0.15, v_cut['cuts'])
    check('S1 look: contact sheet written, 16:9, 6 s, not still', v_cut['sheet'] and os.path.isfile(v_cut['sheet']) and not v_cut['vertical']
          and abs(v_cut['dur'] - 6) < 0.2 and not v_cut['still'], (v_cut['sheet'], v_cut['dur']))
    v_per = vs.look(per)
    check('S1 look: a video repeating every 2 s loops at 2 s, about 100% alike', abs(v_per['loop_end'] - 2.0) <= 0.15 and v_per['loop_match'] > 95,
          (v_per['loop_end'], v_per['loop_match']))
    v_tall = vs.look(tall)
    check('S1 look: a 360x640 video is vertical', v_tall['vertical'] and (v_tall['w'], v_tall['h']) == (360, 640))

    # ---------------------------------------------------------------- S2
    V = lambda d, vert=False, peak=1.0: dict(dur=d, vertical=vert, peak_t=peak, loop_end=d / 2, loop_match=90.0)
    S = lambda d, beats=None, chorus=None: dict(dur=d, beats=beats, chorus=chorus)
    modes = [vs.plan(V(60.5), S(60))['mode'], vs.plan(V(90), S(60))['mode'], vs.plan(V(8, True), S(180))['mode'],
             vs.plan(V(8), S(50))['mode'], vs.plan(V(8), S(180))['mode']]
    check('S2 auto: same length -> as_is, longer -> trim, 9:16 -> chorus, song <= 60 s -> chorus, short 16:9 + long song -> loop',
          modes == ['as_is', 'trim', 'chorus', 'chorus', 'loop'], modes)
    beats = [i * 0.5 for i in range(400)]
    pl = vs.plan(V(8, True, peak=2.2), S(180, beats, (60.0, 75.0)))
    check('S2 chorus: starts on a beat, at or before chorus - peak (57.8 -> 57.5), 8 s, faded out',
          pl['song_start'] == 57.5 and pl['out_dur'] == 8 and pl['fade_out'] > 0, pl)
    pl = vs.plan(V(30), S(180), mode='loop', loop_end=12.3)
    check('S2 loop: the song length, loop_end override kept', pl['mode'] == 'loop' and pl['out_dur'] == 180 and pl['loop_end'] == 12.3, pl)
    pl = vs.plan(V(8, True), S(180, beats, (60.0, 75.0)), song_start=100.0)
    check('S2 chorus: song_start override wins', pl['song_start'] == 100.0, pl)
    try: vs.plan(V(8), S(60), mode='dance'); bad = False
    except ValueError: bad = True
    check('S2 unknown mode -> ValueError', bad)

    # ---------------------------------------------------------------- S3
    s = vs.song_info(song, clip_len=6)
    if s['source'] == 'librosa':
        check('S3 song_info (librosa): about 120 BPM, chorus starts in the loud part (30-45 or 55-65 s)',
              s['bpm'] and abs(s['bpm'] - 120) < 3 and s['chorus'] and (29 <= s['chorus'][0] <= 46 or 54 <= s['chorus'][0] <= 66), (s['bpm'], s['chorus']))
    else:
        check('S3 song_info (no librosa): the loudest part is chosen', s['chorus'] and (29 <= s['chorus'][0] <= 46 or 54 <= s['chorus'][0] <= 66), s['chorus'])
    lw = vs._loudest_window(song, 6, 70.0)
    check('S3 loudness fallback finds a loud part too', 29 <= lw <= 59.5, lw)

    # ---------------------------------------------------------------- S4
    out = os.path.join(tmp, 'tall_out.mp4'); info = {}
    vs.render(tall, song, out, info=info)
    p = probe(out); l, tp = rc._loud(FF, out)
    check('S4 9:16 clip: chorus mode, 6 s, 360x640, audio', info['plan']['mode'] == 'chorus' and abs(p['dur'] - 6) < 0.15
          and (p['w'], p['h']) == (360, 640) and p['audio'], (info['plan'], p))
    check('S4 BT.709 tags', p['cs'] == 'bt709' and p['prim'] == 'bt709', p)
    check('S4 the song at about -14 LUFS, true peak <= -1 dBTP', l is not None and abs(l - rc.MUSIC_LUFS) <= 1.0 and tp <= -1.0, (l, tp))
    check('S4 info for the app: size, thumbnail_t inside the clip, lufs -14', info['size'] == (360, 640) and 0 <= info['thumbnail_t'] <= 6
          and info['lufs'] == rc.MUSIC_LUFS, info.get('size'))

    # ---------------------------------------------------------------- S5
    out = os.path.join(tmp, 'per_out.mp4'); info = {}
    vs.render(per, song, out, info=info)
    p = probe(out)
    check('S5 short 16:9 clip + 70 s song: loops to the song length', info['plan']['mode'] == 'loop' and abs(p['dur'] - 70) < 0.3
          and (p['w'], p['h']) == (640, 360), (info['plan']['mode'], p))
    check('S5 no .part.mp4 left behind', not os.path.exists(rc._part_path(out)))

    # ---------------------------------------------------------------- S6
    vd = os.path.join(tmp, 'videos'); os.makedirs(vd)
    clip = os.path.join(vd, 'dance.mp4'); shutil.copy(tall, clip)
    check('S6 format: a video keeps its own shape whatever Format says', app.format_vertical(clip, app.FORMAT_AUTO)
          and app.format_vertical(clip, '16:9 (YouTube)') and app.scene_info(clip).get('video'))
    o = os.path.join(tmp, 'out'); opts = dict(vertical=False, format=app.FORMAT_AUTO, sound='auto', audio_file=None, capture='png', folder=app.FOLDER_AUTO)
    preset = rc.PRESETS['Draft (fast, 720p)']   # a video keeps its own size whatever the preset
    r = app.render_scene(clip, preset, o, dict(opts))
    check('S6 no song next to it -> error that says to put dance.mp3 there', r['status'] == 'error' and 'dance.mp3' in (r['error'] or ''), (r['status'], r['error']))
    shutil.copy(song, os.path.join(vd, 'dance.wav'))
    r = app.render_scene(clip, preset, o, dict(opts))
    want = os.path.join(o, 'Music', 'dance', 'dance_shorts.mp4')
    check('S6 render_scene: <out>/Music/dance/dance_shorts.mp4 (song -> Music, 9:16 name), the video\'s own size',
          r['status'] == 'done' and os.path.normcase(r['mp4']) == os.path.normcase(want) and (probe(want)['w'], probe(want)['h']) == (360, 640),
          (r['status'], r['error'], r['mp4']))
    check('S6 QC OK (size = the video\'s, -14 LUFS window) + QC report + thumbnail', r['row'].startswith('Done · QC OK') and r['qc_report']
          and os.path.isfile(r['qc_report']) and r.get('thumb') and os.path.isfile(r['thumb']), (r['row'], r['qc'] and r['qc'].get('reasons')))
    r2 = app.render_scene(clip, preset, o, dict(opts, skip_done=True))
    check('S6 skip_done: rendered again only when changed (2nd time = skipped, never the source video itself)',
          r2['status'] == 'skipped' and os.path.normcase(r2['mp4']) == os.path.normcase(want), (r2['status'], r2['mp4']))
    try: app.render_item(clip, os.path.join(tmp, 'n.mp4'), preset, dict(vertical=True, sound='file', audio_file=song), narr={'wav': 'x'}); nr = 'rendered'
    except ValueError as e: nr = str(e)
    check('S6 narration on a video -> ValueError', 'narration' in nr, nr)
    vc = app.video_check(clip, os.path.join(vd, 'dance.wav'))
    check('S6 Check frames for a video: sheet next to it + the plan in the notes', vc['ok'] and os.path.isfile(vc['sheet'])
          and any(n.startswith('Plan: chorus') for n in vc['notes']), vc['notes'][-1:])

    # ---------------------------------------------------------------- S7
    import mcp_server as ms
    v2, s2 = ms.video_song_inputs(clip)
    check('S7 MCP inputs: <video>.wav next to it is the song', os.path.normcase(s2) == os.path.normcase(os.path.join(vd, 'dance.wav')))
    errs = []
    for a in ((os.path.join(vd, 'dance.wav'),), (cut,)):
        try: ms.video_song_inputs(*a); errs.append(None)
        except ValueError as e: errs.append(str(e))
    check('S7 MCP inputs: a non-video and a video without a song -> clear ValueError', errs[0] and 'not a video' in errs[0] and errs[1] and 'no song' in errs[1], errs)
    j = ms.VideoSongJob(clip, s2, os.path.join(tmp, 'mcp'), app.FOLDER_NONE, dict(video_mode='chorus')); j.thread.join(300)
    snap = j.snapshot()
    check('S7 MCP job: finished, mp4 written, QC OK', snap['state'] == 'finished' and snap.get('mp4') and os.path.isfile(snap['mp4'])
          and str(snap.get('status', '')).startswith('Done · QC OK'), {k: snap.get(k) for k in ('state', 'status', 'mp4', 'error')})
finally:
    app.close_log()
    shutil.rmtree(tmp, ignore_errors=True)

print(f'\n{"ALL PASS" if not fails else f"{len(fails)} FAIL: " + ", ".join(fails)}')
sys.exit(1 if fails else 0)
