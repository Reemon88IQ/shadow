"""Upload finished videos to YouTube with the YouTube Data API v3 - standard library only (no google-* packages).

Sign in once per channel in the browser (OAuth for installed apps: loopback + PKCE). Shadow keeps only the refresh token,
in ~/.shadow_youtube_tokens.json; the password is typed on Google's own page. Uploads are resumable (8 MB chunks, retried).
Needs your own OAuth client (Google Cloud, Desktop app) in ~/.shadow_youtube_client.json - see docs/YOUTUBE.md.
Note: until the Google Cloud project passes YouTube's API audit, YouTube locks every API upload to Private.

  python youtube.py signin                                   sign in a channel (opens the browser)
  python youtube.py channels                                 list signed-in channels
  python youtube.py upload video.mp4 [--meta metadata.txt] [--channel "Moon Shift"] [--privacy private|unlisted|public]
                    [--ai yes|no] [--thumbnail cover.jpg | --no-thumbnail] [--publish "2026-11-25 05:00 Sydney"]
"""
import base64, datetime, hashlib, http.server, json, os, re, secrets, socket, sys, threading, time, urllib.error, urllib.parse, urllib.request, webbrowser

HOME = os.path.expanduser('~')
CLIENT_FILE = os.path.join(HOME, '.shadow_youtube_client.json')
TOKENS_FILE = os.path.join(HOME, '.shadow_youtube_tokens.json')
SCOPES = ['https://www.googleapis.com/auth/youtube.upload', 'https://www.googleapis.com/auth/youtube.readonly']
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
API = 'https://www.googleapis.com/youtube/v3'
UPLOAD_URL = 'https://www.googleapis.com/upload/youtube/v3/videos'
THUMB_URL = 'https://www.googleapis.com/upload/youtube/v3/thumbnails/set'
THUMB_MAX = 2 * 1024 * 1024   # YouTube: custom thumbnails up to 2 MB (JPG/PNG)
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
_FIELD = re.compile(r'^(TITLE|DESCRIPTION|TAGS|AUDIENCE|PUBLISH|CATEGORY|PRIVACY|AI|THUMBNAIL)\s*:\s*(.*)$', re.I)
_MONTHS = {m: i + 1 for i, m in enumerate(('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'))}
# zone words written after the time ("5:00 AM Sydney time"); none -> the PC's own time zone
_ZONES = {'sydney': 'Australia/Sydney', 'melbourne': 'Australia/Melbourne', 'aest': 'Australia/Sydney', 'aedt': 'Australia/Sydney',
          'baghdad': 'Asia/Baghdad', 'iraq': 'Asia/Baghdad', 'london': 'Europe/London', 'new york': 'America/New_York',
          'utc': 'UTC', 'gmt': 'UTC'}


def _sydney_offset(d):
    """Fallback when zoneinfo has no data (an exe without tzdata): Sydney/Melbourne is UTC+11 from the first Sunday of October
    02:00 to the first Sunday of April 03:00 (local time), else UTC+10."""
    def first_sunday(y, m): return datetime.datetime(y, m, 1 + (6 - datetime.date(y, m, 1).weekday()) % 7)
    on, off = first_sunday(d.year, 10).replace(hour=2), first_sunday(d.year, 4).replace(hour=3)
    return 11 if (d >= on or d < off) else 10


