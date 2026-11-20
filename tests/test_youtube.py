"""youtube.py without Google: a local fake server plays accounts.google.com + the YouTube API (token, channels, resumable
upload with one 503 in the middle). Checks metadata.txt parsing, the videos.insert body, sign-in (loopback + PKCE + state),
token refresh, a chunked upload with resume, cancel, and errors. No network, no real account, temp files only.
Usage: python tests/test_youtube.py     exit 0 = all PASS"""
import base64, hashlib, http.server, json, os, sys, tempfile, threading, urllib.parse, urllib.request
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import youtube as yt

fails = []
def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f'  ({info})' if info and not ok else ''))
    if not ok: fails.append(name)

META = """=== FULL VIDEO (dont-stop-the-car.mp4) ===

TITLE: Don't Stop the Car – Synthwave Road Trip Song (Night Drive)

DESCRIPTION:
Windows down, the night is ours.

0:00 Intro
0:16 Verse 1

TAGS: don't stop the car, synthwave, road trip song
AUDIENCE: Made for Kids = NO
AI: yes (Suno vocals)
PUBLISH: 5:00 AM Sydney time


=== SHORT (dont-stop-the-car_shorts.mp4) ===

TITLE: Don't Stop the Car 🚗 #shorts
DESCRIPTION:
The chorus.
TAGS: synthwave, shorts
AUDIENCE: Made for Kids = YES
"""

# ---------------------------------------------------------------- metadata
m = yt.parse_metadata(META)
check('metadata: two sections', set(m) == {'full', 'short'}, list(m))
check('metadata: title, multi-line description with chapters, tags', m['full']['title'].startswith("Don't Stop the Car –")
      and m['full']['description'] == 'Windows down, the night is ours.\n\n0:00 Intro\n0:16 Verse 1'
      and m['full']['tags'] == ["don't stop the car", 'synthwave', 'road trip song'], m['full'])
check('metadata: Made for Kids NO / YES', m['full']['kids'] is False and m['short']['kids'] is True, (m['full']['kids'], m['short']['kids']))
check('metadata: no sections -> full', list(yt.parse_metadata('TITLE: A\nTAGS: x')) == ['full'])
check('metadata: AI: yes / missing / no', m['full']['ai'] is True and m['short']['ai'] is None
      and yt.parse_metadata('AI: No')['full']['ai'] is False and yt.parse_metadata('AI: maybe')['full']['ai'] is None)
check('metadata: PUBLISH without a date -> no schedule (text kept)', m['full']['publish_at'] is None and m['full']['publish'] == '5:00 AM Sydney time')

# ---------------------------------------------------------------- PUBLISH -> publishAt (UTC)
P = yt.parse_publish
check('publish: "Thu 19 Nov 2026, 5:00 AM Sydney time" -> 18:00Z the day before (AEDT +11)',
      P('Thu 19 Nov 2026, 5:00 AM Sydney time (proposed; change if that slot is taken)') == '2026-11-18T18:00:00Z')
check('publish: Sydney in winter is +10', P('2026-07-01 05:00 Sydney') == '2026-06-30T19:00:00Z')
check('publish: ISO with offset / Z / UTC word / Baghdad / pm', P('2026-06-25T05:00:00+10:00') == '2026-06-24T19:00:00Z'
      and P('2026-11-19 05:00Z') == '2026-11-19T05:00:00Z' and P('Nov 19 2026 17:30 UTC') == '2026-11-19T17:30:00Z'
      and P('19 Nov 2026, 12:00 PM Baghdad') == '2026-11-19T09:00:00Z' and P('Saturday 5 December 2026 9 pm Sydney') == '2026-12-05T10:00:00Z')
check('publish: no date, no time, nonsense -> None', P('5:00 AM Sydney time') is None and P('19 Nov 2026') is None and P('') is None
      and P('31 Feb 2026 5:00') is None)
