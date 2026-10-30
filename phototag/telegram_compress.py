"""Große Videos: Staging auf /tmp, ffmpeg, dann Telegram (Bot-Limit ~50 MB)."""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from phototag.cli import VIDEO_EXTS, probe_video
from phototag.config import (
    telegram_compress_audio,
    telegram_compress_enabled,
    telegram_compress_target_bytes,
    telegram_compress_timeout_sec,
    telegram_max_send_bytes,
    telegram_staging_max_bytes,
    telegram_temp_dir,
)
from phototag.telegram_forward import TELEGRAM_BOT_FILE_MAX_BYTES

_LOG = logging.getLogger(__name__)
_TEMP_PREFIX = "phototag-tg-"


def is_phototag_temp(path: Path) -> bool:
    return path.name.startswith(_TEMP_PREFIX)


def sweep_stale_temps(*, max_age_sec: int = 7200) -> None:
    """Alte Leichen aus abgebrochenen Jobs (z. B. Worker-Kill nach Telegram-OK)."""
    td = telegram_temp_dir()
    if not td.is_dir():
        return
    now = time.time()
    for p in td.glob(f"{_TEMP_PREFIX}*"):
        if not p.is_file():
            continue
        try:
            if now - p.stat().st_mtime > max_age_sec:
                p.unlink(missing_ok=True)
        except OSError as exc:
            _LOG.warning("phototag telegram temp sweep: %s (%s)", p, exc)


def _stage_from_mount(source: Path) -> Path:
    sweep_stale_temps()
    size = source.stat().st_size
    cap = telegram_staging_max_bytes()
    if size > cap:
        mb = size / (1024 * 1024)
        cap_mb = cap / (1024 * 1024)
        raise RuntimeError(
            f"Video zu gross zum Staging ({mb:.1f} MB, PHOTOTAG_TELEGRAM_STAGING_MAX_MB={cap_mb:.0f})"
        )
    temp_dir = telegram_temp_dir()
    temp_dir.mkdir(parents=True, exist_ok=True)
    staged = temp_dir / f"phototag-tg-in-{uuid.uuid4().hex}{source.suffix.lower()}"
    try:
        shutil.copyfile(source, staged)
    except OSError as exc:
        raise RuntimeError(f"Staging vom Mount fehlgeschlagen: {exc}") from exc
    return staged


def _compress_video(staged: Path) -> Path:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("ffmpeg/ffprobe fehlt (apt install ffmpeg)")
    duration, width, height = probe_video(staged)
    target = telegram_compress_target_bytes()
    # ~85% of target for video bits; rest audio + container
    video_bitrate_k = max(
        400,
        int((target * 8 * 0.85) / max(duration, 1.0) / 1000),
    )
    out = staged.parent / f"phototag-tg-out-{uuid.uuid4().hex}.mp4"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(staged),
    ]
    if width > 1280 or height > 1280:
        cmd.extend(["-vf", "scale='min(1280,iw)':-2"])
    cmd.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-b:v",
            f"{video_bitrate_k}k",
            "-maxrate",
            f"{int(video_bitrate_k * 1.15)}k",
            "-bufsize",
            f"{int(video_bitrate_k * 2)}k",
            "-movflags",
            "+faststart",
        ]
    )
    if telegram_compress_audio():
        cmd.extend(["-c:a", "aac", "-b:a", "128k"])
    else:
        cmd.append("-an")
    cmd.append(str(out))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        timeout=telegram_compress_timeout_sec(),
        check=False,
    )
    if proc.returncode != 0 or not out.is_file():
        err = (proc.stderr or b"")[:400].decode(errors="replace")
        raise RuntimeError(f"ffmpeg Kompression fehlgeschlagen: {err or proc.returncode}")
    out_size = out.stat().st_size
    if out_size > TELEGRAM_BOT_FILE_MAX_BYTES:
        mb = out_size / (1024 * 1024)
        out.unlink(missing_ok=True)
        raise RuntimeError(
            f"Komprimiertes Video immer noch zu gross ({mb:.1f} MB). "
            "Kuerzeres Clip oder staerkere Kompression noetig."
        )
    return out


def prepare_media_for_telegram(source: Path) -> tuple[Path, list[Path], dict]:
    """
    Returns (path_to_send, temps_to_delete_after_send, meta).
    path_to_send may be source or a temp mp4.
    """
    if not source.is_file():
        raise FileNotFoundError(str(source))
    size = source.stat().st_size
    ext = source.suffix.lower()
    max_send = telegram_max_send_bytes()

    if size <= max_send:
        return source, [], {"compressed": False, "bytes": size}

    if ext not in VIDEO_EXTS:
        mb = size / (1024 * 1024)
        raise RuntimeError(
            f"Datei zu gross fuer Telegram-Bot ({mb:.1f} MB, Limit 50 MB): {source.name}"
        )

    if not telegram_compress_enabled():
        mb = size / (1024 * 1024)
        raise RuntimeError(
            f"Video zu gross ({mb:.1f} MB). PHOTOTAG_TELEGRAM_COMPRESS=1 in phototag.env setzen."
        )

    staged = _stage_from_mount(source)
    temps: list[Path] = [staged]
    try:
        compressed = _compress_video(staged)
        temps.append(compressed)
    except Exception:
        for p in temps:
            p.unlink(missing_ok=True)
        raise
    staged.unlink(missing_ok=True)
    temps.remove(staged)
    meta = {
        "compressed": True,
        "source_bytes": size,
        "bytes": compressed.stat().st_size,
    }
    return compressed, temps, meta


def cleanup_temps(paths: list[Path]) -> None:
    seen: set[Path] = set()
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        try:
            if p.is_file():
                p.unlink(missing_ok=True)
        except OSError as exc:
            _LOG.warning("phototag telegram temp cleanup failed: %s (%s)", p, exc)


def cleanup_phototag_temp(path: Path) -> None:
    if is_phototag_temp(path):
        cleanup_temps([path])
