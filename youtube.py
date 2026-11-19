"""Upload finished videos to YouTube with the YouTube Data API v3 - standard library only (no google-* packages).

Sign in once per channel in the browser (OAuth for installed apps: loopback + PKCE). Shadow keeps only the refresh token,
in ~/.shadow_youtube_tokens.json; the password is typed on Google's own page. Uploads are resumable (8 MB chunks, retried).
Needs your own OAuth client (Google Cloud, Desktop app) in ~/.shadow_youtube_client.json - see docs/YOUTUBE.md.
Note: until the Google Cloud project passes YouTube's API audit, YouTube locks every API upload to Private.

  python youtube.py signin                                   sign in a channel (opens the browser)
  python youtube.py channels                                 list signed-in channels
  python youtube.py upload video.mp4 [--meta metadata.txt] [--channel "Moon Shift"] [--privacy private|unlisted|public]
"""
import base64, hashlib, http.server, json, os, re, secrets, socket, sys, threading, time, urllib.error, urllib.parse, urllib.request, webbrowser

HOME = os.path.expanduser('~')
CLIENT_FILE = os.path.join(HOME, '.shadow_youtube_client.json')
TOKENS_FILE = os.path.join(HOME, '.shadow_youtube_tokens.json')
SCOPES = ['https://www.googleapis.com/auth/youtube.upload', 'https://www.googleapis.com/auth/youtube.readonly']
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
API = 'https://www.googleapis.com/youtube/v3'
UPLOAD_URL = 'https://www.googleapis.com/upload/youtube/v3/videos'
CHUNK = 8 * 1024 * 1024
PRIVACY = ('private', 'unlisted', 'public')
CATEGORY = {'music': '10', 'education': '27', 'science': '28', 'entertainment': '24', 'film': '1'}


class YouTubeError(Exception):
    pass


# ---------------------------------------------------------------- files
def load_client(path=None):
    """The OAuth client (Desktop app) as dict(client_id, client_secret). Raises YouTubeError with what to do."""
    path = path or CLIENT_FILE
    try:
        with open(path, encoding='utf-8-sig') as f: d = json.load(f)
    except FileNotFoundError:
        raise YouTubeError(f'No OAuth client file: {path}. Create a Desktop OAuth client in Google Cloud (see docs/YOUTUBE.md) '
                           'and save its JSON there.') from None
    except ValueError as e:
        raise YouTubeError(f'{path} is not valid JSON: {e}') from None
    d = d.get('installed') or d.get('web') or d
    if not d.get('client_id') or not d.get('client_secret'): raise YouTubeError(f'{path}: client_id / client_secret missing')
    return dict(client_id=d['client_id'], client_secret=d['client_secret'])


def load_tokens():
    try:
        with open(TOKENS_FILE, encoding='utf-8') as f: return json.load(f)
    except Exception: return {}


def save_tokens(t):
    tmp = TOKENS_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f: json.dump(t, f, indent=1)
    os.replace(tmp, TOKENS_FILE)


def channels():
    """Signed-in channels: [dict(id, title)] (sorted by title)."""
    return sorted(({'id': k, 'title': v.get('title', k)} for k, v in load_tokens().items()), key=lambda c: c['title'].lower())


def find_channel(name_or_id=None):
    """A signed-in channel by id or (case-insensitive) title; the only one when there is just one. Raises YouTubeError."""
    ch = channels()
    if not ch: raise YouTubeError('No YouTube channel signed in yet: press "Sign in..." (or python youtube.py signin).')
    if not name_or_id:
        if len(ch) == 1: return ch[0]
        raise YouTubeError('Several channels are signed in, choose one: ' + ', '.join(c['title'] for c in ch))
    for c in ch:
        if name_or_id in (c['id'],) or c['title'].lower() == str(name_or_id).lower(): return c
    raise YouTubeError(f'Channel not signed in: {name_or_id} (signed in: ' + ', '.join(c['title'] for c in ch) + ')')


def forget(channel_id):
    t = load_tokens(); t.pop(channel_id, None); save_tokens(t)


# ---------------------------------------------------------------- HTTP
def _req(url, data=None, headers=None, method=None, timeout=60):
    """-> (status, headers, body bytes). HTTP errors come back as their status, network errors raise YouTubeError."""
    if isinstance(data, dict): data = urllib.parse.urlencode(data).encode()
    r = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp: return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        raise YouTubeError(f'network: {getattr(e, "reason", e)}') from None


