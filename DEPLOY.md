# Deploy phototag (CT / Server)

Standard-Pfad: **`/opt/phototag`**, Konfiguration **`phototag.env`** (chmod 600), DB und `categories.txt` bleiben beim Update erhalten.

## 1. Code aktualisieren

```bash
cd /opt/phototag
./scripts/update-from-github.sh
```

Das Skript:

- zieht **GitHub `main`**
- `pip install -e .`
- ruft **`./scripts/post-update.sh`** auf (Checkliste unten)

Falls das Update-Skript sehr alt ist: `./scripts/bootstrap-update-from-main.sh` (einmalig).

## 2. Pflicht nach jedem Update

```bash
cd /opt/phototag
./scripts/post-update.sh
./scripts/sync-phototag-env.sh              # fehlende *aktive* Keys
./scripts/sync-phototag-env.sh --catalog    # alle optionalen Keys aus .env.example
diff -u docs/phototag-env.reference.txt phototag.env  # grober Soll-Ist-Vergleich
nano phototag.env
```

**`.env.example`** im Repo ist die **Referenz aller Keys** — `phototag.env` wird beim rsync **nicht** überschrieben. Neue Versionen fügen Keys hinzu; `sync-phototag-env.sh` vergleicht nur **aktive** Zeilen in `.env.example` (ohne `#`). Kommentierte Optionen dort sind Doku — kein Muss. **Nextcloud:** `PHOTOTAG_NC_TOKEN_FILE` **oder** `PHOTOTAG_NC_APP_PASSWORD` reicht.

## 3. categories.txt (privat)

- Liegt nur auf dem Server, **nicht** im GitHub-Repo (`categories.example.txt` ist nur Demo).
- `post-update.sh` zählt Tag-Zeilen — **keine** Tag-Inhalte aus dem Repo.
- Optional in **`phototag.env`:** `PHOTOTAG_REQUIRED_TAGS_FILE` (eine Tag-ID pro Zeile; nur Server).
- Tag-Widersprüche: optional **`PHOTOTAG_SANITIZE_RULES`** (Pfad zu JSON **nur auf dem Server** — kein Beispiel im GitHub-Repo).
- **Zensur-/Prompt-Anpassungen:** optional **`PHOTOTAG_TAGGING_CUSTOM`** (JSON **nur auf dem Server** — ohne Datei nur generischer Kurz-Prompt).
- **Sanitize in UI:** Browse-**Speichern** und KI-Lauf nutzen dieselbe `sanitize_tags`-Pipeline (ab 0.5.18); bei Flags → **review**, kein push-nc bis bereinigt.

## 4. Verifizieren

```bash
set -a && source phototag.env && set +a
.venv/bin/phototag version
.venv/bin/phototag stats
```

### push-nc testen (Schlagworte zurück nach Nextcloud)

Voraussetzung: Datei in der DB muss **unter demselben relativen Pfad** in NC existieren wie `PHOTOTAG_NC_DAV_PREFIX` — z. B. DB-Pfad `2024/foo.jpg` → NC `…/files/USER/Photos/2024/foo.jpg` (Prefix = dein NC-Unterordner).

1. **Dry-run:** `phototag push-nc --dry-run --limit 5` — zeigt, welche Pfade versucht würden.
2. **Echter Push:** `phototag push-nc --limit 5` — bei Fehler `PROPFIND … HTTP 404` existiert die Datei in NC nicht (lokaler Test-Ordner ≠ NC, unless dieselben Dateien auch unter dem DAV-Prefix liegen).
3. **Erneut pushen:** `--force` (ignoriert `nc_pushed`-Cache).

Erster Tag-Test: kleines Limit, `-v` optional.

### Review-Queue (age_status unsure)

- **`phototag review`** — id, NC-Pfad, Caption; **`--backfill-captions`** zieht alte Captions aus `model_raw`.
- **`phototag approve --id N --push`** — manuell freigeben + optional NC.
- **`phototag thumb --id N /tmp/x.jpg`** — Vorschau (id aus `review`).

### Admin-UI (Review per Browser)

CSS/JS im Python-Paket (kein CDN). **Kein SSH-Tunnel:** CT im LAN, Browser auf **`http://<CT-IP>:8765/`**, Login mit **`PHOTOTAG_ADMIN_USER`** / Passwort (oder Hash) aus **`phototag.env`**.

### Telegram (optional, Browse-UI)