import datetime as _dt
check('publish: Sydney fallback rule without tz data (first Sunday Oct..Apr = +11)', yt._sydney_offset(_dt.datetime(2026, 11, 19, 5)) == 11
      and yt._sydney_offset(_dt.datetime(2026, 7, 1)) == 10 and yt._sydney_offset(_dt.datetime(2026, 4, 5, 2)) == 11
      and yt._sydney_offset(_dt.datetime(2026, 4, 5, 4)) == 10 and yt._sydney_offset(_dt.datetime(2026, 10, 4, 3)) == 11)
two = yt.parse_metadata('=== FULL ===\nTITLE: a\nPUBLISH: 2030-01-02 05:00 UTC\n=== SHORT ===\nTITLE: b\nPUBLISH: same time as the full video\n')
check('publish: a Short that says "same" takes the full video time', two['short']['publish_at'] == two['full']['publish_at'] == '2030-01-02T05:00:00Z')

tmp = tempfile.mkdtemp(prefix='shadow_yt_'); yt.HOME = tmp
yt.CLIENT_FILE = os.path.join(tmp, 'client.json'); yt.TOKENS_FILE = os.path.join(tmp, 'tokens.json')
song = os.path.join(tmp, 'song'); os.makedirs(song)
open(os.path.join(song, 'metadata.txt'), 'w', encoding='utf-8').write(META)
out = os.path.join(tmp, 'Music', 'x'); os.makedirs(out)
full_mp4 = os.path.join(out, 'x.mp4'); short_mp4 = os.path.join(out, 'x_shorts.mp4')
data = os.urandom(2_500_000); open(full_mp4, 'wb').write(data); open(short_mp4, 'wb').write(b'1234')
fm, p = yt.metadata_for(full_mp4, [song]); sm, _ = yt.metadata_for(short_mp4, [song])
check('metadata_for: found in the scene folder, full for x.mp4, short for x_shorts.mp4', p and fm['title'].startswith("Don't") and sm['title'].endswith('#shorts'))
check('metadata_for: nothing -> None', yt.metadata_for(full_mp4)[0] is None)
check('thumbnail: none next to the video -> None', yt.find_thumbnail(full_mp4) is None)
thumb1 = os.path.join(out, 'x_thumb.jpg'); open(thumb1, 'wb').write(b'\xff\xd8old')
thumb2 = os.path.join(out, 'x_thumb_2.jpg'); open(thumb2, 'wb').write(b'\xff\xd8new'); os.utime(thumb1, (1, 1))
open(os.path.join(out, 'x_shorts_thumb.jpg'), 'wb').write(b'\xff\xd8s')
cover = os.path.join(song, 'cover.png'); open(cover, 'wb').write(b'\x89PNG')
check('thumbnail: newest <video>_thumb*.jpg next to the mp4; a Short gets none by itself',
      yt.find_thumbnail(full_mp4) == thumb2 and yt.find_thumbnail(short_mp4) is None, (yt.find_thumbnail(full_mp4), yt.find_thumbnail(short_mp4)))
check('thumbnail: THUMBNAIL: in metadata.txt (relative to it) wins, also for a Short; missing file -> None',
      yt.find_thumbnail(short_mp4, {'thumbnail': 'cover.png'}, os.path.join(song, 'metadata.txt')) == cover
      and yt.find_thumbnail(full_mp4, {'thumbnail': 'gone.jpg'}, os.path.join(song, 'metadata.txt')) is None)

# ---------------------------------------------------------------- body
b = yt.video_body('A <b>' + 'x' * 200, 'd>', ['t1', 'two words', 'z' * 600], 'private', False, '10')
check('body: title cleaned + cut to 100, < > removed, tag limit 500', len(b['snippet']['title']) == 100 and '<' not in b['snippet']['title']
      and b['snippet']['description'] == 'd' and b['snippet']['tags'] == ['t1', 'two words'], b['snippet'])
