#!/usr/bin/env python3
"""Update geoip/geosite .dat on all targets and reload xray/xkeen/geo-updater."""
from __future__ import annotations
import json, os, shlex, hashlib, argparse

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

def resolve_config(config_path: str, env=None) -> dict:
    """Load config from the TARGETS_JSON env var if set, else from config_path.

    Enables clone-and-run with creds sourced from a GitHub Variable, e.g.
    TARGETS_JSON="$(gh variable get TARGETS_JSON)" python3 scripts/update_geofiles.py
    — no local secrets file required.
    """
    if env is None:
        env = os.environ
    raw = env.get("TARGETS_JSON")
    if raw:
        return json.loads(raw)
    return load_config(config_path)

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
        if t["ssh"]["user"] != "root" and "sudo_password" not in t:
            raise UpdateError(f"target {t['name']}: sudo_password required for non-root xui user")
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

import time

def ssh_connect(spec, attempts=4):
    import paramiko
    connect_kwargs = {"hostname": spec["host"], "port": int(spec["port"]),
                      "username": spec["user"], "timeout": 15}
    if spec.get("password"):
        connect_kwargs["password"] = spec["password"]
    else:
        # key from agent (or default key files)
        connect_kwargs["allow_agent"] = True
        connect_kwargs["look_for_keys"] = True
    # Retry transient handshake failures: Keenetic dropbear drops rapid
    # connections ("EOF during negotiation"). Auth errors are never retried.
    last = None
    for i in range(attempts):
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            c.connect(**connect_kwargs)
            return c
        except paramiko.AuthenticationException:
            raise  # config error — never retry
        except (paramiko.SSHException, EOFError, OSError) as e:
            last = e
            if i < attempts - 1:
                print(f"  [ssh] {spec['user']}@{spec['host']}:{spec['port']} handshake failed ({type(e).__name__}), retry {i+2}/{attempts}…")
                time.sleep(2 * (i + 1))
    raise last

def ssh_exec(client, cmd, stdin_data=None):
    stdin, stdout, stderr = client.exec_command(cmd, timeout=60)
    if stdin_data is not None:
        stdin.write(stdin_data); stdin.flush()
    try: stdin.channel.shutdown_write()
    except Exception: pass
    rc = stdout.channel.recv_exit_status()
    return rc, stdout.read().decode(errors="replace"), stderr.read().decode(errors="replace")

def ssh_upload(client, local, remote):
    # Upload via exec (`cat > remote`) rather than SFTP: Keenetic's dropbear
    # drops the transport when the SFTP subsystem (a 2nd channel) is opened
    # ("EOF during negotiation"). cat-over-exec uses the exec channel, which
    # works on every target. Fine for these small binary .dat files (<1 MB).
    with open(local, "rb") as f:
        data = f.read()
    stdin, stdout, stderr = client.exec_command(f"cat > {shlex.quote(remote)}", timeout=60)
    try:
        stdin.write(data); stdin.flush()
        try: stdin.channel.shutdown_write()
        except Exception: pass
        rc = stdout.channel.recv_exit_status()
    except Exception as e:
        raise UpdateError(f"upload to {remote} failed: {e}")
    if rc != 0:
        raise UpdateError(f"upload to {remote} failed (cat rc={rc}): {stderr.read().decode(errors='replace')[:200]}")

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
    restart_container = staticmethod(lambda client, name, deps=None: restart_container(client, name, deps or Deps))
    wait_mirror = staticmethod(lambda mirror, golden, timeout, get=_http_get, sleep=time.sleep: wait_mirror(mirror, golden, timeout, get, sleep))
    sleep = staticmethod(time.sleep)

def probe_target(t, deps):
    c = deps.ssh_connect(t["ssh"])
    try:
        rc, out, _ = deps.ssh_exec(c, "id; uname -a; command -v sha256sum openssl docker xkeen 2>/dev/null; echo END")
        print(f"[probe] {t['name']}: rc={rc}\n{out}")
    finally:
        c.close()

def _post_check_xray(deps, client):
    rc, out, _ = deps.ssh_exec(client, "pgrep -f '[x]ray'")
    return out.strip() != ""

