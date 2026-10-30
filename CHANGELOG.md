# Changelog

Format: Version → was für **Deploy** relevant ist.

## 0.5.22

- **Telegram grosse Videos:** optional `PHOTOTAG_TELEGRAM_COMPRESS=1` — Staging nach `/tmp`, ffmpeg, Send, Temp löschen (Bot-Limit 50 MB).

## 0.5.21

- **WebUI Telegram:** Sichtbarer Upload (Karten-Overlay, Statuszeile, Fehler am Button statt nur kurzes Blinken).

## 0.5.20

- **Telegram:** Upload-Timeout Default 600 s; klare Fehlermeldung ab 50 MB (Bot-Limit); DEPLOY erklaert Mount-Direct-Read (kein Dauer-Cache in `/opt/phototag`).

## 0.5.19

- **Browse → Telegram:** optional `PHOTOTAG_TELEGRAM_BOT_TOKEN` + `PHOTOTAG_TELEGRAM_CHAT_ID` — Button sendet Original (Foto/Video) vom Mount an den Ziel-Chat (Saved Messages / Gruppe).

## 0.5.18

- **Sanitize überall:** Browse-**Speichern** und gleiche Pipeline wie KI (`prepare_stored_tags`); Review-Flags in UI; push-nc nur bei status tagged nach Sanitize.

## 0.5.17

- **Custom strip:** `when` + `when_any_in` / `when_all_in` — z. B. `genitals_visible` entfernen wenn `breasts_censored` oder andere Zensur-Tags; Prompt-Hinweis `vulva_censor_hint` (nur via JSON).

## 0.5.16

- **KI:** Sensitive Zensur-Prompts und `strip_under_censor` aus **`PHOTOTAG_TAGGING_CUSTOM`** (JSON nur Server/tempdok); öffentlicher Code = Loader + generischer Fallback. Gesichts-Partial-Tags über categories + Custom-Prompt (`face_partial_*`, `face_visible`).

## 0.5.15

- **KI:** Zusatz-Prompt für absichtliche Anonymisierung (Blur/Mosaik/Balken) vs. Bokeh; granulare `*_censored`-Regionen inkl. Gesäß/After/Hoden/rear; **kein** `genitals_shaved`/Anatomie-Detail unter Zensur (Prompt + `strip_invalid_detail_under_censor` bei Sanitize).

## 0.5.14

- **Nacht-Lauf:** **Ein** `scan` pro Nacht-Job, danach nur **`tag`-Batches** — kein Voll-Scan in jeder Schleifen-Runde mehr (rclone/CT-schonend). Optional `PHOTOTAG_NIGHT_RESCAN_WHEN_IDLE=1`.

## 0.5.13

- **Scan-Fortschritt:** `state/scan-progress.log` (Zeitstempel, alle 200 Dateien, alle 25 Hashes mit Pfad) — **`tail -f /opt/phototag/state/scan-progress.log`** unabhängig vom Journal.

## 0.5.12

- **`phototag scan`:** Fortschritt alle 200 Mediendateien auf **stderr** (sichtbar in `journalctl`); Nacht-Skript setzt **`PYTHONUNBUFFERED=1`**.

## 0.5.11

- **SQLite:** `busy_timeout` + Scan committet alle 150 Dateien (weniger „database is locked“ bei langem Scan).
- **night-tag-loop:** `flock` — kein zweiter Lauf parallel (systemd + catch-up).

## 0.5.10

- **night-tag-loop:** Klare Meldung + **Exit 1** wenn Stunde ≥ `END_HOUR` (vorher: leerer Erfolg, „Job startet“ ohne Wirkung). **`run-night-catchup.sh`** / `PHOTOTAG_NIGHT_FORCE=1` = eine Scan+Tag-Runde tagsüber. **`--self-test`** prüft DB ohne Mount.

## 0.5.9

- **night-tag-loop.sh:** Pending-Zählung über **Python/sqlite3-Modul** statt CLI `sqlite3` (fehlte auf CT → Nacht-Lauf endete um 00:00:32 sofort).

## 0.5.8

