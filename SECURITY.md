# Security notes

- **`categories.txt` must never be committed** — only `categories.example.txt`.
- **Pinned updates:** set `PHOTOTAG_REF` to a Git tag (e.g. `v0.3.0`) or a full commit SHA; avoid unpinned `main` (`update-from-github.sh` blocks `main` unless `PHOTOTAG_ALLOW_MAIN=1`).
- **Private repo:** set `GITHUB_TOKEN` only in local `.env` on the server (gitignored), minimal scope (`contents: read`).
- **Supply chain:** `pip install -e .` runs code from the downloaded tree — review before changing the pinned tag.
