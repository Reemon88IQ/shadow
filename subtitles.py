"""Subtitles (.srt) from word timings, shared by tools/narrate/make_words.py and the app (every narrated render gets
<video>.srt next to its mp4). Pure functions, no dependencies, so the exe can import it."""
import json, re


def words_list(words):
    """words.json content (a path, {"words": [...]} or a list of {"w","s","e"}) -> the list of words. Raises ValueError."""
    if isinstance(words, str):
        try:
            with open(words, encoding='utf-8') as fh: words = json.load(fh)
        except (OSError, ValueError) as e: raise ValueError(f'words file could not be read: {e}')
    if isinstance(words, dict) and 'words' in words: words = words['words']
    if not isinstance(words, list) or not all(isinstance(x, dict) and {'w', 's', 'e'} <= set(x) for x in words):
        raise ValueError('words must be a list of {"w","s","e"} (or {"words": [...]})')
    return words


def srt(words, max_chars=42, max_dur=3.5):
    """Subtitle lines from word timings: break at sentence ends, max_chars or max_dur."""
    def ts(t):
        ms = int(round(max(t, 0) * 1000)); return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'
    lines, cur = [], []
    for w in words:
        if cur and (len(' '.join(x['w'] for x in cur + [w])) > max_chars or w['e'] - cur[0]['s'] > max_dur):
            lines.append(cur); cur = []
        cur.append(w)
        if re.search(r'[.!?]["\')\]]?$', w['w']): lines.append(cur); cur = []
    if cur: lines.append(cur)
    return ''.join(f"{i}\n{ts(l[0]['s'])} --> {ts(l[-1]['e'])}\n{' '.join(x['w'] for x in l)}\n\n" for i, l in enumerate(lines, 1))
