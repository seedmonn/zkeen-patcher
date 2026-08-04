import re
import update_geofiles as ugf


class FakeDeps:
    def __init__(self):
        self.box = {"/d/ip.dat": b"OLD_IP", "/d/geo.dat": b"OLD_GEO"}  # remote current bytes
        self.uploads = {}
        self.exec_log = []
        self.xray_alive = True
        self.restart_ok = True
        self.xkeen_rc = 0

    def ssh_connect(self, spec): return "CLIENT"

    def ssh_upload(self, client, local, remote):
        with open(local, "rb") as f: self.uploads[remote] = f.read()

    def remote_sha256(self, client, path):
        # an uploaded file also "exists on the remote box"
        b = self.box.get(path) or self.uploads.get(path)
        return ugf.sha256_bytes(b) if b else None

    def ssh_exec(self, client, cmd, stdin_data=None):
        self.exec_log.append((cmd, stdin_data))
        # simulate apply: tmp path written via upload; mv moves it into box path
        rc, out = 0, ""
        if cmd.startswith("sudo -S") or cmd.startswith("sh -c"):
            # rollback restore (also wrapped in sh -c / sudo -S so non-root can
            # move root-owned targets): "[ -f <bak> ] && mv -f <bak> <tgt>"
            if "[ -f" in cmd:
                m = re.search(r"mv -f ([^;\s]+) ([^;\s]+)", cmd)
                if m and m.group(1) in self.box:
                    self.box[m.group(2)] = self.box[m.group(1)]
            else:
                # parse "cp -f <tgt> <bak>" (backup creation); [^;\s] avoids trailing ';'
                mcp = re.search(r"cp -f ([^;\s]+) ([^;\s]+)", cmd)
                if mcp and mcp.group(1) in self.box:
                    self.box[mcp.group(2)] = self.box[mcp.group(1)]
                # parse "mv -f <tmp> <target>"
                m = re.search(r"mv -f ([^;\s]+) ([^;\s]+)", cmd)
                if m:
                    tmp, tgt = m.group(1), m.group(2)
                    self.box[tgt] = self.uploads.get(tmp, b"")
        elif "pgrep" in cmd:
            out = "1234\n" if self.xray_alive else ""
            rc = 0 if self.xray_alive else 1
        elif "xkeen -restart" in cmd:
            rc = self.xkeen_rc
        return rc, out, ""

    def restart_xray(self, base, token, post=None): return self.restart_ok

    sleep = staticmethod(lambda *a, **k: None)


def test_apply_xui_skips_when_already_golden():
    t = {"name": "MSK", "kind": "xui", "ssh": {"host": "h", "port": 22, "user": "root"}, "geo_dir": "/d", "panel": {"base": "b", "token": "t"}}
    golden = {"geoip.dat": {"path": "/g/geoip.dat", "sha": ugf.sha256_bytes(b"OLD_IP")},  # already matches
              "geosite.dat": {"path": "/g/geosite.dat", "sha": ugf.sha256_bytes(b"OLD_GEO")}}
    d = FakeDeps()
    r = ugf.apply_xui(t, golden, force=False, deps=d)
    assert r["ok"] and "up to date" in r["msg"]
    assert d.exec_log == []  # nothing applied


def test_apply_xui_writes_and_restarts(tmp_path):
    t = {"name": "SPB", "kind": "xui", "ssh": {"host": "h", "port": 53908, "user": "seedmon"}, "geo_dir": "/d", "sudo_password": "PW", "panel": {"base": "b", "token": "t"}}
    golden = {"geoip.dat": {"path": str(tmp_path / "geoip.dat"), "sha": ugf.sha256_bytes(b"NEW_IP")},
              "geosite.dat": {"path": str(tmp_path / "geosite.dat"), "sha": ugf.sha256_bytes(b"NEW_GEO")}}
    d = FakeDeps()
    # seed local golden paths
    for rel, b in [("geoip.dat", b"NEW_IP"), ("geosite.dat", b"NEW_GEO")]:
        (tmp_path / rel).write_bytes(b)
    r = ugf.apply_xui(t, golden, force=False, deps=d)
    assert r["ok"], r["msg"]
    assert d.box["/d/ip.dat"] == b"NEW_IP"
    assert any("pgrep" in c for c, _ in d.exec_log)
    assert any(cmd.startswith("sudo -S") for cmd, _ in d.exec_log)  # seedmon used sudo
    assert any(sd == t["sudo_password"] for cmd, sd in d.exec_log if sd)  # password reached stdin


