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
./scripts/post-update.sh          # zeigt fehlende Env-Keys + Category-Tags
./scripts/sync-phototag-env.sh --apply   # fehlende Keys aus .env.example an phototag.env (kommentiert)
nano phototag.env                 # neue Zeilen aktivieren / Werte setzen
```

**`.env.example`** im Repo ist die **Referenz aller Keys** — `phototag.env` wird beim rsync **nicht** überschrieben. Neue Versionen fügen Keys hinzu; ohne `sync-phototag-env.sh` fehlen dir Features still (Nacht-Lauf, Sanitize, …).

## 3. categories.txt (privat)

- Liegt nur auf dem Server, **nicht** im GitHub-Repo.
- `post-update.sh` prüft **`scripts/required-category-tags.txt`**.
- Fehlende Zeilen: Text aus **`scripts/category-tag-snippets.txt`** in deine `categories.txt` kopieren (oder aus deinem privaten Master).

## 4. Verifizieren

```bash
set -a && source phototag.env && set +a
.venv/bin/phototag version
.venv/bin/phototag stats
```

Erster Tag-Test: kleines Limit, `-v` optional.

## 5. Nacht-Lauf (optional, noch manuell aktivieren)

**Im Repo:** nur Vorlagen — **auf CT 142 ist kein systemd-Timer automatisch installiert**, bis du `cp` + `enable` ausführst. Prüfen: `systemctl status phototag-night.timer` (wenn „not found“ → noch nicht eingerichtet).

### Was `PHOTOTAG_SCAN_ROOT` ist (nicht `/mnt/nextcloud-of`)

`/mnt/nextcloud-of` war nur ein **Platzhalter** in einer frühen Vorlage. Du setzt den **echten Mount-Punkt auf dem CT**, an dem die OF-Dateien **wie auf der Platte** liegen:

- **rclone-Remote-Name** (z. B. in `~/.config/rclone/rclone.conf`) ≠ Mount-Pfad.
- **`PHOTOTAG_NC_DAV_PREFIX=OF`** = Pfad **unter** Nextcloud-User-Files für WebDAV/push-nc.
- **`PHOTOTAG_SCAN_ROOT`** = Wurzel für `phototag scan`, sodass relative Pfade in der DB zu NC passen (z. B. Mount zeigt Inhalt von `…/files/OF` → Scan-Root ist dieser Mount, Datei `foo.jpg` → DB-Pfad `foo.jpg` oder Unterordner).

Bisherige Tests: **`/var/cache/phototag-test`** (Kopie). Produktion: dein rclone-Mount oder später FUSE — **Pfad selbst in `phototag.env` eintragen**.

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
cp /opt/phototag/scripts/systemd/phototag-night.service /etc/systemd/system/
cp /opt/phototag/scripts/systemd/phototag-night.timer /etc/systemd/system/
# phototag.env: PHOTOTAG_SCAN_ROOT, optional NIGHT_* setzen
systemctl daemon-reload
systemctl enable --now phototag-night.timer
systemctl list-timers | grep phototag
```

Timer startet **täglich 00:00**, Service läuft bis **`PHOTOTAG_NIGHT_END_HOUR`** (Default 7). Homelab-Detail: **`doku/phototag/nacht-tagging.md`** (privates Repo).

## 6. Release-Notizen

Pro Version: **`CHANGELOG.md`** — was sich geändert hat und ob du Env/Categories nachziehen musst.

## Was du **nicht** committen sollst

`phototag.env`, `categories.txt`, `state/*.db`, `secrets/` — siehe `.gitignore` und `SECURITY.md`.
