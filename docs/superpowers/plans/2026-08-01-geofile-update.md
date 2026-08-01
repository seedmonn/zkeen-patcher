# Geofile Auto-Update Script — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python CLI that updates `geoip.dat`/`geosite.dat` (→ `ip.dat`/`geo.dat`) from the latest `zkeen-patcher` release on 5 targets and reloads them, verified by SHA256, with rollback.

**Architecture:** Single importable script `scripts/update_geofiles.py` (helpers + `main()`). Pure logic and orchestration are unit-tested via dependency injection (callables for SSH exec / HTTP / docker / sleep); the real paramiko/requests/docker paths are thin wrappers verified through the script's own `--dry-run` and staged `--only <target>` live runs.

**Tech Stack:** Python ≥3.10, `paramiko` (SSH), `requests` (HTTP), `pytest` (tests). Secrets in `~/.config/zkeen-patcher/targets.json` (outside repo, `0600`).

## Global Constraints

- Source URLs (verbatim): `geoip.dat ← https://github.com/seedmonn/zkeen-patcher/releases/latest/download/geoip.dat`; `geosite.dat ← …/geosite.dat`.
- File mapping (every target): remote `ip.dat ← release geoip.dat`, remote `geo.dat ← release geosite.dat`.
- Restart for `xui`: `POST <base>/panel/api/server/restartXrayService` (Bearer, `verify=False`, empty body); 404 → fallback `<base>/xui/API/server/restartXrayService`; expect `{"success":true,…}`. **Do NOT use `/panel/api/xray/update`** (does not reload geo).
- Restart for `router`: `xkeen -restart`. Restart for `docker-updater`: `docker restart geo-updater`.
- Atomic write: download/SFTP to a temp path, SHA-verify, then `mv` into place; keep `.bak`; rollback on failed post-check.
- Idempotent: skip a file if its on-box SHA already equals the golden SHA (unless `--force`).
- Secrets (tokens, `basePath`, passwords) **never** in committed files — only placeholders in `scripts/targets.example.json`; real values in `~/.config/zkeen-patcher/targets.json` (`0600`). Tokens redacted in all logs.
- `MIN_SIZE = 10240`; `MIRROR_TIMEOUT = 30s`.
- Targets run independently; one failure does not abort others. Exit `0` iff all succeed.
- `LAN-MIRROR`: restart only `geo-updater`; do **not** touch `xray-msk/spb/est`.

## Testing Strategy

- **Unit (pytest):** pure helpers + orchestration logic, using injected fakes for SSH/HTTP/docker/sleep. These run with no network.
- **Integration (manual, via the script itself):** `--dry-run` (preflight + plan only) then `--only <NAME>` live against each real target, one at a time, before the full run.

`conftest.py` at repo root puts `scripts/` on `sys.path` so tests do `import update_geofiles as ugf`.

---

## File Structure

- **Create** `scripts/update_geofiles.py` — the script (importable helpers + `main()`).
- **Create** `scripts/targets.example.json` — config template (placeholders).
- **Create** `scripts/README.md` — usage.
- **Create** `requirements.txt` — `paramiko`, `requests`.
- **Create** `conftest.py` — sys.path shim for tests.
- **Create** `tests/test_helpers.py`, `tests/test_preflight.py`, `tests/test_panel.py`, `tests/test_flows.py` — unit tests.
- **Modify** `README.md` (root) — add "Auto-update geo files on nodes" section.

---

### Task 1: Scaffold, config loading, redaction

**Files:**
- Create: `scripts/update_geofiles.py`, `scripts/targets.example.json`, `conftest.py`, `tests/test_helpers.py`
- Test: `tests/test_helpers.py`

**Interfaces:**
- Produces: `load_config(path) -> dict`, `validate_target(t) -> None` (raises `UpdateError`), `redact(s, n=8) -> str`, constants `RELEASE_URLS`, `FILE_MAP`, `DEFAULT_MIN_SIZE`, exception `UpdateError`.

- [ ] **Step 1: Create `conftest.py`**

```python
import pathlib, sys
SCRIPTS = pathlib.Path(__file__).resolve().parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
```

- [ ] **Step 2: Write failing tests**

`tests/test_helpers.py`:
```python
import json, os, tempfile, pytest
import update_geofiles as ugf

def test_redact_short_unchanged():
    assert ugf.redact("abc") == "abc"

def test_redact_long_truncated():
    assert ugf.redact("aaaaaaaaaaaaaaaaaaaa", 8) == "aaaaaaaa…"

def test_load_config_and_validate_ok():
    data = {"min_size": 10240, "targets": [
        {"name": "MSK", "kind": "xui", "ssh": {"host": "1.2.3.4", "port": 22, "user": "root"},
         "geo_dir": "/usr/local/x-ui/bin", "panel": {"base": "https://1.2.3.4:31441/abc", "token": "tok"}},
        {"name": "ROUTER", "kind": "router", "ssh": {"host": "192.168.1.1", "port": 22, "user": "root", "password": "p"},
         "geo_dir": "/opt/etc/xray/dat"},
        {"name": "LAN-MIRROR", "kind": "docker-updater", "ssh": {"host": "192.168.1.101", "port": 20202, "user": "ginseng"},
         "container": "geo-updater", "mirror": "http://192.168.1.101:33133"},
    ]}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(data, f); path = f.name
    cfg = ugf.load_config(path)
    assert cfg["min_size"] == 10240
    for t in cfg["targets"]:
        ugf.validate_target(t)
    os.unlink(path)

def test_validate_target_xui_requires_panel():
    with pytest.raises(ugf.UpdateError):
        ugf.validate_target({"name": "X", "kind": "xui", "ssh": {"host": "h", "port": 22, "user": "root"}, "geo_dir": "/d"})
```