check('body: private + made for kids flag', b['status'] == {'privacyStatus': 'private', 'selfDeclaredMadeForKids': False})
b2 = yt.video_body('a', privacy='public', ai=True, publish_at='2030-01-02T05:00:00Z')
check('body: ai -> containsSyntheticMedia, publish_at -> publishAt and forced Private', b2['status'] == {'privacyStatus': 'private',
      'selfDeclaredMadeForKids': False, 'containsSyntheticMedia': True, 'publishAt': '2030-01-02T05:00:00Z'}, b2['status'])
check('body: ai False is sent, ai None is left out', yt.video_body('a', ai=False)['status'].get('containsSyntheticMedia') is False
      and 'containsSyntheticMedia' not in yt.video_body('a')['status'])
for bad in (dict(title=''), dict(title='a', privacy='secret')):
    try: yt.video_body(**bad); check(f'body rejects {bad}', False)
    except yt.YouTubeError: check(f'body rejects {bad}', True)

# ---------------------------------------------------------------- fake Google
S = dict(received=bytearray(), fail_once=True, puts=0, meta=None, refreshes=0, token_posts=[], thumbs=[])
class Fake(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body=b'', headers=()):
        self.send_response(code)
        for k, v in headers: self.send_header(k, v)
        self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0); body = self.rfile.read(n)
        if self.path.startswith('/token'):
            f = dict(urllib.parse.parse_qsl(body.decode())); S['token_posts'].append(f)
            if f.get('grant_type') == 'authorization_code':
                ok = f.get('code') == 'CODE1' and base64.urlsafe_b64encode(hashlib.sha256(f['code_verifier'].encode()).digest()).rstrip(b'=').decode() == S['challenge']
                return self._send(200 if ok else 400, json.dumps(dict(access_token='AT1', refresh_token='RT1', expires_in=3600) if ok else dict(error='invalid_grant')).encode())
            if f.get('refresh_token') == 'RT1': S['refreshes'] += 1; return self._send(200, json.dumps(dict(access_token='AT2', expires_in=3600)).encode())
            return self._send(400, b'{"error":"invalid_grant"}')
        if self.path.startswith('/thumb'):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            S['thumbs'].append(dict(video=q.get('videoId'), body=body, ctype=self.headers.get('Content-Type'), auth=self.headers.get('Authorization')))
            if S.get('thumb_403'): return self._send(403, b'{"error":{"message":"The authenticated user doesnt have permissions to upload and set custom video thumbnails.","errors":[{"reason":"forbidden"}]}}')
            return self._send(200, b'{"items":[{"default":{"url":"x"}}]}')
        if self.path.startswith('/upload'):
            S['meta'] = json.loads(body); S['upload_auth'] = self.headers.get('Authorization')
            return self._send(200, b'', [('Location', f'http://127.0.0.1:{PORT}/session1')])
        self._send(404)
    def do_GET(self):
        if self.path.startswith('/api/channels'):
            ok = self.headers.get('Authorization') in ('Bearer AT1', 'Bearer AT2')
            return self._send(200 if ok else 401, json.dumps(dict(items=[dict(id='UC_moon', snippet=dict(title='Moon Shift'))])).encode())
        self._send(404)
    def do_PUT(self):
        n = int(self.headers.get('Content-Length') or 0); body = self.rfile.read(n); S['puts'] += 1
        rng = self.headers.get('Content-Range', '')
        if rng.startswith('bytes */'):
            return self._send(308, b'', [('Range', f"bytes=0-{len(S['received']) - 1}")] if S['received'] else [])
        if S['fail_once'] and len(S['received']) >= 1_000_000:
            S['fail_once'] = False; return self._send(503, b'{"error":{"message":"busy"}}')
        start = int(rng.split(' ')[1].split('-')[0]); total = int(rng.split('/')[1])
        if start != len(S['received']): return self._send(400, b'{"error":{"message":"bad offset"}}')
        S['received'] += body
        if len(S['received']) == total:
            return self._send(200, json.dumps(dict(id='VID123', status=dict(privacyStatus='private'))).encode())
        return self._send(308, b'', [('Range', f"bytes=0-{len(S['received']) - 1}")])
    def log_message(self, *a): pass

srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fake); PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{PORT}'
yt.TOKEN_URL = base + '/token'; yt.API = base + '/api'; yt.UPLOAD_URL = base + '/upload'; yt.AUTH_URL = base + '/auth'
yt.THUMB_URL = base + '/thumb'

try:
    yt.load_client(); check('no client file -> clear error', False)
except yt.YouTubeError as e: check('no client file -> clear error', 'OAuth client' in str(e), e)
json.dump({'installed': {'client_id': 'CID', 'client_secret': 'SEC'}}, open(yt.CLIENT_FILE, 'w'))
check('client file read (installed JSON from Google Cloud)', yt.load_client() == {'client_id': 'CID', 'client_secret': 'SEC'})

def fake_browser(url):
    """What Google does: the user signs in, Google redirects to the loopback address with ?code&state."""
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    S['challenge'] = q['code_challenge']; S['auth_q'] = q
    def go(): urllib.request.urlopen(q['redirect_uri'] + '/?' + urllib.parse.urlencode(dict(code='CODE1', state=q['state'])), timeout=5).read()
    threading.Thread(target=go, daemon=True).start()
    return True
ch = yt.sign_in(open_browser=fake_browser, timeout=20)
q = S['auth_q']
check('sign-in URL: loopback 127.0.0.1, PKCE S256, offline, upload + readonly scopes', q['redirect_uri'].startswith('http://127.0.0.1:')
      and q['code_challenge_method'] == 'S256' and q['access_type'] == 'offline' and 'youtube.upload' in q['scope'] and 'youtube.readonly' in q['scope'], q)
check('sign-in: channel found and refresh token stored (no password anywhere)', ch == {'id': 'UC_moon', 'title': 'Moon Shift'}
      and yt.load_tokens()['UC_moon']['refresh_token'] == 'RT1' and 'password' not in open(yt.TOKENS_FILE).read().lower())
check('channels() / find_channel by name, any case', yt.channels() == [ch] and yt.find_channel('moon shift')['id'] == 'UC_moon')

def bad_browser(url):
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    threading.Thread(target=lambda: urllib.request.urlopen(q['redirect_uri'] + '/?code=X&state=WRONG', timeout=5).read(), daemon=True).start()
try: yt.sign_in(open_browser=bad_browser, timeout=10); check('sign-in with a wrong state is refused', False)
except yt.YouTubeError as e: check('sign-in with a wrong state is refused', 'state' in str(e), e)

t = yt.load_tokens(); t['UC_moon']['expires'] = 0; yt.save_tokens(t)
check('expired access token -> refreshed with the refresh token', yt.access_token('UC_moon') == 'AT2' and S['refreshes'] == 1)

prog = []; yt.CHUNK = 512 * 1024   # 5 chunks, the 503 hits the third
r = yt.upload_video(full_mp4, 'Moon Shift', candidates=[song], progress=lambda d, tot: prog.append(d))
check('upload: whole file arrived byte for byte (chunks + one 503 resumed)', bytes(S['received']) == data and not S['fail_once'], len(S['received']))
check('upload: metadata from metadata.txt, private, not for kids, AI label, no schedule, music category', S['meta']['snippet']['title'].startswith("Don't Stop the Car –")
      and S['meta']['status'] == {'privacyStatus': 'private', 'selfDeclaredMadeForKids': False, 'containsSyntheticMedia': True}
      and S['meta']['snippet']['categoryId'] == '10', S['meta'])
check('upload: result has the video id + Studio link', r['id'] == 'VID123' and r['studio'].endswith('/VID123/edit') and r['privacy'] == 'private', r)
check('upload: progress reached 100%', prog and prog[-1] == len(data))
check('upload: thumbnail set on that video (newest _thumb, jpeg, signed in)', r['thumb'] == 'set' and len(S['thumbs']) == 1
      and S['thumbs'][0]['video'] == 'VID123' and S['thumbs'][0]['body'] == b'\xff\xd8new' and S['thumbs'][0]['ctype'] == 'image/jpeg'
      and S['thumbs'][0]['auth'] == 'Bearer AT2', (r.get('thumb'), S['thumbs']))

