"""Push phototag tags to Nextcloud Schlagworte (system tags) via WebDAV + OCS."""
from __future__ import annotations

import base64
import json
import re
import ssl
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from dataclasses import dataclass

from phototag.config import (
    nc_app_token,
    nc_dav_prefix,
    nc_tls_ca_file,
    nc_tls_insecure,
    nc_url,
    nc_user,
)

NS = {
    "d": "DAV:",
    "oc": "http://owncloud.org/ns",
    "nc": "http://nextcloud.org/ns",
}


@dataclass(frozen=True)
class NcConfig:
    base_url: str
    user: str
    app_token: str
    dav_prefix: str  # path under .../files/USER/ (no leading slash)


def load_nc_config() -> NcConfig:
    url = nc_url()
    user = nc_user()
    app_token = nc_app_token()
    if not url or not user or not app_token:
        raise RuntimeError(
            "Nextcloud nicht konfiguriert: PHOTOTAG_NC_URL, PHOTOTAG_NC_USER, "
            "PHOTOTAG_NC_APP_PASSWORD (oder PHOTOTAG_NC_TOKEN_FILE) setzen."
        )
    return NcConfig(
        base_url=url.rstrip("/"),
        user=user,
        app_token=app_token,
        dav_prefix=nc_dav_prefix().strip("/"),
    )


def dav_file_url(cfg: NcConfig, relative_path: str) -> str:
    """relative_path = phototag path under scan root (posix)."""
    rel = relative_path.lstrip("/")
    if cfg.dav_prefix:
        rel = f"{cfg.dav_prefix}/{rel}" if rel else cfg.dav_prefix
    segments = urllib.parse.quote(rel, safe="/").split("/")
    path = "/".join(segments)
    user = urllib.parse.quote(cfg.user, safe="")
    return f"{cfg.base_url}/remote.php/dav/files/{user}/{path}"


def _auth_header(cfg: NcConfig) -> str:
    token = base64.b64encode(f"{cfg.user}:{cfg.app_token}".encode()).decode("ascii")
    return f"Basic {token}"


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ca = nc_tls_ca_file()
    if ca:
        cap = Path(ca)
        if not cap.is_file():
            raise RuntimeError(
                f"PHOTOTAG_NC_CA_FILE nicht gefunden: {ca} "
                "(Datei anlegen, Zeile entfernen, oder PHOTOTAG_NC_TLS_INSECURE=1 nur im VLAN)"
            )
        ctx.load_verify_locations(cafile=str(cap))
    if nc_tls_insecure():
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _urlopen(req: urllib.request.Request, timeout: int = 120):
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ssl_context()))
    return opener.open(req, timeout=timeout)


