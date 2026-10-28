# phototag — Agent notes

- Run tests before push: `pip install -e ".[dev]"` → `pytest -q`
- Never commit `categories.txt` — only `categories.example.txt` in git.
- **Deploy:** keep `DEPLOY.md` + `CHANGELOG.md` current; new env keys → `.env.example`; new required tags → `scripts/required-category-tags.txt` + `scripts/category-tag-snippets.txt`. `update-from-github.sh` must call `post-update.sh`.