def _zone(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return None


def parse_publish(text):
    """PUBLISH: text -> RFC 3339 UTC ('2026-11-18T18:00:00Z'), or None when it holds no full date + time.
    Understands '2026-11-25 05:00', '2026-11-25T05:00:00+11:00', 'Thu 19 Nov 2026, 5:00 AM Sydney time', 'Nov 19 2026 17:30 UTC'.
    An offset or a zone word (Sydney, Baghdad, UTC...) after the time sets the zone, else the PC's own zone."""
    low = (text or '').strip().lower()
    if not low: return None
    m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})[ t,]+(\d{1,2}):(\d{2})(?::(\d{2}))?', low)
    if m:
        y, mo, d, h, mi, sec = (int(x or 0) for x in m.groups())
        rest = low[m.end():]
    else:
        dm = (re.search(r'\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*\.?,?\s+(\d{4})\b', low)
              or re.search(r'\b([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b', low))
        if not dm: return None
        g = dm.groups()
        if g[0].isdigit(): d, mon, y = int(g[0]), g[1], int(g[2])
        else: mon, d, y = g[0], int(g[1]), int(g[2])
        if mon not in _MONTHS: return None
        mo = _MONTHS[mon]
        tail = low[dm.end():]
        tm = re.search(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?(?![\d:])', tail)
        if not tm or (tm.group(2) is None and tm.group(3) is None): return None
        h, mi, sec = int(tm.group(1)), int(tm.group(2) or 0), 0
        ap = (tm.group(3) or '').replace('.', '')
        if ap == 'pm' and h < 12: h += 12
        if ap == 'am' and h == 12: h = 0
        rest = tail[tm.end():]
    try: local = datetime.datetime(y, mo, d, h, mi, sec)
    except ValueError: return None
    off = re.match(r'\s*(z\b|[+-]\d{2}:?\d{2}\b)', rest)
    if off:
        o = off.group(1)
        tz = datetime.timezone.utc if o == 'z' else datetime.timezone(
            (1 if o[0] == '+' else -1) * datetime.timedelta(hours=int(o[1:3]), minutes=int(o[-2:])))
        aware = local.replace(tzinfo=tz)
    else:
        zname = next((v for k, v in _ZONES.items() if re.search(r'\b' + k + r'\b', rest)), None)
        zi = _zone(zname) if zname else None
        if zi is not None: aware = local.replace(tzinfo=zi)
        elif zname in ('Australia/Sydney', 'Australia/Melbourne'):
            aware = local.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=_sydney_offset(local))))
        elif zname == 'Asia/Baghdad': aware = local.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=3)))
        elif zname: aware = local.replace(tzinfo=datetime.timezone.utc)
        else: aware = local.astimezone()   # the PC's own zone
    return aware.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_metadata(text):
    """metadata.txt -> {'full': {...}, 'short': {...}} (or {'full': {...}} without sections). Each: title, description,
    tags (list), kids (bool|None), category (id|None), privacy (or None), ai (bool|None; AI: yes/no = altered or synthetic
    content), thumbnail (path text or ''), publish (the PUBLISH text) and publish_at (RFC 3339 UTC or None; a Short whose PUBLISH
    says 'same' takes the full video's). Sections start with a line '=== ... SHORT ... ===' (short) or any other '=== ... ==='
    (full). DESCRIPTION runs until the next field line."""
    out, cur, key = {}, None, None
    def sec(name):
        return out.setdefault(name, dict(title='', description='', tags=[], kids=None, category=None, privacy=None, ai=None,
                                         thumbnail='', publish='', publish_at=None, _desc=[]))
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
            elif key == 'AI':
                yn = re.match(r'\s*(yes|y|true|1|no|n|false|0)\b', val, re.I)
                cur['ai'] = (yn.group(1).lower() in ('yes', 'y', 'true', '1')) if yn else None
            elif key == 'THUMBNAIL': cur['thumbnail'] = val.strip('"')
            elif key == 'PUBLISH': cur['publish'] = val; cur['publish_at'] = parse_publish(val)
            if key != 'DESCRIPTION': key = None
            continue
        if key == 'DESCRIPTION': cur['_desc'].append(line)
    for s in out.values():
        s['description'] = '\n'.join(s.pop('_desc')).strip()
    sh, fu = out.get('short'), out.get('full')
    if sh and fu and not sh['publish_at'] and re.search(r'\bsame\b', sh['publish'], re.I): sh['publish_at'] = fu['publish_at']
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