def _api_error(status, body):
    try:
        e = json.loads(body or b'{}').get('error', {})
        msg = e.get('message') if isinstance(e, dict) else e
        reason = (e.get('errors') or [{}])[0].get('reason') if isinstance(e, dict) else None
    except Exception: msg, reason = (body or b'')[:200].decode('utf-8', 'replace'), None
    hint = {'quotaExceeded': ' (daily API quota used up; try again tomorrow)', 'uploadLimitExceeded': ' (YouTube upload limit for today)',
            'invalid_grant': ' (sign in again: the permission expired or was removed)'}.get(reason or msg, '')
    return YouTubeError(f'YouTube API {status}: {msg or reason}{hint}')


# ---------------------------------------------------------------- sign in (loopback + PKCE)
def auth_request(client_id, redirect_uri, state, verifier):
    """The browser URL for Google's consent page (pure)."""
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    q = dict(client_id=client_id, redirect_uri=redirect_uri, response_type='code', scope=' '.join(SCOPES), state=state,
             code_challenge=challenge, code_challenge_method='S256', access_type='offline', prompt='consent select_account')
    return AUTH_URL + '?' + urllib.parse.urlencode(q)


def sign_in(open_browser=webbrowser.open, timeout=300, client=None):
    """Opens Google's sign-in page, waits for the answer on 127.0.0.1, stores the refresh token. -> dict(id, title)."""
    client = client or load_client()
    verifier = secrets.token_urlsafe(64); state = secrets.token_urlsafe(16); got = {}
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if 'code' in q or 'error' in q:
                got.update({k: v[0] for k, v in q.items()})
                ok = 'code' in q and q.get('state', [''])[0] == state
                body = ('<h2>Shadow: signed in. You can close this tab.</h2>' if ok else '<h2>Shadow: sign-in failed. Close this tab and try again.</h2>')
            else: body = ''
            self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.end_headers()
            self.wfile.write(body.encode())
        def log_message(self, *a): pass
    srv = http.server.HTTPServer(('127.0.0.1', 0), H)
    redirect = f'http://127.0.0.1:{srv.server_address[1]}'
    stop = threading.Event()
    def serve():
        while not got and not stop.is_set():
            try: srv.handle_request()
            except Exception: break
    srv.timeout = 0.5; th = threading.Thread(target=serve, daemon=True); th.start()
    try:
        open_browser(auth_request(client['client_id'], redirect, state, verifier))
        t0 = time.time()
        while not got and time.time() - t0 < timeout: time.sleep(0.2)
    finally:
        stop.set(); th.join(2); srv.server_close()
    if not got: raise YouTubeError('sign-in timed out (no answer from the browser)')
    if 'error' in got: raise YouTubeError(f"sign-in refused: {got['error']}")
    if got.get('state') != state: raise YouTubeError('sign-in answer did not match (state); try again')
    st, _, body = _req(TOKEN_URL, dict(code=got['code'], client_id=client['client_id'], client_secret=client['client_secret'],
                                       redirect_uri=redirect, grant_type='authorization_code', code_verifier=verifier))
    if st != 200: raise _api_error(st, body)
    tok = json.loads(body)
    if not tok.get('refresh_token'): raise YouTubeError('Google gave no refresh token; remove Shadow at myaccount.google.com/permissions and sign in again')
    ch = my_channel(tok['access_token'])
    t = load_tokens()
    t[ch['id']] = dict(title=ch['title'], refresh_token=tok['refresh_token'], access_token=tok['access_token'],
                       expires=time.time() + int(tok.get('expires_in', 3600)) - 60)
    save_tokens(t)
    return ch


def my_channel(access_token):
    st, _, body = _req(API + '/channels?part=snippet&mine=true', headers={'Authorization': 'Bearer ' + access_token})
    if st != 200: raise _api_error(st, body)
    items = json.loads(body).get('items') or []
    if not items: raise YouTubeError('this Google account has no YouTube channel')
    return dict(id=items[0]['id'], title=items[0]['snippet']['title'])


def access_token(channel_id, client=None):
    """A valid access token for a signed-in channel (refreshed when it is about to expire)."""
    t = load_tokens(); e = t.get(channel_id)
    if not e: raise YouTubeError(f'channel not signed in: {channel_id}')
    if e.get('access_token') and e.get('expires', 0) > time.time(): return e['access_token']
    client = client or load_client()
    st, _, body = _req(TOKEN_URL, dict(client_id=client['client_id'], client_secret=client['client_secret'],
                                       refresh_token=e['refresh_token'], grant_type='refresh_token'))
    if st != 200:
        err = _api_error(st, body)
        if b'invalid_grant' in (body or b''):
            raise YouTubeError(f"{e.get('title', channel_id)}: sign in again (the permission expired or was removed; "
                               'while the Google app is in Testing it lasts 7 days)')
        raise err
    tok = json.loads(body); e['access_token'] = tok['access_token']; e['expires'] = time.time() + int(tok.get('expires_in', 3600)) - 60
    save_tokens(t)
    return e['access_token']