def again(**kw):
    S['received'] = bytearray(); S['thumbs'] = []
    return yt.upload_video(full_mp4, 'Moon Shift', candidates=[song], **kw)
r = again(overrides=dict(publish_at='2030-01-02T05:00:00Z', ai=False), thumbnail=False)
check('upload: scheduled (publishAt, Private), AI override False, thumbnail off -> no thumbnails call',
      S['meta']['status'].get('publishAt') == '2030-01-02T05:00:00Z' and S['meta']['status']['containsSyntheticMedia'] is False
      and r['publish_at'] == '2030-01-02T05:00:00Z' and not S['thumbs'] and r['thumb'] is None, (S['meta']['status'], r))
r = again(overrides=dict(publish_at='2001-01-01T00:00:00Z'), thumbnail=cover)
check('upload: a time already past is not sent; an explicit PNG thumbnail', 'publishAt' not in S['meta']['status'] and r['publish_at'] is None
      and S['thumbs'] and S['thumbs'][0]['ctype'] == 'image/png', (S['meta']['status'], S['thumbs']))
r = again(overrides=dict(publish_at='2030-01-02T05:00:00Z'), schedule=False)
check('upload: schedule=False ignores publish_at', 'publishAt' not in S['meta']['status'])
S['thumb_403'] = True; r = again()
check('upload: thumbnail refused (unverified channel) -> video still uploaded, thumb says why', r['id'] == 'VID123'
      and r['thumb'].startswith('error') and 'verif' in r['thumb'], r.get('thumb'))
S['thumb_403'] = False
big = os.path.join(out, 'big.jpg'); open(big, 'wb').write(b'\xff' * (yt.THUMB_MAX + 1))
try: yt.set_thumbnail('VID123', 'UC_moon', big); check('thumbnail over 2 MB refused before sending', False)
except yt.YouTubeError as e: check('thumbnail over 2 MB refused before sending', '2 MB' in str(e), e)

ev = threading.Event(); ev.set(); S['received'] = bytearray()
try: yt.upload(full_mp4, 'UC_moon', b, cancel=ev); check('cancel stops the upload', False)
except yt.YouTubeError as e: check('cancel stops the upload', 'cancel' in str(e), e)

t = yt.load_tokens(); t['UC_moon'].update(expires=0, refresh_token='GONE'); yt.save_tokens(t)
try: yt.access_token('UC_moon'); check('revoked/expired permission -> "sign in again"', False)
except yt.YouTubeError as e: check('revoked/expired permission -> "sign in again"', 'sign in again' in str(e), e)
try: yt.find_channel('Waddle Club'); check('unknown channel -> error naming the signed-in ones', False)
except yt.YouTubeError as e: check('unknown channel -> error naming the signed-in ones', 'Moon Shift' in str(e), e)

# ---------------------------------------------------------------- MCP: shadow_upload_preview / shadow_upload (tools/mcp_server.py)
t = yt.load_tokens(); t['UC_moon'].update(refresh_token='RT1', expires=0); yt.save_tokens(t)
sys.path.insert(0, os.path.join(ROOT, 'tools')); import time as _time
import mcp_server as ms
check('mcp: same youtube module as the test (fake Google applies)', ms.yt is yt)
S['received'] = bytearray(); S['thumbs'] = []; S['meta'] = None
pv = ms.upload_settings(full_mp4, scene=os.path.join(song, 'x.html'))['view']
check('mcp preview: metadata.txt next to the scene, title, AI label, thumbnail, private, nothing sent',
      pv['title'] == fm['title'] and pv['ai_label'] is True and pv['thumbnail'] == thumb2 and pv['privacy'] == 'private'
      and pv['channel'] == 'Moon Shift' and pv['metadata'].endswith('metadata.txt') and S['meta'] is None and not S['received'], pv)
