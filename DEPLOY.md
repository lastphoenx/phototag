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
- Tag-Widersprüche: optional **`PHOTOTAG_SANITIZE_RULES`** (Pfad zu JSON **nur auf dem Server** — kein Beispiel im GitHub-Repo; Homelab-Master privat in `doku/`).

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

### Admin-UI (`phototag serve`)

Lokal auf dem CT, **kein CDN** — CSS/JS liegen im Python-Paket.

```bash
pip install -e ".[admin]"    # einmalig nach Update
# phototag.env: PHOTOTAG_ADMIN_TOKEN=<lang>, optional BIND/PORT
set -a && source phototag.env && set +a
phototag serve               # http://127.0.0.1:8765
```

Im Browser: Token aus `phototag.env` einmal eingeben (sessionStorage). Tags editieren, Caption anpassen, **Freigeben** (+ optional push-nc). Nur über **localhost** oder nginx mit Auth erreichbar machen — Token nicht committen.

## 5. Nacht-Lauf (optional, noch manuell aktivieren)

**Im Repo:** nur Vorlagen — **auf CT 142 ist kein systemd-Timer automatisch installiert**, bis du `cp` + `enable` ausführst. Prüfen: `systemctl status phototag-night.timer` (wenn „not found“ → noch nicht eingerichtet).

### Was `PHOTOTAG_SCAN_ROOT` ist

Mount- oder Cache-Pfad auf dem CT, dessen **relative Pfade** zu Nextcloud passen:

- **Remote-Name** (rclone o. Ä.) ≠ Mount-Pfad.
- **`PHOTOTAG_NC_DAV_PREFIX`** = Unterordner unter `…/dav/files/USER/` für WebDAV/push-nc (z. B. `Photos`).
- **`PHOTOTAG_SCAN_ROOT`** = Wurzel für `phototag scan`; DB speichert Pfade relativ dazu (z. B. `foo.jpg` → NC `…/USER/Photos/foo.jpg` wenn Prefix `Photos` und Scan-Root den gleichen Baum zeigt).

Test: kleiner lokaler Ordner mit Kopien. Produktion: **rclone mount** (siehe unten).

### rclone-Mount (CT, Nextcloud-WebDAV)

**Remote-Name ≠ Mount-Pfad.** Syntax immer mit **Doppelpunkt**: `nextcloud_OF:` — nicht `REMOTE:OF` und nicht `nextcloud_OF` ohne `:`.

Remote-Pfad herausfinden:

```bash
rclone listremotes
rclone lsd nextcloud_OF:
rclone lsd nextcloud_OF:OF          # falls OF ein Unterordner ist
rclone ls nextcloud_OF: | head -5   # oder nextcloud_OF:OF
```

In **`phototag.env`** (nur Server):

```bash
PHOTOTAG_SCAN_ROOT=/mnt/nextcloud-of
PHOTOTAG_RCLONE_MOUNT=nextcloud_OF:          # Wurzel des Remotes
# oder PHOTOTAG_RCLONE_MOUNT=nextcloud_OF:OF  # wenn OF Unterordner ist
PHOTOTAG_NC_DAV_PREFIX=OF                     # muss zum Mount-Baum passen
```

Mount manuell testen:

```bash
mkdir -p /mnt/nextcloud-of
./scripts/ensure-rclone-mount.sh
mountpoint /mnt/nextcloud-of
ls /mnt/nextcloud-of | head
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
| `PHOTOTAG_NIGHT_SLEEP` | 45 | Sekunden **zwischen** Runden (scan → tag → warten) — GPU/CPU für andere Dienste |
| `PHOTOTAG_NIGHT_END_HOUR` | 7 | Schleife endet ab 07:00 |

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

Timer startet **täglich 00:00**, Service läuft bis **`PHOTOTAG_NIGHT_END_HOUR`** (Default 7). Homelab-Detail: **`doku/phototag/nacht-tagging.md`** (privates Repo).

## 6. Release-Notizen

Pro Version: **`CHANGELOG.md`** — was sich geändert hat und ob du Env/Categories nachziehen musst.

## Was du **nicht** committen sollst

`phototag.env`, `categories.txt`, `state/*.db`, `secrets/` — siehe `.gitignore` und `SECURITY.md`.