Originaldatei vom Mount an einen Chat senden (Weiterleiten in der Telegram-App). In **`phototag.env`** (chmod 600):

- **`PHOTOTAG_TELEGRAM_BOT_TOKEN`** — Bot von @BotFather
- **`PHOTOTAG_TELEGRAM_CHAT_ID`** — Ziel: deine User-ID → Privatchat mit dem Bot; Gruppen-ID (`-100…`) → Gruppe
- Optional **`PHOTOTAG_TELEGRAM_UPLOAD_TIMEOUT`** — Default 600 s (Upload)

**Grosse Videos (> 50 MB Bot-Limit):** mit **`PHOTOTAG_TELEGRAM_COMPRESS=1`** — zuerst **Staging** (`copy` Mount → `/tmp`), **ffmpeg** lokal auf CT, Send, Temp-Dateien löschen. Optional: `PHOTOTAG_TELEGRAM_STAGING_MAX_MB` (Default 600), `PHOTOTAG_TELEGRAM_COMPRESS_TARGET_MB` (48), `PHOTOTAG_TELEGRAM_COMPRESS_TIMEOUT` (1800 s). **`ffmpeg`** auf dem CT nötig.

**Kleine Dateien (≤ 50 MB):** weiterhin direkt vom Mount, kein Dauer-Cache unter `/opt/phototag`.

Einmal **`/start`** an den Bot schicken (Privatchat), sonst liefert die API oft „chat not found“. Danach **`systemctl restart phototag-admin.service`**. Button **→ Telegram** erscheint nur wenn beide Keys gesetzt sind.

**Gruppen-Save-Inbox (empfohlen zum Weiterleiten):**

1. Private Gruppe anlegen (z. B. „phototag outbox“), Bot als Mitglied hinzufügen.
2. In der Gruppe eine Nachricht schicken (z. B. `ping`) — damit der Bot den Chat kennt.
3. Chat-ID ermitteln (nur Token in URL, Wert nicht committen):
   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | head -c 2000
   ```
   Unter `result[].message.chat` steht `"id": -100…` für Supergruppen — **negative** Zahl in `PHOTOTAG_TELEGRAM_CHAT_ID`.
4. Optional Bot-Rechte: reicht Mitglied; „Post messages“ nur nötig wenn der Bot sonst nicht senden darf.

**Saved Messages:** statt Gruppe deine **eigene numerische User-ID** als `PHOTOTAG_TELEGRAM_CHAT_ID` + `/start` im Bot-Privatchat.

**Dauerbetrieb (empfohlen):** systemd + gunicorn — startet nach Reboot, kein Flask-Dev-Warnung.

```bash
cd /opt/phototag
# phototag.env: PHOTOTAG_ADMIN_USER=..., PHOTOTAG_ADMIN_PASSWORD=... (chmod 600)
# Optional: PHOTOTAG_ADMIN_SESSION_SECRET=langer-zufall
./scripts/install-admin-service.sh
systemctl status phototag-admin.service
journalctl -u phototag-admin.service -n 30 --no-pager
```

Die Unit **`phototag-admin.service`** bindet per **`run-admin-wsgi.sh`** auf **`0.0.0.0`** (`PHOTOTAG_ADMIN_SERVICE_BIND`) — unabhängig von `PHOTOTAG_ADMIN_BIND=127.0.0.1` in `phototag.env` (gilt nur für manuelles `phototag serve`).

Nach **`update-from-github.sh`:** `systemctl restart phototag-admin.service` (wenn installiert).

**Manuell / Debug** (foreground, Flask dev server):

```bash
pip install -e ".[admin]"
set -a && source phototag.env && set +a
phototag serve   # respektiert PHOTOTAG_ADMIN_BIND in phototag.env
```

**Admin-Env (eine Zeile Wahrheit):**

| Variable | Pflicht für `serve`? | Bedeutung |
|----------|----------------------|-----------|
| `PHOTOTAG_ADMIN_USER` | ja | Benutzername im Login-Formular |
| `PHOTOTAG_ADMIN_PASSWORD` | ja* | Passwort im Klartext in `phototag.env` (Datei nur auf CT, `chmod 600`) |
| `PHOTOTAG_ADMIN_PASSWORD_HASH` | ja* | Alternative zu Klartext: `werkzeug`-Hash — im Browser trotzdem Klartext eingeben |
| `PHOTOTAG_ADMIN_BIND` | nein (Default `0.0.0.0`) | **`127.0.0.1`** = nur localhost auf dem CT; **`0.0.0.0`** = vom PC im LAN |
| `PHOTOTAG_ADMIN_PORT` | nein (8765) | TCP-Port |
| `PHOTOTAG_ADMIN_SESSION_SECRET` | nein | Signiert Session-Cookies (Zufallsstring empfohlen) |
| `PHOTOTAG_ADMIN_TOKEN` | nein | **Nicht** für Browser — nur API mit Header `X-Phototag-Token` |

\* Entweder `PASSWORD` oder `PASSWORD_HASH`, nicht beides nötig.

Hash erzeugen (einmal auf dem CT):

```bash
.venv/bin/python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('DEIN_PASSWORT'))"
# Zeile in phototag.env: PHOTOTAG_ADMIN_PASSWORD_HASH=scrypt:...
# PHOTOTAG_ADMIN_PASSWORD= dann weglassen oder auskommentieren
```

**Warum Klartext in `.env`?** Gleiches Muster wie Nextcloud-App-Passwort: die Datei ist das Geheimnis auf dem Server, nicht Git. Hash in der Env verhindert Leser mit Dateizugriff das Passwort zu sehen; wer die Env-Datei ändern kann, kann ohnehin alles. Hash ist optional ab 0.3.27.

**Flask-Warnung «development server»:** nur bei **`phototag serve`**. Mit **`phototag-admin.service`** läuft **gunicorn** (Production-WSGI).

**Sync-Meldung «Keys vorhanden»:** heißt nur, dass Key-Namen (auch als `#`-Kommentar) in `phototag.env` vorkommen — **nicht**, dass Admin-Login gesetzt ist. `./scripts/post-update.sh` prüft User/Passwort/BIND separat.

