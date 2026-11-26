"""Local desktop document for the flying-site reception history."""

from __future__ import annotations

import html
import re
from pathlib import Path

from .imagery_tiles import IMAGERY_TILE_JAVASCRIPT
from .map_basemaps import BASEMAP_RUNTIME_JAVASCRIPT, build_basemap_config
from .reception_map_renderer import _asset_uri, _json_for_script, _message_html


def build_reception_history_html(payload: dict[str, object] | None, dark: bool = True) -> str:
    """Build the history viewer for a local file URL (datasets may exceed Qt's setHtml limit)."""
    if not payload or not payload.get("logs"):
        message = (payload or {}).get("message") or "No paired GPS and reception readings at this site."
        return _message_html(str(message), dark, "empty")
    assets = Path(__file__).resolve().parent / "assets"
    template = (assets / "history-viewer.html").read_text(encoding="utf-8")
    replacements = {
        "__THEME__": "dark" if dark else "light",
        "__MODEL__": (assets / "history-model.js").read_text(encoding="utf-8"),
        "__HISTORY__": _json_for_script(payload),
        "__BASEMAP_CONFIG__": _json_for_script(build_basemap_config(payload)),
        "__BASEMAP_RUNTIME__": BASEMAP_RUNTIME_JAVASCRIPT + IMAGERY_TILE_JAVASCRIPT,
        "__MAP_CSS__": html.escape(_asset_uri("maplibre-gl.css"), quote=True),
        "__MAP_MODULE__": _json_for_script(_asset_uri("maplibre-gl.mjs")),
        "__MAP_WORKER__": _json_for_script(_asset_uri("maplibre-gl-worker.mjs")),
    }
    return re.sub(r"__[A-Z_]+__", lambda match: replacements[match[0]], template)
