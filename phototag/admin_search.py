"""DB search for admin UI (same rules as ``phototag search`` CLI)."""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from phototag.cli import has_trigram


def search_terms(query: str) -> list[str]:
    return [t for t in re.split(r"\s+", query.strip()) if t and t.upper() not in {"OR", "AND"}]


def build_search_where(
    con: sqlite3.Connection,
    *,
    query: str,
    tags: list[str] | None,
    any_term: bool,
    fuzzy: bool,
    folder: str,
    kind: str | None,
) -> tuple[str, list[Any]]:
    """WHERE clause (without ``WHERE``) and bind params for ``images i`` alias."""
    terms = search_terms(query)
    where: list[str] = ["i.status='tagged'"]
    params: list[Any] = []
    if terms:
        joiner = " OR " if any_term else " AND "
        if fuzzy and has_trigram(con):
            long_t = [t for t in terms if len(t) >= 3]
            match = joiner.join('"' + t.replace('"', "") + '"' for t in long_t) or None
            table = "search_tri"
        else:
            match = joiner.join('"' + t.replace('"', "") + '"*' for t in terms)
            table = "search"
        if match:
            where.append(f"i.id IN (SELECT rowid FROM {table} WHERE {table} MATCH ?)")
            params.append(match)
    for tag in tags or []:
        t = tag.strip().lower()
        if t:
            where.append("i.id IN (SELECT image_id FROM tags WHERE tag=?)")
            params.append(t)
    folder = folder.strip()
    if folder:
        where.append("i.folder LIKE ?")
        params.append(f"%{folder}%")
    if kind:
        where.append("i.kind=?")
        params.append(kind)
    return " AND ".join(where), params


def search_image_rows(
    con: sqlite3.Connection,
    *,
    query: str = "",
    tags: list[str] | None = None,
    any_term: bool = False,
    fuzzy: bool = False,
    folder: str = "",
    kind: str | None = None,
    limit: int = 80,
    offset: int = 0,
    columns: str,
) -> tuple[list[sqlite3.Row], int]:
    where, params = build_search_where(
        con,
        query=query,
        tags=tags,
        any_term=any_term,
        fuzzy=fuzzy,
        folder=folder,
        kind=kind,
    )
    total = int(con.execute(f"SELECT COUNT(*) FROM images i WHERE {where}", params).fetchone()[0])
    lim = max(1, min(200, int(limit)))
    off = max(0, int(offset))
    rows = con.execute(
        f"SELECT {columns} FROM images i WHERE {where} ORDER BY i.mtime DESC LIMIT ? OFFSET ?",
        (*params, lim, off),
    ).fetchall()
    return list(rows), total
