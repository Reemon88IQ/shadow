"""Narration WAV -> words.json (word timings) for Shadow's narration, free and offline (faster-whisper, MIT).
Usage: python tools/narrate/make_words.py voice.wav [script.txt] [-o voice.words.json] [--model small.en] [--cpu]
- Without a script: the recognised words and their times.
- With the script (recommended): the SCRIPT's words (exact spelling, punctuation, names) get the recognised times, matched
  in order (difflib); words the recogniser missed get times spread between their neighbours.
Output: {"duration": s, "words": [{"w", "s", "e"}, ...], "source": "faster-whisper <model>"} - what Shadow's narration reads.
Also writes <out>.srt subtitles next to it (lines of max 42 characters / 3.5 s)."""
import argparse, difflib, json, os, re, sys, wave
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from subtitles import srt  # noqa: E402  (shared with the app: narrated renders get a .srt too)


def transcribe(wav, model_name='small.en', cpu=False):
    from faster_whisper import WhisperModel
    tries = [('cpu', 'int8')] if cpu else [('cuda', 'float16'), ('cpu', 'int8')]
    for dev, ct in tries:   # the GPU path needs CUDA libraries; fall back to the CPU quietly
        try:
            m = WhisperModel(model_name, device=dev, compute_type=ct)
            segs, info = m.transcribe(wav, word_timestamps=True, vad_filter=False, beam_size=5)
            out = [{'w': w.word.strip(), 's': round(w.start, 3), 'e': round(w.end, 3)} for s in segs for w in (s.words or []) if w.word.strip()]
            return out, f'faster-whisper {model_name} ({dev})'
        except Exception as e:
            last = e
    raise RuntimeError(f'faster-whisper failed: {last}')


def norm(w):
    return re.sub(r"[^a-z0-9']", '', w.lower())


def fit_script(script, rec):
    """Script words with the recogniser's times (in order); unmatched script words are spread between matched neighbours."""
    words = script.split(); a = [norm(w) for w in words]; b = [norm(r['w']) for r in rec]
    times = [None] * len(words)
    for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(blk.size): times[blk.a + k] = (rec[blk.b + k]['s'], rec[blk.b + k]['e'])
    i = 0
    while i < len(words):   # fill gaps between known times
        if times[i] is None:
            j = i
            while j < len(words) and times[j] is None: j += 1
            t0 = times[i - 1][1] if i > 0 else (rec[0]['s'] if rec else 0.0)
            t1 = times[j][0] if j < len(words) else (rec[-1]['e'] if rec else t0 + 0.3 * (j - i))
            step = max(t1 - t0, 0.05 * (j - i)) / (j - i)
            for k in range(i, j): times[k] = (round(t0 + step * (k - i), 3), round(t0 + step * (k - i + 1), 3))
            i = j
        else: i += 1
    matched = sum(1 for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks() for _ in range(blk.size))
    return [{'w': w, 's': t[0], 'e': t[1]} for w, t in zip(words, times)], matched / max(len(words), 1)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('wav'); ap.add_argument('script', nargs='?'); ap.add_argument('-o', '--out')
    ap.add_argument('--model', default='small.en'); ap.add_argument('--cpu', action='store_true'); a = ap.parse_args()
    out = a.out or os.path.splitext(a.wav)[0] + '.words.json'
    with wave.open(a.wav) as wf: dur = wf.getnframes() / wf.getframerate()
    rec, src = transcribe(a.wav, a.model, a.cpu)
    if a.script:
        words, cover = fit_script(open(a.script, encoding='utf-8').read(), rec)
        print(f'{len(words)} script words, {cover:.0%} matched to the voice ({len(rec)} recognised)')
        if cover < 0.8: print('WARNING: under 80% matched - is this the right script for this voice?')
    else:
        words = rec; print(f'{len(words)} words recognised (no script: spelling as heard)')
    json.dump({'duration': round(dur, 3), 'words': words, 'source': src}, open(out, 'w', encoding='utf-8'), ensure_ascii=False)
    open(os.path.splitext(out)[0].replace('.words', '') + '.srt', 'w', encoding='utf-8').write(srt(words))
    print('wrote', out, 'and', os.path.splitext(out)[0].replace('.words', '') + '.srt', f'({src})')


if __name__ == '__main__':
    main()
