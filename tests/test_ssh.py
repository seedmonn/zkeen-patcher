import pytest
import update_geofiles as ugf

HEX = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"

def _stub_ssh_exec(canned_out, recorded=None):
    def fake(client, cmd, stdin_data=None):
        if recorded is not None:
            recorded.append(cmd)
        return (0, canned_out, "")
    return fake

def test_remote_sha256_sha256sum_format(monkeypatch):
    monkeypatch.setattr(ugf, "ssh_exec", _stub_ssh_exec(f"{HEX}  /d/ip.dat\n"))
    assert ugf.remote_sha256(object(), "/d/ip.dat") == HEX

def test_remote_sha256_openssl_format(monkeypatch):
    out = f"SHA256(/d/ip.dat)= {HEX}\n"
    monkeypatch.setattr(ugf, "ssh_exec", _stub_ssh_exec(out))
    assert ugf.remote_sha256(object(), "/d/ip.dat") == HEX

def test_remote_sha256_missing_file_returns_none(monkeypatch):
    monkeypatch.setattr(ugf, "ssh_exec", _stub_ssh_exec(""))
    assert ugf.remote_sha256(object(), "/d/ip.dat") is None

def test_remote_sha256_command_tries_sha256sum_then_openssl(monkeypatch):
    recorded = []
    monkeypatch.setattr(ugf, "ssh_exec", _stub_ssh_exec(f"{HEX}  /d/ip.dat\n", recorded))
    ugf.remote_sha256(object(), "/d/ip.dat")
    assert len(recorded) == 1
    cmd = recorded[0]
    assert "sha256sum" in cmd
    assert "||" in cmd
    assert "openssl dgst -sha256" in cmd
    # sha256sum must appear before the openssl fallback
    assert cmd.index("sha256sum") < cmd.index("openssl")
