# Changelog

Format: Version → was für **Deploy** relevant ist.

## 0.7.9

- **Fix:** `run-night-catchup.sh` / Nacht-Lauf ohne `PHOTOTAG_NIGHT_IMAGES_FIRST` — `run_tag` ohne Argument crashte unter `set -u` (`$1 ist nicht gesetzt`).

## 0.7.8

- **Admin Browse:** Seitengrösse **100 oder 200** (Dropdown «Pro Seite»), Pager **oben und unten** («← Zurück» / «Weiter →»). Backend erlaubt weiterhin max. 200 pro Request.

## 0.7.7

- **Nacht-Lauf:** Default **ohne** `phototag scan` — nur **`tag`** für DB-Queue (`new`/`error`). Optional `PHOTOTAG_NIGHT_SCAN_AT_START=1` (früheres Verhalten: ein Scan vor dem Taggen). Index neuer Medien tagsüber manuell `phototag scan`.

## 0.7.6

- **Fix:** Video Face-Blur Fortschritt im Button — häufigere DB-Updates (Staging-Ticks, Analyse/Encode), UI pollt zentral alle 1 s.

## 0.7.5

- **Admin:** Ansicht «Stichworte & Tags (DB)» — `GET /api/search` (FTS wie CLI `phototag search`, Tag-Filter, Paginierung), Karten mit Thumb-Vorschau.

## 0.7.4

- **Video/Gesenkter Kopf (Patch 23):** Kollabierte Augen-Landmarks → stabile Achse aus Erkennungs-Drehung; `despike`/`gate_unsure`/`settle_axes` glätten springende Boxen; Tracker bestraft Sprünge (`JUMP_WEIGHT`). `PHOTOTAG_FACEBLUR_UNSURE=face` optional: Eyebar wird ganze Gesichts-Box.

## 0.7.3

- **Thumbs ohne Browse-Index:** `/api/browse/thumb` lädt nur das eine `thumb`-BLOB per Pfad-Suffix-Query, nicht mehr über den leichten Browse-Index (dort fehlt `thumb` seit 0.7.2).
- **Vorschau:** UI-Blur-Einstellungen werden mit `temporary_blur_overlay` nur für einen Request gesetzt — «Env verwenden» bleibt danach unverändert. Defaults-`summary` ohne `score=` (nur Server).

## 0.7.2

- **Admin Browse schneller:** Listen-Queries laden keine `thumb`-BLOBs mehr (jeder Ordnerwechsel zog vorher alle Vorschaubilder aus SQLite). DB-Filter (Review/Tagged/Blur) paginieren per SQL statt alles in den RAM zu laden. Tag-Katalog wird im Browser nur einmal geholt; Unterordner-Liste parallel zum Browse-Request.

## 0.7.1

- **Browse-Leiste Face-Blur:** Presets (Standard *Env verwenden*), Custom mit Teilen/fill/margin/strength/mode; Auswahl in `localStorage`. **`GET /api/blur/defaults`** liefert erlaubte Teile und Presets. **`POST /api/blur/preview`** zeigt das Ergebnis in der Karte ohne Mount-Datei; **`Face-Blur`** schreibt die Kopie mit denselben Optionen (Env-Overlay nur im Job-Prozess). `blur_info` enthält **`source=ui|env`**. SCORE/VIDEO_STRIDE nicht in der UI.

## 0.7.0

- **Video-Blur neu aufgebaut.** Vorher entschied jedes Frame allein; Körpermuster (Brustwarzen und Nabel) und Hände galten als "Gesicht", dasselbe Gesicht tauchte in mehreren Drehungen als Geister-Treffer an falscher Stelle auf, und die Grösse sprang von Frame zu Frame. Jetzt:
  1. **Analyse auf einem kleinen Bild** (max. 1280 px), alle `PHOTOTAG_FACEBLUR_VIDEO_STRIDE` (3) Frames, immer in allen 4 Drehungen.
  2. **Verfolgung über die Zeit:** Treffer werden zu Spuren verbunden. Pro Spur gilt die Drehung mit dem besten Treffer; dasselbe Gesicht in mehreren Drehungen wird zu **einem** Gesicht (keine Geister).
  3. **Unsichere Spuren fliegen raus:** nur in einer Drehung gefunden und Mittel < `TRACK_TRUST_SCORE` (0.85); Mittel < `TRACK_MIN_SCORE` (0.55); ein einzelner Treffer unter `TRACK_SURE_SCORE` (0.90). `PHOTOTAG_FACEBLUR_CORROBORATE=0` schaltet die Drehungsprüfung ab, `PHOTOTAG_FACEBLUR_MAX_FACES=1` behält nur die beste Spur.
  4. **Glättung und Interpolation:** Positionen und Landmarken werden geglättet und zwischen den Treffern interpoliert; Lücken bis `GAP_SEC` (1.0 s) werden überbrückt, vor dem ersten und nach dem letzten Treffer wird `HOLD_SEC` (0.3 s) weitergeblurrt.
  5. **Ein Render-Durchlauf** über das Vollbild, auf `PHOTOTAG_FACEBLUR_VIDEO_MAX_SIDE` (1920) verkleinert, x264 `PHOTOTAG_FACEBLUR_VIDEO_PRESET` (veryfast).