- **Browse:** Filter **In Unterordnern** + **Pagination** (80 pro Seite) für alle Filter (Mit Tags / Ohne Tags / Review / Alle).
- **Ordner KI-taggen:** Vorschau-Dialog mit Anzahl **wird getaggt** / **bereits getaggt übersprungen**; Job-Panel mit Pfadliste und **aktiver Datei**; sichtbare Karten im Job gelb/blau markiert (getaggte nicht im Job).

## 0.5.7

- **Retag in der Web-UI:** Bereits **tagged** Dateien ohne `retag` lieferten Jobs mit **0 Dateien** (Backend überspringt sie absichtlich). Beim Klick **KI taggen** erscheint jetzt eine **Bestätigung**; danach wird `retag: true` gesendet. Checkbox **Neu taggen** neben dem Filter sichtbar; klarere Fehlermeldung wenn kein Lauf startet.

## 0.5.6

- **`categories.example.txt`:** Abschnitt Stoff/Transparenz (`sheer`, `see_through`, `sheer_wet`, …) als Vorlage für lokale `categories.txt`.

## 0.5.5

- **KI-Queue UI:** Warteschlange = **gelber** Rahmen/Button/Badge; laufende Datei = **blau** (pulsierender Rahmen).
- **Speichern:** Klick sichtbar («Speichere …», grauer Rahmen), Erfolg **grüner Rahmen** + Button «Gespeichert ✓» (~3 s).
- **Caption:** Default-Limit **320 Zeichen** (`PHOTOTAG_CAPTION_MAX`, vorher fest 160) — Prompt, DB und Speichern einheitlich.

## 0.5.4

- **Browse-Filter «Mit Tags» / «In Review»:** DB-first (Status-Index), kein Mount-Durchlauf mit Full-Table-Scan pro Datei — Ordner mit vielen ungetaggten Dateien lädt nur getaggte/review-Einträge schnell.
- **SQLite:** WAL + Indizes `images(status)`, `images(folder, status)`; für Zehntausende Medien ausreichend (Engpass war O(n×m)-Lookup, nicht SQLite an sich).

## 0.5.3

- **Tag-UI:** Eingabe vor Speichern/Freigabe übernehmen; mehrere Tags (Komma/Leerzeichen); klickbare Vorschläge aus `/api/tags` (categories.txt). Review-**Speichern** nutzt dieselbe Freigabe-API wie **Freigeben** (inkl. push-nc).
- **KI-Warteschlange:** deutliche Rahmen/Badge «Warteschlange 2/5» vs «KI aktiv 2/5»; Job-Panel zeigt aktuelle Datei.
- **Thumbs:** Browser-Cache (`Cache-Control` 24h), direkte Thumb-URLs (kein Blob pro Reload). **DB:** weiter SQLite (+ Thumb-BLOB in `images.thumb`) — kein Redis/PostgreSQL im Stack.

## 0.5.2

- **Review-UI:** Queue-Ansicht erzwingt editierbare Tags + Freigeben/Überspringen (unabhängig von API-Feldern); Cache-Bust auf `admin.js`/`admin.css` via Versions-Query.
- **KI-Stapel:** Mehrere «KI taggen»-Klicks → ein Hintergrund-Job (wie Ordner-Job); Fortschritt im Job-Panel; nach Ende Reload mit Status aus DB.
- **Mount:** KI-Jobs/`tag/one` liefern 503, wenn `PHOTOTAG_SCAN_ROOT` nicht lesbar ist (`phototag-rclone-mount.service`).

## 0.5.1

- **Review-UI:** API liefert wieder `status`/`in_review` — Freigeben, Überspringen, Tag-Editor in der Review-Queue. Browse: gleiche Buttons bei `status=review`.
- **KI taggen:** sichtbarer Busy-Zustand (Karte, Button «KI läuft …»), nur ein Lauf gleichzeitig.

## 0.5.0

- **KI-Tagging aus Admin-UI:** pro Datei **KI taggen** (sync, langer gunicorn-Timeout); **Ordner KI-taggen** startet Hintergrund-Job (`POST /api/tag/jobs`, Fortschritt per Poll). CLI: **`phototag tag --folder`**, **`--nc-path`**, **`--no-recursive`**. Jobs: `tag-job-run` (Subprozess, Log unter `state/tag-job-*.log`).

