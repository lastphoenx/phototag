"""Local photo/video tagging with a vision LLM and SQLite."""

try:
    from importlib.metadata import version

    __version__ = version("phototag")
except Exception:
    __version__ = "0.0.0"
