"""tools/moonshift/song_structure.py (librosa song analysis) on a generated song - no real songs, no whisper, no network.
The synthetic song: 112 BPM kick on every beat, A B A B C B sections of 8 bars (A = verse chord, B = louder chorus chord +
snare, C = a third chord), so beats, BPM, boundaries and the chorus are known exactly. Lyrics alignment uses made-up word
times (the 'recogniser' output), so the tag parsing / naming / YouTube rules are checked without faster-whisper.
Run: python tests/test_song_structure.py  (prints PASS/FAIL, exit 1 on any FAIL; ~15 s)"""
import os, sys
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'tools', 'moonshift'), os.path.join(ROOT, 'tools', 'waddle'), ROOT]
import song_structure as ss

SR, BPM, PH = 22050, 112.0, 0.25
BAR = 4 * 60 / BPM; SEC = 8 * BAR
PLAN = 'ABABCB'
CHORDS = {'A': [110.0, 138.6, 164.8], 'B': [146.8, 185.0, 220.0], 'C': [98.0, 123.5, 146.8]}
fails = []


def check(name, ok, info=''):
    print(('PASS ' if ok else 'FAIL ') + name + (f'  ({info})' if info else '')); ok or fails.append(name)


def song():
    n = int(SEC * len(PLAN) * SR); t = np.arange(n) / SR; y = np.zeros(n)
    rng = np.random.default_rng(7)   # seeded: the same signal every run
    for i, s in enumerate(PLAN):
        a, b = int(i * SEC * SR), int((i + 1) * SEC * SR); tt = t[a:b]; g = 0.9 if s == 'B' else 0.45
        for f in CHORDS[s]:
            y[a:b] += g * 0.12 * (np.sin(2 * np.pi * f * tt) + 0.4 * np.sin(4 * np.pi * f * tt))
    beat_t = np.arange(PH, n / SR, 60 / BPM)
    kick = np.sin(2 * np.pi * 60 * np.arange(int(0.12 * SR)) / SR) * np.exp(-np.arange(int(0.12 * SR)) / (0.03 * SR))
    snare = rng.standard_normal(int(0.1 * SR)) * np.exp(-np.arange(int(0.1 * SR)) / (0.02 * SR))
    for k, bt in enumerate(beat_t):
        i = int(bt * SR); sec = PLAN[min(int(bt // SEC), len(PLAN) - 1)]
        y[i:i + len(kick)] += 0.8 * kick[:max(0, min(len(kick), n - i))]
        if sec == 'B' and k % 2 == 1: y[i:i + len(snare)] += 0.25 * snare[:max(0, min(len(snare), n - i))]
    return (y / np.abs(y).max() * 0.9).astype(np.float32), beat_t


y, truth = song(); dur = len(y) / SR

# ---- beats
b = ss.beats(y); bt = np.array(b['times'])
err = np.array([np.abs(bt - x).min() for x in truth[2:-2]])
check('BPM within 0.5 of 112', abs(b['bpm'] - BPM) <= 0.5, f"{b['bpm']} ({b['source']}, librosa's own {b['librosa_bpm']})")
check('95% of the beats within 25 ms', np.mean(err < 0.025) >= 0.95, f'median {1000 * np.median(err):.1f} ms, max {1000 * err.max():.0f} ms')
check('steady song -> one steady grid', b['source'] == 'grid', b['source'])
try:   # the old method (Waddle assemble.beats on the 30 fps analysis) on the same song, for comparison only
    import analyze_audio, assemble, tempfile, wave
    tmp = os.path.join(tempfile.mkdtemp(prefix='songst_'), 'syn.wav')
    with wave.open(tmp, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes((y * 32767).astype('<i2').tobytes())
    ot, obpm = assemble.old_beats(analyze_audio.analyze(tmp)) if hasattr(assemble, 'old_beats') else (None, None)
    if ot:
        oe = np.array([np.abs(np.array(ot) - x).min() for x in truth[2:-2]])
        print(f'     (old method: {obpm} BPM, median {1000 * np.median(oe):.1f} ms, max {1000 * oe.max():.0f} ms)')
except Exception as e: print('     (old method not compared:', e, ')')

# ---- structure (audio only)
segs, bounds = ss.structure(y, b['times'])
true_b = [i * SEC for i in range(1, len(PLAN))]
found = [min(abs(x - tb) for x in bounds) for tb in true_b]
check('every section boundary found within 1.5 s', max(found) <= 1.5, f'errors {[round(f, 2) for f in found]} s')
starts = [s['t'] for s in segs]
check('no extra segment boundaries', len(segs) <= len(PLAN) + 1, f'{len(segs)} segments at {[round(x, 1) for x in starts]}')
g = ss.guess_chapters(segs); names = dict((round(t / SEC), n) for t, n in g)
chorus_at = [i for i, s in enumerate(PLAN) if s == 'B']
check('the loud repeated part is named Chorus', all('Chorus' in names.get(i, '') for i in chorus_at), str(g))
check('the single C between choruses is the Bridge', names.get(4) == 'Bridge', str(names.get(4)))
yt = ss.youtube(g, dur)
check('YouTube list: first 0:00, >= 3 chapters', yt and yt[0].startswith('0:00 ') and len(yt) >= 3, str(yt))

# ---- lyrics: tagged parse (headers before the first tag ignored, bare 'Chorus' lines are tags too)
tagged_txt = 'MY SONG (Night Mix)\nby Moonshift\n\n[Verse 1]\na1 a2 a3\na4 a5 a6\n\nChorus\nb1 b2 b3\nb4 b5 b6\n\n[Verse 2]\nc1 c2 c3\nc4 c5 c6\n' \
             '\n[Chorus]\nb1 b2 b3\nb4 b5 b6\n\n[Bridge]\nd1 d2 d3\nd4 d5 d6\n\n[Final Chorus]\nb1 b2 b3\nb4 b5 b6\n(ooh)\n'
blocks, tagged = ss.parse_lyrics(tagged_txt)
check('tags parsed, header lines ignored', tagged and [x['name'] for x in blocks] == ['Verse 1', 'Chorus', 'Verse 2', 'Chorus', 'Bridge', 'Final Chorus']
      and blocks[0]['lines'] == ['a1 a2 a3', 'a4 a5 a6'] and blocks[-1]['lines'] == ['b1 b2 b3', 'b4 b5 b6'], str([(x['name'], x['lines']) for x in blocks]))


def fake_rec(blocks, starts, drop=()):
    """Word times as a recogniser would give them: each block's lines 4 s apart from its start; words in `drop` not heard."""
    rec = []
    for blk, t0 in zip(blocks, starts):
        for li, l in enumerate(blk['lines']):
            for wi, w in enumerate(l.split()):
                if w not in drop: rec.append(dict(w=w, s=round(t0 + 4 * li + 0.6 * wi, 2), e=round(t0 + 4 * li + 0.6 * wi + 0.5, 2)))
    return rec


starts = [6.0, 30.0, 52.0, 74.0, 98.0, 120.0]; total = 145.0
cover = ss.align(blocks, fake_rec(blocks, starts, drop={'b4'}))
check('aligned starts = sung starts', [x['t'] for x in blocks] == starts, str([x['t'] for x in blocks]))
ch = ss.lyric_chapters(blocks, True, bounds=[29.4, 74.5], beat_times=np.arange(0, total, 0.5), dur=total)
check('chapter times snap to a nearby structure boundary', (29.4, 'Chorus') in ch and (74.5, 'Chorus') in ch, str(ch))
check('first lyric chapter moved to 0:00 (vocals at 6 s)', ch[0] == (0.0, 'Verse 1'), str(ch[0]))
yt = ss.youtube(ch, total)
check('YouTube chapters from the tags', yt == ['0:00 Verse 1', '0:29 Chorus', '0:51 Verse 2', '1:14 Chorus', '1:37 Bridge', '1:59 Final Chorus', '2:09 Outro'], str(yt))

# ---- untagged lyrics: blocks from repeated lines
un = ['intro la', 'v1 one', 'v1 two', 'v1 three', 'pre x', 'pre y', 'ch a', 'ch b', 'ch c', 'v2 one', 'v2 two', 'v2 three',
      'pre x', 'pre y', 'ch a', 'ch b', 'ch c', 'br one', 'br two', 'br three', 'ch a', 'ch b', 'ch c', 'out bye']
ub, tg = ss.parse_lyrics('\n'.join(un))
check('untagged: split where repetition changes', not tg and [len(x['lines']) for x in ub] == [4, 2, 3, 3, 2, 3, 3, 3, 1], str([x['lines'] for x in ub]))
st = [12.0, 26.0, 34.0, 46.0, 62.0, 70.0, 82.0, 94.0, 106.0]
ss.align(ub, fake_rec(ub, st)); uch = ss.lyric_chapters(ub, False, bounds=[], beat_times=np.arange(0, 120, 0.5), dur=120.0)
check('untagged names by repetition', [n for _, n in uch] == ['Intro', 'Verse 1', 'Pre-Chorus', 'Chorus', 'Verse 2', 'Pre-Chorus', 'Chorus', 'Bridge', 'Final Chorus', 'Outro'], str(uch))

# ---- YouTube rules
r = ss.youtube([(0, 'Intro'), (4, 'Verse'), (30, 'Pre'), (36, 'Chorus'), (60, 'Outro')], 75.0)
check('short chapters dropped, the next one moves to 0:00', r == ['0:00 Verse', '0:36 Chorus', '1:00 Outro'], str(r))
check('fewer than 3 chapters -> none', ss.youtube([(0, 'A'), (30, 'B')], 60.0) == [])
r = ss.youtube([(0, 'Intro'), (12, 'Verse'), (40, 'Pre'), (46, 'Chorus'), (80, 'Outro')], 95.0)
check('valid list kept in order', r == ['0:00 Intro', '0:12 Verse', '0:46 Chorus', '1:20 Outro'], str(r))

print('\nALL PASS' if not fails else f'\n{len(fails)} FAIL: {fails}'); sys.exit(1 if fails else 0)