- [ ] **Step 3: Run tests — expect FAIL**

Run: `python3 -m pytest tests/test_helpers.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'update_geofiles'`).

- [ ] **Step 4: Implement scaffold + config**

`scripts/update_geofiles.py`:
```python
#!/usr/bin/env python3
"""Update geoip/geosite .dat on all targets and reload xray/xkeen/geo-updater."""
from __future__ import annotations
import json, os

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
```

`scripts/targets.example.json` (placeholders only):
```json
{
  "min_size": 10240,
  "mirror_timeout": 30,
  "targets": [
    {"name": "MSK",  "kind": "xui", "ssh": {"host": "77.105.169.97", "port": 22, "user": "root"}, "geo_dir": "/usr/local/x-ui/bin", "panel": {"base": "https://77.105.169.97:31441/<basePath_MSK>", "token": "<TOKEN_MSK>"}},
    {"name": "SPB",  "kind": "xui", "ssh": {"host": "212.67.12.230", "port": 53908, "user": "seedmon"}, "geo_dir": "/usr/local/x-ui/bin", "sudo_password": "<SUDO_PASSWORD>", "panel": {"base": "https://rus-panel.zyulkov.ru:8443/<basePath_SPB>", "token": "<TOKEN_SPB>"}},
    {"name": "EST",  "kind": "xui", "ssh": {"host": "38.180.164.100", "port": 53908, "user": "seedmon"}, "geo_dir": "/usr/local/x-ui/bin", "sudo_password": "<SUDO_PASSWORD>", "panel": {"base": "https://est-panel.zyulkov.ru:8443/<basePath_EST>", "token": "<TOKEN_EST>"}},
    {"name": "ROUTER", "kind": "router", "ssh": {"host": "192.168.1.1", "port": 22, "user": "root", "password": "<ROUTER_PASSWORD>"}, "geo_dir": "/opt/etc/xray/dat"},
    {"name": "LAN-MIRROR", "kind": "docker-updater", "ssh": {"host": "192.168.1.101", "port": 20202, "user": "ginseng"}, "container": "geo-updater", "mirror": "http://192.168.1.101:33133"}
  ]
}
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `python3 -m pytest tests/test_helpers.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/update_geofiles.py scripts/targets.example.json conftest.py tests/test_helpers.py
git commit -m "feat(geoupdate): scaffold, config loading, redaction"
```

---

### Task 2: Pure helpers (sha256, skip-decision, restart URLs, apply command)

**Files:**
- Modify: `scripts/update_geofiles.py`
- Test: `tests/test_helpers.py`

**Interfaces:**
- Consumes: `FILE_MAP`, `UpdateError`.
- Produces: `sha256_bytes(data)->str`, `sha256_file(path)->str`, `should_skip_file(remote_sha, golden_sha, force)->bool`, `needs_sudo(user)->bool`, `restart_urls(base)->list[str]`, `parse_restart_response(text)->bool`, `build_apply_command(geo_dir, remote_name, tmp_path, user)->tuple[str,str|None]`, `filter_targets(targets, only)->list`.

- [ ] **Step 1: Write failing tests** (append to `tests/test_helpers.py`)

```python
def test_sha256_bytes_and_file():
    data = b"hello world"
    assert ugf.sha256_bytes(data) == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    import tempfile, os
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(data); p = f.name
    assert ugf.sha256_file(p) == ugf.sha256_bytes(data); os.unlink(p)

def test_should_skip_file():
    assert ugf.should_skip_file("abc", "abc", False) is True
    assert ugf.should_skip_file("abc", "abc", True) is False
    assert ugf.should_skip_file(None, "abc", False) is False
    assert ugf.should_skip_file("abc", "other", False) is False

def test_needs_sudo():
    assert ugf.needs_sudo("root") is False
    assert ugf.needs_sudo("seedmon") is True

def test_restart_urls_order():
    urls = ugf.restart_urls("https://h:8443/abc/")
    assert urls[0] == "https://h:8443/abc/panel/api/server/restartXrayService"
    assert urls[1] == "https://h:8443/abc/xui/API/server/restartXrayService"

def test_parse_restart_response():
    assert ugf.parse_restart_response('{"success":true,"msg":"x"}') is True
    assert ugf.parse_restart_response('{"success":false}') is False
    assert ugf.parse_restart_response('not json') is False

def test_build_apply_command_root_has_no_sudo():
    cmd, stdin = ugf.build_apply_command("/d", "ip.dat", "/tmp/x", "root")
    assert "sudo" not in cmd and stdin is None
    assert "mv -f /tmp/x /d/ip.dat" in cmd and ".bak" in cmd

def test_build_apply_command_seedmon_uses_sudo_S():
    cmd, stdin = ugf.build_apply_command("/d", "ip.dat", "/tmp/x", "seedmon")
    assert cmd.startswith("sudo -S -p ''") and stdin == "PW"

def test_filter_targets():
    ts = [{"name": "MSK"}, {"name": "SPB"}]
    assert [t["name"] for t in ugf.filter_targets(ts, None)] == ["MSK", "SPB"]
    assert [t["name"] for t in ugf.filter_targets(ts, "MSK")] == ["MSK"]
    assert [t["name"] for t in ugf.filter_targets(ts, "msk,SPB")] == ["MSK", "SPB"]
```

- [ ] **Step 2: Run — expect FAIL** (functions undefined)

Run: `python3 -m pytest tests/test_helpers.py -v`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement** (append to `scripts/update_geofiles.py`)

```python
import shlex, hashlib

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
```

> Note: `stdin == "PW"` is a placeholder token; the real password is substituted by the SSH layer from `t["sudo_password"]` (Task 5). `filter_targets` below uses no secrets.

```python
def filter_targets(targets, only):
    if not only:
        return list(targets)
    want = {x.strip().upper() for x in only.split(",") if x.strip()}
    return [t for t in targets if t["name"].upper() in want]