check('mcp preview: notes say PUBLISH has no date', any('no date' in n for n in pv['notes']), pv['notes'])
pv2 = ms.upload_settings(short_mp4, scene=os.path.join(song, 'x.html'), ai='no', thumbnail='none', publish='2030-01-02 05:00 UTC')['view']
check('mcp preview: Short section, ai=no, thumbnail=none, publish text -> UTC + Private', pv2['title'] == sm['title'] and pv2['ai_label'] is False
      and pv2['thumbnail'] is None and pv2['publish_at_utc'] == '2030-01-02T05:00:00Z' and pv2['privacy'] == 'private', pv2)
for bad, why in ((dict(video=os.path.join(out, 'x.mov')), '.mp4'), (dict(video=full_mp4, publish='tomorrow'), 'no date'),
                 (dict(video=full_mp4, ai='maybe'), "'auto'"), (dict(video=full_mp4, channel='Waddle Club'), 'not signed in'),
                 (dict(video=os.path.join(out, 'gone.mp4')), 'not found'), (dict(video=full_mp4, meta=os.path.join(tmp, 'none.txt')), 'not found')):
    try: ms.upload_settings(**bad); check(f'mcp preview refuses {why}', False)
    except (ValueError, yt.YouTubeError) as e: check(f'mcp preview refuses {why}', why in str(e), e)
s_ = ms.upload_settings(full_mp4, scene=os.path.join(song, 'x.html'), publish='2030-01-02 05:00 UTC')
u = ms.Upload(s_['view'], s_['args']); ms.UPLOADS[u.id] = u; ms.UPLOAD_ORDER.append(u.id); u.thread.join(60)
snap = ms.get_upload().snapshot()
check('mcp upload: done, the whole file, the previewed body (title, AI, publishAt), thumbnail set, links',
      snap['state'] == 'done' and snap['percent'] == 100 and bytes(S['received']) == data and S['meta']['snippet']['title'] == fm['title']
      and S['meta']['status'].get('containsSyntheticMedia') is True and S['meta']['status'].get('publishAt') == '2030-01-02T05:00:00Z'
      and snap['thumbnail'] == 'set' and snap['studio'].endswith('/VID123/edit') and snap['scheduled_utc'] == '2030-01-02T05:00:00Z', snap)
S['received'] = bytearray(); u2 = ms.Upload(s_['view'], s_['args']); u2.cancel.set(); u2.thread.join(30)
check('mcp upload cancel -> state cancelled, error says cancelled', u2.snapshot()['state'] == 'cancelled' and 'cancel' in u2.snapshot()['error'], u2.snapshot())
import assistant as A
check('assistant: preview/progress/cancel/channels pre-approved, shadow_upload only by "Allow once"',
      {'mcp__shadow__shadow_upload_preview', 'mcp__shadow__shadow_youtube', 'mcp__shadow__shadow_upload_progress'} <= set(A.SAFE_TOOLS)
      and 'mcp__shadow__shadow_upload' not in A.SAFE_TOOLS and 'mcp__shadow__shadow_upload' in A.ASK_TOOLS)

srv.shutdown()

