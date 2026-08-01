import json, os, tempfile, pytest
import update_geofiles as ugf

def test_redact_short_unchanged():
    assert ugf.redact("abc") == "abc"

def test_redact_long_truncated():
    assert ugf.redact("qeHhFJi47iSkmxBxaFMs", 8) == "qeHhFJi4…"

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