```

- [ ] **Step 4: Run — expect PASS**

Run: `python3 -m pytest tests/test_helpers.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/update_geofiles.py tests/test_helpers.py
git commit -m "feat(geoupdate): pure helpers (sha256, skip, restart urls, apply cmd)"
```

---

### Task 3: Preflight — download golden + SHA256 (DI)

**Files:**
- Modify: `scripts/update_geofiles.py`
- Test: `tests/test_preflight.py`

**Interfaces:**
- Consumes: `RELEASE_URLS`, `sha256_bytes`, `DEFAULT_MIN_SIZE`, `UpdateError`.
- Produces: `download_release(urls, dest_dir, min_size, fetch)->dict` where `fetch(url)->(bytes,int)` and the returned dict maps release name → `{"path","sha","size"}`.

- [ ] **Step 1: Write failing test**

`tests/test_preflight.py`:
```python
import os, tempfile, pytest
import update_geofiles as ugf

def _fake_fetch(table):
    def f(url):
        return table[url]
    return f

def test_download_release_ok():
    d = tempfile.mkdtemp()
    urls = {"geoip.dat": "U1", "geosite.dat": "U2"}
    table = {"U1": (b"x" * 20000, 200), "U2": (b"y" * 15000, 200)}
    g = ugf.download_release(urls, d, 10240, _fake_fetch(table))
    assert g["geoip.dat"]["sha"] == ugf.sha256_bytes(b"x" * 20000)
    assert g["geoip.dat"]["size"] == 20000
    assert os.path.exists(os.path.join(d, "geosite.dat"))

def test_download_release_rejects_small():
    with pytest.raises(ugf.UpdateError):
        ugf.download_release({"geoip.dat": "U"}, tempfile.mkdtemp(), 10240,
                             _fake_fetch({"U": (b"tiny", 200)}))

def test_download_release_rejects_non200():
    with pytest.raises(ugf.UpdateError):
        ugf.download_release({"geoip.dat": "U"}, tempfile.mkdtemp(), 1,
                             _fake_fetch({"U": (b"x" * 50000, 404)}))
```

- [ ] **Step 2: Run — expect FAIL**

Run: `python3 -m pytest tests/test_preflight.py -v`
Expected: FAIL (`AttributeError: ... download_release`).

- [ ] **Step 3: Implement** (append to `scripts/update_geofiles.py`)

```python
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
```

Production `fetch` default (used by `main`):
```python
def _http_fetch(url):
    import requests
    r = requests.get(url, timeout=60)
    return (r.content, r.status_code)
```

- [ ] **Step 4: Run — expect PASS**

Run: `python3 -m pytest tests/test_preflight.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/update_geofiles.py tests/test_preflight.py
git commit -m "feat(geoupdate): preflight golden download + SHA256"
```

---

### Task 4: Panel restart + mirror wait (DI)

**Files:**
- Modify: `scripts/update_geofiles.py`
- Test: `tests/test_panel.py`

**Interfaces:**
- Consumes: `restart_urls`, `parse_restart_response`, `sha256_bytes`, `DEFAULT_MIRROR_TIMEOUT`, `UpdateError`.
- Produces: `restart_xray(base, token, post)->bool` (`post(url, headers, data, verify, timeout)->(text, status)`); `mirror_sha(mirror, remote_name, get)->str|None` (`get(url)->(bytes,int)`); `wait_mirror(mirror, golden, timeout, get, sleep)->bool`.

- [ ] **Step 1: Write failing test**

`tests/test_panel.py`:
```python
import pytest
import update_geofiles as ugf

def test_restart_xray_first_url_success():
    def post(url, headers, data, verify, timeout):
        assert headers["Authorization"] == "Bearer TOK"
        assert verify is False
        return ('{"success":true}', 200) if "panel/api" in url else ('{"success":false}', 200)
    assert ugf.restart_xray("https://h/abc", "TOK", post) is True

def test_restart_xray_falls_back_to_xui_prefix():
    calls = []
    def post(url, headers, data, verify, timeout):
        calls.append(url)
        return ('{"success":true}', 200) if "xui/API" in url else ('{"success":false}', 200)
    assert ugf.restart_xray("https://h/abc", "TOK", post) is True
    assert len(calls) == 2

def test_restart_xray_all_fail():
    def post(url, headers, data, verify, timeout):
        return ('{"success":false}', 200)
    assert ugf.restart_xray("https://h/abc", "TOK", post) is False

def test_wait_mirror_matches_then_succeeds():
    sha = ugf.sha256_bytes(b"GOLD")
    seq = [(b"old", 200), (b"GOLD", 200)]
    def get(url):
        return seq.pop(0)
    sleeps = []
    assert ugf.wait_mirror("http://m", {"geoip.dat": {"sha": sha}}, 5, get, sleeps.append) is True
    assert len(sleeps) == 1

def test_wait_mirror_timeout():
    def get(url):
        return (b"old", 200)
    def sleep_nop(_): pass
    assert ugf.wait_mirror("http://m", {"geoip.dat": {"sha": "x"}}, 0, get, sleep_nop) is False
```

- [ ] **Step 2: Run — expect FAIL**

Run: `python3 -m pytest tests/test_panel.py -v`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Implement** (append to `scripts/update_geofiles.py`)

```python
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
    want = {remote: golden[rel]["sha"] for remote, rel in FILE_MAP}
    deadline = timeout
    while deadline >= 0:
        ok = all(mirror_sha(mirror, remote, get) == sha for remote, sha in want.items())
        if ok:
            return True
        sleep(2)
        deadline -= 2
    return False