# ---------------------------------------------------------------- metadata.txt
_FIELD = re.compile(r'^(TITLE|DESCRIPTION|TAGS|AUDIENCE|PUBLISH|CATEGORY|PRIVACY)\s*:\s*(.*)$', re.I)

def parse_metadata(text):
    """metadata.txt -> {'full': {...}, 'short': {...}} (or {'full': {...}} without sections). Each: title, description,
    tags (list), kids (bool|None), category (id|None), privacy (or None). Sections start with a line '=== ... SHORT ... ==='
    (short) or any other '=== ... ===' (full). DESCRIPTION runs until the next field line."""
    out, cur, key = {}, None, None
    def sec(name):
        return out.setdefault(name, dict(title='', description='', tags=[], kids=None, category=None, privacy=None, _desc=[]))
    for raw in (text or '').splitlines():
        line = raw.rstrip()
        m = re.match(r'^\s*===+\s*(.*?)\s*===+\s*$', line)
        if m:
            cur = sec('short' if re.search(r'\bshorts?\b', m.group(1), re.I) else 'full'); key = None; continue
        if cur is None: cur = sec('full')
        f = _FIELD.match(line.strip())
        if f:
            key, val = f.group(1).upper(), f.group(2).strip()
            if key == 'TITLE': cur['title'] = val
            elif key == 'DESCRIPTION': cur['_desc'] = [val] if val else []
            elif key == 'TAGS': cur['tags'] = [t.strip() for t in val.split(',') if t.strip()]
            elif key == 'AUDIENCE': cur['kids'] = bool(re.search(r'kids\s*=\s*yes|\byes\b', val, re.I)) if re.search(r'yes|no', val, re.I) else None
            elif key == 'CATEGORY': cur['category'] = CATEGORY.get(val.lower(), val if val.isdigit() else None)
            elif key == 'PRIVACY': cur['privacy'] = val.lower() if val.lower() in PRIVACY else None
            if key != 'DESCRIPTION': key = None
            continue
        if key == 'DESCRIPTION': cur['_desc'].append(line)
    for s in out.values():
        s['description'] = '\n'.join(s.pop('_desc')).strip()
    return out


def metadata_for(mp4, candidates=()):
    """The metadata for one video: metadata.txt next to the mp4, else in one of `candidates` (folders or files), the
    'short' section for *_shorts.mp4 / a 9:16 video, else 'full'. -> (dict | None, path | None)."""
    paths = [os.path.join(os.path.dirname(mp4), 'metadata.txt')]
    for c in candidates:
        if c: paths.append(c if c.lower().endswith('.txt') else os.path.join(c, 'metadata.txt'))
    short = os.path.splitext(os.path.basename(mp4))[0].lower().endswith('_shorts')
    for p in paths:
        if os.path.isfile(p):
            with open(p, encoding='utf-8-sig') as f: d = parse_metadata(f.read())
            m = d.get('short' if short else 'full') or d.get('full')
            if m: return m, p
    return None, None


# ---------------------------------------------------------------- upload
def video_body(title, description='', tags=(), privacy='private', kids=False, category='10', publish_at=None):
    """The videos.insert JSON (pure). YouTube limits: title 100 chars, description 5000 bytes, tags 500 chars total."""
    title = (title or '').strip()
    if not title: raise YouTubeError('a title is needed')
    if privacy not in PRIVACY: raise YouTubeError(f'privacy must be one of {", ".join(PRIVACY)}')
    title = title.replace('<', '').replace('>', '')[:100]
    desc = (description or '').replace('<', '').replace('>', '')
    while len(desc.encode('utf-8')) > 5000: desc = desc[:-1]
    keep, total = [], 0
    for t in tags or ():
        t = t.replace('<', '').replace('>', '').strip(); cost = len(t) + (2 if ' ' in t else 0) + (1 if keep else 0)
        if t and total + cost <= 500: keep.append(t); total += cost
    status = dict(privacyStatus=privacy, selfDeclaredMadeForKids=bool(kids))
    if publish_at: status.update(privacyStatus='private', publishAt=publish_at)
    return dict(snippet=dict(title=title, description=desc, tags=keep, categoryId=str(category or '10')), status=status)


