#!/usr/bin/env python3
"""Update geoip/geosite .dat on all targets and reload xray/xkeen/geo-updater."""
from __future__ import annotations
import json, os, shlex, hashlib

RELEASE_URLS = {
    "geoip.dat": "https://github.com/seedmonn/zkeen-patcher/releases/latest/download/geoip.dat",
    "geosite.dat": "https://github.com/seedmonn/zkeen-patcher/releases/latest/download/geosite.dat",
}
FILE_MAP = [("ip.dat", "geoip.dat"), ("geo.dat", "geosite.dat")]  # (remote_name, release_name)
DEFAULT_MIN_SIZE = 10240
DEFAULT_MIRROR_TIMEOUT = 30

class UpdateError(Exception):
    pass

def redact(s: str, n: int = 8) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[:n] + "…"

def load_config(path: str) -> dict:
    with open(path, "r") as f:
        return json.load(f)

def _need(t, *keys):
    for k in keys:
        if k not in t:
            raise UpdateError(f"target {t.get('name','?')} missing {k}")

def validate_target(t: dict) -> None:
    _need(t, "name", "kind", "ssh")
    ssh = t["ssh"]
    for k in ("host", "port", "user"):
        if k not in ssh:
            raise UpdateError(f"target {t['name']} ssh missing {k}")
    if t["kind"] == "xui":
        _need(t, "geo_dir", "panel")
        for k in ("base", "token"):
            if k not in t["panel"]:
                raise UpdateError(f"target {t['name']} panel missing {k}")
    elif t["kind"] == "router":
        _need(t, "geo_dir")
    elif t["kind"] == "docker-updater":
        _need(t, "container", "mirror")
    else:
        raise UpdateError(f"target {t['name']} unknown kind {t['kind']}")

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def should_skip_file(remote_sha, golden_sha, force) -> bool:
    return (not force) and remote_sha is not None and remote_sha == golden_sha

def needs_sudo(user: str) -> bool:
    return user != "root"

def restart_urls(base: str) -> list:
    b = base.rstrip("/")
    return [f"{b}/panel/api/server/restartXrayService",
            f"{b}/xui/API/server/restartXrayService"]

def parse_restart_response(text: str) -> bool:
    try:
        return bool(json.loads(text).get("success"))
    except Exception:
        return False

def build_apply_command(geo_dir, remote_name, tmp_path, user):
    target = f"{geo_dir}/{remote_name}"
    bak = f"{target}.bak"
    inner = (f"cp -f {shlex.quote(target)} {shlex.quote(bak)} 2>/dev/null || true; "
             f"mv -f {shlex.quote(tmp_path)} {shlex.quote(target)}; "
             f"chmod 644 {shlex.quote(target)}; chown root:root {shlex.quote(target)}")
    if not needs_sudo(user):
        return (f"sh -c {shlex.quote(inner)}", None)
    # sudo -S reads password from stdin (no-op if NOPASSWD); password never hits argv
    return (f"sudo -S -p '' sh -c {shlex.quote(inner)}", "PW")

def filter_targets(targets, only):
    if not only:
        return list(targets)
    want = {x.strip().upper() for x in only.split(",") if x.strip()}
    return [t for t in targets if t["name"].upper() in want]

def download_release(urls, dest_dir, min_size, fetch):
    golden = {}
    for name, url in urls.items():
        data, status = fetch(url)
        if status != 200:
            raise UpdateError(f"{name}: HTTP {status} fetching {url}")
        if len(data) < min_size:
            raise UpdateError(f"{name}: too small ({len(data)} < {min_size})")
        path = os.path.join(dest_dir, name)
        with open(path, "wb") as f:
            f.write(data)
        golden[name] = {"path": path, "sha": sha256_bytes(data), "size": len(data)}
    return golden

def _http_fetch(url):
    import requests
    r = requests.get(url, timeout=60)
    return (r.content, r.status_code)

def restart_xray(base, token, post):
    headers = {"Authorization": f"Bearer {token}"}
    for url in restart_urls(base):
        try:
            text, status = post(url, headers=headers, data=b"", verify=False, timeout=30)
        except Exception:
            continue
        if status == 404:
            continue
        if parse_restart_response(text):
            return True
    return False

def mirror_sha(mirror, remote_name, get):
    try:
        data, status = get(f"{mirror.rstrip('/')}/{remote_name}")
    except Exception:
        return None
    if status != 200:
        return None
    return sha256_bytes(data)

def wait_mirror(mirror, golden, timeout, get, sleep):
    # golden keyed by release name; mirror serves remote names -> map via FILE_MAP
    missing = [rel for _, rel in FILE_MAP if rel not in golden]
    if missing:
        raise UpdateError(f"wait_mirror: golden missing releases: {missing}")
    want = {remote: golden[rel]["sha"] for remote, rel in FILE_MAP}
    deadline = timeout
    while deadline >= 0:
        ok = all(mirror_sha(mirror, remote, get) == sha for remote, sha in want.items())
        if ok:
            return True
        sleep(2)
        deadline -= 2
    return False

def _http_post(url, headers, data, verify, timeout):
    import requests
    r = requests.post(url, headers=headers, data=data, verify=verify, timeout=timeout)
    return (r.text, r.status_code)

def _http_get(url):
    import requests
    r = requests.get(url, timeout=15)
    return (r.content, r.status_code)