def find_thumbnail(mp4, meta=None, meta_path=None):
    """The image to set as the video's thumbnail, or None: THUMBNAIL: in metadata.txt (relative to that file), else the newest
    <video>_thumb.jpg / _thumb_N.jpg next to the mp4 (Shadow writes it when the scene sets window.THUMBNAIL_T).
    A Short gets one only when its section names THUMBNAIL: (YouTube takes Shorts thumbnails from the video itself)."""
    t = (meta or {}).get('thumbnail')
    if t:
        p = t if os.path.isabs(t) else os.path.join(os.path.dirname(meta_path or mp4), t)
        return p if os.path.isfile(p) else None
    base = os.path.splitext(mp4)[0]
    if base.lower().endswith('_shorts'): return None
    folder, name = os.path.split(base)
    try: files = os.listdir(folder or '.')
    except OSError: return None
    pat = re.compile(re.escape(name) + r'_thumb(_\d+)?\.(jpe?g|png)$', re.I)
    hits = [os.path.join(folder, f) for f in files if pat.match(f)]
    return max(hits, key=os.path.getmtime) if hits else None


# ---------------------------------------------------------------- upload
def video_body(title, description='', tags=(), privacy='private', kids=False, category='10', publish_at=None, ai=None):
    """The videos.insert JSON (pure). YouTube limits: title 100 chars, description 5000 bytes, tags 500 chars total.
    ai True/False -> status.containsSyntheticMedia (YouTube's 'altered or synthetic content' label); None leaves it unset.
    publish_at (RFC 3339) schedules the video; YouTube needs it Private until then."""
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
    if ai is not None: status['containsSyntheticMedia'] = bool(ai)
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


def set_thumbnail(video_id, channel_id, image):
    """thumbnails.set for an uploaded video (50 quota units). The youtube.upload scope covers it (no new sign-in).
    Raises YouTubeError (file too big, channel not verified for custom thumbnails, ...)."""
    if not os.path.isfile(image): raise YouTubeError(f'thumbnail not found: {image}')
    size = os.path.getsize(image)
    if size > THUMB_MAX: raise YouTubeError(f'thumbnail is {size / 1048576:.1f} MB; YouTube takes up to 2 MB')
    ctype = 'image/png' if image.lower().endswith('.png') else 'image/jpeg'
    with open(image, 'rb') as f: data = f.read()
    st, _, body = _req(THUMB_URL + '?' + urllib.parse.urlencode(dict(videoId=video_id)), data=data, method='POST',
                       headers={'Authorization': 'Bearer ' + access_token(channel_id), 'Content-Type': ctype, 'Content-Length': str(size)})
    if st != 200:
        e = _api_error(st, body)
        raise YouTubeError(f'{e} (custom thumbnails need a verified channel: youtube.com/verify)') if st == 403 else e


def prepare(mp4, channel=None, meta=None, privacy='private', candidates=(), overrides=None, meta_path=None, thumbnail=True, schedule=True):
    """Everything an upload will send, without sending it (upload_video and the MCP preview both use this):
    -> (channel dict, videos.insert body, thumbnail path or None, notes [str]). Raises YouTubeError (no channel, no title...)."""
    if not os.path.isfile(mp4): raise YouTubeError(f'video not found: {mp4}')
    ch = find_channel(channel); notes = []
    if meta is None: meta, meta_path = metadata_for(mp4, candidates)
    if not meta: notes.append('no metadata.txt found: the title is made from the file name, no description or tags')
    m = dict(meta or {}); m.update({k: v for k, v in (overrides or {}).items() if v is not None})
    title = m.get('title') or os.path.splitext(os.path.basename(mp4))[0].replace('_shorts', '').replace('-', ' ').replace('_', ' ').title()
    when = m.get('publish_at') if schedule else None
    if when and when <= time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() + 300)):
        notes.append(f'publish time {when} has passed: uploaded without a schedule'); when = None
    if schedule and not when and m.get('publish') and not m.get('publish_at'):
        notes.append(f'PUBLISH "{m["publish"]}" has no date + time: not scheduled')
    body = video_body(title, m.get('description', ''), m.get('tags', ()), m.get('privacy') or privacy, bool(m.get('kids')),
                      m.get('category') or '10', publish_at=when, ai=m.get('ai'))
    img = (thumbnail if isinstance(thumbnail, str) else find_thumbnail(mp4, m, meta_path)) if thumbnail else None
    if isinstance(thumbnail, str) and not os.path.isfile(thumbnail): notes.append(f'thumbnail not found: {thumbnail}'); img = None
    if img and os.path.getsize(img) > THUMB_MAX: notes.append(f'thumbnail {os.path.basename(img)} is over 2 MB: YouTube will refuse it')
    if m.get('ai') is None: notes.append('no AI: line: the altered/synthetic content label is not sent')
    return ch, body, img, notes