```

Production defaults (used by `main`):
```python
def _http_post(url, headers, data, verify, timeout):
    import requests
    r = requests.post(url, headers=headers, data=data, verify=verify, timeout=timeout)
    return (r.text, r.status_code)

def _http_get(url):
    import requests
    r = requests.get(url, timeout=15)
    return (r.content, r.status_code)
```

- [ ] **Step 4: Run — expect PASS**

Run: `python3 -m pytest tests/test_panel.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/update_geofiles.py tests/test_panel.py
git commit -m "feat(geoupdate): panel restartXrayService + mirror wait"
```

---

### Task 5: SSH ops wrappers (paramiko) + remote_sha256 + `--probe`

**Files:**
- Modify: `scripts/update_geofiles.py`
- Test: integration (no unit test — thin wrappers).

**Interfaces:**
- Consumes: `build_apply_command`, `should_skip_file`, `sha256_file`, `UpdateError`.
- Produces: `ssh_connect(spec)->client`, `ssh_exec(client, cmd, stdin_data=None)->(rc,out,err)`, `ssh_upload(client, local, remote)`, `remote_sha256(client, path)->str|None`. `Deps` namespace bundling these for the apply tasks.

- [ ] **Step 1: Implement wrappers** (append to `scripts/update_geofiles.py`)

```python
import time

def ssh_connect(spec):
    import paramiko
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_kwargs = {"hostname": spec["host"], "port": int(spec["port"]),
                      "username": spec["user"], "timeout": 15}
    if spec.get("password"):
        connect_kwargs["password"] = spec["password"]
    else:
        # key from agent (or default key files)
        connect_kwargs["allow_agent"] = True
        connect_kwargs["look_for_keys"] = True
    c.connect(**connect_kwargs)
    return c

def ssh_exec(client, cmd, stdin_data=None):
    stdin, stdout, stderr = client.exec_command(cmd, timeout=60)
    if stdin_data is not None:
        stdin.write(stdin_data); stdin.flush()
        try: stdin.channel.shutdown_write()
        except Exception: pass
    rc = stdout.channel.recv_exit_status()
    return rc, stdout.read().decode(errors="replace"), stderr.read().decode(errors="replace")

def ssh_upload(client, local, remote):
    sftp = client.open_sftp()
    try: sftp.put(local, remote)
    finally: sftp.close()

def remote_sha256(client, path):
    # try sha256sum, fallback openssl
    rc, out, _ = ssh_exec(client, f"sha256sum {shlex.quote(path)} 2>/dev/null || openssl dgst -sha256 {shlex.quote(path)} 2>/dev/null")
    out = out.strip()
    if not out:
        return None
    # sha256sum: "<hex>  <path>"; openssl: "SHA256(<path>)= <hex>"
    token = out.split()[0]
    return token if all(c in "0123456789abcdef" for c in token) and len(token) == 64 else (out.split("=")[-1].strip() or None)

class Deps:
    """Real I/O backend. Tests pass a fake with the same methods."""
    ssh_connect = staticmethod(ssh_connect)
    ssh_exec = staticmethod(ssh_exec)
    ssh_upload = staticmethod(ssh_upload)
    remote_sha256 = staticmethod(remote_sha256)
    restart_xray = staticmethod(lambda base, token, post=_http_post: restart_xray(base, token, post))
    restart_container = None  # set in Task 8
    wait_mirror = staticmethod(lambda mirror, golden, timeout, get=_http_get, sleep=time.sleep: wait_mirror(mirror, golden, timeout, get, sleep))
```

- [ ] **Step 2: Add `--probe` integration check** (append near `main`, see Task 9 for full `main`; here add a helper)

```python
def probe_target(t, deps):
    c = deps.ssh_connect(t["ssh"])
    try:
        rc, out, _ = deps.ssh_exec(c, "id; uname -a; command -v sha256sum openssl docker xkeen 2>/dev/null; echo END")
        print(f"[probe] {t['name']}: rc={rc}\n{out}")
    finally:
        c.close()
```

- [ ] **Step 3: Integration-verify reachability (run manually)**

First `pip install --user paramiko requests`, then for each target run (substitute your real `targets.json`):
```bash
python3 -c "import sys;sys.path.insert(0,'scripts');import update_geofiles as u;\
cfg=ugf.load_config('$HOME/.config/zkeen-patcher/targets.json');\
import update_geofiles as u;[u.probe_target(t,ugf.Deps) for t in cfg['targets']]"
```
Expected: each prints `rc=0` plus `uid=...`, and `sha256sum`, `openssl` present everywhere; `docker` present on `LAN-MIRROR`; `xkeen` present on `ROUTER`. If a `seedmon` box shows no `sha256sum`/`openssl`, note it (rollback path still works via `.bak`).

> If `paramiko` import fails, run `pip install --user paramiko` first. If SSH to a `seedmon`/root box fails with publickey, confirm the key is loaded (`ssh-add -l`).

- [ ] **Step 4: Commit**

```bash
git add scripts/update_geofiles.py
git commit -m "feat(geoupdate): paramiko ssh ops + remote_sha256 + probe"
```

---

### Task 6: `xui` apply flow (write → restart → post-check → rollback)

**Files:**
- Modify: `scripts/update_geofiles.py`
- Test: `tests/test_flows.py`

**Interfaces:**
- Consumes: `FILE_MAP`, `build_apply_command`, `should_skip_file`, `Deps`.
- Produces: `apply_xui(t, golden, force, deps)->dict` (`{"ok":bool,"msg":str,"sha":{...}}`).

- [ ] **Step 1: Write failing test with a fake `deps`**

`tests/test_flows.py`:
```python
import update_geofiles as ugf