def upload(mp4, channel_id, body, progress=lambda done, total: None, cancel=None, chunk=None, retries=5):
    """Resumable upload of one mp4 -> dict(id, url, studio, privacy). progress(bytes_done, total); cancel: threading.Event."""
    if not os.path.isfile(mp4): raise YouTubeError(f'video not found: {mp4}')
    size = os.path.getsize(mp4); chunk = chunk or CHUNK
    tok = access_token(channel_id)
    st, hd, rb = _req(UPLOAD_URL + '?uploadType=resumable&part=snippet,status', data=json.dumps(body).encode(), method='POST',
                      headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json; charset=UTF-8',
                               'X-Upload-Content-Type': 'video/mp4', 'X-Upload-Content-Length': str(size)})
    if st != 200: raise _api_error(st, rb)
    loc = {k.lower(): v for k, v in hd.items()}.get('location')
    if not loc: raise YouTubeError('YouTube gave no upload address')
    done, fails = 0, 0
    with open(mp4, 'rb') as f:
        while True:
            if cancel is not None and cancel.is_set(): raise YouTubeError('upload cancelled')
            f.seek(done); part = f.read(chunk); end = done + len(part) - 1
            hdr = {'Authorization': 'Bearer ' + access_token(channel_id), 'Content-Length': str(len(part)),
                   'Content-Range': f'bytes {done}-{end}/{size}' if part else f'bytes */{size}'}
            try: st, hd, rb = _req(loc, data=part, method='PUT', headers=hdr, timeout=300)
            except YouTubeError:
                st = None
            if st in (200, 201):
                progress(size, size); v = json.loads(rb)
                return dict(id=v['id'], url=f"https://youtu.be/{v['id']}", studio=f"https://studio.youtube.com/video/{v['id']}/edit",
                            privacy=v.get('status', {}).get('privacyStatus'))
            if st == 308:
                rng = {k.lower(): v for k, v in hd.items()}.get('range')
                done = int(rng.split('-')[1]) + 1 if rng else 0; fails = 0; progress(done, size); continue
            if st is not None and st not in (500, 502, 503, 504) and st < 500: raise _api_error(st, rb)
            fails += 1
            if fails > retries: raise YouTubeError(f'upload failed after {retries} retries (last status {st})')
            time.sleep(min(2 ** fails, 30))
            try:   # ask how much arrived, then go on from there
                st, hd, rb = _req(loc, data=b'', method='PUT', headers={'Authorization': 'Bearer ' + access_token(channel_id),
                                                                         'Content-Length': '0', 'Content-Range': f'bytes */{size}'})
            except YouTubeError: continue
            if st in (200, 201):   # it finished after all
                progress(size, size); v = json.loads(rb)
                return dict(id=v['id'], url=f"https://youtu.be/{v['id']}", studio=f"https://studio.youtube.com/video/{v['id']}/edit",
                            privacy=v.get('status', {}).get('privacyStatus'))
            rng = {k.lower(): v for k, v in (hd or {}).items()}.get('range')
            if st == 308: done = int(rng.split('-')[1]) + 1 if rng else 0


def upload_video(mp4, channel=None, meta=None, privacy='private', candidates=(), progress=lambda d, t: None, cancel=None, overrides=None):
    """One call for the app / CLI / MCP: channel (title or id), metadata from `meta` (dict) or metadata.txt (next to the mp4 or
    in candidates), overrides (title, description, tags, kids, category, privacy) win. -> upload() result + title, channel."""
    ch = find_channel(channel)
    if meta is None: meta, _ = metadata_for(mp4, candidates)
    m = dict(meta or {}); m.update({k: v for k, v in (overrides or {}).items() if v is not None})
    title = m.get('title') or os.path.splitext(os.path.basename(mp4))[0].replace('_shorts', '').replace('-', ' ').replace('_', ' ').title()
    body = video_body(title, m.get('description', ''), m.get('tags', ()), m.get('privacy') or privacy, bool(m.get('kids')), m.get('category') or '10')
    r = upload(mp4, ch['id'], body, progress, cancel)
    r.update(title=body['snippet']['title'], channel=ch['title']); return r


# ---------------------------------------------------------------- CLI
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog='youtube.py'); sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('signin'); sub.add_parser('channels')
    u = sub.add_parser('upload'); u.add_argument('mp4'); u.add_argument('--meta'); u.add_argument('--channel')
    u.add_argument('--privacy', default='private', choices=PRIVACY)
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'signin': c = sign_in(); print(f"signed in: {c['title']} ({c['id']})")
        elif a.cmd == 'channels': print('\n'.join(f"{c['title']}  ({c['id']})" for c in channels()) or '(none)')
        else:
            last = [0]
            def prog(d, t):
                if time.time() - last[0] > 2 or d == t: last[0] = time.time(); print(f'  {d * 100 // max(t, 1)}%  {d // 1048576}/{t // 1048576} MB', flush=True)
            meta = None
            if a.meta:
                with open(a.meta, encoding='utf-8-sig') as f: d = parse_metadata(f.read())
                meta = d.get('short' if a.mp4.lower().endswith('_shorts.mp4') else 'full') or d.get('full')
            r = upload_video(a.mp4, a.channel, meta, a.privacy, progress=prog)
            print(f"uploaded to {r['channel']}: {r['title']} [{r['privacy']}]\n  {r['url']}\n  {r['studio']}")
    except YouTubeError as e:
        print(f'ERROR: {e}'); return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