def test_apply_xui_rollback_when_xray_down(tmp_path):
    t = {"name": "MSK", "kind": "xui", "ssh": {"host": "h", "port": 22, "user": "root"}, "geo_dir": "/d", "panel": {"base": "b", "token": "t"}}
    golden = {"geoip.dat": {"path": str(tmp_path / "geoip.dat"), "sha": ugf.sha256_bytes(b"NEW_IP")},
              "geosite.dat": {"path": str(tmp_path / "geosite.dat"), "sha": ugf.sha256_bytes(b"NEW_GEO")}}
    for rel, b in [("geoip.dat", b"NEW_IP"), ("geosite.dat", b"NEW_GEO")]:
        (tmp_path / rel).write_bytes(b)
    d = FakeDeps(); d.xray_alive = False
    r = ugf.apply_xui(t, golden, force=False, deps=d)
    assert r["ok"] is False
    # rollback restored originals
    assert d.box["/d/ip.dat"] == b"OLD_IP"


def test_apply_xui_no_restart_skips_restart(tmp_path):
    t = {"name": "MSK", "kind": "xui", "ssh": {"host": "h", "port": 22, "user": "root"}, "geo_dir": "/d", "panel": {"base": "b", "token": "t"}}
    golden = {"geoip.dat": {"path": str(tmp_path / "geoip.dat"), "sha": ugf.sha256_bytes(b"NEW_IP")},
              "geosite.dat": {"path": str(tmp_path / "geosite.dat"), "sha": ugf.sha256_bytes(b"NEW_GEO")}}
    for rel, b in [("geoip.dat", b"NEW_IP"), ("geosite.dat", b"NEW_GEO")]:
        (tmp_path / rel).write_bytes(b)
    d = FakeDeps()
    r = ugf.apply_xui(t, golden, force=False, deps=d, no_restart=True)
    assert r["ok"], r["msg"]
    assert "--no-restart" in r["msg"]
    assert d.box["/d/ip.dat"] == b"NEW_IP"
    assert not any("pgrep" in c for c, _ in d.exec_log)
    assert not any(cmd.startswith("restart") for cmd, _ in d.exec_log)


def test_apply_router_writes_and_xkeen_restart(tmp_path):
    t = {"name": "ROUTER", "kind": "router", "ssh": {"host": "r", "port": 22, "user": "root", "password": "p"}, "geo_dir": "/opt/etc/xray/dat"}
    golden = {"geoip.dat": {"path": str(tmp_path / "geoip.dat"), "sha": ugf.sha256_bytes(b"NEW_IP")},
              "geosite.dat": {"path": str(tmp_path / "geosite.dat"), "sha": ugf.sha256_bytes(b"NEW_GEO")}}
    for rel, b in [("geoip.dat", b"NEW_IP"), ("geosite.dat", b"NEW_GEO")]:
        (tmp_path / rel).write_bytes(b)
    d = FakeDeps(); d.box = {"/opt/etc/xray/dat/ip.dat": b"OLD"}
    r = ugf.apply_router(t, golden, force=False, deps=d)
    assert r["ok"], r["msg"]
    assert d.box["/opt/etc/xray/dat/ip.dat"] == b"NEW_IP"
    assert any("xkeen -restart" in c for c, _ in d.exec_log)


def test_apply_xui_restart_fails_returns_false(tmp_path):
    # #1: restartXrayService API fails -> ok=False, but written files stay in place
    t = {"name": "SPB", "kind": "xui", "ssh": {"host": "h", "port": 53908, "user": "seedmon"}, "geo_dir": "/d", "sudo_password": "PW", "panel": {"base": "b", "token": "t"}}
    golden = {"geoip.dat": {"path": str(tmp_path / "geoip.dat"), "sha": ugf.sha256_bytes(b"NEW_IP")},
              "geosite.dat": {"path": str(tmp_path / "geosite.dat"), "sha": ugf.sha256_bytes(b"NEW_GEO")}}
    for rel, b in [("geoip.dat", b"NEW_IP"), ("geosite.dat", b"NEW_GEO")]:
        (tmp_path / rel).write_bytes(b)
    d = FakeDeps(); d.restart_ok = False
    r = ugf.apply_xui(t, golden, force=False, deps=d)
    assert r["ok"] is False
    assert "restart API failed" in r["msg"]
    assert d.box["/d/ip.dat"] == b"NEW_IP"  # files written, not rolled back