class FakeDeps:
    def __init__(self):
        self.box = {"/d/ip.dat": b"OLD_IP", "/d/geo.dat": b"OLD_GEO"}  # remote current bytes
        self.uploads = {}
        self.exec_log = []
        self.xray_alive = True
        self.restart_ok = True
    def ssh_connect(self, spec): return "CLIENT"
    def ssh_upload(self, client, local, remote):
        with open(local,"rb") as f: self.uploads[remote] = f.read()
    def remote_sha256(self, client, path):
        b = self.box.get(path)
        return ugf.sha256_bytes(b) if b else None
    def ssh_exec(self, client, cmd, stdin_data=None):
        self.exec_log.append((cmd, stdin_data))
        # simulate apply: tmp path written via upload; mv moves it into box path
        rc, out = 0, ""
        if cmd.startswith("sudo -S") or cmd.startswith("sh -c"):
            # parse "mv -f <tmp> <target>"
            import re
            m = re.search(r"mv -f (\S+) (\S+)", cmd)
            if m:
                tmp, tgt = m.group(1), m.group(2)
                self.box[tgt] = self.uploads.get(tmp, b"")
        elif "pgrep -x xray" in cmd:
            out = "1234\n" if self.xray_alive else ""
            rc = 0 if self.xray_alive else 1
        return rc, out, ""
    def restart_xray(self, base, token, post=None): return self.restart_ok

def test_apply_xui_skips_when_already_golden():
    t = {"name":"MSK","kind":"xui","ssh":{"host":"h","port":22,"user":"root"},"geo_dir":"/d","panel":{"base":"b","token":"t"}}
    golden = {"geoip.dat":{"path":"/g/geoip.dat","sha":ugf.sha256_bytes(b"OLD_IP")},  # already matches
              "geosite.dat":{"path":"/g/geosite.dat","sha":ugf.sha256_bytes(b"OLD_GEO")}}
    d = FakeDeps()
    r = ugf.apply_xui(t, golden, force=False, deps=d)
    assert r["ok"] and "up to date" in r["msg"]
    assert d.exec_log == []  # nothing applied

def test_apply_xui_writes_and_restarts():
    t = {"name":"SPB","kind":"xui","ssh":{"host":"h","port":53908,"user":"seedmon"},"geo_dir":"/d","sudo_password":"PW","panel":{"base":"b","token":"t"}}
    golden = {"geoip.dat":{"path":"/g/geoip.dat","sha":ugf.sha256_bytes(b"NEW_IP")},
              "geosite.dat":{"path":"/g/geosite.dat","sha":ugf.sha256_bytes(b"NEW_GEO")}}
    d = FakeDeps()
    # seed local golden paths
    import os, pathlib
    for rel, b in [("geoip.dat",b"NEW_IP"),("geosite.dat",b"NEW_GEO")]:
        pathlib.Path(golden[rel]["path"]).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(golden[rel]["path"]).write_bytes(b)
    r = ugf.apply_xui(t, golden, force=False, deps=d)
    assert r["ok"], r["msg"]
    assert d.box["/d/ip.dat"] == b"NEW_IP"
    assert any("pgrep -x xray" in c for c,_ in d.exec_log)
    assert any(cmd.startswith("sudo -S") for cmd,_ in d.exec_log)  # seedmon used sudo
    import shutil; shutil.rmtree("/g", ignore_errors=True)

def test_apply_xui_rollback_when_xray_down():
    t = {"name":"MSK","kind":"xui","ssh":{"host":"h","port":22,"user":"root"},"geo_dir":"/d","panel":{"base":"b","token":"t"}}
    golden = {"geoip.dat":{"path":"/g/geoip.dat","sha":ugf.sha256_bytes(b"NEW_IP")},
              "geosite.dat":{"path":"/g/geosite.dat","sha":ugf.sha256_bytes(b"NEW_GEO")}}
    import pathlib
    for rel, b in [("geoip.dat",b"NEW_IP"),("geosite.dat",b"NEW_GEO")]:
        pathlib.Path(golden[rel]["path"]).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(golden[rel]["path"]).write_bytes(b)
    d = FakeDeps(); d.xray_alive = False
    r = ugf.apply_xui(t, golden, force=False, deps=d)
    assert r["ok"] is False
    # rollback restored originals
    assert d.box["/d/ip.dat"] == b"OLD_IP"
    import shutil; shutil.rmtree("/g", ignore_errors=True)
```

- [ ] **Step 2: Run — expect FAIL**

Run: `python3 -m pytest tests/test_flows.py -v`
Expected: FAIL (`apply_xui` missing).

- [ ] **Step 3: Implement `apply_xui`** (append to `scripts/update_geofiles.py`)

```python
def _post_check_xray(deps, client):
    rc, out, _ = deps.ssh_exec(client, "pgrep -x xray || pgrep -f '[x]ray -conf'")
    return out.strip() != ""

def _restore(client, deps, geo_dir, remote_name):
    bak = f"{geo_dir}/{remote_name}.bak"
    tgt = f"{geo_dir}/{remote_name}"
    deps.ssh_exec(client, f"[ -f {shlex.quote(bak)} ] && mv -f {shlex.quote(bak)} {shlex.quote(tgt)} || true")

