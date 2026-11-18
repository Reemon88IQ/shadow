"""tools/narrate/make_words.py without the speech model: script fitting and .srt lines (pure functions).
Usage: python tests/test_make_words.py   exit 0 = all PASS"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT, 'tools', 'narrate'))
import make_words as mw

res = []
def check(name, ok, info=''):
    res.append(bool(ok)); print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else f'  ({info})'))

rec = [{'w': 'why', 's': 0.3, 'e': 0.5}, {'w': 'is', 's': 0.5, 'e': 0.6}, {'w': 'the', 's': 0.6, 'e': 0.7}, {'w': 'skye', 's': 0.7, 'e': 1.0},
       {'w': 'blue', 's': 1.0, 'e': 1.4}, {'w': 'light', 's': 2.0, 'e': 2.3}, {'w': 'scatters', 's': 2.3, 'e': 2.9}]
words, cover = mw.fit_script('Why is the sky blue? Sunlight scatters.', rec)
check('script spelling kept, times from the voice', [w['w'] for w in words] == ['Why', 'is', 'the', 'sky', 'blue?', 'Sunlight', 'scatters.']
      and words[0]['s'] == 0.3 and words[4]['e'] == 1.4 and words[6]['s'] == 2.3, words)
check('unmatched words ("sky" misheard, "Sunlight") get times between neighbours, in order',
      all(a['s'] <= b['s'] for a, b in zip(words, words[1:])) and 0.7 <= words[3]['s'] <= 1.0 and 1.4 <= words[5]['s'] <= 2.3, words)
check(f'match share reported ({cover:.0%})', 0.6 < cover < 0.9, cover)
s = mw.srt(words)
check('srt: numbered blocks, HH:MM:SS,mmm, a new line after a sentence end',
      s.startswith('1\n00:00:00,300 --> 00:00:01,400\nWhy is the sky blue?\n\n2\n') and '00:00:02,' in s, s)
long = [{'w': f'word{i}', 's': i * 0.3, 'e': i * 0.3 + 0.25} for i in range(30)]
check('srt lines stay <= 42 characters and <= 3.5 s', all(len(l) <= 42 for l in mw.srt(long).splitlines() if l.startswith('word')), mw.srt(long)[:200])
print(f'\n{sum(res)}/{len(res)} passed'); sys.exit(0 if all(res) else 1)