- **Schneller:** Das Video wird einmal lokal bereitgestellt (`PHOTOTAG_FACEBLUR_STAGE=1`), danach alles lokal; nur die fertige Datei geht einmal zurück auf den Mount. Prüfsumme und Vorschaubild werden aus der lokalen Datei berechnet. Frames werden ohne Kopie in einen wiederverwendeten Puffer gelesen. Gemessen auf 1080p mit 4 Kernen: 354 ms pro Frame vorher, 55 bis 81 ms jetzt.
- **Ehrlichere Karte:** Die Notiz nennt Frames mit Blur in Prozent, verfolgte und verworfene Gesichter und warnt, wenn in mehr als 10 % der Frames kein Gesicht gefunden wurde.
- **Eye-Abstand-Untergrenze:** die Teile schrumpfen bei Profil und gesenktem Kopf nicht mehr auf Splitter (Skala mindestens 0.38 x Boxbreite).
- **`phototag faceblur-debug <nc_pfad>`:** nur analysieren, schreibt ein Prüfbild (`state/faceblur-debug-<name>.png`) mit behaltenen (grün) und verworfenen (rot) Treffern und listet Spuren mit Grund.
- **WebUI:** *Blur OK* und Fehler zeigen jetzt eine Meldung direkt auf der Karte; ein laufender Blur-Job wird nach einem Neuladen der Seite wieder aufgenommen (`GET /api/blur/jobs/active`).
- **Browse Filter *Blur-Kopien*:** Zeigt Kopien **immer im Baum unter dem gewählten Ordner** (inkl. Unterordner), nicht nur direkte Kinder — auch wenn «In Unterordnern» aus war.

## 0.6.6

- **Vorschaubilder immer aktuell:** `/api/browse/thumb` und `/api/images/<id>/thumb` senden ETag + `no-cache` statt `max-age=86400`. Gleicher Dateiname nach *Blur verwerfen* + neuem Blur zeigte sonst 24 h das alte Bild (Job hatte trotzdem neue Einstellungen). Browse-Karten nutzen `thumb_v` (SHA1-Prefix) in der Thumb-URL.
- **Erkennung in 4 Drehungen (`PHOTOTAG_FACEBLUR_ROTATE=1`, Standard):** YuNet erkennt liegende/kopfstehende Gesichter, liefert Landmarks aber wie bei aufrechtem Kopf — Teile lagen daneben. Zusätzliche Pässe 90/180/270°, bester Treffer pro Gesicht; Extra-Pässe min. Score 0,6. ~4× Erkennungszeit (~104 ms vs ~27 ms bei 1280×720). `PHOTOTAG_FACEBLUR_ROTATE=0` schaltet aus.
- **Face-Blur bei gedrehtem Kopf:** «Abwärts» am Gesicht aus Augen→Mund, nicht Bildachse.

## 0.6.5

- **Face-Blur: Lücken zwischen den Teilen füllen.** `PHOTOTAG_FACEBLUR_FILL=none` (Standard, einzelne Balken) | `bridge` (Lücken zwischen übereinanderliegenden Teilen werden geschlossen, z. B. Augenbalken, Nase, Mund zu einer Säule; Wangen neben schmaleren Teilen bleiben sichtbar) | `hull` (eine einzige konvexe Fläche um alle gewählten Teile). `PHOTOTAG_FACEBLUR_FILL_GAP` (1.5 Augenabstände) begrenzt die grösste Lücke bei `bridge`. Folgt der Kopfneigung, gilt für Bilder und Videos.
- Breite und Höhe je Teil getrennt: `PHOTOTAG_FACEBLUR_SCALE_<TEIL>_W` und `_H` (zusätzlich zu `SCALE_<TEIL>`).