def apply_xui(t, golden, force, deps):
    name, geo_dir, user = t["name"], t["geo_dir"], t["ssh"]["user"]
    sudo_pw = t.get("sudo_password")
    client = deps.ssh_connect(t["ssh"])
    try:
        applied = {}
        for remote, rel in FILE_MAP:
            tgt = f"{geo_dir}/{remote}"
            gsha = golden[rel]["sha"]
            if should_skip_file(deps.remote_sha256(client, tgt), gsha, force):
                continue
            tmp = f"/tmp/.zkeen.{remote}.{os.getpid()}"
            deps.ssh_upload(client, golden[rel]["path"], tmp)
            if deps.remote_sha256(client, tmp) != gsha:
                raise UpdateError(f"{name}: uploaded {remote} SHA mismatch")
            cmd, stdin = build_apply_command(geo_dir, remote, tmp, user)
            deps.ssh_exec(client, cmd, stdin_data=(sudo_pw if stdin else None))
            if deps.remote_sha256(client, tgt) != gsha:
                raise UpdateError(f"{name}: post-write {remote} SHA mismatch")
            applied[remote] = gsha
        if not applied:
            return {"ok": True, "msg": f"{name}: up to date (sha match)", "sha": {}}
        ok = deps.restart_xray(t["panel"]["base"], t["panel"]["token"])
        time.sleep(5)
        if _post_check_xray(deps, client):
            return {"ok": True, "msg": f"{name}: updated {','.join(applied)} + xray restarted", "sha": applied}
        # rollback
        for remote, _ in FILE_MAP:
            _restore(client, deps, geo_dir, remote)
        deps.restart_xray(t["panel"]["base"], t["panel"]["token"])
        time.sleep(3)
        return {"ok": _post_check_xray(deps, client),
                "msg": f"{name}: xray down after update; rolled back", "sha": applied}
    except Exception as e:
        return {"ok": False, "msg": f"{name}: ERROR {e}", "sha": {}}
    finally:
        try: client.close()
        except Exception: pass
```

- [ ] **Step 4: Run — expect PASS**

Run: `python3 -m pytest tests/test_flows.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/update_geofiles.py tests/test_flows.py
git commit -m "feat(geoupdate): xui apply/restart/post-check/rollback"
```

- [ ] **Step 6: Live integration — `--only MSK`, then SPB, then EST**

After Task 9 wires `main`, run (with your real `targets.json`):
```bash
python3 scripts/update_geofiles.py --only MSK -v
python3 scripts/update_geofiles.py --only SPB -v
python3 scripts/update_geofiles.py --only EST -v
```
Expected per host: `✓ MSK: updated ip.dat,geo.dat + xray restarted (sha …)`; exit 0. Confirm on a box: `ssh root@77.105.169.97 'sha256sum /usr/local/x-ui/bin/ip.dat'` matches the release sha printed in preflight.

---

### Task 7: `router` apply flow

**Files:**
- Modify: `scripts/update_geofiles.py`
- Test: `tests/test_flows.py`

**Interfaces:**
- Consumes: `FILE_MAP`, `build_apply_command` (user=root → no sudo), `should_skip_file`, `Deps`.
- Produces: `apply_router(t, golden, force, deps)->dict`.

- [ ] **Step 1: Write failing test** (append to `tests/test_flows.py`)

```python
def test_apply_router_writes_and_xkeen_restart():
    t = {"name":"ROUTER","kind":"router","ssh":{"host":"r","port":22,"user":"root","password":"p"},"geo_dir":"/opt/etc/xray/dat"}
    golden = {"geoip.dat":{"path":"/g/geoip.dat","sha":ugf.sha256_bytes(b"NEW_IP")},
              "geosite.dat":{"path":"/g/geosite.dat","sha":ugf.sha256_bytes(b"NEW_GEO")}}
    import pathlib, shutil
    for rel, b in [("geoip.dat",b"NEW_IP"),("geosite.dat",b"NEW_GEO")]:
        pathlib.Path(golden[rel]["path"]).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(golden[rel]["path"]).write_bytes(b)
    d = FakeDeps(); d.box = {"/opt/etc/xray/dat/ip.dat": b"OLD"}
    r = ugf.apply_router(t, golden, force=False, deps=d)
    assert r["ok"], r["msg"]
    assert d.box["/opt/etc/xray/dat/ip.dat"] == b"NEW_IP"
    assert any("xkeen -restart" in c for c,_ in d.exec_log)
    shutil.rmtree("/g", ignore_errors=True)
```

> `FakeDeps.ssh_exec` already handles `mv -f <tmp> <tgt>` for the `sh -c` root command; add `xkeen` handling: in `FakeDeps.ssh_exec`, the `pgrep` branch covers post-check, and `xkeen -restart` falls through returning `rc=0`. Confirm by also asserting no exception.

- [ ] **Step 2: Run — expect FAIL**

Run: `python3 -m pytest tests/test_flows.py::test_apply_router_writes_and_xkeen_restart -v`
Expected: FAIL (`apply_router` missing).

- [ ] **Step 3: Implement** (append)

```python
def apply_router(t, golden, force, deps):
    name, geo_dir = t["name"], t["geo_dir"]
    client = deps.ssh_connect(t["ssh"])
    try:
        applied = {}
        for remote, rel in FILE_MAP:
            tgt = f"{geo_dir}/{remote}"
            gsha = golden[rel]["sha"]
            if should_skip_file(deps.remote_sha256(client, tgt), gsha, force):
                continue
            tmp = f"/tmp/.zkeen.{remote}.{os.getpid()}"
            deps.ssh_upload(client, golden[rel]["path"], tmp)
            if deps.remote_sha256(client, tmp) != gsha:
                raise UpdateError(f"{name}: uploaded {remote} SHA mismatch")
            cmd, _ = build_apply_command(geo_dir, remote, tmp, "root")  # root → no sudo
            deps.ssh_exec(client, cmd)
            if deps.remote_sha256(client, tgt) != gsha:
                raise UpdateError(f"{name}: post-write {remote} SHA mismatch")
            applied[remote] = gsha
        if not applied:
            return {"ok": True, "msg": f"{name}: up to date", "sha": {}}
        deps.ssh_exec(client, "xkeen -restart")
        time.sleep(5)
        if _post_check_xray(deps, client):
            return {"ok": True, "msg": f"{name}: updated + xkeen restarted", "sha": applied}
        for remote, _ in FILE_MAP:
            _restore(client, deps, geo_dir, remote)
        deps.ssh_exec(client, "xkeen -restart"); time.sleep(3)
        return {"ok": _post_check_xray(deps, client), "msg": f"{name}: xray down; rolled back", "sha": applied}
    except Exception as e:
        return {"ok": False, "msg": f"{name}: ERROR {e}", "sha": {}}
    finally:
        try: client.close()
        except Exception: pass
