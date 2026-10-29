# Changelog

Format: Version → was für **Deploy** relevant ist.

## 0.3.22

- `scripts/ensure-rclone-mount.sh`, `stop-rclone-mount.sh`, systemd `phototag-rclone-mount.service`; Nacht-Lauf startet Mount wenn `PHOTOTAG_RCLONE_MOUNT` gesetzt.

## 0.3.21

- `docs/sanitize-rules.example.json` entfernt — **keine** Sanitize-/Regel-JSON im GitHub-Repo (nur Server + privates `doku/`).

## 0.3.20

- **Public repo:** Snippet-/Required-Tag-Dateien entfernt; `post-update.sh` ohne Tag-Texte.
- Sanitize: nur generisches `outdoor`/`indoor` im Code; weitere Regeln optional via **`PHOTOTAG_SANITIZE_RULES`** (JSON-Pfad nur auf dem Server, **keine** Regel-Datei im Repo).
- Doku/Beispiele generisch (kein Homelab-Ordnerprefix in Referenz).

## 0.3.19

- `docs/phototag-env.reference.txt` — Soll-Vorlage; `sync-phototag-env.sh --catalog` zeigt optionale Keys.
- `DEPLOY.md`: push-nc Testablauf.

## 0.3.18

- `sync-phototag-env.sh`: nur **aktive** Keys aus `.env.example` (kommentierte Optionen ≠ fehlend); `PHOTOTAG_NC_TOKEN_FILE` ersetzt `PHOTOTAG_NC_APP_PASSWORD` in der Prüfung.

## 0.3.17

- Nacht-Skript: **`PHOTOTAG_SCAN_ROOT` nur aus `phototag.env`** (kein fester Beispiel-Mount).
- **`PHOTOTAG_NIGHT_IMAGES_FIRST=1`**, **`PHOTOTAG_NIGHT_KIND`**, Defaults Batch **20** / Sleep **45**.
- systemd-Vorlage: `EnvironmentFile=phototag.env` — Timer **nicht** automatisch auf dem Server.
- Doku: `DEPLOY.md` Nacht-Abschnitt erweitert; Homelab: `doku/phototag/nacht-tagging.md`.

## 0.3.16

- **`./scripts/post-update.sh`** — nach Update ausführen (wird von `update-from-github.sh` aufgerufen).
- **`./scripts/sync-phototag-env.sh --apply`** — fehlende Keys aus `.env.example` an `phototag.env` (kommentiert).
- **`DEPLOY.md`** — vollständige Deploy-Anleitung.
- Category-Check: `scripts/required-category-tags.txt` + Snippets in `scripts/category-tag-snippets.txt`.

## 0.3.15

- **Env (neu, in `.env.example`):** `PHOTOTAG_TAG_SANITIZE`, `PHOTOTAG_SCAN_ROOT`, `PHOTOTAG_NIGHT_*` — in `phototag.env` nachziehen.
- **categories.txt:** erweiterte Taxonomie nur lokal auf dem Server.
- **Nacht:** `scripts/night-tag-loop.sh`, systemd unter `scripts/systemd/`.
- Tag-Widersprüche → Status `review` (`phototag review` zeigt `[sanitize:…]`).

## 0.3.14

- Update-Skript: immer `main`, Version aus ZIP, `phototag version`.
- `phototag.env` wird beim rsync nicht überschrieben.

## 0.3.11–0.3.13

- `model_raw`, `observations`, `phototag tag -v`, `PHOTOTAG_PROMPT_LANG`.
- Update: `PHOTOTAG_UPDATE_REF` statt `PHOTOTAG_REF` in der Shell.
