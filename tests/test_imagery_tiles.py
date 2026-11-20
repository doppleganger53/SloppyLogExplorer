"""Exercise the shared imagery loader in Chromium with a disposable database and provider."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def test_imagery_retry_and_persistent_cache(tmp_path: Path) -> None:
    # Use a fresh QApplication: the suite's offscreen widgets do not initialize
    # WebEngine and its profile must close before temporary storage is removed.
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(tmp_path)],
        check=True, timeout=60, env={**os.environ, "APPDATA": str(tmp_path)},
    )


def run_browser_checks(root: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QUrl
    from PyQt6.QtGui import QColor, QImage
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
    from sloppy_log_explorer.imagery_tiles import IMAGERY_TILE_JAVASCRIPT
    from tools.validate_gps_map_runtime import _run_event_loop, _run_js

    app = QApplication(sys.argv)
    profile = QWebEngineProfile("tile-test", app)
    profile.setPersistentStoragePath(str(root / "storage"))
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
    page = QWebEnginePage(profile)
    page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
    image = QImage(2, 2, QImage.Format.Format_ARGB32)
    image.fill(QColor(90, 40, 180, 120))
    raw = QByteArray()
    buf = QBuffer(raw)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buf, "PNG")
    counts: Counter[str] = Counter()
    failing: set[str] = set()

    class TileServer(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path
            counts[path] += 1
            status = 200
            if path in failing or (path == "/flaky" and counts[path] == 1):
                status = 503
            if path == "/missing":
                status = 404
            invalid = path == "/invalid" and counts[path] == 1
            self.send_response(status)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", "application/json" if invalid else "image/png")
            control = {
                "/old": "max-age=0",
                "/strict": "max-age=0, must-revalidate",
                "/private": "no-store",
            }.get(path, "max-age=3600")
            self.send_header("Cache-Control", control if status == 200 else "no-store")
            self.end_headers()
            self.wfile.write(b"{}" if invalid else bytes(raw))

        def log_message(self, fmt: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), TileServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    document = """<script>
        const basemapConfig = {sources: {
          "usgs-naip-raster-source": {tiles: ["https://test/naip"]},
          "nasa-gibs-raster-source": {tiles: ["https://test/gibs"]}
        }};
        const maplibregl = {addProtocol(name, handler) { window.loadTile = handler; }};
    """ + IMAGERY_TILE_JAVASCRIPT + """
        installImageryTileLoader();
    </script>"""

    def open_map(name: str) -> None:
        path = root / (name + ".html")
        path.write_text(document, encoding="utf-8")
        page.setUrl(QUrl.fromLocalFile(str(path)))
        for _ in range(100):
            _run_event_loop(20)
            if _run_js(page, "typeof loadTile === 'function' && location.pathname.endsWith(" +
                       json.dumps(name + ".html") + ")"):
                return
        raise AssertionError("Imagery loader did not initialize")

    def evaluate_async(expression: str):
        _run_js(page, "window.outcome = null; (" + expression +
                ").then(value => { window.outcome = value; }).catch(error => {"
                " window.outcome = String(error); }); true;")
        for _ in range(150):
            _run_event_loop(20)
            value = _run_js(page, "window.outcome")
            if value is not None:
                return value
        raise AssertionError("Tile operation did not settle")

    def load(path: str, *, abort: bool = False) -> str:
        url = json.dumps("sle-imagery://" + base + path)
        abort_js = "setTimeout(() => ctrl.abort(), 100);" if abort else ""
        return evaluate_async(f"""
          (async () => {{
            const ctrl = new AbortController();
            {abort_js}
            try {{
              const result = await loadTile({{url:{url}}}, ctrl);
              const ctx = new OffscreenCanvas(2,2).getContext("2d");
              ctx.drawImage(result.data,0,0);
              return Array.from(ctx.getImageData(0,0,1,1).data).join(",");
            }} catch (error) {{
              return error.name === "AbortError" ? "aborted" : "failed";
            }}
          }})()
        """)

    try:
        open_map("flight")
        color = load("/flaky")
        assert color.endswith(",120") and counts["/flaky"] == 2
        assert load("/invalid") == color and counts["/invalid"] == 2
        for path in ("/old", "/strict", "/private"):
            assert load(path) == color
        open_map("heat")
        failing.update(("/flaky", "/old", "/private", "/strict", "/abort"))
        assert load("/flaky") == color and counts["/flaky"] == 2
        assert load("/old") == color and counts["/old"] == 2
        for path in ("/private", "/strict"):
            assert load(path) == "failed" and counts[path] == 4
        assert load("/missing") == "failed" and counts["/missing"] == 1
        assert load("/abort", abort=True) == "aborted" and counts["/abort"] == 1
        # Fill the remaining slots with disposable keys in one transaction.
        # The next successful fetch must evict the oldest entry atomically.
        seeded = evaluate_async("""new Promise(resolve => {
          const open = indexedDB.open("sloppy-imagery-v1");
          open.onsuccess = () => {
            const tx = open.result.transaction("tiles", "readwrite");
            const store = tx.objectStore("tiles");
            store.clear();
            for (let i = 0; i < 384; i++) store.put({url: String(i), saved: i});
            tx.oncomplete = () => resolve(true);
          };
        })""")
        assert seeded is True
        assert load("/new") == color
        count_and_oldest = evaluate_async("""new Promise(resolve => {
          const open = indexedDB.open("sloppy-imagery-v1");
          open.onsuccess = () => {
            const store = open.result.transaction("tiles").objectStore("tiles");
            const total = store.count();
            const oldest = store.index("saved").openKeyCursor();
            oldest.onsuccess = () => resolve([total.result, oldest.result.primaryKey]);
          };
        })""")
        assert count_and_oldest == [384, "1"]
    finally:
        server.shutdown()
        server.server_close()
        from PyQt6 import sip
        sip.delete(page)
        sip.delete(profile)


if __name__ == "__main__":
    run_browser_checks(Path(sys.argv[1]))