```

- [ ] **Step 4: Run — expect PASS**

Run: `python3 -m pytest tests/test_flows.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/update_geofiles.py tests/test_flows.py
git commit -m "feat(geoupdate): router apply/xkeen-restart/rollback"
```

- [ ] **Step 6: Live integration — `--only ROUTER`**

```bash
python3 scripts/update_geofiles.py --only ROUTER -v
```
Expected: `✓ ROUTER: updated + xkeen restarted`; exit 0. Verify: `ssh root@192.168.1.1 'sha256sum /opt/etc/xray/dat/geo.dat'` matches release sha.

---

### Task 8: `docker-updater` apply flow

**Files:**
- Modify: `scripts/update_geofiles.py`
- Test: `tests/test_flows.py`

**Interfaces:**
- Consumes: `Deps` (`ssh_exec`, `wait_mirror`).
- Produces: `apply_mirror(t, golden, deps)->dict`; `restart_container(client, name, deps)->bool`.

- [ ] **Step 1: Write failing test** (append to `tests/test_flows.py`)

```python
def test_apply_mirror_restart_and_verify():
    sha = ugf.sha256_bytes(b"FRESH")
    t = {"name":"LAN-MIRROR","kind":"docker-updater","ssh":{"host":"m","port":20202,"user":"ginseng"},
         "container":"geo-updater","mirror":"http://m:33133"}
    golden = {"geoip.dat":{"path":"/g/geoip.dat","sha":sha},"geosite.dat":{"path":"/g/geosite.dat","sha":sha}}
    class MD(FakeDeps):
        def wait_mirror(self, mirror, golden_, timeout, get=None, sleep=None): return True
    d = MD()
    r = ugf.apply_mirror(t, golden, MD())
    assert r["ok"], r["msg"]
    assert any("docker restart geo-updater" in c for c,_ in d.exec_log)
```

- [ ] **Step 2: Run — expect FAIL**

Run: `python3 -m pytest tests/test_flows.py::test_apply_mirror_restart_and_verify -v`
Expected: FAIL (`apply_mirror` missing).

- [ ] **Step 3: Implement** (append)

```python
def restart_container(client, name, deps):
    rc, out, err = deps.ssh_exec(client, f"docker restart {shlex.quote(name)}")
    return rc == 0

def apply_mirror(t, golden, deps):
    name, mirror = t["name"], t["mirror"]
    client = deps.ssh_connect(t["ssh"])
    try:
        if not restart_container(client, t["container"], deps):
            return {"ok": False, "msg": f"{name}: docker restart {t['container']} failed", "sha": {}}
        if deps.wait_mirror(mirror, golden, DEFAULT_MIRROR_TIMEOUT):
            return {"ok": True, "msg": f"{name}: geo-updater restarted, mirror verified", "sha": {k:v["sha"] for k,v in golden.items()}}
        return {"ok": False, "msg": f"{name}: mirror did not converge to golden SHA", "sha": {}}
    except Exception as e:
        return {"ok": False, "msg": f"{name}: ERROR {e}", "sha": {}}
    finally:
        try: client.close()
        except Exception: pass
```

Wire `Deps.restart_container`:
```python
# in class Deps:  restart_container = staticmethod(lambda client, name, deps=None: restart_container(client, name, deps or Deps))
```

- [ ] **Step 4: Run — expect PASS**

Run: `python3 -m pytest tests/test_flows.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/update_geofiles.py tests/test_flows.py
git commit -m "feat(geoupdate): docker-updater restart + mirror verify"
```

- [ ] **Step 6: Live integration — `--only LAN-MIRROR`**

```bash
python3 scripts/update_geofiles.py --only LAN-MIRROR -v
```
Expected: `✓ LAN-MIRROR: geo-updater restarted, mirror verified`; exit 0. Cross-check: `curl -s http://192.168.1.101:33133/ip.dat | shasum -a 256` equals the release sha.

---

### Task 9: Orchestration, CLI, summary, exit codes, docs

**Files:**
- Modify: `scripts/update_geofiles.py`
- Create: `scripts/README.md`, `requirements.txt`
- Modify: `README.md` (root)
- Test: `tests/test_flows.py` (render_plan/format_summary).

**Interfaces:**
- Consumes: all prior.
- Produces: `render_plan(targets, golden)->str`, `format_summary(results)->str`, `main(argv)->int`.

- [ ] **Step 1: Write failing tests for plan/summary** (append to `tests/test_flows.py`)

```python
def test_format_summary_counts():
    res = [{"name":"MSK","ok":True},{"name":"SPB","ok":False}]
    s = ugf.format_summary(res)
    assert "OK 1/2" in s and "SPB" in s

def test_render_plan_lists_targets():
    targets = [{"name":"MSK","kind":"xui"},{"name":"ROUTER","kind":"router"}]
    golden = {"geoip.dat":{"sha":"a"*16},"geosite.dat":{"sha":"b"*16}}
    plan = ugf.render_plan(targets, golden)
    assert "MSK" in plan and "ROUTER" in plan and "geoip.dat" in plan
```

- [ ] **Step 2: Run — expect FAIL**

Run: `python3 -m pytest tests/test_flows.py -v`
Expected: FAIL (`format_summary`/`render_plan` missing).

- [ ] **Step 3: Implement** (append to `scripts/update_geofiles.py`)