def _request(
    cfg: NcConfig,
    method: str,
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    status, raw, _ = _request_ex(cfg, method, url, data=data, headers=headers)
    return status, raw


def _request_ex(
    cfg: NcConfig,
    method: str,
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes, object]:
    hdrs = {
        "Authorization": _auth_header(cfg),
        "OCS-APIRequest": "true",
    }
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with _urlopen(req, timeout=120) as resp:
            return resp.status, resp.read(), resp
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc


_PROPFIND_SYSTEMTAGS = (
    '<?xml version="1.0"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" '
    'xmlns:nc="http://nextcloud.org/ns">'
    "<d:prop><oc:id/><oc:display-name/></d:prop></d:propfind>"
).encode("utf-8")


def _parse_systemtags_multistatus(raw: bytes) -> dict[str, int]:
    root = ET.fromstring(raw)
    out: dict[str, int] = {}
    for response in root.findall(f".//{{{NS['d']}}}response"):
        id_el = response.find(f".//{{{NS['oc']}}}id")
        name_el = response.find(f".//{{{NS['oc']}}}display-name")
        if id_el is None or name_el is None or not id_el.text or not name_el.text:
            continue
        if not id_el.text.isdigit():
            continue
        out[name_el.text.strip().lower()] = int(id_el.text)
    return out


def file_id(cfg: NcConfig, relative_path: str) -> int:
    url = dav_file_url(cfg, relative_path)
    body = (
        '<?xml version="1.0"?>'
        '<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" '
        'xmlns:nc="http://nextcloud.org/ns">'
        "<d:prop><oc:fileid/><nc:fileid/></d:prop></d:propfind>"
    ).encode("utf-8")
    status, raw = _request(
        cfg,
        "PROPFIND",
        url,
        data=body,
        headers={"Depth": "0", "Content-Type": "application/xml"},
    )
    if status not in (207, 200):
        raise RuntimeError(f"PROPFIND {relative_path}: HTTP {status} — Datei in NC? dav_prefix ok?")
    root = ET.fromstring(raw)
    for tag in ("fileid",):
        for ns in ("oc", "nc"):
            el = root.find(f".//{{{NS[ns]}}}{tag}")
            if el is not None and el.text and el.text.isdigit():
                return int(el.text)
    raise RuntimeError(f"Keine fileid in PROPFIND für {relative_path}")


def _list_system_tags(cfg: NcConfig) -> dict[str, int]:
    url = f"{cfg.base_url}/remote.php/dav/systemtags/"
    status, raw = _request(
        cfg,
        "PROPFIND",
        url,
        data=_PROPFIND_SYSTEMTAGS,
        headers={"Depth": "1", "Content-Type": "application/xml"},
    )
    if status not in (207, 200):
        raise RuntimeError(f"Systemtags auflisten: HTTP {status}")
    return _parse_systemtags_multistatus(raw)


def ensure_system_tag(cfg: NcConfig, cache: dict[str, int], tag_name: str) -> int:
    key = tag_name.strip().lower()
    if key in cache:
        return cache[key]
    url = f"{cfg.base_url}/remote.php/dav/systemtags/"
    payload = json.dumps(
        {"name": key, "userVisible": True, "userAssignable": True},
    ).encode("utf-8")
    status, _raw, resp = _request_ex(
        cfg,
        "POST",
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    if status == 201:
        loc = resp.headers.get("Content-Location") or resp.headers.get("content-location") or ""
        m = re.search(r"/systemtags/(\d+)", loc)
        if m:
            cache[key] = int(m.group(1))
            return cache[key]
    if status in (201, 409):
        cache.clear()
        cache.update(_list_system_tags(cfg))
        if key in cache:
            return cache[key]
    raise RuntimeError(f"Systemtag anlegen/fehlend: {key} (HTTP {status})")


def assign_tag_to_file(cfg: NcConfig, file_id: int, tag_id: int) -> None:
    url = f"{cfg.base_url}/remote.php/dav/systemtags-relations/files/{file_id}/{tag_id}"
    status, _ = _request(cfg, "PUT", url)
    if status in (201, 204, 200):
        return
    if status == 412:
        return  # Relation existiert bereits
    raise RuntimeError(f"Tag zuweisen file={file_id} tag={tag_id}: HTTP {status}")


def ensure_nc_pushed_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS nc_pushed(
          image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
          tag TEXT NOT NULL,
          pushed_at REAL NOT NULL,
          PRIMARY KEY(image_id, tag)
        )
        """
    )


def push_image_tags(
    con: sqlite3.Connection,
    cfg: NcConfig,
    *,
    image_id: int,
    path: str,
    tags: list[str],
    tag_cache: dict[str, int],
    dry_run: bool = False,
) -> int:
    """Returns number of tags newly pushed."""
    if dry_run:
        return len(tags)
    fid = file_id(cfg, path)
    pushed = 0
    now = time.time()
    for tag in tags:
        tid = ensure_system_tag(cfg, tag_cache, tag)
        assign_tag_to_file(cfg, fid, tid)
        con.execute(
            "INSERT OR REPLACE INTO nc_pushed(image_id, tag, pushed_at) VALUES(?,?,?)",
            (image_id, tag.lower(), now),
        )
        pushed += 1
    return pushed


def pending_tagged_images(
    con: sqlite3.Connection,
    *,
    limit: int,
    force: bool,
) -> list[sqlite3.Row]:
    ensure_nc_pushed_table(con)
    if force:
        sql = """
            SELECT i.id, i.path, i.root
            FROM images i
            WHERE i.status='tagged'
            ORDER BY i.id
        """
        if limit:
            sql += " LIMIT ?"
            return con.execute(sql, (limit,)).fetchall()
        return con.execute(sql).fetchall()

    sql = """
        SELECT i.id, i.path, i.root
        FROM images i
        WHERE i.status='tagged'
          AND EXISTS (
            SELECT 1 FROM tags t
            WHERE t.image_id=i.id
              AND NOT EXISTS (
                SELECT 1 FROM nc_pushed p
                WHERE p.image_id=i.id AND p.tag=t.tag
              )
          )
        ORDER BY i.id
    """
    if limit:
        sql += " LIMIT ?"
        return con.execute(sql, (limit,)).fetchall()
    return con.execute(sql).fetchall()
