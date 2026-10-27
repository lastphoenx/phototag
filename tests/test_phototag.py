"""Integration tests with mock vision API and local fixtures."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from PIL import Image

from phototag.cli import connect, frame_times, load_categories, split_words


@pytest.fixture
def categories_file(tmp_path: Path) -> Path:
    path = tmp_path / "categories.txt"
    path.write_text(
        "outdoor: draussen\nindoor: drinnen\nportrait: person\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def env_db(tmp_path: Path, categories_file: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "test.db"
    monkeypatch.setenv("PHOTOTAG_DB", str(db))
    monkeypatch.setenv("PHOTOTAG_CATEGORIES", str(categories_file))
    monkeypatch.setenv("PHOTOTAG_API", "http://127.0.0.1:0/v1")
    return db


def test_split_words_folder_tokens():
    assert "beach" in split_words("album/beach_2024")
    assert "2024" in split_words("album/beach_2024")


def test_load_categories(categories_file: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PHOTOTAG_CATEGORIES", str(categories_file))
    cats = load_categories()
    assert ("outdoor", "draussen") in cats
    assert len(cats) == 3


def test_frame_times_even_spread():
    times = frame_times(60.0, 6)
    assert len(times) == 6
    assert times[0] < times[-1]
    assert all(0 < t < 60 for t in times)


def test_scan_tag_search_mock_api(tmp_path: Path, env_db: Path, monkeypatch: pytest.MonkeyPatch):
    media = tmp_path / "media"
    sub = media / "album"
    sub.mkdir(parents=True)
    img = sub / "beach.jpg"
    Image.new("RGB", (64, 48), color=(10, 120, 200)).save(img, "JPEG")

    port_holder: list[int] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            body = json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "age_status": "yes",
                                        "tags": ["outdoor", "portrait"],
                                        "caption": "Test caption.",
                                    }
                                )
                            }
                        }
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port_holder.append(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    api = f"http://127.0.0.1:{port_holder[0]}/v1"
    monkeypatch.setenv("PHOTOTAG_API", api)
    monkeypatch.setenv("PHOTOTAG_MODEL", "mock")

    from phototag.cli import cmd_scan, cmd_search, cmd_tag

    class Args:
        root = str(media)

    cmd_scan(Args())

    class TagArgs:
        limit = 10
        retag = False
        kind = "image"

    cmd_tag(TagArgs())

    class SearchArgs:
        query = "beac"
        any = False
        fuzzy = False
        tag = None
        folder = ""
        kind = None
        limit = 20
        nextcloud = ""
        export_thumbs = ""

    cmd_search(SearchArgs())

    con = connect()
    row = con.execute(
        "SELECT status, folder FROM images WHERE path=?", ("album/beach.jpg",)
    ).fetchone()
    assert row["status"] == "tagged"
    assert row["folder"] == "album"
    tags = {r["tag"] for r in con.execute("SELECT tag FROM tags").fetchall()}
    assert tags == {"outdoor", "portrait"}
    server.shutdown()


def test_review_when_age_status_unclear(tmp_path: Path, env_db: Path, monkeypatch: pytest.MonkeyPatch):
    media = tmp_path / "m"
    media.mkdir()
    img = media / "x.jpg"
    Image.new("RGB", (32, 32), color=(1, 2, 3)).save(img, "JPEG")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            payload = json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"age_status":"unsure","tags":["indoor"],"caption":"?"}'
                            }
                        }
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("PHOTOTAG_API", f"http://127.0.0.1:{server.server_address[1]}/v1")

    from phototag.cli import cmd_scan, cmd_tag

    class ScanArgs:
        root = str(media)

    cmd_scan(ScanArgs())

    class TagArgs:
        limit = 5
        retag = False
        kind = None

    cmd_tag(TagArgs())
    con = connect()
    row = con.execute("SELECT status FROM images").fetchone()
    assert row["status"] == "review"
    assert con.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0
    server.shutdown()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_video_tag_mock_api(tmp_path: Path, env_db: Path, monkeypatch: pytest.MonkeyPatch):
    media = tmp_path / "v"
    media.mkdir()
    video = media / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x120:d=2",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        timeout=60,
    )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            payload = json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "age_status": "yes",
                                        "tags": ["outdoor"],
                                        "caption": "Short clip.",
                                    }
                                )
                            }
                        }
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("PHOTOTAG_API", f"http://127.0.0.1:{server.server_address[1]}/v1")
    monkeypatch.setenv("PHOTOTAG_VIDEO_FRAMES", "5")

    from phototag.cli import cmd_scan, cmd_tag

    class ScanArgs:
        root = str(media)

    cmd_scan(ScanArgs())

    class TagArgs:
        limit = 1
        retag = False
        kind = "video"

    cmd_tag(TagArgs())
    con = connect()
    row = con.execute("SELECT status, frames, kind FROM images").fetchone()
    assert row["kind"] == "video"
    assert row["status"] == "tagged"
    assert row["frames"] == 5
    server.shutdown()
