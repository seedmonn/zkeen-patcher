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