def _restore(client, deps, geo_dir, remote_name):
    bak = f"{geo_dir}/{remote_name}.bak"
    tgt = f"{geo_dir}/{remote_name}"
    deps.ssh_exec(client, f"[ -f {shlex.quote(bak)} ] && mv -f {shlex.quote(bak)} {shlex.quote(tgt)} || true")

def apply_xui(t, golden, force, deps, no_restart=False):
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
        if no_restart:
            return {"ok": True, "msg": f"{name}: updated {','.join(applied)} (--no-restart; core not restarted)", "sha": applied}
        ok = deps.restart_xray(t["panel"]["base"], t["panel"]["token"])
        if not ok:
            return {"ok": False, "msg": f"{name}: restart API failed; files written but xray NOT restarted", "sha": applied}
        deps.sleep(5)
        if _post_check_xray(deps, client):
            return {"ok": True, "msg": f"{name}: updated {','.join(applied)} + xray restarted", "sha": applied}
        # rollback — only files written this run; restoring untouched files
        # would clobber them with a stale .bak left over from a prior apply.
        for remote in applied:
            _restore(client, deps, geo_dir, remote)
        deps.restart_xray(t["panel"]["base"], t["panel"]["token"])
        deps.sleep(3)
        return {"ok": False, "msg": f"{name}: xray down after update; rolled back", "sha": applied}
    except Exception as e:
        return {"ok": False, "msg": f"{name}: ERROR {e}", "sha": {}}
    finally:
        try: client.close()
        except Exception: pass

def apply_router(t, golden, force, deps, no_restart=False):
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
        if no_restart:
            return {"ok": True, "msg": f"{name}: updated {','.join(applied)} (--no-restart; core not restarted)", "sha": applied}
        rc, _, _ = deps.ssh_exec(client, "xkeen -restart")
        if rc != 0:
            return {"ok": False, "msg": f"{name}: xkeen -restart failed (rc={rc}); files written but core NOT restarted", "sha": applied}
        deps.sleep(5)
        if _post_check_xray(deps, client):
            return {"ok": True, "msg": f"{name}: updated + xkeen restarted", "sha": applied}
        # rollback — only files written this run (see apply_xui note).
        for remote in applied:
            _restore(client, deps, geo_dir, remote)
        deps.ssh_exec(client, "xkeen -restart"); deps.sleep(3)
        return {"ok": False, "msg": f"{name}: xray down after update; rolled back", "sha": applied}
    except Exception as e:
        return {"ok": False, "msg": f"{name}: ERROR {e}", "sha": {}}
    finally:
        try: client.close()
        except Exception: pass


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


def render_plan(targets, golden):
    lines = [f"golden: geoip.dat={golden['geoip.dat']['sha'][:12]}… geosite.dat={golden['geosite.dat']['sha'][:12]}…"]
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
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    cfg = resolve_config(args.config)
    min_size = cfg.get("min_size", DEFAULT_MIN_SIZE)
    errors = []
    for t in cfg["targets"]:
        try:
            validate_target(t)
        except UpdateError as e:
            errors.append(str(e))
    if errors:
        print("config errors:\n" + "\n".join(errors))
        return 1
    targets = filter_targets(cfg["targets"], args.only)
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
            if t["kind"] == "docker-updater" and args.no_restart:
                print(f"✓ {t['name']}: skipped (--no-restart; mirror needs restart to fetch)")
                results.append({"name": t["name"], "ok": True})
                continue
            if t["kind"] == "xui":      r = apply_xui(t, golden, args.force, deps, no_restart=args.no_restart)
            elif t["kind"] == "router": r = apply_router(t, golden, args.force, deps, no_restart=args.no_restart)
            elif t["kind"] == "docker-updater": r = apply_mirror(t, golden, deps)
            else: r = {"ok": False, "msg": f"{t['name']}: unknown kind", "sha": {}}
            mark = "✓" if r["ok"] else "✗"
            print(f"{mark} {r['msg']}")
            results.append({"name": t["name"], "ok": r["ok"]})
    print(format_summary(results))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    import sys; sys.exit(main())
