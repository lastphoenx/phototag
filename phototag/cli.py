#!/usr/bin/env python3
"""Lokale Foto- UND Video-Verschlagwortung mit einem Vision-Modell + SQLite.

Medien bleiben lokal; Pixel gehen nur an deine konfigurierte Vision-API (/v1).

  python phototag.py scan   /pfad/zu/medien          # Bilder + Videos erfassen
  python phototag.py tag    [--limit 50] [--retag] [--kind image|video]
  python phototag.py search "strand pool"            # elastisch: Präfix, AND
  python phototag.py search "strand pool" --any      # irgendeiner (OR)
  python phototag.py search "bikni" --fuzzy          # Teilstring (Trigram)
  python phototag.py search "x" --tag outdoor --tag portrait       # exakte Tags (AND)
  python phototag.py search "" --folder Urlaub                     # nur Ordnerfilter
  python phototag.py reindex                          # Suchindex neu aufbauen
  python phototag.py stats | review | thumb <pfad> <out.jpg>
  python phototag.py push-nc [--limit N] [--dry-run] [--force]

Konfiguration per Umgebungsvariablen:
  PHOTOTAG_DB            (default phototag.db)
  PHOTOTAG_API           (default http://127.0.0.1:8080/v1)
  PHOTOTAG_MODEL         (default qwen3.8-27b)
  PHOTOTAG_CATEGORIES    (default categories.txt neben dem Skript)
  PHOTOTAG_MAX_SIDE      (default 1024)  Bildgrösse zum Modell
  PHOTOTAG_VIDEO_FRAMES  (default 6, erlaubt 5-8) Frames pro Video
  PHOTOTAG_VIDEO_SIDE    (default 768)   Kantenlänge pro Video-Frame
  PHOTOTAG_MAX_TAGS      (default 12)    max. Tags pro Datei
  PHOTOTAG_CAPTION_MAX   (default 320)   max. Caption-Zeichen (Modell + DB)
  PHOTOTAG_PROMPT_LANG   (de|en, default de)  Anweisungssprache an das Modell
  PHOTOTAG_VERBOSE       (1/true) oder phototag tag -v  zeigt caption, observations, raw JSON
  PHOTOTAG_THUMB_SIDE / PHOTOTAG_THUMB_QUALITY (default 320 / 70)
Videos brauchen ffmpeg + ffprobe.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

from phototag.config import (
    api_url,
    categories_path,
    cli_verbose,
    db_path,
    caption_max,
    max_side,
    max_tags,
    max_completion_tokens,
    model_name,
    prompt_lang,
    reasoning_effort,
    tag_sanitize_enabled,
    thumb_quality,
    thumb_side,
    video_frames,
    video_side,
)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
SKIP_DIRS = {".trashbin", "thumbnails", "cache", "@eaDir", ".git"}


SCHEMA = """
CREATE TABLE IF NOT EXISTS images(
  id INTEGER PRIMARY KEY,
  root TEXT NOT NULL,
  path TEXT NOT NULL,                 -- relativ zu root (= Pfad in Nextcloud)
  folder TEXT NOT NULL DEFAULT '',    -- Ordner relativ zu root, z. B. 'Urlaub/Strand'
  kind TEXT NOT NULL DEFAULT 'image', -- image | video
  size INTEGER, mtime REAL, sha1 TEXT,
  width INTEGER, height INTEGER, duration REAL, frames INTEGER,
  status TEXT NOT NULL DEFAULT 'new', -- new | tagged | review | error
  age_status TEXT,                    -- yes | no | unsure (Personenalters-Check)
  caption TEXT,
  thumb BLOB,
  model TEXT, tagged_at REAL, error TEXT,
  UNIQUE(root, path)
);
CREATE TABLE IF NOT EXISTS tags(
  image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
  tag TEXT NOT NULL,
  PRIMARY KEY(image_id, tag)
);
CREATE INDEX IF NOT EXISTS tags_tag ON tags(tag);
CREATE INDEX IF NOT EXISTS images_status ON images(status);
CREATE INDEX IF NOT EXISTS images_folder_status ON images(folder, status);
"""


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(db_path(), timeout=120.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=120000")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    con.execute("CREATE INDEX IF NOT EXISTS images_folder ON images(folder)")
    cols = {r[1] for r in con.execute("PRAGMA table_info(images)")}
    if "model_raw" not in cols:
        con.execute("ALTER TABLE images ADD COLUMN model_raw TEXT")
    if "derived_from" not in cols:  # blur copy -> id of the untouched original
        con.execute("ALTER TABLE images ADD COLUMN derived_from INTEGER")
    if "blur_faces" not in cols:
        con.execute("ALTER TABLE images ADD COLUMN blur_faces INTEGER")
    fts_cols = "path, folder, caption, tags"
    con.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5({fts_cols}, "
        "tokenize='unicode61 remove_diacritics 2')"
    )
    try:
        con.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS search_tri USING fts5({fts_cols}, tokenize='trigram')"
        )
    except sqlite3.OperationalError:
        pass  # SQLite ohne trigram: --fuzzy eingeschränkt
    return con


def has_trigram(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE name='search_tri'").fetchone() is not None


def split_words(text: str) -> str:
    """'Urlaub/Strand_2024' -> 'Urlaub/Strand_2024 Urlaub Strand 2024' (ganze + zerlegte Form)."""
    parts = [p for p in re.split(r"[\\/_\-\.\s]+", text) if p]
    return " ".join([text, *parts]) if text else ""


def index_image(con: sqlite3.Connection, image_id: int) -> None:
    con.execute("DELETE FROM search WHERE rowid=?", (image_id,))
    if has_trigram(con):
        con.execute("DELETE FROM search_tri WHERE rowid=?", (image_id,))
    row = con.execute("SELECT path, folder, caption, status FROM images WHERE id=?", (image_id,)).fetchone()
    if not row or row["status"] != "tagged":
        return
    tags = " ".join(r["tag"] for r in con.execute("SELECT tag FROM tags WHERE image_id=? ORDER BY tag", (image_id,)))
    vals = (image_id, split_words(row["path"]), split_words(row["folder"]), row["caption"] or "", tags)
    con.execute("INSERT INTO search(rowid,path,folder,caption,tags) VALUES(?,?,?,?,?)", vals)
    if has_trigram(con):
        con.execute("INSERT INTO search_tri(rowid,path,folder,caption,tags) VALUES(?,?,?,?,?)", vals)


def reindex(con: sqlite3.Connection) -> None:
    con.execute("DELETE FROM search")
    if has_trigram(con):
        con.execute("DELETE FROM search_tri")
    for (i,) in con.execute("SELECT id FROM images WHERE status='tagged'").fetchall():
        index_image(con, i)
    con.commit()


# ---------------------------------------------------------------- Kategorien

def load_categories() -> list[tuple[str, str]]:
    path = categories_path()
    if not path.is_file():
        sys.exit(f"Kategorien-Datei fehlt: {path}")
    out: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tag, _, hint = line.partition(":")
        tag = re.sub(r"\s+", "_", tag.strip().lower())
        if tag and tag not in {t for t, _ in out}:
            out.append((tag, hint.strip()))
    if not out:
        sys.exit("Keine Kategorien definiert.")
    return out


# ---------------------------------------------------------------- scan

def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_scan(args) -> None:
    root = Path(args.root).resolve()
    if not root.is_dir():
        sys.exit(f"Kein Ordner: {root}")

    progress_file = os.environ.get("PHOTOTAG_SCAN_PROGRESS", "").strip()
    if not progress_file:
        progress_file = str(Path(db_path()).parent / "scan-progress.log")
    progress_first = True

    def scan_log(msg: str) -> None:
        nonlocal progress_first
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
        print(line, file=sys.stderr, flush=True)
        try:
            mode = "w" if progress_first else "a"
            progress_first = False
            with open(progress_file, mode, encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    scan_log(f"scan: start {root} → log: {progress_file}")
    con = connect()
    new = changed = seen = 0
    hash_ops = 0
    pending_commit = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            p = Path(dirpath) / name
            ext = p.suffix.lower()
            if ext not in IMAGE_EXTS and ext not in VIDEO_EXTS:
                continue
            kind = "video" if ext in VIDEO_EXTS else "image"
            seen += 1
            if seen == 1 or seen % 200 == 0:
                scan_log(f"scan: {seen} Mediendateien … (neu={new}, geändert={changed})")
            rel = p.relative_to(root).as_posix()
            folder = Path(rel).parent.as_posix()
            folder = "" if folder == "." else folder
            st = p.stat()
            row = con.execute("SELECT id,size,mtime,folder,kind FROM images WHERE root=? AND path=?", (str(root), rel)).fetchone()
            if row and row["size"] == st.st_size and abs(row["mtime"] - st.st_mtime) < 1:
                if row["folder"] != folder or row["kind"] != kind:
                    con.execute("UPDATE images SET folder=?,kind=? WHERE id=?", (folder, kind, row["id"]))
                    index_image(con, row["id"])
                    pending_commit += 1
                continue
            digest = sha1_of(p)
            hash_ops += 1
            if hash_ops == 1 or hash_ops % 25 == 0:
                scan_log(f"scan: hash {hash_ops} … {rel}")
            if row:
                con.execute(
                    "UPDATE images SET size=?,mtime=?,sha1=?,folder=?,kind=?,"
                    "status=CASE WHEN status IN ('blurred_review','blurred_ok') THEN status ELSE 'new' END,"
                    "error=CASE WHEN status IN ('blurred_review','blurred_ok') THEN error ELSE NULL END WHERE id=?",
                    (st.st_size, st.st_mtime, digest, folder, kind, row["id"]),
                )
                changed += 1
            else:
                con.execute(
                    "INSERT INTO images(root,path,folder,kind,size,mtime,sha1) VALUES(?,?,?,?,?,?,?)",
                    (str(root), rel, folder, kind, st.st_size, st.st_mtime, digest),
                )
                new += 1
            pending_commit += 1
            if pending_commit >= 150:
                con.commit()
                pending_commit = 0
    con.commit()
    scan_log(f"scan: fertig — {seen} gesehen, {new} neu, {changed} geändert")
    print(f"{seen} Dateien gesehen, {new} neu, {changed} geändert.")


# ---------------------------------------------------------------- Medien -> Modell

def _jpeg(im: Image.Image, side: int, quality: int) -> bytes:
    im = im.copy()
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=quality < 85)
    return buf.getvalue()


def encode_image(path: Path) -> dict:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")  # EXIF-Drehung; GPS wird nie mitgesendet
        w, h = im.size
        return {
            "frames": [_jpeg(im, max_side(), 88)], "width": w, "height": h,
            "duration": None, "thumb": _jpeg(im, thumb_side(), thumb_quality()),
        }


def probe_video(path: Path) -> tuple[float, int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if out.returncode != 0:
        raise ValueError(f"ffprobe fehlgeschlagen: {out.stderr[:200].strip()}")
    data = json.loads(out.stdout or "{}")
    duration = float((data.get("format") or {}).get("duration") or 0)
    stream = (data.get("streams") or [{}])[0]
    if duration <= 0:
        raise ValueError("Videodauer unbekannt")
    return duration, int(stream.get("width") or 0), int(stream.get("height") or 0)


def frame_times(duration: float, count: int) -> list[float]:
    """Gleichmässig verteilt, nie genau am Anfang/Ende."""
    step = duration / (count + 1)
    return [step * (i + 1) for i in range(count)]


def extract_frame(path: Path, at: float) -> bytes:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.3f}", "-i", str(path),
         "-an", "-sn", "-dn", "-frames:v", "1",
         "-vf", f"scale='min({video_side()},iw)':'min({video_side()},ih)':force_original_aspect_ratio=decrease",
         "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4", "-"],
        capture_output=True, timeout=120, check=False,
    )
    if proc.returncode != 0 or not proc.stdout.startswith(b"\xff\xd8"):
        raise ValueError(f"Frame bei {at:.1f}s nicht lesbar: {(proc.stderr or b'')[:150].decode(errors='replace')}")
    return proc.stdout


def encode_video(path: Path) -> dict:
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        raise ValueError("ffmpeg/ffprobe nicht installiert")
    duration, w, h = probe_video(path)
    n_frames = video_frames()
    frames = [extract_frame(path, t) for t in frame_times(duration, n_frames)]
    with Image.open(io.BytesIO(frames[len(frames) // 2])) as mid:
        thumb = _jpeg(mid.convert("RGB"), thumb_side(), thumb_quality())
    return {"frames": frames, "width": w, "height": h, "duration": duration, "thumb": thumb}


def build_prompt(categories: list[tuple[str, str]], is_video: bool) -> str:
    from phototag.tagging import vision_prompt_anonymization_block

    lines = "\n".join(f"- {t}: {h}" if h else f"- {t}" for t, h in categories)
    lang = prompt_lang()
    allowed_ids = {t for t, _ in categories}
    anon_block = vision_prompt_anonymization_block(lang, allowed_ids)
    anon_suffix = f"\n{anon_block}" if anon_block else ""
    if lang == "en":
        where = (
            "The images are frames of ONE video in chronological order. Apply each tag that "
            "matches at least one frame."
            if is_video else "You see a single photo."
        )
        return (
            "You analyze images for a personal photo collection. " + where + "\n"
            "Reply ONLY with JSON, no text before or after:\n"
            '{"age_status": "yes|no|unsure", "tags": ["..."], "observations": ["..."], "caption": "..."}\n'
            "- age_status: Are all visible people clearly adults? If unsure use \"unsure\" or \"no\".\n"
            f"- tags: apply ALL matching tags (multiple encouraged, at most {max_tags()}) and ONLY from this list:\n"
            f"{lines}\n"
            "- Follow the short hint after each tag in the list (lines \"tag: …\").\n"
            "- observations: optional short phrases for details without a matching tag, max 8 items.\n"
            f"- caption: one factual sentence in German (max {caption_max()} characters).\n"
            "Do not invent tag ids outside the list; put extras in observations."
            + anon_suffix
        )
    where = (
        "Die Bilder sind Frames EINES Videos in zeitlicher Reihenfolge. Vergib jeden Tag, der in "
        "mindestens einem Frame zutrifft."
        if is_video else "Du siehst ein einzelnes Foto."
    )
    return (
        "Du analysierst Bilder für ein persönliches Fotoarchiv. " + where + "\n"
        "Antworte AUSSCHLIESSLICH mit JSON, ohne Text davor oder danach:\n"
        '{"age_status": "yes|no|unsure", "tags": ["..."], "observations": ["..."], "caption": "..."}\n'
        "- age_status: Sind alle sichtbaren Personen eindeutig volljährig? Bei Zweifel \"unsure\" oder \"no\".\n"
        f"- tags: vergib ALLE zutreffenden Tags (mehrere sind erwünscht, höchstens {max_tags()}) und NUR aus dieser Liste:\n"
        f"{lines}\n"
        "- Halte dich an die Kurz-Hinweise hinter jedem Tag in der Liste (Zeilen «tag: …»).\n"
        "- observations: optional kurze Beobachtungen zu Details ohne passenden Tag, max. 8 Einträge.\n"
        f"- caption: ein sachlicher Satz auf Deutsch (max. {caption_max()} Zeichen).\n"
        "Erfinde keine Tag-IDs ausserhalb der Liste; Extras gehören in observations."
        + anon_suffix
    )


def _message_text(message: dict) -> str:
    """OpenAI-compatible message; some backends use list content or empty content."""
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                chunks.append(str(part.get("text") or ""))
        joined = "".join(chunks).strip()
        if joined:
            return joined
    for key in ("reasoning_content", "reasoning"):
        val = message.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def _salvage_partial_json(blob: str) -> dict | None:
    """Aus abgeschnittener Modell-Antwort age_status + tags retten."""
    age_m = re.search(r'"age_status"\s*:\s*"(yes|no|unsure)"', blob, re.I)
    if not age_m:
        return None
    tags: list[str] = []
    if '"tags"' in blob:
        tail = blob.split('"tags"', 1)[1]
        tags = re.findall(r'"([a-z][a-z0-9_]*)"', tail)
        tags = [t for t in tags if t not in ("yes", "no", "unsure")]
    cap_m = re.search(r'"caption"\s*:\s*"([^"]*)', blob)
    out: dict = {"age_status": age_m.group(1).lower(), "tags": tags}
    if cap_m:
        out["caption"] = cap_m.group(1)[: caption_max()]
    return out if tags or cap_m else None


def parse_reply(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"```json\s*", "", text, flags=re.I)
    text = re.sub(r"```", "", text)
    m = re.search(r"\{.*", text, flags=re.S)
    if not m:
        raise ValueError(f"keine JSON-Antwort: {text[:120]!r}")
    blob = m.group(0).strip()
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        for suffix in ('"}', '"]}', ']}', '}'):
            try:
                return json.loads(blob + suffix)
            except json.JSONDecodeError:
                continue
        salvaged = _salvage_partial_json(blob)
        if salvaged:
            return salvaged
        raise ValueError(f"keine JSON-Antwort: {text[:120]!r}") from None


def ask_model(frames: list[bytes], categories: list[tuple[str, str]], is_video: bool) -> dict:
    content = [{"type": "text", "text": build_prompt(categories, is_video)}]
    for raw in frames:
        b64 = base64.b64encode(raw).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    payload = {
        "model": model_name(),
        "temperature": 0.1,
        "max_tokens": max_completion_tokens(),
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{"role": "user", "content": content}],
    }
    effort = reasoning_effort()
    if effort:
        payload["reasoning_effort"] = effort
    body = json.dumps(payload).encode()
    url = f"{api_url()}/chat/completions"
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.load(resp)
            choice = data["choices"][0]
            text = _message_text(choice.get("message") or {})
            if not text.strip():
                finish = choice.get("finish_reason")
                raise ValueError(f"leere Modell-Antwort (finish_reason={finish!r})")
            return parse_reply(text)
        except (urllib.error.URLError, urllib.error.HTTPError):
            raise
        except (ValueError, json.JSONDecodeError, KeyError, IndexError) as exc:
            last_err = exc
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
    assert last_err is not None
    raise last_err


def _apply_model_output(out: dict, allowed: set[str]) -> tuple[list[str], list[str], str, str]:
    raw_tag_list = [str(t).strip().lower() for t in out.get("tags", []) if str(t).strip()]
    tags = sorted({t for t in raw_tag_list if t in allowed})[: max_tags()]
    dropped = sorted({t for t in raw_tag_list if t not in allowed})
    caption = str(out.get("caption", ""))[: caption_max()]
    model_raw = json.dumps(out, ensure_ascii=False)[:16000]
    return tags, dropped, caption, model_raw


def _load_sanitize_rules() -> dict:
    path = os.getenv("PHOTOTAG_SANITIZE_RULES", "").strip()
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _norm_rule_tag(tag: str) -> str:
    return str(tag).strip().lower()


def _apply_sanitize_rules(
    t: set[str], allowed: set[str], rules: dict, original_tags: list[str]
) -> list[str]:
    flags: list[str] = []
    orig = {_norm_rule_tag(x) for x in original_tags}

    for rule in rules.get("discard_if", []):
        if not isinstance(rule, dict):
            continue
        when = _norm_rule_tag(rule.get("when", ""))
        if when and when in t:
            for r in rule.get("remove", []):
                t.discard(_norm_rule_tag(r))

    for rule in rules.get("drop_on_pair", []):
        if not isinstance(rule, dict):
            continue
        parts = [_norm_rule_tag(x) for x in rule.get("if_all", [])]
        if len(parts) < 2 or not all(p in t for p in parts):
            continue
        drop = _norm_rule_tag(rule.get("drop", parts[0]))
        t.discard(drop)
        flag = rule.get("flag")
        if flag:
            flags.append(str(flag))
        add_spec = rule.get("add_if_trigger_in")
        if isinstance(add_spec, dict):
            add_tag = _norm_rule_tag(add_spec.get("add", ""))
            triggers = {_norm_rule_tag(x) for x in add_spec.get("any_of", [])}
            if add_tag in allowed and (t & triggers or orig & triggers):
                t.add(add_tag)

    for rule in rules.get("drop_on_pair_unless", []):
        if not isinstance(rule, dict):
            continue
        parts = [_norm_rule_tag(x) for x in rule.get("if_all", [])]
        unless = _norm_rule_tag(rule.get("unless", ""))
        if len(parts) < 2 or not all(p in t for p in parts) or unless in t:
            continue
        drop = _norm_rule_tag(rule.get("drop", parts[0]))
        t.discard(drop)
        flag = rule.get("flag")
        if flag:
            flags.append(str(flag))

    for rule in rules.get("require_any_when", []):
        if not isinstance(rule, dict):
            continue
        when = _norm_rule_tag(rule.get("when", ""))
        if when not in allowed or when not in t:
            continue
        any_of = {_norm_rule_tag(x) for x in rule.get("any_of", [])} & allowed
        if any_of and not (t & any_of):
            flags.append(str(rule.get("flag", "require_any")))

    return flags


def sanitize_tags(tags: list[str], allowed: set[str]) -> tuple[list[str], list[str]]:
    """Widersprüche bereinigen; flags lösen review aus (auch bei age_status=yes)."""
    if not tag_sanitize_enabled():
        return tags, []
    t = set(tags) & allowed
    flags: list[str] = []

    flags.extend(_apply_sanitize_rules(t, allowed, _load_sanitize_rules(), tags))

    from phototag.tagging import strip_invalid_detail_under_censor

    flags.extend(strip_invalid_detail_under_censor(t))

    if "outdoor" in t and "indoor" in t:
        flags.append("outdoor_and_indoor")
        if any(x.startswith("location_") for x in t):
            t.discard("outdoor")
            t.discard("indoor")

    cleaned = sorted(t)[: max_tags()]
    return cleaned, flags


def prepare_stored_tags(tags: list[str], allowed: set[str]) -> tuple[list[str], list[str]]:
    """Tags für DB/NC: categories-Filter + sanitize (wie KI-Lauf)."""
    from phototag.review_queue import normalize_chosen_tags

    base = normalize_chosen_tags(tags, allowed)
    if not base:
        raise ValueError("Mindestens ein gültiger Tag (categories.txt)")
    return sanitize_tags(base, allowed)


def sanitize_review_note(flags: list[str]) -> str | None:
    if not flags:
        return None
    return "sanitize:" + ",".join(flags)


def _print_tag_extras(out: dict, caption: str, dropped: list[str], verbose: bool, sanitize_flags: list[str] | None = None) -> None:
    if not verbose:
        return
    obs = out.get("observations") or []
    if isinstance(obs, str):
        obs = [obs]
    elif not isinstance(obs, list):
        obs = []
    obs = [str(o).strip() for o in obs if str(o).strip()][:8]
    if caption:
        print(f"         caption: {caption}")
    if obs:
        print(f"         observations: {'; '.join(obs)}")
    if dropped:
        print(f"         verworfen (nicht in Liste): {' '.join(dropped)}")
    if sanitize_flags:
        print(f"         sanitize → review: {', '.join(sanitize_flags)}")
    raw_preview = json.dumps(out, ensure_ascii=False)
    if len(raw_preview) > 600:
        raw_preview = raw_preview[:600] + "…"
    print(f"         raw: {raw_preview}")


def cmd_tag(args) -> None:
    from phototag.admin_browse import normalize_folder
    from phototag.tagging import run_tag_batch
    from phototag.tag_jobs import resolve_rows_for_spec

    con = connect()
    folders = [
        normalize_folder(f)
        for f in (getattr(args, "folder", None) or [])
        if normalize_folder(f)
    ]
    nc_paths = [p.strip().lstrip("/") for p in (getattr(args, "nc_path", None) or []) if p.strip()]
    verbose = bool(getattr(args, "verbose", False) or cli_verbose())

    if folders or nc_paths:
        spec = {
            "folders": folders,
            "nc_paths": nc_paths,
            "retag": bool(args.retag),
            "recursive": not getattr(args, "no_recursive", False),
            "kind": args.kind,
            "limit": args.limit or 0,
            "include_db_folder_rows": False,
        }
        rows = resolve_rows_for_spec(con, spec)
    else:
        where = ["status IN ('new','error')" if not args.retag else "1=1"]
        params: list = []
        if args.kind:
            where.append("kind=?")
            params.append(args.kind)
        lim = args.limit or -1
        rows = con.execute(
            f"SELECT * FROM images WHERE {' AND '.join(where)} ORDER BY id LIMIT ?",
            (*params, lim),
        ).fetchall()

    print(
        f"{len(rows)} Dateien zu verarbeiten (Modell {model_name()} @ {api_url()}, "
        f"prompt={prompt_lang()})"
    )

    def on_progress(ev: dict) -> None:
        if ev.get("phase") != "done_one":
            return
        res = ev["result"]
        i = ev.get("index", 0)
        total = ev.get("total", len(rows))
        if not res.get("ok"):
            print(f"[{i}/{total}] FEHLER {res.get('nc_path')}: {res.get('error')}")
            return
        extra = ""
        print(
            f"[{i}/{total}] {res.get('status','?'):6} {res.get('nc_path')}  "
            f"{' '.join(res.get('tags') or [])}  ({res.get('seconds')}s){extra}"
        )

    stats = run_tag_batch(con, rows, verbose=verbose, on_progress=on_progress)
    print(f"-- fertig: {stats['ok']} ok, {stats['failed']} fehler, {stats['total']} gesamt")


# ---------------------------------------------------------------- Suche

def _terms(query: str) -> list[str]:
    return [t for t in re.split(r"\s+", query.strip()) if t and t.upper() not in {"OR", "AND"}]


def cmd_search(args) -> None:
    con = connect()
    terms = _terms(args.query)
    where, params = ["i.status='tagged'"], []
    if terms:
        joiner = " OR " if args.any else " AND "
        if args.fuzzy and has_trigram(con):
            # Trigram braucht >= 3 Zeichen; kürzere Begriffe -> Präfix im Standardindex
            long_t = [t for t in terms if len(t) >= 3]
            match = joiner.join('"' + t.replace('"', '') + '"' for t in long_t) or None
            table = "search_tri"
        else:
            match = joiner.join('"' + t.replace('"', '') + '"*' for t in terms)  # Präfix: "stran" findet "strand"
            table = "search"
        if match:
            where.append(f"i.id IN (SELECT rowid FROM {table} WHERE {table} MATCH ?)")
            params.append(match)
    for tag in args.tag or []:
        where.append("i.id IN (SELECT image_id FROM tags WHERE tag=?)")
        params.append(tag.lower())
    if args.folder:
        where.append("i.folder LIKE ?")
        params.append(f"%{args.folder}%")
    if args.kind:
        where.append("i.kind=?")
        params.append(args.kind)
    rows = con.execute(
        "SELECT i.id,i.path,i.folder,i.kind,i.caption,i.thumb,i.duration,"
        "(SELECT group_concat(tag,' ') FROM tags WHERE image_id=i.id) AS tags "
        f"FROM images i WHERE {' AND '.join(where)} ORDER BY i.mtime DESC LIMIT ?",
        (*params, args.limit),
    ).fetchall()
    out_dir = Path(args.export_thumbs) if args.export_thumbs else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    for r in rows:
        if out_dir and r["thumb"]:
            (out_dir / f"{r['id']}.jpg").write_bytes(r["thumb"])
        link = f"{args.nextcloud}/apps/files/?dir=/{r['folder']}" if args.nextcloud else ""
        dur = f" {r['duration']:.0f}s" if r["kind"] == "video" and r["duration"] else ""
        print(f"{r['path']}  [{r['kind']}{dur}]\n   Ordner: {r['folder'] or '/'} | Tags: {r['tags'] or ''}\n   {r['caption'] or ''} {link}")
    print(f"-- {len(rows)} Treffer")


def cmd_thumb(args) -> None:
    con = connect()
    if args.id:
        if not args.out:
            sys.exit("Usage: phototag thumb --id ID OUTFILE")
        row = con.execute(
            "SELECT thumb, path, root FROM images WHERE id=?", (args.id,)
        ).fetchone()
    else:
        if not args.path or not args.out:
            sys.exit("Usage: phototag thumb PATH OUTFILE  oder  phototag thumb --id ID OUTFILE")
        row = con.execute(
            "SELECT thumb, path, root FROM images WHERE path=?", (args.path,)
        ).fetchone()
    if not row or not row["thumb"]:
        sys.exit("keine Vorschau gefunden")
    Path(args.out).write_bytes(row["thumb"])
    print(f"{len(row['thumb'])} Bytes -> {args.out} ({row['path']})")


def cmd_stats(_args) -> None:
    con = connect()
    for status, kind, n in con.execute("SELECT status,kind,COUNT(*) FROM images GROUP BY status,kind"):
        print(f"{status:8} {kind:6} {n}")
    size = con.execute("SELECT COALESCE(SUM(LENGTH(thumb)),0) FROM images").fetchone()[0]
    print(f"Vorschaubilder gesamt: {size/1024:.0f} KiB")
    print("Top-Tags:")
    for tag, n in con.execute("SELECT tag,COUNT(*) c FROM tags GROUP BY tag ORDER BY c DESC LIMIT 15"):
        print(f"  {tag:22} {n}")
    print("Ordner:")
    for folder, n in con.execute("SELECT folder,COUNT(*) c FROM images GROUP BY folder ORDER BY c DESC LIMIT 10"):
        print(f"  {folder or '/':30} {n}")


def cmd_review(args) -> None:
    from phototag.review_queue import backfill_review_captions, nc_display_path

    con = connect()
    if args.backfill_captions:
        n = backfill_review_captions(con)
        print(f"{n} Review-Captions aus model_raw nachgezogen.")
    for r in con.execute(
        "SELECT id, root, path, age_status, kind, caption, error FROM images "
        "WHERE status='review' ORDER BY path"
    ):
        extra = f"  [{r['error']}]" if r["error"] else ""
        print(f"{r['id']:4} {r['age_status'] or '?':7} {r['kind']:5} {nc_display_path(r)}{extra}")
        if r["caption"]:
            print(f"      {r['caption'][:160]}")


def cmd_approve(args) -> None:
    from phototag.review_queue import approve_image, list_review_rows

    con = connect()
    ids: list[int] = []
    if args.all:
        ids = [r["id"] for r in list_review_rows(con)[: args.limit or 9999]]
    elif args.id:
        ids = [args.id]
    else:
        sys.exit("approve: --id N oder --all angeben")
    ok = err = 0
    for iid in ids:
        try:
            approve_image(con, iid, push_nc=args.push, tags=args.tags or None)
            ok += 1
            print(f"OK id={iid}")
        except (LookupError, ValueError) as exc:
            err += 1
            print(f"FEHLER id={iid}: {exc}", file=sys.stderr)
    print(f"-- {ok} freigegeben, {err} Fehler")


def cmd_serve(_args) -> None:
    from phototag.admin_app import run_server

    run_server()


def cmd_reindex(_args) -> None:
    con = connect()
    reindex(con)
    print("Suchindex neu aufgebaut.")


def cmd_push_nc(args) -> None:
    from phototag.nextcloud_sync import (
        load_nc_config,
        nc_dav_relative_path,
        pending_tagged_images,
        push_image_tags,
    )

    cfg = load_nc_config()
    con = connect()
    rows = pending_tagged_images(con, limit=args.limit or 0, force=args.force)
    if not rows:
        print("Nichts zu pushen (alle Tags schon in nc_pushed — ggf. --force).")
        return
    tag_cache: dict[str, int] = {}
    files_ok = 0
    tags_pushed = 0
    for row in rows:
        tags = [
            r["tag"]
            for r in con.execute("SELECT tag FROM tags WHERE image_id=? ORDER BY tag", (row["id"],))
        ]
        if not tags:
            continue
        if args.force:
            con.execute("DELETE FROM nc_pushed WHERE image_id=?", (row["id"],))
        dav_path = nc_dav_relative_path(row["root"], row["path"])
        try:
            n = push_image_tags(
                con,
                cfg,
                image_id=row["id"],
                path=dav_path,
                tags=tags,
                tag_cache=tag_cache,
                dry_run=args.dry_run,
            )
            if not args.dry_run:
                con.commit()
            tags_pushed += n
            files_ok += 1
            print(f"{'[dry-run] ' if args.dry_run else ''}{row['path']}: {len(tags)} Tag(s)")
        except RuntimeError as exc:
            print(f"FEHLER {row['path']}: {exc}", file=sys.stderr)
    print(f"-- {files_ok} Datei(en), {tags_pushed} Tag-Zuweisung(en)")


def cmd_tag_job_run(args) -> None:
    from phototag.tag_jobs import run_tag_job

    con = connect()
    run_tag_job(con, args.job_id, verbose=bool(getattr(args, "verbose", False)))


def cmd_blur_job_run(args) -> None:
    from phototag.blur_jobs import run_blur_job

    run_blur_job(connect(), args.job_id)


def cmd_faceblur(args) -> None:
    """Blur one file synchronously (CLI/testing). Result is registered like a UI job."""
    from phototag.blur_jobs import blur_one_file
    from phototag.faceblur import FaceBlurError, NoFacesFound

    con = connect()
    try:
        res = blur_one_file(con, args.nc_path, progress=lambda f: print(f"\r{f:4.0%}", end="", file=sys.stderr))
    except NoFacesFound as exc:
        sys.exit(str(exc))
    except (FaceBlurError, FileNotFoundError) as exc:
        sys.exit(f"faceblur: {exc}")
    print(f"\nOK: {res['nc_path']}  faces={res['faces']}  status={res['status']}  id={res['id']}")


def cmd_version(_args) -> None:
    from phototag import __version__

    print(__version__)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan"); s.add_argument("root"); s.set_defaults(fn=cmd_scan)
    t = sub.add_parser("tag")
    t.add_argument("--limit", type=int, default=0)
    t.add_argument("--retag", action="store_true")
    t.add_argument("--kind", choices=["image", "video"])
    t.add_argument(
        "--folder",
        action="append",
        default=[],
        help="NC-Ordner (Mount-relativ), mehrfach möglich; mit --no-recursive nur direkte Dateien",
    )
    t.add_argument(
        "--nc-path",
        action="append",
        default=[],
        help="Einzelne Datei (NC-Pfad, z. B. Album/Test/a.jpg)",
    )
    t.add_argument(
        "--no-recursive",
        action="store_true",
        help="Mit --folder nur Dateien direkt im Ordner, keine Unterordner",
    )
    t.add_argument("-v", "--verbose", action="store_true", help="caption, observations, verworfene Tags, raw JSON")
    t.set_defaults(fn=cmd_tag, recursive=True)
    tjr = sub.add_parser("tag-job-run", help=argparse.SUPPRESS)
    tjr.add_argument("job_id")
    tjr.add_argument("-v", "--verbose", action="store_true")
    tjr.set_defaults(fn=cmd_tag_job_run)
    bjr = sub.add_parser("blur-job-run", help=argparse.SUPPRESS)
    bjr.add_argument("job_id")
    bjr.set_defaults(fn=cmd_blur_job_run)
    fbp = sub.add_parser("faceblur", help="Gesichter in einer Datei unkenntlich machen (Kopie *.ai-blurred.*)")
    fbp.add_argument("nc_path", help="Pfad relativ zu PHOTOTAG_SCAN_ROOT")
    fbp.set_defaults(fn=cmd_faceblur)
    q = sub.add_parser("search"); q.add_argument("query", nargs="?", default="")
    q.add_argument("--any", action="store_true"); q.add_argument("--fuzzy", action="store_true")
    q.add_argument("--tag", action="append"); q.add_argument("--folder", default=""); q.add_argument("--kind", choices=["image", "video"])
    q.add_argument("--limit", type=int, default=50); q.add_argument("--nextcloud", default="")
    q.add_argument("--export-thumbs", default=""); q.set_defaults(fn=cmd_search)
    th = sub.add_parser("thumb")
    th.add_argument("path", nargs="?", default="")
    th.add_argument("out", nargs="?", default="")
    th.add_argument("--id", type=int, default=0, help="DB-id (z. B. aus phototag review)")
    th.set_defaults(fn=cmd_thumb)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    rv = sub.add_parser("review")
    rv.add_argument(
        "--backfill-captions",
        action="store_true",
        help="caption aus model_raw für review-Zeilen nachziehen",
    )
    rv.set_defaults(fn=cmd_review)
    ap = sub.add_parser("approve", help="Review manuell freigeben (optional push-nc)")
    ap.add_argument("--id", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--push", action="store_true", help="Nach Freigabe push-nc für diese Datei(en)")
    ap.add_argument("--tag", action="append", dest="tags", help="Tags statt Vorschlag aus model_raw")
    ap.set_defaults(fn=cmd_approve)
    sub.add_parser(
        "serve",
        help="Review-Admin-UI (Flask): PHOTOTAG_ADMIN_USER/PASSWORD, BIND 0.0.0.0 fuer LAN",
    ).set_defaults(fn=cmd_serve)
    sub.add_parser("reindex").set_defaults(fn=cmd_reindex)
    sub.add_parser("version", help="Installierte Paketversion (pyproject / pip -e)").set_defaults(fn=cmd_version)
    pn = sub.add_parser("push-nc", help="Tags als Nextcloud-Schlagworte (Systemtags) setzen")
    pn.add_argument("--limit", type=int, default=0)
    pn.add_argument("--dry-run", action="store_true")
    pn.add_argument("--force", action="store_true", help="Alle tagged erneut pushen")
    pn.set_defaults(fn=cmd_push_nc)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