def test_apply_router_restart_fails_returns_false(tmp_path):
    # #1: xkeen -restart returns non-zero -> ok=False, but written files stay in place
    t = {"name": "ROUTER", "kind": "router", "ssh": {"host": "r", "port": 22, "user": "root", "password": "p"}, "geo_dir": "/opt/etc/xray/dat"}
    golden = {"geoip.dat": {"path": str(tmp_path / "geoip.dat"), "sha": ugf.sha256_bytes(b"NEW_IP")},
              "geosite.dat": {"path": str(tmp_path / "geosite.dat"), "sha": ugf.sha256_bytes(b"NEW_GEO")}}
    for rel, b in [("geoip.dat", b"NEW_IP"), ("geosite.dat", b"NEW_GEO")]:
        (tmp_path / rel).write_bytes(b)
    d = FakeDeps(); d.box = {"/opt/etc/xray/dat/ip.dat": b"OLD"}; d.xkeen_rc = 2
    r = ugf.apply_router(t, golden, force=False, deps=d)
    assert r["ok"] is False
    assert "xkeen -restart failed" in r["msg"] and "rc=2" in r["msg"]
    assert d.box["/opt/etc/xray/dat/ip.dat"] == b"NEW_IP"  # files written, not rolled back


def test_apply_router_rollback_when_xray_down(tmp_path):
    # #2: restart succeeds but xray stays down -> rollback, ok=False
    t = {"name": "ROUTER", "kind": "router", "ssh": {"host": "r", "port": 22, "user": "root", "password": "p"}, "geo_dir": "/opt/etc/xray/dat"}
    golden = {"geoip.dat": {"path": str(tmp_path / "geoip.dat"), "sha": ugf.sha256_bytes(b"NEW_IP")},
              "geosite.dat": {"path": str(tmp_path / "geosite.dat"), "sha": ugf.sha256_bytes(b"NEW_GEO")}}
    for rel, b in [("geoip.dat", b"NEW_IP"), ("geosite.dat", b"NEW_GEO")]:
        (tmp_path / rel).write_bytes(b)
    d = FakeDeps(); d.box = {"/opt/etc/xray/dat/ip.dat": b"OLD"}; d.xray_alive = False
    r = ugf.apply_router(t, golden, force=False, deps=d)
    assert r["ok"] is False
    assert "rolled back" in r["msg"]
    assert d.box["/opt/etc/xray/dat/ip.dat"] == b"OLD"  # restored


def test_apply_xui_rollback_preserves_skipped_file(tmp_path):
    # Regression: rollback must only restore files written this run. A file
    # that is already golden (skipped) with a stale .bak on disk must NOT be
    # clobbered by rollback when the *other* file's update triggers a rollback.
    t = {"name": "MSK", "kind": "xui", "ssh": {"host": "h", "port": 22, "user": "root"}, "geo_dir": "/d", "panel": {"base": "b", "token": "t"}}
    golden = {"geoip.dat": {"path": str(tmp_path / "geoip.dat"), "sha": ugf.sha256_bytes(b"GOLD_IP")},  # already on box -> skipped
              "geosite.dat": {"path": str(tmp_path / "geosite.dat"), "sha": ugf.sha256_bytes(b"NEW_GEO")}}
    for rel, b in [("geoip.dat", b"GOLD_IP"), ("geosite.dat", b"NEW_GEO")]:
        (tmp_path / rel).write_bytes(b)
    d = FakeDeps()
    d.box = {"/d/ip.dat": b"GOLD_IP", "/d/ip.dat.bak": b"ANCIENT_IP", "/d/geo.dat": b"OLD_GEO"}
    d.xray_alive = False  # force rollback
    r = ugf.apply_xui(t, golden, force=False, deps=d)
    assert r["ok"] is False and "rolled back" in r["msg"]
    assert d.box["/d/ip.dat"] == b"GOLD_IP"  # skipped file untouched, NOT restored from stale .bak


def test_apply_xui_nonroot_rollback_uses_sudo(tmp_path):
    # Regression: apply chowns files to root:root, so non-root rollback must
    # use sudo -S (with the sudo password on stdin) or mv fails silently.
    t = {"name": "SPB", "kind": "xui", "ssh": {"host": "h", "port": 53908, "user": "seedmon"},
         "geo_dir": "/d", "sudo_password": "PW", "panel": {"base": "b", "token": "t"}}
    golden = {"geoip.dat": {"path": str(tmp_path / "geoip.dat"), "sha": ugf.sha256_bytes(b"NEW_IP")},
              "geosite.dat": {"path": str(tmp_path / "geosite.dat"), "sha": ugf.sha256_bytes(b"NEW_GEO")}}
    for rel, b in [("geoip.dat", b"NEW_IP"), ("geosite.dat", b"NEW_GEO")]:
        (tmp_path / rel).write_bytes(b)
    d = FakeDeps(); d.xray_alive = False
    r = ugf.apply_xui(t, golden, force=False, deps=d)
    assert r["ok"] is False and "rolled back" in r["msg"]
    assert d.box["/d/ip.dat"] == b"OLD_IP"
    assert d.box["/d/geo.dat"] == b"OLD_GEO"
    restore_cmds = [(c, sd) for c, sd in d.exec_log if "[ -f" in c]
    assert restore_cmds, "expected restore commands"
    assert all(c.startswith("sudo -S") for c, _ in restore_cmds)
    assert all(sd == "PW" for _, sd in restore_cmds)


