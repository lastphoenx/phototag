"""Tests for Nextcloud tag push (mock HTTP)."""
from __future__ import annotations

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from phototag.cli import connect
from phototag.nextcloud_sync import (
    NcConfig,
    assign_tag_to_file,
    ensure_system_tag,
    file_id,
    push_image_tags,
)


@pytest.fixture
def nc_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("PHOTOTAG_DB", str(db))
    monkeypatch.setenv("PHOTOTAG_NC_URL", "http://127.0.0.1:0")  # patched per test
    monkeypatch.setenv("PHOTOTAG_NC_USER", "u")
    monkeypatch.setenv("PHOTOTAG_NC_APP_PASSWORD", "secret")
    monkeypatch.setenv("PHOTOTAG_NC_DAV_PREFIX", "Photos")
    return db


def test_push_nc_mock_server(nc_env: Path, monkeypatch: pytest.MonkeyPatch):
    assigned: list[tuple[int, int]] = []
    port_holder: list[int] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_PROPFIND(self) -> None:
            self.send_response(207)
            self.send_header("Content-Type", "application/xml")
            self.end_headers()
            self.wfile.write(
                b'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" '
                b'xmlns:oc="http://owncloud.org/ns">'
                b"<d:response><d:propstat><d:prop><oc:fileid>42</oc:fileid></d:prop></d:propstat></d:response>"
                b"</d:multistatus>"
            )

        def do_GET(self) -> None:
            if "systemtags" in self.path:
                body = json.dumps({"ocs": {"meta": {"status": "ok"}, "data": []}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode()
            name = "outdoor"
            if raw.strip().startswith("{"):
                name = json.loads(raw).get("name", name)
            else:
                qs = parse_qs(raw)
                name = qs.get("name", [name])[0]
            body = json.dumps({"ocs": {"meta": {"status": "ok"}, "data": {"id": 7, "name": name}}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_PUT(self) -> None:
            path = urlparse(self.path).path
            parts = path.rstrip("/").split("/")
            assigned.append((int(parts[-2]), int(parts[-1])))
            self.send_response(201)
            self.end_headers()

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port_holder.append(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{port_holder[0]}"
    monkeypatch.setenv("PHOTOTAG_NC_URL", base)
    cfg = NcConfig(base_url=base, user="u", app_token="secret", dav_prefix="Photos")

    assert file_id(cfg, "a/b.jpg") == 42
    cache: dict[str, int] = {}
    tid = ensure_system_tag(cfg, cache, "outdoor")
    assert tid == 7
    assign_tag_to_file(cfg, 42, tid)
    assert assigned == [(42, 7)]

    con = connect()
    con.execute(
        "INSERT INTO images(root,path,folder,kind,size,mtime,sha1,status) VALUES(?,?,?,?,?,?,?,?)",
        ("/mnt", "a/b.jpg", "a", "image", 1, 1.0, "x", "tagged"),
    )
    iid = con.execute("SELECT id FROM images").fetchone()[0]
    con.execute("INSERT INTO tags(image_id, tag) VALUES(?,?)", (iid, "outdoor"))
    con.commit()
    n = push_image_tags(con, cfg, image_id=iid, path="a/b.jpg", tags=["outdoor"], tag_cache=cache)
    assert n == 1
    server.shutdown()