**2FA (optional, später):** phototag selbst = **User + Passwort + Session-Cookie** (kein TOTP im Repo). Optional **Reverse-Proxy** davor (nginx, SSO/TOTP) → HTTPS + 2FA; phototag bleibt dahinter auf HTTP `:8765` im internen Netz.

**Später HTTPS/Domain:** gleicher Proxy (TLS-Termination), Ziel upstream `http://<phototag-CT-IP>:8765` — keine Änderung am Python-Code nötig.

**Ordner durchsuchen (ab 0.4.0):** Navigation liest **`PHOTOTAG_SCAN_ROOT`** (rclone-Mount). Pro Datei werden DB-Daten (Tags, Caption, Status) **mitgeladen**, falls vorhanden. **Speichern** schreibt Tags/Caption in die DB (legt Eintrag an, falls nötig); mit Häkchen **push-nc** zusätzlich Schlagworte nach Nextcloud. Mount muss laufen (`phototag-rclone-mount.service`).

**KI-Tagging (ab 0.5.0):** In der Browse-Ansicht **KI taggen** (einzelne Datei, blockiert bis Modell fertig — gunicorn **`--timeout 600`**, Standard **1 Worker**). **Ordner KI-taggen** legt einen Job an und startet `python -m phototag tag-job-run <id>` im Hintergrund; Fortschritt in der UI, Log: **`state/tag-job-<id>.log`**. CLI parallel: `phototag tag --folder 'Unterordner' [--no-recursive]`, `phototag tag --nc-path 'foo.jpg'`. Option **Neu taggen (retag)** auch für bereits **tagged** Dateien.

**Datenbank / Thumbs:** phototag nutzt **SQLite** (`PHOTOTAG_DB`) — keine PostgreSQL/Redis im Projekt. Für **Zehntausende** Bilder/Videos reicht SQLite mit WAL und Status-Indizes (ab 0.5.4); langsam war vor allem Browse «Mit Tags», das jedes Mount-File mit der ganzen DB abglich — behoben. Vorschaubilder: BLOB in `images.thumb` + HTTP-Cache auf Thumb-URLs. Tags aus **`categories.txt`** (lokal): Bekleidung z. B. `bikini`, `leggings`, `tanktop` — siehe `categories.example.txt`, dann Admin neu starten.

## 5. Nacht-Lauf (optional, noch manuell aktivieren)

**Im Repo:** nur Vorlagen — **auf dem Server ist kein systemd-Timer automatisch installiert**, bis du `cp` + `enable` ausführst. Prüfen: `systemctl status phototag-night.timer` (wenn „not found“ → noch nicht eingerichtet).