## 0.6.4

- **Face-Blur: Teile einzeln und kombinierbar.** `PHOTOTAG_FACEBLUR_REGION` nimmt jetzt eine Liste (Komma oder `+`): `eyebar`, `eyes`, `nose`, `mouth`, `ears`, `chin`, `forehead` sowie `face` und `tight`. Beispiel: `eyebar,nose,mouth`. Die Teile sind gedrehte Rechtecke, die der Kopfneigung folgen und sich nach dem Augenabstand richten; `PHOTOTAG_FACEBLUR_MARGIN` addiert Rand rundherum. Feinjustage je Teil: `PHOTOTAG_FACEBLUR_SCALE_NOSE|MOUTH|EARS|CHIN|FOREHEAD` (1.0).
- Alte Namen bleiben gültig (`eyes_mouth` = `eyes,mouth`, `eyes_mouth_ears` = `eyes,mouth,ears`). Unbekannte Namen werden gemeldet und ignoriert.
- Warnungen und Prüfungen (Blur-Fläche, Rest-Gesicht) richten sich nach den gewählten Teilen.

## 0.6.3

- **Face-Blur Region `eyebar`:** ein Balken von Auge zu Auge, etwa bis zu den Ohren, deckt Augenbrauen (oben) und Augenringe (unten) ab und folgt der Kopfneigung (gedrehtes Rechteck). Feinjustage in Augenabständen: `PHOTOTAG_FACEBLUR_BAR_UP` (0.45), `PHOTOTAG_FACEBLUR_BAR_DOWN` (0.35), `PHOTOTAG_FACEBLUR_BAR_SIDE` (1.0 = Gesichtsrand bis Gesichtsrand); `PHOTOTAG_FACEBLUR_MARGIN` addiert Rand rundherum.
- Mosaik (`MODE=pixel`) bei langgestreckten Formen: Zellgrösse nach der kurzen Seite, damit die Augen nicht lesbar bleiben.

## 0.6.2

- **Face-Blur sichtbar prüfen:** Hinweise am Blur-Kopie-Karte (Einstellungen, Blur-Fläche in %, Warnung bei grossem Blur, Warnung wenn in der Kopie noch ein Gesicht erkannt wird; Region face/tight). Ungültige `PHOTOTAG_FACEBLUR_*`-Werte werden im Job-Log und am Ergebnis gemeldet statt still ignoriert.
- `phototag.env`: Inline-Kommentare (`KEY=wert   # Text`) werden beim Einlesen entfernt.
- Video: bei Landmark-Regionen (eyes/mouth/…) wird jedes Frame erkannt (Stride 1).

## 0.6.1

- **Face-Blur:** `PHOTOTAG_FACEBLUR_REGION` (Landmark-Modi `eyes`, `mouth`, `eyes_mouth`, `eyes_mouth_ears`, plus `tight`) und `PHOTOTAG_FACEBLUR_STRENGTH` (0.2–4). YuNet-Landmarks werden ausgewertet; Button-Tooltip zeigt aktive Region/Stärke/Modus.
- **Fix:** Blur-Hintergrundprozess lädt `phototag.env` explizit; `MARGIN` skaliert auch Landmark-Boxen; `SCORE` pro Detect aktualisiert. Job-Log und UI-Status zeigen `faceblur settings: …`. **`SCORE` ändert nur Erkennung, nicht Blur-Grösse.**

## 0.6.0

- **Face-Blur (neu):** Button **Face-Blur** je Datei (Bild oder Video) im Ordner-Browser. Erzeugt `<name>.ai-blurred.<ext>` im **gleichen Ordner** (Mount → Nextcloud), das Original bleibt unverändert. Neue Zeile in der DB mit Status **`blurred_review`**, Verweis auf das Original (`derived_from`).
- Prüfung durch den Menschen: **Blur OK** → `blurred_ok`, **Blur verwerfen** → Kopie wird gelöscht. Beide Status werden vom Nacht-Lauf und von Ordner-Jobs **nicht** getaggt; KI-Tagging nur manuell (Karte auswählen → *KI taggen*). Neuer Filter *Blur-Kopien (prüfen)*.
- Erkennung: YuNet (OpenCV, lokal, CPU), Video in zwei Durchläufen (Erkennung alle `STRIDE` Frames, Blur mit Boxen der Nachbar-Frames), Ton als AAC, **Metadaten (EXIF/GPS/Container) entfernt**. Ohne erkanntes Gesicht wird **keine** Kopie erzeugt.
- Neu: `scripts/download-faceblur-model.sh`, Extra `faceblur` (opencv-python-headless, numpy), Keys `PHOTOTAG_FACEBLUR_*`, CLI `phototag faceblur <nc_path>`.
- `scan` setzt `blurred_*`-Zeilen nicht mehr auf `new` zurück.

