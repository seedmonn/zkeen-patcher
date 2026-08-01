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


import paramiko


def _fake_client_factory(connect_fn):
    """SSHClient fake whose connect() calls connect_fn(attempt_number)."""
    state = {"n": 0}

    class FakeClient:
        def set_missing_host_key_policy(self, p):
            pass

        def connect(self, **kw):
            state["n"] += 1
            connect_fn(state["n"])

    return FakeClient, state


def test_ssh_connect_retries_on_eof_then_succeeds(monkeypatch):
    monkeypatch.setattr(ugf.time, "sleep", lambda *a, **k: None)

    def cb(n):
        if n < 3:
            raise paramiko.SSHException("EOF during negotiation")

    FakeClient, state = _fake_client_factory(cb)
    monkeypatch.setattr(paramiko, "SSHClient", lambda: FakeClient())
    c = ugf.ssh_connect({"host": "h", "port": 22, "user": "root", "password": "p"})
    assert c is not None and state["n"] == 3


def test_ssh_connect_no_retry_on_auth_failure(monkeypatch):
    monkeypatch.setattr(ugf.time, "sleep", lambda *a, **k: None)

    def cb(n):
        raise paramiko.AuthenticationException("Permission denied")

    FakeClient, state = _fake_client_factory(cb)
    monkeypatch.setattr(paramiko, "SSHClient", lambda: FakeClient())
    with pytest.raises(paramiko.AuthenticationException):
        ugf.ssh_connect({"host": "h", "port": 22, "user": "root", "password": "p"})
    assert state["n"] == 1  # auth errors must not be retried


def test_ssh_connect_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(ugf.time, "sleep", lambda *a, **k: None)

    def cb(n):
        raise EOFError("EOF during negotiation")

    FakeClient, state = _fake_client_factory(cb)
    monkeypatch.setattr(paramiko, "SSHClient", lambda: FakeClient())
    with pytest.raises(EOFError):
        ugf.ssh_connect({"host": "h", "port": 22, "user": "root", "password": "p"}, attempts=3)
    assert state["n"] == 3


def test_ssh_upload_uses_cat_over_exec(tmp_path):
    # Keenetic dropbear breaks on SFTP; upload must go via `cat > remote` on the exec channel.
    f = tmp_path / "geo.bin"
    f.write_bytes(b"HELLO\x00BIN")
    recorded = {}

    class FakeChan:
        def shutdown_write(self):
            recorded["shutdown"] = True

        def recv_exit_status(self):
            return 0

    class FakeStream:
        def __init__(self, chan):
            self.channel = chan

        def write(self, data):
            recorded["data"] = data

        def flush(self):
            pass

        def read(self):
            return b""

    class FakeClient:
        def exec_command(self, cmd, timeout=None):
            recorded["cmd"] = cmd
            ch = FakeChan()
            return FakeStream(ch), FakeStream(ch), FakeStream(ch)

    ugf.ssh_upload(FakeClient(), str(f), "/tmp/x.dat")
    assert recorded["cmd"] == "cat > /tmp/x.dat"
    assert recorded["data"] == b"HELLO\x00BIN"
    assert recorded.get("shutdown") is True


def test_ssh_upload_raises_on_nonzero_rc(tmp_path):
    f = tmp_path / "geo.bin"
    f.write_bytes(b"data")

    class FakeChan:
        def shutdown_write(self):
            pass

        def recv_exit_status(self):
            return 1  # cat failed

    class FakeStream:
        def __init__(self, chan, payload=b""):
            self.channel = chan
            self._payload = payload

        def write(self, data):
            pass

        def flush(self):
            pass

        def read(self):
            return self._payload

    class FakeClient:
        def exec_command(self, cmd, timeout=None):
            ch = FakeChan()
            return FakeStream(ch), FakeStream(ch), FakeStream(ch, b"no space")

    with pytest.raises(ugf.UpdateError):
        ugf.ssh_upload(FakeClient(), str(f), "/tmp/x.dat")
