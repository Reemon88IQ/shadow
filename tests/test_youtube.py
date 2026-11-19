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

# ---------------------------------------------------------------- body
b = yt.video_body('A <b>' + 'x' * 200, 'd>', ['t1', 'two words', 'z' * 600], 'private', False, '10')
check('body: title cleaned + cut to 100, < > removed, tag limit 500', len(b['snippet']['title']) == 100 and '<' not in b['snippet']['title']
      and b['snippet']['description'] == 'd' and b['snippet']['tags'] == ['t1', 'two words'], b['snippet'])
check('body: private + made for kids flag', b['status'] == {'privacyStatus': 'private', 'selfDeclaredMadeForKids': False})
for bad in (dict(title=''), dict(title='a', privacy='secret')):
    try: yt.video_body(**bad); check(f'body rejects {bad}', False)
    except yt.YouTubeError: check(f'body rejects {bad}', True)

# ---------------------------------------------------------------- fake Google
S = dict(received=bytearray(), fail_once=True, puts=0, meta=None, refreshes=0, token_posts=[])
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
check('upload: metadata from metadata.txt, private, not for kids, music category', S['meta']['snippet']['title'].startswith("Don't Stop the Car –")
      and S['meta']['status'] == {'privacyStatus': 'private', 'selfDeclaredMadeForKids': False} and S['meta']['snippet']['categoryId'] == '10', S['meta'])
check('upload: result has the video id + Studio link', r['id'] == 'VID123' and r['studio'].endswith('/VID123/edit') and r['privacy'] == 'private', r)
check('upload: progress reached 100%', prog and prog[-1] == len(data))

ev = threading.Event(); ev.set(); S['received'] = bytearray()
try: yt.upload(full_mp4, 'UC_moon', b, cancel=ev); check('cancel stops the upload', False)
except yt.YouTubeError as e: check('cancel stops the upload', 'cancel' in str(e), e)

t = yt.load_tokens(); t['UC_moon'].update(expires=0, refresh_token='GONE'); yt.save_tokens(t)
try: yt.access_token('UC_moon'); check('revoked/expired permission -> "sign in again"', False)
except yt.YouTubeError as e: check('revoked/expired permission -> "sign in again"', 'sign in again' in str(e), e)
try: yt.find_channel('Waddle Club'); check('unknown channel -> error naming the signed-in ones', False)
except yt.YouTubeError as e: check('unknown channel -> error naming the signed-in ones', 'Moon Shift' in str(e), e)

srv.shutdown()

# ---------------------------------------------------------------- the YouTube... dialog (upload faked)
try:
    import app
    a = app.App(); a.withdraw()
    scene = os.path.join(song, 'x.html'); open(scene, 'w').close()
    a.files = [scene]; a.out_mp4 = {scene: [full_mp4, short_mp4, os.path.join(out, 'gone.mp4')]}
    check('dialog items: the rendered videos of the rows (missing files skipped)', a.youtube_items() == [(full_mp4, scene), (short_mp4, scene)], a.youtube_items())
    calls = []
    def fake_upload(mp4, channel=None, meta=None, privacy='private', candidates=(), progress=lambda d, t: None, cancel=None, overrides=None):
        calls.append(dict(mp4=os.path.basename(mp4), channel=channel, title=(meta or {}).get('title'), **(overrides or {})))
        progress(5, 10); progress(10, 10)
        return dict(id='V' + str(len(calls)), url='u', studio='https://studio.youtube.com/video/V/edit', privacy='private', title='t', channel=channel)
    t = yt.load_tokens(); t['UC_moon'] = dict(title='Moon Shift', refresh_token='RT1'); yt.save_tokens(t)
    d = a.open_youtube_dialog(); d.ytm.upload_video = fake_upload
    check('dialog: titles from metadata.txt in the scene folder, Made for Kids from it, Private, channel preselected',
          [d.tree.set(i['iid'], 'title') for i in d.items] == [fm['title'], sm['title']] and d.kids.get() is False
          and d.privacy.get() == 'private' and d.channel.get() == 'Moon Shift', [d.tree.set(i['iid'], 'title') for i in d.items])
    d.start(wait=True); a.update()
    check('dialog upload: both videos, right section each, private, to Moon Shift', [(c['mp4'], c['title'], c['privacy'], c['channel']) for c in calls]
          == [('x.mp4', fm['title'], 'private', 'Moon Shift'), ('x_shorts.mp4', sm['title'], 'private', 'Moon Shift')], calls)
    check('dialog: rows say Done, Upload button back, Open in Studio enabled', all(d.tree.set(i['iid'], 'status').startswith('Done') for i in d.items)
          and d.up_btn.cget('text') == 'Upload' and str(d.studio_btn.cget('state')) == 'normal')
    n = len(calls); d.start(wait=True); a.update()
    check('dialog: a second Upload does not upload the same videos again', len(calls) == n)
    d.top.destroy(); a.destroy()
except Exception as e:
    import traceback; traceback.print_exc(); check('dialog test ran', False, e)

import shutil; shutil.rmtree(tmp, ignore_errors=True)
print('\nALL PASS' if not fails else f'\n{len(fails)} FAIL: {fails}'); sys.exit(1 if fails else 0)
