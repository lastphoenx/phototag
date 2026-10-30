"""Optional: Medien aus dem Mount an Telegram senden (Bot API, nur Server-Env)."""
from __future__ import annotations

import json
import mimetypes
import uuid
from pathlib import Path
import urllib.error
import urllib.request

from phototag.cli import IMAGE_EXTS, VIDEO_EXTS
from phototag.config import telegram_bot_token, telegram_chat_id, telegram_upload_timeout_sec

# Bot API: sendDocument/sendPhoto practical limit (bytes)
TELEGRAM_BOT_FILE_MAX_BYTES = 50 * 1024 * 1024


def telegram_configured() -> bool:
    return bool(telegram_bot_token() and telegram_chat_id())


def _multipart_body(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----phototag{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    chunks.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode()
    )
    chunks.append(file_path.read_bytes())
    chunks.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def send_media_to_telegram(path: Path, *, caption: str = "") -> dict:
    """sendPhoto (Bild) oder sendDocument (Video/gross) an PHOTOTAG_TELEGRAM_CHAT_ID."""
    token = telegram_bot_token()
    chat_id = telegram_chat_id()
    if not token or not chat_id:
        raise RuntimeError(
            "Telegram nicht konfiguriert: PHOTOTAG_TELEGRAM_BOT_TOKEN und "
            "PHOTOTAG_TELEGRAM_CHAT_ID in phototag.env (nur Server)."
        )
    if not path.is_file():
        raise FileNotFoundError(str(path))
    size = path.stat().st_size
    if size > TELEGRAM_BOT_FILE_MAX_BYTES:
        mb = size / (1024 * 1024)
        raise RuntimeError(
            f"Datei zu gross fuer Telegram-Bot ({mb:.1f} MB, Limit 50 MB): {path.name}"
        )
    ext = path.suffix.lower()
    cap = (caption or "")[:1024]
    if ext in IMAGE_EXTS:
        method = "sendPhoto"
        file_key = "photo"
    elif ext in VIDEO_EXTS:
        method = "sendDocument"
        file_key = "document"
    else:
        method = "sendDocument"
        file_key = "document"
    fields = {"chat_id": chat_id}
    if cap:
        fields["caption"] = cap
    body, content_type = _multipart_body(fields, file_key, path)
    url = f"https://api.telegram.org/bot{token}/{method}"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=telegram_upload_timeout_sec()) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise RuntimeError(f"Telegram API HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Telegram API: {exc.reason}") from exc
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API: {payload.get('description', payload)!s}")
    result = payload.get("result") or {}
    return {
        "message_id": result.get("message_id"),
        "chat_id": result.get("chat", {}).get("id"),
    }
