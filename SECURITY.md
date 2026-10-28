# Security notes

- **`categories.txt` must never be committed** — only `categories.example.txt`.
- **Pinned updates (optional):** set `PHOTOTAG_REF` to a full commit SHA or Git tag; default in `update-from-github.sh` is **`main`**.
- **Private repo:** set `GITHUB_TOKEN` only in local `.env` on the server (gitignored), minimal scope (`contents: read`).
- **Supply chain:** `pip install -e .` runs code from the downloaded tree — review before changing the pinned tag.