## 0.5.24

- **Fix KI-Tagging:** `sanitize_review_note` import; `nc_pushed`-Tabelle vor Speichern/Freigeben; `too_many_tags` → Review statt alphabetischem Abschneiden.
- **Security:** kein Default-Session-Key; API-Token timing-safe; XSS-Fix Job-Panel (`escHtml`).
- **Public repo:** neutrale Beispiele/Doku/Tests; `.gitignore` für `state/`, `phototag.env`, `sanitize-rules.json`.

## 0.5.23

- **Telegram Temp:** Cleanup nach Upload (auch bei Worker-Abbruch: Sweep alter `phototag-tg-*`); Dateiname in Telegram = Original-Stem (`.mp4`).

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

- **Custom strip:** Regeln in `PHOTOTAG_TAGGING_CUSTOM` unterstützen `when` + `when_any_in` / `when_all_in` (Tags entfernen, wenn bestimmte andere Tags vorliegen).

## 0.5.16

- **KI:** Prompt-Zusätze und Strip-Regeln aus **`PHOTOTAG_TAGGING_CUSTOM`** (JSON nur auf dem Server); öffentlicher Code = Loader + generischer Fallback.

## 0.5.15

- **KI:** Zusatz-Prompt für absichtliche Anonymisierung (Blur/Mosaik/Balken) vs. Bokeh; Detail-Tags unter verdeckten Bereichen werden bei Sanitize entfernt.

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

- **Browse:** Filter **In Unterordnern** + **Pagination** (damals 80 pro Seite; ab 0.7.8: 100/200 wählbar) für alle Filter (Mit Tags / Ohne Tags / Review / Alle).
- **Ordner KI-taggen:** Vorschau-Dialog mit Anzahl **wird getaggt** / **bereits getaggt übersprungen**; Job-Panel mit Pfadliste und **aktiver Datei**; sichtbare Karten im Job gelb/blau markiert (getaggte nicht im Job).

## 0.5.7

- **Retag in der Web-UI:** Bereits **tagged** Dateien ohne `retag` lieferten Jobs mit **0 Dateien** (Backend überspringt sie absichtlich). Beim Klick **KI taggen** erscheint jetzt eine **Bestätigung**; danach wird `retag: true` gesendet. Checkbox **Neu taggen** neben dem Filter sichtbar; klarere Fehlermeldung wenn kein Lauf startet.

## 0.5.6

- **`categories.example.txt`:** Abschnitt Stoff/Material als Vorlage für lokale `categories.txt`.

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

- **Admin-Browse:** Unterordner vom **Mount** (`PHOTOTAG_SCAN_ROOT`) plus DB — ganzer Medienbaum sichtbar; Dateien weiterhin nur wo **scan** indexiert hat. Bildliste über alle DB-`root`-Werte.

## 0.3.31

- **Admin-Browse:** Ordnernavigation aus **NC-Anzeigepfad** (`nc_dav_relative_path`), nicht nur DB-`folder` — funktioniert auch bei Scan nur eines Unterordners (z. B. `…/Test`).

## 0.3.30

- **Admin-UI:** grösseres Caption-Feld; **Ordner durchsuchen** (Breadcrumb, Unterordner, Filter: alle / Review / mit Tags / ohne Tags). API: `/api/browse`, `/api/browse/folders`.

## 0.3.29

- **Admin-Dienst:** gunicorn ohne `--factory` (`phototag.admin_app:application`); **`run-admin-wsgi.sh`** bindet LAN (`PHOTOTAG_ADMIN_SERVICE_BIND`), sicheres **`source phototag.env`**; Preflight vor Start. **`install-admin-service.sh`** zeigt journal bei Fehler. **`update-from-github.sh`:** `chmod +x` auf Skripte.

## 0.3.28