def upload_video(mp4, channel=None, meta=None, privacy='private', candidates=(), progress=lambda d, t: None, cancel=None, overrides=None,
                 meta_path=None, thumbnail=True, schedule=True):
    """One call for the app / CLI / MCP: channel (title or id), metadata from `meta` (dict) or metadata.txt (next to the mp4 or
    in candidates), overrides (title, description, tags, kids, category, privacy, ai, publish_at, thumbnail) win.
    thumbnail: True = find_thumbnail(), a path = that image, False = none. schedule=False ignores publish_at; a time already
    past is ignored too (the video is uploaded unscheduled).
    -> upload() result + title, channel, publish_at, thumb ('set' / None / 'error: ...'; a failed thumbnail never fails the upload)."""
    ch, body, img, _ = prepare(mp4, channel, meta, privacy, candidates, overrides, meta_path, thumbnail, schedule)
    r = upload(mp4, ch['id'], body, progress, cancel)
    r.update(title=body['snippet']['title'], channel=ch['title'], publish_at=body['status'].get('publishAt'), thumb=None)
    if img:
        try: set_thumbnail(r['id'], ch['id'], img); r['thumb'] = 'set'
        except YouTubeError as e: r['thumb'] = f'error: {e}'
    return r


# ---------------------------------------------------------------- CLI
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog='youtube.py'); sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('signin'); sub.add_parser('channels')
    u = sub.add_parser('upload'); u.add_argument('mp4'); u.add_argument('--meta'); u.add_argument('--channel')
    u.add_argument('--privacy', default='private', choices=PRIVACY)
    u.add_argument('--ai', choices=('yes', 'no'), help='altered or synthetic content label (default: AI: in metadata.txt)')
    u.add_argument('--thumbnail', help='thumbnail image (default: THUMBNAIL: in metadata.txt or <video>_thumb.jpg)')
    u.add_argument('--no-thumbnail', action='store_true')
    u.add_argument('--publish', help='schedule, e.g. "2026-11-25 05:00 Sydney" (default: PUBLISH: in metadata.txt)')
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
            ov = dict(ai={'yes': True, 'no': False}.get(a.ai))
            if a.publish:
                ov['publish_at'] = parse_publish(a.publish)
                if not ov['publish_at']: print(f'ERROR: --publish: no date and time in "{a.publish}"'); return 1
            r = upload_video(a.mp4, a.channel, meta, a.privacy, progress=prog, overrides=ov, meta_path=a.meta,
                             thumbnail=False if a.no_thumbnail else (a.thumbnail or True))
            print(f"uploaded to {r['channel']}: {r['title']} [{r['privacy']}]\n  {r['url']}\n  {r['studio']}")
            if r.get('publish_at'): print(f"  scheduled: {r['publish_at']} (UTC)")
            if r.get('thumb'): print(f"  thumbnail: {r['thumb']}")
    except YouTubeError as e:
        print(f'ERROR: {e}'); return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
