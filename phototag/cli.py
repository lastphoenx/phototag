#!/usr/bin/env python3
"""Lokale Foto- UND Video-Verschlagwortung mit einem Vision-Modell + SQLite.

Private Medienarchive: Bilder/Frames nur an deine eigene Vision-API (/v1), nicht an Cloud-Dienste.

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
    max_side,
    max_tags,
    max_completion_tokens,
    model_name,
    prompt_lang,
    reasoning_effort,
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
"""


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(db_path())
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    con.execute("CREATE INDEX IF NOT EXISTS images_folder ON images(folder)")
    cols = {r[1] for r in con.execute("PRAGMA table_info(images)")}
    if "model_raw" not in cols:
        con.execute("ALTER TABLE images ADD COLUMN model_raw TEXT")
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
    con = connect()
    new = changed = seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            p = Path(dirpath) / name
            ext = p.suffix.lower()
            if ext not in IMAGE_EXTS and ext not in VIDEO_EXTS:
                continue
            kind = "video" if ext in VIDEO_EXTS else "image"
            seen += 1
            rel = p.relative_to(root).as_posix()
            folder = Path(rel).parent.as_posix()
            folder = "" if folder == "." else folder
            st = p.stat()
            row = con.execute("SELECT id,size,mtime,folder,kind FROM images WHERE root=? AND path=?", (str(root), rel)).fetchone()
            if row and row["size"] == st.st_size and abs(row["mtime"] - st.st_mtime) < 1:
                if row["folder"] != folder or row["kind"] != kind:
                    con.execute("UPDATE images SET folder=?,kind=? WHERE id=?", (folder, kind, row["id"]))
                    index_image(con, row["id"])
                continue
            digest = sha1_of(p)
            if row:
                con.execute(
                    "UPDATE images SET size=?,mtime=?,sha1=?,folder=?,kind=?,status='new',error=NULL WHERE id=?",
                    (st.st_size, st.st_mtime, digest, folder, kind, row["id"]),
                )
                changed += 1
            else:
                con.execute(
                    "INSERT INTO images(root,path,folder,kind,size,mtime,sha1) VALUES(?,?,?,?,?,?,?)",
                    (str(root), rel, folder, kind, st.st_size, st.st_mtime, digest),
                )
                new += 1
    con.commit()
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
    lines = "\n".join(f"- {t}: {h}" if h else f"- {t}" for t, h in categories)
    lang = prompt_lang()
    if lang == "en":
        where = (
            "The images are frames of ONE video in chronological order. Apply each tag that "
            "matches at least one frame."
            if is_video else "You see a single photo."
        )
        return (
            "You tag a private photo archive. " + where + "\n"
            "Reply ONLY with JSON, no text before or after:\n"
            '{"age_status": "yes|no|unsure", "tags": ["..."], "observations": ["..."], "caption": "..."}\n'
            "- age_status: Are all visible people clearly adults? If unsure use \"unsure\" or \"no\".\n"
            f"- tags: apply ALL matching tags (multiple encouraged, at most {max_tags()}) and ONLY from this list:\n"
            f"{lines}\n"
            "- If the list includes location, furniture, or pose tags, add them when clearly visible.\n"
            "- observations: optional short phrases (German or English) for visible details with no matching "
            "tag (expressions, props, small details), max 8 items.\n"
            "- caption: one factual sentence in German (max 160 characters).\n"
            "Do not invent tag ids outside the list; put extras in observations."
        )
    where = (
        "Die Bilder sind Frames EINES Videos in zeitlicher Reihenfolge. Vergib jeden Tag, der in "
        "mindestens einem Frame zutrifft."
        if is_video else "Du siehst ein einzelnes Foto."
    )
    return (
        "Du verschlagwortest eigenes Archivmaterial. " + where + "\n"
        "Antworte AUSSCHLIESSLICH mit JSON, ohne Text davor oder danach:\n"
        '{"age_status": "yes|no|unsure", "tags": ["..."], "observations": ["..."], "caption": "..."}\n'
        "- age_status: Sind alle sichtbaren Personen eindeutig volljährig? Bei Zweifel \"unsure\" oder \"no\".\n"
        f"- tags: vergib ALLE zutreffenden Tags (mehrere sind erwünscht, höchstens {max_tags()}) und NUR aus dieser Liste:\n"
        f"{lines}\n"
        "- Enthält die Liste Tags zu Ort, Möbeln oder Pose, vergib diese zusätzlich zu den "
        "Haupt-Tags, sobald sie klar erkennbar sind.\n"
        "- observations: optional kurze Beobachtungen zu Details ohne passenden Tag "
        "(Mimik, Gegenstände, Pose-Details), max. 8 Einträge.\n"
        "- caption: ein sachlicher Satz auf Deutsch (max. 160 Zeichen).\n"
        "Erfinde keine Tag-IDs ausserhalb der Liste; Extras gehören in observations."
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
        out["caption"] = cap_m.group(1)[:160]
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
    caption = str(out.get("caption", ""))[:160]
    model_raw = json.dumps(out, ensure_ascii=False)[:16000]
    return tags, dropped, caption, model_raw


def _print_tag_extras(out: dict, caption: str, dropped: list[str], verbose: bool) -> None:
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
    raw_preview = json.dumps(out, ensure_ascii=False)
    if len(raw_preview) > 600:
        raw_preview = raw_preview[:600] + "…"
    print(f"         raw: {raw_preview}")


def cmd_tag(args) -> None:
    categories = load_categories()
    allowed = {t for t, _ in categories}
    con = connect()
    where = ["status IN ('new','error')" if not args.retag else "1=1"]
    params: list = []
    if args.kind:
        where.append("kind=?")
        params.append(args.kind)
    rows = con.execute(
        f"SELECT * FROM images WHERE {' AND '.join(where)} ORDER BY id LIMIT ?", (*params, args.limit or -1)
    ).fetchall()
    print(f"{len(rows)} Dateien zu verarbeiten (Modell {model_name()} @ {api_url()}, prompt={prompt_lang()})")
    verbose = bool(getattr(args, "verbose", False) or cli_verbose())
    for i, row in enumerate(rows, 1):
        path = Path(row["root"]) / row["path"]
        is_video = row["kind"] == "video"
        t0 = time.time()
        try:
            media = encode_video(path) if is_video else encode_image(path)
            out = ask_model(media["frames"], categories, is_video)
            age_status = str(out.get("age_status", "unsure")).lower()
            tags, dropped, caption, model_raw = _apply_model_output(out, allowed)
        except (OSError, urllib.error.URLError, ValueError, KeyError, json.JSONDecodeError,
                subprocess.TimeoutExpired) as exc:
            con.execute("UPDATE images SET status='error', error=? WHERE id=?", (str(exc)[:300], row["id"]))
            con.commit()
            print(f"[{i}/{len(rows)}] FEHLER {row['path']}: {exc}")
            continue
        status = "tagged" if age_status == "yes" else "review"
        con.execute("DELETE FROM tags WHERE image_id=?", (row["id"],))
        if status == "tagged":
            con.executemany("INSERT INTO tags VALUES(?,?)", [(row["id"], t) for t in tags])
        con.execute(
            "UPDATE images SET status=?,age_status=?,caption=?,model_raw=?,width=?,height=?,duration=?,frames=?,thumb=?,"
            "model=?,tagged_at=?,error=NULL WHERE id=?",
            (status, age_status, caption if status == "tagged" else None, model_raw, media["width"], media["height"],
             media["duration"], len(media["frames"]), media["thumb"], model_name(), time.time(), row["id"]),
        )
        index_image(con, row["id"])
        con.commit()
        extra = f" [{len(media['frames'])} Frames]" if is_video else ""
        print(f"[{i}/{len(rows)}] {status:6} {row['path']}{extra}  {' '.join(tags)}  ({time.time()-t0:.1f}s)")
        _print_tag_extras(out, caption if status == "tagged" else str(out.get("caption", ""))[:160], dropped, verbose)


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
    row = con.execute("SELECT thumb FROM images WHERE path=?", (args.path,)).fetchone()
    if not row or not row["thumb"]:
        sys.exit("keine Vorschau gefunden")
    Path(args.out).write_bytes(row["thumb"])
    print(f"{len(row['thumb'])} Bytes -> {args.out}")


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


def cmd_review(_args) -> None:
    con = connect()
    for r in con.execute("SELECT path,age_status,kind FROM images WHERE status='review' ORDER BY path"):
        print(f"{r['age_status'] or '?':7} {r['kind']:5} {r['path']}")


def cmd_reindex(_args) -> None:
    con = connect()
    reindex(con)
    print("Suchindex neu aufgebaut.")


def cmd_push_nc(args) -> None:
    from phototag.nextcloud_sync import (
        load_nc_config,
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
        try:
            n = push_image_tags(
                con,
                cfg,
                image_id=row["id"],
                path=row["path"],
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


def cmd_version(_args) -> None:
    from phototag import __version__

    print(__version__)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan"); s.add_argument("root"); s.set_defaults(fn=cmd_scan)
    t = sub.add_parser("tag"); t.add_argument("--limit", type=int, default=0); t.add_argument("--retag", action="store_true")
    t.add_argument("--kind", choices=["image", "video"])
    t.add_argument("-v", "--verbose", action="store_true", help="caption, observations, verworfene Tags, raw JSON")
    t.set_defaults(fn=cmd_tag)
    q = sub.add_parser("search"); q.add_argument("query", nargs="?", default="")
    q.add_argument("--any", action="store_true"); q.add_argument("--fuzzy", action="store_true")
    q.add_argument("--tag", action="append"); q.add_argument("--folder", default=""); q.add_argument("--kind", choices=["image", "video"])
    q.add_argument("--limit", type=int, default=50); q.add_argument("--nextcloud", default="")
    q.add_argument("--export-thumbs", default=""); q.set_defaults(fn=cmd_search)
    th = sub.add_parser("thumb"); th.add_argument("path"); th.add_argument("out"); th.set_defaults(fn=cmd_thumb)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    sub.add_parser("review").set_defaults(fn=cmd_review)
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
