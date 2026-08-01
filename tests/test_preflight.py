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
