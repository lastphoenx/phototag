from __future__ import annotations

from phototag.config import api_url, db_path, video_frames


def test_env_read_at_runtime(monkeypatch):
    monkeypatch.setenv("PHOTOTAG_DB", "/tmp/a.db")
    assert db_path() == "/tmp/a.db"
    monkeypatch.setenv("PHOTOTAG_DB", "/tmp/b.db")
    assert db_path() == "/tmp/b.db"

    monkeypatch.setenv("PHOTOTAG_API", "http://127.0.0.1:9999/v1")
    assert api_url() == "http://127.0.0.1:9999/v1"

    monkeypatch.setenv("PHOTOTAG_VIDEO_FRAMES", "5")
    assert video_frames() == 5
    monkeypatch.setenv("PHOTOTAG_VIDEO_FRAMES", "99")
    assert video_frames() == 8