### Was `PHOTOTAG_SCAN_ROOT` ist

Mount- oder Cache-Pfad auf dem CT, dessen **relative Pfade** zu Nextcloud passen:

- **Remote-Name** (rclone o. Ä.) ≠ Mount-Pfad.
- **`PHOTOTAG_NC_DAV_PREFIX`** = Unterordner unter `…/dav/files/USER/` für WebDAV/push-nc (z. B. `Photos`).
- **`PHOTOTAG_SCAN_ROOT`** = Wurzel für `phototag scan`; DB speichert Pfade relativ dazu (z. B. `foo.jpg` → NC `…/USER/Photos/foo.jpg` wenn Prefix `Photos` und Scan-Root den gleichen Baum zeigt).

Test: kleiner lokaler Ordner mit Kopien. Produktion: **rclone mount** (siehe unten).

### rclone-Mount (CT, Nextcloud-WebDAV)

**Remote-Name ≠ Mount-Pfad.** Syntax immer mit **Doppelpunkt**: `nextcloud_media:` — nicht `REMOTE:Media` und nicht `nextcloud_media` ohne `:`.

Remote-Pfad herausfinden:

```bash
rclone listremotes
rclone lsd nextcloud_media:
rclone lsd nextcloud_media:Media          # falls Media ein Unterordner ist
rclone ls nextcloud_media: | head -5   # oder nextcloud_media:Media
```

In **`phototag.env`** (nur Server):

```bash
PHOTOTAG_SCAN_ROOT=/mnt/media
PHOTOTAG_RCLONE_MOUNT=nextcloud_media:          # Wurzel des Remotes
# oder PHOTOTAG_RCLONE_MOUNT=nextcloud_media:Media  # wenn Media Unterordner ist
PHOTOTAG_NC_DAV_PREFIX=Media                     # muss zum Mount-Baum passen
```

Mount manuell testen:

```bash
mkdir -p /mnt/media
./scripts/ensure-rclone-mount.sh
mountpoint /mnt/media
ls /mnt/media | head
```

**systemd (Dauerbetrieb, einmalig):**

```bash
cp /opt/phototag/scripts/systemd/phototag-rclone-mount.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now phototag-rclone-mount.service
systemctl status phototag-rclone-mount.service
```

CT braucht **FUSE** (`fuse3`, ggf. `pct set 142 -features fuse=1` + Reboot). `night-tag-loop.sh` ruft **`ensure-rclone-mount.sh`** auf, wenn das Skript existiert.

### Batch + Pause (15 / 60 ist nicht Pflicht)

| Variable | Default im Skript | Zweck |
|----------|-------------------|--------|
| `PHOTOTAG_NIGHT_BATCH` | 20 | Max. Dateien **pro** `phototag tag`-Aufruf (Ollama entlasten, Abbruch um 07:00 feiner) |
| `PHOTOTAG_NIGHT_SLEEP` | 45 | Sekunden **zwischen Tag-Batches** (nach dem einmaligen Scan) |
| `PHOTOTAG_NIGHT_END_HOUR` | 7 | Schleife endet ab 07:00 |
| `PHOTOTAG_NIGHT_RESCAN_WHEN_IDLE` | 0 | **0** = kein erneuter Voll-Scan wenn Queue leer (Default). **1** = ein Rescan wenn `new`/`error` leer |

**Ablauf Nacht:** **einmal** `phototag scan` auf `PHOTOTAG_SCAN_ROOT`, danach bis 07:00 nur **`phototag tag`** in Batches — **kein** Voll-Scan pro Runde (früher: verschwendete rclone-Läufe).

Kleiner Bestand, schnelle Nacht: z. B. `BATCH=50`, `SLEEP=10`. Volle Bibliothek, vorsichtig: `BATCH=10`, `SLEEP=90`. Alles in **`phototag.env`**.

### Bilder und Videos

- **`scan`** nimmt **Bilder und Videos** (ffmpeg nötig).
- **`tag` ohne Option** verarbeitet **beides** gemischt (Reihenfolge DB).
- **`PHOTOTAG_NIGHT_IMAGES_FIRST=1`** in `phototag.env`: erst alle **`new`/`error` mit `kind=image`**, danach Rest (Videos + offene Fehler).
- **`PHOTOTAG_NIGHT_KIND=image`** oder **`video`**: nur diese Art (dauerhaft in der Nacht).

