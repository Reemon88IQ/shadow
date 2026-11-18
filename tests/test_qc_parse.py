"""Unit checks for qc.py: parsing real ffmpeg output (tests/fixtures/qc/*.txt) and classification. No render, no browser.
Usage: python tests/test_qc_parse.py      exit 0 = all PASS, 1 = any FAIL
Samples come from tests/fixtures/qc/make_samples.py (ffmpeg 9.0.2); regenerate them after an ffmpeg upgrade."""
import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import qc

FIX = os.path.join(ROOT, 'tests', 'fixtures', 'qc')
results = []
def check(label, got, want):
    ok = got == want; results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {got!r}" + ('' if ok else f'   (expected {want!r})'))
def sample(name):
    with open(os.path.join(FIX, name + '.txt'), encoding='utf-8') as fh: return fh.read()
def rounded(segs): return [tuple(round(x, 2) for x in s) for s in segs]
def measured(name, dur, w=640, h=360, audio=True):
    t = sample(name); e = qc.parse_ebur128(t)
    return dict(width=w, height=h, duration=dur, has_audio=audio, lufs=e['lufs'], tp=e['tp'],
                black=qc.parse_blackdetect(t), freeze=qc.parse_freezedetect(t, dur))
EXP = lambda dur, silent=False, w=640, h=360: dict(width=w, height=h, duration=dur, silent=silent)

# ---------------------------------------------------------------- parsing real samples
t = sample('black_then_frozen_tone')
check('A ebur128', qc.parse_ebur128(t), {'lufs': -21.8, 'tp': -17.1})
check('A blackdetect', rounded(qc.parse_blackdetect(t)), [(0.0, 1.0, 1.0)])
check('A freeze to end of file (start only)', rounded(qc.parse_freezedetect(t, 7.0)), [(1.0, 7.0, 6.0)])

t = sample('freeze_with_end_no_audio')
check('B ebur128 with no audio', qc.parse_ebur128(t), {'lufs': None, 'tp': None})
check('B blackdetect none', qc.parse_blackdetect(t), [])
check('B freeze with start and end', rounded(qc.parse_freezedetect(t, 8.0)), [(0.0, 6.0, 6.0)])

t = sample('black_middle_loud')
check('C ebur128 loud', qc.parse_ebur128(t), {'lufs': -4.3, 'tp': 0.5})
check('C blackdetect middle', rounded(qc.parse_blackdetect(t)), [(2.0, 4.0, 2.0)])
check('C freeze none', qc.parse_freezedetect(t, 6.0), [])

t = sample('real_render')
check('D real render ebur128', qc.parse_ebur128(t), {'lufs': -15.8, 'tp': -1.5})
check('D real render black/freeze none', (qc.parse_blackdetect(t), qc.parse_freezedetect(t, 11.6)), ([], []))

# ---------------------------------------------------------------- parsing edge cases (hand-written, same format)
check('ebur128 -inf (digital silence)', qc.parse_ebur128('Summary:\n  Integrated loudness:\n    I:         -inf LUFS\n  True peak:\n    Peak:      -inf dBFS'),
      {'lufs': float('-inf'), 'tp': float('-inf')})
check('freeze with no file_dur keeps only closed ones', qc.parse_freezedetect('lavfi.freezedetect.freeze_start: 3', None), [])

# ---------------------------------------------------------------- classification of the real samples
check('A classify', qc.classify(measured('black_then_frozen_tone', 7.0), EXP(7.0)),
      ('Warn', ['loudness -21.8 LUFS, outside -17..-13', 'frozen picture 1.00-7.00 s (6.00 s)']))
check('B classify, silent by design', qc.classify(measured('freeze_with_end_no_audio', 8.0, audio=False), EXP(8.0, silent=True)),
      ('Warn', ['frozen picture 0.00-6.00 s (6.00 s)']))
check('B classify, sound expected', qc.classify(measured('freeze_with_end_no_audio', 8.0, audio=False), EXP(8.0))[0], 'Fail')
check('C classify', qc.classify(measured('black_middle_loud', 6.0), EXP(6.0)),
      ('Warn', ['loudness -4.3 LUFS, outside -17..-13', 'true peak 0.5 dBTP, above -1', 'black 2.00-4.00 s (2.00 s)']))
check('D classify real render', qc.classify(measured('real_render', 11.6), EXP(11.6)), ('OK', []))

# ---------------------------------------------------------------- classification rules
good = dict(width=1920, height=1080, duration=10.0, has_audio=True, lufs=-15.0, tp=-1.5, black=[], freeze=[])
E = EXP(10.0, w=1920, h=1080)
check('all good', qc.classify(good, E), ('OK', []))
check('wrong resolution -> Fail', qc.classify(dict(good, width=1080, height=1920), E)[0], 'Fail')
check('duration off 1.5 s -> Fail', qc.classify(dict(good, duration=11.5), E)[0], 'Fail')
check('duration off 0.5 s -> Warn', qc.classify(dict(good, duration=10.5), E)[0], 'Warn')
check('duration off 0.2 s -> OK', qc.classify(dict(good, duration=10.2), E)[0], 'OK')
check('loudness -13.0 edge -> OK', qc.classify(dict(good, lufs=-13.0), E)[0], 'OK')
check('loudness -12.9 -> Warn', qc.classify(dict(good, lufs=-12.9), E)[0], 'Warn')
check('true peak -1.0 edge -> OK', qc.classify(dict(good, tp=-1.0), E)[0], 'OK')
check('true peak -0.9 -> Warn', qc.classify(dict(good, tp=-0.9), E)[0], 'Warn')
check('black only in first 0.5 s -> OK', qc.classify(dict(good, black=[(0.0, 0.5, 0.5)]), E)[0], 'OK')
check('black 0-1.2 s counts from 0.5 -> Warn', qc.classify(dict(good, black=[(0.0, 1.2, 1.2)]), E)[1], ['black 0.50-1.20 s (0.70 s)'])
check('black 0.5 s mid-video -> OK', qc.classify(dict(good, black=[(4.0, 4.5, 0.5)]), E)[0], 'OK')
check('freeze 5.0 s edge -> OK', qc.classify(dict(good, freeze=[(1.0, 6.0, 5.0)]), E)[0], 'OK')
check('no audio when expected -> Fail', qc.classify(dict(good, has_audio=False, lufs=None, tp=None), E)[0], 'Fail')
check('silent by design skips audio', qc.classify(dict(good, has_audio=False, lufs=None, tp=None), dict(E, silent=True)), ('OK', []))
check('silent by design ignores loud audio', qc.classify(dict(good, lufs=-3.0, tp=1.0), dict(E, silent=True)), ('OK', []))
check('Fail wins over Warn', qc.classify(dict(good, width=1, lufs=-30.0), E)[0], 'Fail')

print(f'{sum(results)}/{len(results)} passed')
sys.exit(0 if all(results) else 1)