```python
def render_plan(targets, golden):
    lines = [f"golden: geoip={golden['geoip.dat']['sha'][:12]}… geosite={golden['geosite.dat']['sha'][:12]}…"]
    for t in targets:
        lines.append(f"  - {t['name']} ({t['kind']}): restart=" + {
            "xui": "restartXrayService", "router": "xkeen -restart",
            "docker-updater": f"docker restart {t.get('container')}"}.get(t["kind"], "?"))
    return "\n".join(lines)

def format_summary(results):
    ok = sum(1 for r in results if r["ok"])
    total = len(results)
    head = f"OK {ok}/{total}" if ok == total else f"FAIL {ok}/{total}"
    failed = ",".join(r["name"] for r in results if not r["ok"])
    return head + (f": {failed}" if failed else "")

def main(argv=None):
    p = argparse.ArgumentParser(description="Update geo .dat on all targets and reload.")
    p.add_argument("--config", default=os.path.expanduser("~/.config/zkeen-patcher/targets.json"))
    p.add_argument("--only", help="comma-separated target names")
    p.add_argument("--force", action="store_true", help="apply even if SHA already matches")
    p.add_argument("--no-restart", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="preflight + plan only")
    p.add_argument("-v","--verbose", action="store_true")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    min_size = cfg.get("min_size", DEFAULT_MIN_SIZE)
    targets = [t for t in cfg["targets"] if validate_target(t) is None]
    targets = filter_targets(targets, args.only)
    if not targets:
        print("no targets selected"); return 2

    import tempfile
    with tempfile.TemporaryDirectory(prefix="zkeen-geo-") as tmp:
        try:
            golden = download_release(RELEASE_URLS, tmp, min_size, _http_fetch)
        except UpdateError as e:
            print(f"preflight FAILED: {e}"); return 1
        print(render_plan(targets, golden))
        if args.dry_run:
            print("(dry-run)"); return 0
        deps = Deps
        results = []
        for t in targets:
            if t["kind"] == "xui":      r = apply_xui(t, golden, args.force, deps)
            elif t["kind"] == "router": r = apply_router(t, golden, args.force, deps)
            elif t["kind"] == "docker-updater": r = apply_mirror(t, golden, deps)
            else: r = {"ok": False, "msg": f"{t['name']}: unknown kind", "sha": {}}
            mark = "✓" if r["ok"] else "✗"
            print(f"{mark} {r['msg']}")
            results.append({"name": t["name"], "ok": r["ok"]})
    print(format_summary(results))
    return 0 if all(r["ok"] for r in results) else 1

if __name__ == "__main__":
    import sys; sys.exit(main())
```

- [ ] **Step 4: Run — expect PASS**

Run: `python3 -m pytest -q`
Expected: all passed.

- [ ] **Step 5: Add docs**

`requirements.txt`:
```
paramiko>=3.4
requests>=2.31
```

`scripts/README.md`:
````markdown
# update_geofiles.py

Updates `ip.dat`/`geo.dat` on all nodes from the latest zkeen-patcher release and reloads xray/xkeen/geo-updater.

## Setup (one-time)
```bash
pip install --user -r requirements.txt
mkdir -p ~/.config/zkeen-patcher
cp scripts/targets.example.json ~/.config/zkeen-patcher/targets.json
chmod 600 ~/.config/zkeen-patcher/targets.json
# edit targets.json: fill <TOKEN_*>, <basePath_*>, <SUDO_PASSWORD>, <ROUTER_PASSWORD>
```
The ed25519 key must be loaded in ssh-agent (`ssh-add -l`) for MSK/SPB/EST/LAN-MIRROR.

## Usage
```bash
python3 scripts/update_geofiles.py --dry-run       # preflight + plan
python3 scripts/update_geofiles.py --only MSK -v   # one target
python3 scripts/update_geofiles.py                 # all
```
Exit code 0 iff every target succeeded.
````

Append to root `README.md` (new section before `## Build locally`):
````markdown
## Auto-update geo files on nodes

`scripts/update_geofiles.py` pushes the latest `geoip.dat`/`geosite.dat` to all
nodes (3× 3x-ui VPS, router, LAN geo-updater mirror) and reloads them, verified
by SHA256. See `scripts/README.md`. Secrets live in
`~/.config/zkeen-patcher/targets.json` (never committed).
````

- [ ] **Step 6: Commit**

```bash
git add scripts/update_geofiles.py scripts/README.md requirements.txt README.md tests/test_flows.py
git commit -m "feat(geoupdate): CLI orchestration, summary, docs"
```

- [ ] **Step 7: Full dry-run, then full live run**

```bash
python3 scripts/update_geofiles.py --dry-run -v
python3 scripts/update_geofiles.py -v
```
Expected: dry-run prints golden SHAs + 5-target plan; live run prints `✓` for all five and `OK 5/5`, exit 0.

---

## Self-Review (run after writing — results recorded here)

- **Spec coverage:** §2 inventory → Tasks 1,9 (config); §3 preflight → Task 3; §4 restart API → Task 4 (+§5.1 step 3); §5.1 xui → Task 6; §5.2 router → Task 7; §5.3 mirror → Task 8; §6 safety (atomic/bak/rollback/idempotent) → Tasks 2 (`should_skip_file`), 6/7 (`_restore`, `.bak`); §7 CLI → Task 9; §9 prereqs → Task 5 step 3 + `requirements.txt`; §10 files → all tasks; §11 follow-ups → out of scope (separate commit). **Gap:** none.
- **Placeholder scan:** no TBD/TODO. Secrets are `<…>` placeholders by design (config template only).
- **Type consistency:** `golden[name]["sha"]` used consistently; `apply_*` return `{"ok","msg","sha"}` consistently; `Deps` methods match call sites; `restart_urls`/`parse_restart_response` signatures match Task 4 usage. **OK.**

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-08-01-geofile-update.md`.