### systemd einrichten (einmalig, du auf dem CT)

```bash
cp /opt/phototag/scripts/systemd/phototag-rclone-mount.service /etc/systemd/system/
cp /opt/phototag/scripts/systemd/phototag-night.service /etc/systemd/system/
cp /opt/phototag/scripts/systemd/phototag-night.timer /etc/systemd/system/
# phototag.env: PHOTOTAG_SCAN_ROOT, PHOTOTAG_RCLONE_MOUNT, optional NIGHT_*
systemctl daemon-reload
systemctl enable --now phototag-rclone-mount.service
systemctl enable --now phototag-night.timer
systemctl list-timers | grep phototag
```

Timer startet **täglich 00:00**, Service läuft bis **`PHOTOTAG_NIGHT_END_HOUR`** (Default 7).

## 6. Release-Notizen

Pro Version: **`CHANGELOG.md`** — was sich geändert hat und ob du Env/Categories nachziehen musst.

## Was du **nicht** committen sollst

`phototag.env`, `categories.txt`, `state/*.db`, `secrets/` — siehe `.gitignore` und `SECURITY.md`.

## Face-Blur (optional)

```bash
cd /opt/phototag
./scripts/download-faceblur-model.sh          # lädt YuNet, prüft SHA-256
echo 'PHOTOTAG_FACEBLUR_MODEL=/opt/phototag/models/face_detection_yunet_2023mar.onnx' >> phototag.env
./scripts/update-from-github.sh               # installiert opencv-python-headless + numpy
systemctl restart phototag-admin
```

- Button **Face-Blur** erscheint pro Datei erst, wenn `PHOTOTAG_FACEBLUR_MODEL` gesetzt ist.
- Ergebnis: `<name>.ai-blurred.<ext>` neben dem Original (Videos als `.mp4`), DB-Status `blurred_review`. Der rclone-Mount lädt die Datei nach Nextcloud hoch (`--vfs-cache-mode writes`; bei grossen Videos kann der Upload nach dem Job noch laufen).
- Es läuft immer nur **ein** Blur-Job; Fortschritt im Button. Log: `state/blur-job-<id>.log`.
- Prüfen im Filter *Blur-Kopien (prüfen)*: **Blur OK** oder **Blur verwerfen**. Gesichter, die der Detektor nicht findet (stark gedreht, verdeckt, sehr klein), bleiben unverändert — immer Stichprobe ansehen, bevor eine Kopie weitergegeben wird.
- CLI: `phototag faceblur "Ordner/bild.jpg"` (Pfad relativ zu `PHOTOTAG_SCAN_ROOT`).

**Feintuning** (`phototag.env`, danach `systemctl restart phototag-admin`):

| Variable | Werte | Wirkung |
|----------|--------|---------|
| `PHOTOTAG_FACEBLUR_REGION` | `face` (Standard), `tight`, `eyes`, `mouth`, `eyes_mouth`, `eyes_mouth_ears` | `face`/`tight` = Detektor-Box (+ `MARGIN`); Landmark-Modi nutzen YuNet-Punkte (Augen/Mund); Ohren sind Näherung an der Gesichtsseite |
| `PHOTOTAG_FACEBLUR_STRENGTH` | `0.2`–`4`, Standard `1` | Höher = stärkerer Unkenntlichmachungs-Effekt |
| `PHOTOTAG_FACEBLUR_SCORE` | `0.2`–`0.95` | **Nur Erkennung** (wie viele Gesichter) — ändert **nicht** die Blur-Fläche |
| `PHOTOTAG_FACEBLUR_MARGIN` | nur bei `face`/`tight` und skaliert Landmark-Pads | kleiner = weniger Fläche |
| `PHOTOTAG_FACEBLUR_MODE` | `blur` \| `pixel` | Gaussian vs. Mosaik |

Empfehlung wenn der Blur zu gross wirkte: `PHOTOTAG_FACEBLUR_REGION=eyes_mouth` und/oder `MARGIN=0.2`, `STRENGTH=0.7`–`1.2`.

Blur-Jobs lesen **`phototag.env` beim Start** (auch im Hintergrund-Prozess); Log `state/blur-job-<id>.log` beginnt mit Zeile `faceblur settings: …` — dort siehst du, ob Margin/Region wirklich ankommen. Keys müssen **ohne `#`** aktiv sein.