def test_apply_xui_exception_rolls_back_partial(tmp_path):
    # Regression: if the 2nd file fails after the 1st was written, roll back
    # the partial apply instead of leaving mismatched geoip/geosite on disk.
    t = {"name": "MSK", "kind": "xui", "ssh": {"host": "h", "port": 22, "user": "root"},
         "geo_dir": "/d", "panel": {"base": "b", "token": "t"}}
    golden = {"geoip.dat": {"path": str(tmp_path / "geoip.dat"), "sha": ugf.sha256_bytes(b"NEW_IP")},
              "geosite.dat": {"path": str(tmp_path / "geosite.dat"), "sha": ugf.sha256_bytes(b"NEW_GEO")}}
    for rel, b in [("geoip.dat", b"NEW_IP"), ("geosite.dat", b"NEW_GEO")]:
        (tmp_path / rel).write_bytes(b)
    d = FakeDeps()
    real_sha = d.remote_sha256
    calls = {"n": 0}

    def flaky_sha(client, path):
        # Let apply of ip.dat succeed (upload check + post-write check), then
        # fail the post-write check for geo.dat.
        sha = real_sha(client, path)
        if path == "/d/geo.dat":
            calls["n"] += 1
            if calls["n"] >= 1:
                return "0" * 64  # mismatch vs golden
        return sha

    d.remote_sha256 = flaky_sha
    r = ugf.apply_xui(t, golden, force=False, deps=d)
    assert r["ok"] is False
    assert "ERROR" in r["msg"] and "rolled back" in r["msg"]
    assert d.box["/d/ip.dat"] == b"OLD_IP"  # first file restored
    assert d.box["/d/geo.dat"] == b"OLD_GEO"  # second never committed (or restored)


def test_apply_mirror_restart_and_verify():
    sha = ugf.sha256_bytes(b"FRESH")
    t = {"name": "LAN-MIRROR", "kind": "docker-updater", "ssh": {"host": "m", "port": 20202, "user": "ginseng"},
         "container": "geo-updater", "mirror": "http://m:33133"}
    golden = {"geoip.dat": {"path": "/g/geoip.dat", "sha": sha}, "geosite.dat": {"path": "/g/geosite.dat", "sha": sha}}
    class MD(FakeDeps):
        def wait_mirror(self, mirror, golden_, timeout, get=None, sleep=None): return True
    d = MD()
    r = ugf.apply_mirror(t, golden, d)
    assert r["ok"], r["msg"]
    assert any("docker restart geo-updater" in c for c, _ in d.exec_log)


def test_apply_mirror_honors_mirror_timeout():
    # Regression: mirror_timeout from config must reach wait_mirror.
    t = {"name": "LAN-MIRROR", "kind": "docker-updater", "ssh": {"host": "m", "port": 20202, "user": "ginseng"},
         "container": "geo-updater", "mirror": "http://m:33133"}
    golden = {"geoip.dat": {"path": "/g/geoip.dat", "sha": "a"}, "geosite.dat": {"path": "/g/geosite.dat", "sha": "b"}}
    seen = {}
    class MD(FakeDeps):
        def wait_mirror(self, mirror, golden_, timeout, get=None, sleep=None):
            seen["timeout"] = timeout
            return True
    d = MD()
    ugf.apply_mirror(t, golden, d, mirror_timeout=120)
    assert seen["timeout"] == 120


def test_format_summary_counts():
    res = [{"name": "MSK", "ok": True}, {"name": "SPB", "ok": False}]
    s = ugf.format_summary(res)
    assert "FAIL 1/2" in s and "SPB" in s


def test_format_summary_full_success():
    res = [{"name": "MSK", "ok": True}, {"name": "SPB", "ok": True}]
    s = ugf.format_summary(res)
    assert "OK 2/2" in s
    assert ": " not in s


def test_render_plan_lists_targets():
    targets = [{"name": "MSK", "kind": "xui"}, {"name": "ROUTER", "kind": "router"}]
    golden = {"geoip.dat": {"sha": "a" * 16}, "geosite.dat": {"sha": "b" * 16}}
    plan = ugf.render_plan(targets, golden)
    assert "MSK" in plan and "ROUTER" in plan and "geoip.dat" in plan
