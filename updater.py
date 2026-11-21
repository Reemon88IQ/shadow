"""Update check against GitHub Releases (no GUI, no token, stdlib only).

A release is the tag vX.Y with the asset Shadow-X.Y-Setup.exe (made by release.bat). The app asks
https://api.github.com/repos/<REPO>/releases/latest in the background; if that version is newer than
render_core.VERSION it offers to download the setup (size + sha256 checked) and run it.
SHADOW_UPDATE_URL overrides the API url (tests point it at a local server)."""
import hashlib, json, os, re, urllib.request

REPO = 'ShadowStudioApps/seekframe'   # since 3.26 (Shadow Studio's organization; the old Reemon88IQ/shadow and /seekframe redirect); the public GitHub repo (owner/name) whose releases are checked
API = 'https://api.github.com/repos/{repo}/releases/latest'
ASSET_RE = re.compile(r'^(Seekframe|Shadow)-[\d.]+-Setup\.exe$', re.I)   # Seekframe since 3.25 (the app was called Shadow before)


class UpdateError(Exception):
    pass


def parse_version(s):
    """'v3.0' / '3.0.1' -> (3, 0) / (3, 0, 1); trailing zeros dropped so 3.0 == 3. None if not a version."""
    m = re.fullmatch(r'\s*v?(\d+(?:\.\d+)*)\s*', str(s or ''))
    if not m: return None
    t = [int(x) for x in m.group(1).split('.')]
    while len(t) > 1 and t[-1] == 0: t.pop()
    return tuple(t)


def is_newer(latest, current):
    a, b = parse_version(latest), parse_version(current)
    return bool(a and b and a > b)


def api_url(repo=None):
    return os.environ.get('SHADOW_UPDATE_URL') or API.format(repo=repo or REPO)


def configured(repo=None):
    return bool(os.environ.get('SHADOW_UPDATE_URL')) or not (repo or REPO).startswith('OWNER/')


def latest_release(repo=None, timeout=8):
    """The newest published release: {'version', 'tag', 'url', 'size', 'sha256', 'notes', 'page'}; None if it has no setup asset.
    Raises UpdateError on network / API problems (the caller just stays quiet)."""
    req = urllib.request.Request(api_url(repo), headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'Shadow-updater'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: d = json.loads(r.read().decode('utf-8'))
    except Exception as e:
        raise UpdateError(f'update check failed: {e}') from e
    ver = parse_version(d.get('tag_name'))
    if not ver or d.get('draft') or d.get('prerelease'): return None
    for a in sorted(d.get('assets') or [], key=lambda x: not str(x.get('name', '')).lower().startswith('seekframe')):   # the new name first
        if ASSET_RE.match(a.get('name', '')) and a.get('browser_download_url'):
            dg = a.get('digest') or ''   # GitHub gives 'sha256:<hex>' for release assets
            return {'version': str(d['tag_name']).strip().lstrip('vV'), 'tag': d.get('tag_name'), 'url': a['browser_download_url'],
                    'name': a['name'], 'size': int(a.get('size') or 0),
                    'sha256': dg[7:].lower() if dg.lower().startswith('sha256:') else None,
                    'notes': (d.get('body') or '').strip(), 'page': d.get('html_url') or ''}
    return None


def download(rel, dest_dir, progress=None, cancel=None, timeout=30):
    """Downloads the setup of `rel` into dest_dir; checks size and sha256 (when GitHub gave one). Returns the path."""
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, os.path.basename(rel['name'])); part = path + '.part'
    h = hashlib.sha256(); got = 0
    req = urllib.request.Request(rel['url'], headers={'User-Agent': 'Shadow-updater'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(part, 'wb') as f:
            total = rel.get('size') or int(r.headers.get('Content-Length') or 0)
            while True:
                if cancel is not None and cancel.is_set(): raise UpdateError('cancelled')
                b = r.read(1 << 16)
                if not b: break
                f.write(b); h.update(b); got += len(b)
                if progress: progress(got, total)
        if rel.get('size') and got != rel['size']:
            raise UpdateError(f'download incomplete ({got} of {rel["size"]} bytes)')
        if rel.get('sha256') and h.hexdigest() != rel['sha256']:
            raise UpdateError('download is damaged (sha256 does not match)')
        os.replace(part, path)
        return path
    except UpdateError:
        _rm(part); raise
    except Exception as e:
        _rm(part); raise UpdateError(f'download failed: {e}') from e


def child_env(environ=None):
    """Environment for the setup started by the update: without PyInstaller's onefile variables of THIS Shadow.
    Otherwise the setup passes them on to the new Shadow.exe it starts, which then looks for python3xx.dll in this
    copy's _MEI temp folder - already deleted, since this copy closed ('Failed to load Python DLL')."""
    env = {k: v for k, v in (os.environ if environ is None else environ).items()
           if not k.upper().startswith(('_PYI_', '_MEIPASS'))}
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'   # PyInstaller >= 6.9: a onefile child starts fresh
    return env


def _rm(p):
    try: os.remove(p)
    except OSError: pass