## 0.4.0

- **Admin-UI Mount-first:** Ordner und Dateien vom **rclone-Mount**; DB nur Anreicherung + **Speichern** (Index bei Bedarf, optional **push-nc**). Review-Queue unverändert separat.

## 0.3.32

- **Admin-Browse:** Unterordner vom **Mount** (`PHOTOTAG_SCAN_ROOT`) plus DB — ganzer OF-Baum sichtbar; Dateien weiterhin nur wo **scan** indexiert hat. Bildliste über alle DB-`root`-Werte.

## 0.3.31

- **Admin-Browse:** Ordnernavigation aus **NC-Anzeigepfad** (`nc_dav_relative_path`), nicht nur DB-`folder` — funktioniert auch bei Scan nur eines Unterordners (z. B. `…/Test`).

## 0.3.30

- **Admin-UI:** grösseres Caption-Feld; **Ordner durchsuchen** (OF-Breadcrumb, Unterordner, Filter: alle / Review / mit Tags / ohne Tags). API: `/api/browse`, `/api/browse/folders`.

## 0.3.29

- **Admin-Dienst:** gunicorn ohne `--factory` (`phototag.admin_app:application`); **`run-admin-wsgi.sh`** bindet LAN (`PHOTOTAG_ADMIN_SERVICE_BIND`), sicheres **`source phototag.env`**; Preflight vor Start. **`install-admin-service.sh`** zeigt journal bei Fehler. **`update-from-github.sh`:** `chmod +x` auf Skripte.

## 0.3.28

- **Review-Admin Dauerbetrieb:** `scripts/systemd/phototag-admin.service`, `run-admin-wsgi.sh` (gunicorn), **`install-admin-service.sh`**. LAN ohne SSH (`BIND=0.0.0.0` in der Unit). Updates: `pip install -e ".[admin]"`. 2FA weiterhin per Homelab-Reverse-Proxy (DEPLOY.md).

## 0.3.27

- **Admin:** Startup-Hinweise (BIND 127.0.0.1 vs LAN, TOKEN nur API, Flask-Warnung). Optional **`PHOTOTAG_ADMIN_PASSWORD_HASH`** (werkzeug). **`post-update.sh`** / **`sync-phototag-env.sh`**: klarere Meldungen zu Admin-Login. **DEPLOY.md** / **`.env.example`**: Env-Tabelle.

## 0.3.26

- **Admin-UI:** Login mit **`PHOTOTAG_ADMIN_USER` / `PHOTOTAG_ADMIN_PASSWORD`** (Session-Cookie), Standard-**`BIND=0.0.0.0`** für LAN ohne SSH. Optional **`PHOTOTAG_ADMIN_TOKEN`** nur noch für API-Header. 2FA über Homelab-Reverse-Proxy (Doku privat).

## 0.3.25

- **Review:** `caption` wird auch bei `status=review` gespeichert; `phototag review --backfill-captions` für alte Zeilen.
- **`phototag approve`** (CLI) und **`phototag serve`** — lokale Admin-UI (Flask, Static im Repo, kein CDN): Tags bearbeiten, freigeben, optional `push-nc`.
- **`phototag thumb --id N`** — Vorschau per DB-id aus `phototag review`.

## 0.3.24

- **`push-nc`:** Tag-Zuweisung an Dateien mit WebDAV-PUT + XML (wie Nextcloud-UI). Leerer PUT + HTTP **412** wurde fälschlich als «schon ok» gewertet → Schlagworte existierten global, aber **nicht** dateispezifisch angehakt. Nach Update: `phototag push-nc --force` für betroffene Dateien.

## 0.3.23

- **`push-nc`:** WebDAV-Pfad aus `PHOTOTAG_SCAN_ROOT` + DB-`root` + `path` — Subfolder-Scan (z. B. `…/Zum Löschen/Test`) braucht **kein** manuelles Überschreiben von `PHOTOTAG_NC_DAV_PREFIX` mehr; `.env` bleibt `PHOTOTAG_NC_DAV_PREFIX=OF`.

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
