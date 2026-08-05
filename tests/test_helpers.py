import json, os, tempfile, pytest
import update_geofiles as ugf

def test_redact_short_unchanged():
    assert ugf.redact("abc") == "abc"

def test_redact_long_truncated():
    assert ugf.redact("aaaaaaaaaaaaaaaaaaaa", 8) == "aaaaaaaa…"

def test_load_config_and_validate_ok():
    data = {"min_size": 10240, "targets": [
        {"name": "MSK", "kind": "xui", "ssh": {"host": "192.0.2.10", "port": 22, "user": "root"},
         "geo_dir": "/usr/local/x-ui/bin", "panel": {"base": "https://192.0.2.10:31441/abc", "token": "tok"}},
        {"name": "ROUTER", "kind": "router", "ssh": {"host": "192.0.2.1", "port": 22, "user": "root", "password": "p"},
         "geo_dir": "/opt/etc/xray/dat"},
        {"name": "LAN-MIRROR", "kind": "docker-updater", "ssh": {"host": "192.0.2.101", "port": 20202, "user": "u"},
         "container": "geo-updater", "mirror": "http://192.0.2.101:33133"},
    ]}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(data, f); path = f.name
    cfg = ugf.load_config(path)
    assert cfg["min_size"] == 10240
    for t in cfg["targets"]:
        ugf.validate_target(t)
    os.unlink(path)

def test_resolve_config_from_env_takes_precedence(monkeypatch):
    data = {"min_size": 999, "targets": [{"name": "X", "kind": "router",
             "ssh": {"host": "h", "port": 22, "user": "root", "password": "p"}, "geo_dir": "/d"}]}
    monkeypatch.setenv("TARGETS_JSON", json.dumps(data))
    cfg = ugf.resolve_config("/nonexistent/path.json")  # env wins, file not touched
    assert cfg["min_size"] == 999 and cfg["targets"][0]["name"] == "X"

def test_resolve_config_falls_back_to_file(monkeypatch, tmp_path):
    monkeypatch.delenv("TARGETS_JSON", raising=False)
    f = tmp_path / "t.json"
    f.write_text('{"min_size": 10240, "targets": []}')
    cfg = ugf.resolve_config(str(f))
    assert cfg["min_size"] == 10240 and cfg["targets"] == []

def test_validate_target_xui_requires_panel():
    with pytest.raises(ugf.UpdateError):
        ugf.validate_target({"name": "X", "kind": "xui", "ssh": {"host": "h", "port": 22, "user": "root"}, "geo_dir": "/d"})

def test_validate_target_xui_non_root_requires_sudo_password():
    # #6: non-root xui user must carry sudo_password; root is unaffected
    with pytest.raises(ugf.UpdateError, match="sudo_password required"):
        ugf.validate_target({"name": "SPB", "kind": "xui",
                             "ssh": {"host": "h", "port": 22, "user": "seedmon"},
                             "geo_dir": "/d", "panel": {"base": "b", "token": "t"}})
    # root xui without sudo_password still validates
    ugf.validate_target({"name": "MSK", "kind": "xui",
                         "ssh": {"host": "h", "port": 22, "user": "root"},
                         "geo_dir": "/d", "panel": {"base": "b", "token": "t"}})
    # non-root xui WITH sudo_password validates
    ugf.validate_target({"name": "SPB", "kind": "xui",
                         "ssh": {"host": "h", "port": 22, "user": "seedmon"},
                         "geo_dir": "/d", "sudo_password": "PW",
                         "panel": {"base": "b", "token": "t"}})

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
    assert "set -e" in cmd
    # Live file must be backed up before mv; failure must not be swallowed.
    assert "|| true" not in cmd
    assert "if [ -f /d/ip.dat ]" in cmd

def test_build_apply_command_seedmon_uses_sudo_S():
    cmd, stdin = ugf.build_apply_command("/d", "ip.dat", "/tmp/x", "seedmon")
    assert cmd.startswith("sudo -S -p ''") and stdin == "PW"
    assert "set -e" in cmd and "|| true" not in cmd

def test_build_restore_command_root_has_no_sudo():
    cmd, stdin = ugf.build_restore_command("/d", "ip.dat", "root")
    assert "sudo" not in cmd and stdin is None
    assert "ip.dat.bak" in cmd and "mv -f" in cmd

def test_build_restore_command_seedmon_uses_sudo_S():
    # apply chowns targets to root:root; non-root rollback must elevate or it
    # silently fails (Permission denied) while reporting "rolled back".
    cmd, stdin = ugf.build_restore_command("/d", "ip.dat", "seedmon")
    assert cmd.startswith("sudo -S -p ''") and stdin == "PW"
    assert "ip.dat.bak" in cmd

def test_filter_targets():
    ts = [{"name": "MSK"}, {"name": "SPB"}]
    assert [t["name"] for t in ugf.filter_targets(ts, None)] == ["MSK", "SPB"]
    assert [t["name"] for t in ugf.filter_targets(ts, "MSK")] == ["MSK"]
    assert [t["name"] for t in ugf.filter_targets(ts, "msk,SPB")] == ["MSK", "SPB"]