- **Review-Admin Dauerbetrieb:** `scripts/systemd/phototag-admin.service`, `run-admin-wsgi.sh` (gunicorn), **`install-admin-service.sh`**. LAN ohne SSH (`BIND=0.0.0.0` in der Unit). Updates: `pip install -e ".[admin]"`. 2FA optional per vorgeschaltetem Reverse-Proxy (DEPLOY.md).

## 0.3.27

- **Admin:** Startup-Hinweise (BIND 127.0.0.1 vs LAN, TOKEN nur API, Flask-Warnung). Optional **`PHOTOTAG_ADMIN_PASSWORD_HASH`** (werkzeug). **`post-update.sh`** / **`sync-phototag-env.sh`**: klarere Meldungen zu Admin-Login. **DEPLOY.md** / **`.env.example`**: Env-Tabelle.

## 0.3.26

- **Admin-UI:** Login mit **`PHOTOTAG_ADMIN_USER` / `PHOTOTAG_ADMIN_PASSWORD`** (Session-Cookie), Standard-**`BIND=0.0.0.0`** für LAN ohne SSH. Optional **`PHOTOTAG_ADMIN_TOKEN`** nur noch für API-Header. 2FA optional über vorgeschalteten Reverse-Proxy.

## 0.3.25

- **Review:** `caption` wird auch bei `status=review` gespeichert; `phototag review --backfill-captions` für alte Zeilen.
- **`phototag approve`** (CLI) und **`phototag serve`** — lokale Admin-UI (Flask, Static im Repo, kein CDN): Tags bearbeiten, freigeben, optional `push-nc`.
- **`phototag thumb --id N`** — Vorschau per DB-id aus `phototag review`.

## 0.3.24

- **`push-nc`:** Tag-Zuweisung an Dateien mit WebDAV-PUT + XML (wie Nextcloud-UI). Leerer PUT + HTTP **412** wurde fälschlich als «schon ok» gewertet → Schlagworte existierten global, aber **nicht** dateispezifisch angehakt. Nach Update: `phototag push-nc --force` für betroffene Dateien.

## 0.3.23

- **`push-nc`:** WebDAV-Pfad aus `PHOTOTAG_SCAN_ROOT` + DB-`root` + `path` — Subfolder-Scan (z. B. `…/Album/Test`) braucht **kein** manuelles Überschreiben von `PHOTOTAG_NC_DAV_PREFIX` mehr; `.env` bleibt `PHOTOTAG_NC_DAV_PREFIX=Media`.

## 0.3.22

- `scripts/ensure-rclone-mount.sh`, `stop-rclone-mount.sh`, systemd `phototag-rclone-mount.service`; Nacht-Lauf startet Mount wenn `PHOTOTAG_RCLONE_MOUNT` gesetzt.

## 0.3.21

- `docs/sanitize-rules.example.json` entfernt — **keine** Sanitize-/Regel-JSON im GitHub-Repo (nur auf dem Server).

## 0.3.20

- **Public repo:** Snippet-/Required-Tag-Dateien entfernt; `post-update.sh` ohne Tag-Texte.
- Sanitize: nur generisches `outdoor`/`indoor` im Code; weitere Regeln optional via **`PHOTOTAG_SANITIZE_RULES`** (JSON-Pfad nur auf dem Server, **keine** Regel-Datei im Repo).
- Doku/Beispiele generisch.

## 0.3.19

- `docs/phototag-env.reference.txt` — Soll-Vorlage; `sync-phototag-env.sh --catalog` zeigt optionale Keys.
- `DEPLOY.md`: push-nc Testablauf.

## 0.3.18

- `sync-phototag-env.sh`: nur **aktive** Keys aus `.env.example` (kommentierte Optionen ≠ fehlend); `PHOTOTAG_NC_TOKEN_FILE` ersetzt `PHOTOTAG_NC_APP_PASSWORD` in der Prüfung.

## 0.3.17

- Nacht-Skript: **`PHOTOTAG_SCAN_ROOT` nur aus `phototag.env`** (kein fester Beispiel-Mount).
- **`PHOTOTAG_NIGHT_IMAGES_FIRST=1`**, **`PHOTOTAG_NIGHT_KIND`**, Defaults Batch **20** / Sleep **45**.
- systemd-Vorlage: `EnvironmentFile=phototag.env` — Timer **nicht** automatisch auf dem Server.
- Doku: `DEPLOY.md` Nacht-Abschnitt erweitert.

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
