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
    ip_sha = ugf.sha256_bytes(b"GOLD-IP")
    geo_sha = ugf.sha256_bytes(b"GOLD-GEO")
    golden = {"geoip.dat": {"sha": ip_sha}, "geosite.dat": {"sha": geo_sha}}
    # first poll stale for both, second poll matches for both
    state = {"ip": b"old-ip", "geo": b"old-geo"}
    def get(url):
        if url.endswith("/ip.dat"):
            return (state["ip"], 200)
        if url.endswith("/geo.dat"):
            return (state["geo"], 200)
        return (b"", 404)
    sleeps = []
    def sleep_fn(n):
        sleeps.append(n)
        # after first poll cycle, flip both to matching bytes
        state["ip"] = b"GOLD-IP"
        state["geo"] = b"GOLD-GEO"
    assert ugf.wait_mirror("http://m", golden, 5, get, sleep_fn) is True
    assert len(sleeps) == 1

def test_wait_mirror_timeout():
    ip_sha = ugf.sha256_bytes(b"new-ip")
    geo_sha = ugf.sha256_bytes(b"new-geo")
    golden = {"geoip.dat": {"sha": ip_sha}, "geosite.dat": {"sha": geo_sha}}
    def get(url):
        # always stale for both remotes
        if url.endswith("/ip.dat"):
            return (b"stale-ip", 200)
        if url.endswith("/geo.dat"):
            return (b"stale-geo", 200)
        return (b"", 404)
    def sleep_nop(_): pass
    assert ugf.wait_mirror("http://m", golden, 0, get, sleep_nop) is False

def test_wait_mirror_raises_on_incomplete_golden():
    ip_sha = ugf.sha256_bytes(b"new-ip")
    golden = {"geoip.dat": {"sha": ip_sha}}  # missing geosite.dat
    def get(url):
        return (b"x", 200)
    def sleep_nop(_): pass
    with pytest.raises(ugf.UpdateError):
        ugf.wait_mirror("http://m", golden, 5, get, sleep_nop)

def test_wait_mirror_requires_both_files_when_complete():
    ip_sha = ugf.sha256_bytes(b"MATCH-IP")
    geo_sha = ugf.sha256_bytes(b"MATCH-GEO")
    golden = {"geoip.dat": {"sha": ip_sha}, "geosite.dat": {"sha": geo_sha}}
    def get(url):
        # /ip.dat matches, /geo.dat stale
        if url.endswith("/ip.dat"):
            return (b"MATCH-IP", 200)
        if url.endswith("/geo.dat"):
            return (b"stale-geo", 200)
        return (b"", 404)
    def sleep_nop(_): pass
    assert ugf.wait_mirror("http://m", golden, 2, get, sleep_nop) is False