# ---------------------------------------------------------------- the YouTube... dialog (upload faked)
try:
    import app
    real_log = app.LOG; stat = lambda p: (os.path.getsize(p), os.path.getmtime(p)) if os.path.isfile(p) else None; real_before = stat(real_log)
    app.LOG = os.path.join(tmp, 'shadow.log'); app.LOG_DIR = os.path.join(tmp, 'logs')   # never the real ~/shadow.log
    for h in list(app.log.handlers): app.log.removeHandler(h)
    a = app.App(); a.withdraw()
    scene = os.path.join(song, 'x.html'); open(scene, 'w').close()
    a.files = [scene]; a.out_mp4 = {scene: [full_mp4, short_mp4, os.path.join(out, 'gone.mp4')]}
    check('dialog items: the rendered videos of the rows (missing files skipped)', a.youtube_items() == [(full_mp4, scene), (short_mp4, scene)], a.youtube_items())
    calls = []
    def fake_upload(mp4, channel=None, meta=None, privacy='private', candidates=(), progress=lambda d, t: None, cancel=None, overrides=None,
                    meta_path=None, thumbnail=True, schedule=True):
        calls.append(dict(mp4=os.path.basename(mp4), channel=channel, title=(meta or {}).get('title'), meta_path=meta_path,
                          thumbnail=thumbnail, schedule=schedule, **(overrides or {})))
        progress(5, 10); progress(10, 10)
        sh = mp4.endswith('_shorts.mp4')
        return dict(id='V' + str(len(calls)), url='u', studio='https://studio.youtube.com/video/V/edit', privacy='private', title='t', channel=channel,
                    publish_at=None if sh else '2030-01-02T05:00:00Z', thumb='error: 403 verify' if sh else 'set')
    t = yt.load_tokens(); t['UC_moon'] = dict(title='Moon Shift', refresh_token='RT1'); yt.save_tokens(t)
    d = a.open_youtube_dialog(); d.ytm.upload_video = fake_upload
    check('dialog: titles from metadata.txt in the scene folder, Made for Kids from it, Private, channel preselected',
          [d.tree.set(i['iid'], 'title') for i in d.items] == [fm['title'], sm['title']] and d.kids.get() is False
          and d.privacy.get() == 'private' and d.channel.get() == 'Moon Shift', [d.tree.set(i['iid'], 'title') for i in d.items])
    d.start(wait=True); a.update()
    check('dialog upload: both videos, right section each, private, to Moon Shift', [(c['mp4'], c['title'], c['privacy'], c['channel']) for c in calls]
          == [('x.mp4', fm['title'], 'private', 'Moon Shift'), ('x_shorts.mp4', sm['title'], 'private', 'Moon Shift')], calls)
    check('dialog: AI checkbox from metadata.txt, thumbnail + schedule on, passed with the metadata path',
          [(c['ai'], c['thumbnail'], c['schedule'], bool(c['meta_path'])) for c in calls] == [(True, True, True, True)] * 2, calls)
    check('dialog: Publish column ("(not a date)" for "5:00 AM Sydney time")', d.tree.set(d.items[0]['iid'], 'when') == '(not a date)'
          and d.when_text({'publish_at': '2030-01-02T05:00:00Z'}).count(':') == 1 and d.when_text(None) == '', d.tree.set(d.items[0]['iid'], 'when'))
    st = [d.tree.set(i['iid'], 'status') for i in d.items]
    check('dialog: rows say Done (+ scheduled, thumbnail / no thumbnail), Upload button back, Open in Studio enabled',
          st == ['Done (private, scheduled, thumbnail)', 'Done (private, no thumbnail)']
          and d.up_btn.cget('text') == 'Upload' and str(d.studio_btn.cget('state')) == 'normal' and 'Thumbnail not set' in d.msg.cget('text'), st)
    n = len(calls); d.start(wait=True); a.update()
    check('dialog: a second Upload does not upload the same videos again', len(calls) == n)
    d.top.destroy(); a.destroy()
    for h in list(app.log.handlers): h.close(); app.log.removeHandler(h)
    tlog = open(app.LOG, encoding='utf-8').read() if os.path.isfile(app.LOG) else ''
    check('log: upload lines went to the temp log, the real ~/shadow.log is untouched', 'youtube upload' in tlog and stat(real_log) == real_before,
          (len(tlog), real_before, stat(real_log)))
except Exception as e:
    import traceback; traceback.print_exc(); check('dialog test ran', False, e)

sys.modules['app'].close_log() if 'app' in sys.modules else None   # shadow.log in the temp folder is open: close it first
import shutil; shutil.rmtree(tmp, ignore_errors=True)
print('\nALL PASS' if not fails else f'\n{len(fails)} FAIL: {fails}'); sys.exit(1 if fails else 0)
