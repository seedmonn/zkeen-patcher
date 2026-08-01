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
