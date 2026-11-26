"""Exercise the local history document in the same Chromium engine as the HeatMap."""

import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch


def test_history_viewer(tmp_path: Path) -> None:
    # A separate Qt process also keeps WebEngine out of the offscreen widget suite.
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(tmp_path)],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "APPDATA": str(tmp_path)},
        check=True, timeout=45,
    )


def run_viewer(tmp_path: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from PyQt6.QtCore import QPoint, Qt, QUrl
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from sloppy_log_explorer.reception_history_renderer import build_reception_history_html
    from tools.validate_gps_map_runtime import _run_event_loop, _run_js

    app = QApplication(sys.argv)
    view = QWebEngineView()
    profile = QWebEngineProfile(app)
    page = QWebEnginePage(profile, view)
    view.setPage(page)
    view.resize(900, 560)
    view.show()
    channel = "VFR 2.4G(%)"
    comparison = "VFR 900M(%)"
    aircraft = "</script> & aircraft"
    logs = []
    for date, x, model, value in [
        ("2024-01-01", 0, aircraft, 40), ("2024-01-31", 0, aircraft, 100),
        ("2024-09-01", 1, "B", 80),
    ]:
        grid = {channel: [[x, 0, value, 12]], comparison: [[x, 0, 52, 2]]}
        logs.append({"date": date, "model": model, "grids": {"5": grid, "2": grid}})
    comparison_only = {comparison: [[1, 0, 62, 2]]}
    logs.append({"date": "2025-05-01", "model": "B", "grids": {"5": comparison_only, "2": comparison_only}})
    from sloppy_log_explorer.map_basemaps import build_basemap_config
    config = build_basemap_config()
    # Supply all three tile providers locally for the MapLibre control checks.
    png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jMZkAAAAASUVORK5CYII="
    for source in config["sources"].values():
        source["tiles"] = [png]
    with patch("sloppy_log_explorer.reception_history_renderer.build_basemap_config", return_value=config):
        html = build_reception_history_html({"logs": logs, "center": [39, -75], "basemap": "offline"})
    assert aircraft not in html
    path = tmp_path / "view.html"
    path.write_text(html, encoding="utf-8")
    page.load(QUrl.fromLocalFile(str(path.resolve())))

    def js(expression):
        return _run_js(page, expression)

    for _ in range(100):
        _run_event_loop(50)
        if js("window.sloppyReceptionMap?.getState().ready"):
            break
    assert js("window.sloppyReceptionMap?.getState().ready")
    check_basemaps(js, _run_event_loop, app.platformName() != "offscreen")
    if app.platformName() != "offscreen":
        return
    assert js("document.getElementById('aircraft').options[1].text") == aircraft
    assert js("selection.channel") == channel
    assert js("series.length") == 2
    assert js("prepared.chartPeriods.length") == 4
    assert js("prepared.periods.length") == 3

    def control_point(element_id, fraction):
        rect = js(f"""(() => {{
            const r = document.getElementById('{element_id}').getBoundingClientRect();
            return [r.x, r.y + r.height / 2, r.width];
        }})()""")
        return QPoint(round(rect[0] + rect[2] * fraction), round(rect[1]))

    surface = view.focusProxy()
    QTest.mousePress(surface, Qt.MouseButton.LeftButton, pos=control_point("scrub", .15))
    QTest.mouseMove(surface, control_point("scrub", .7))
    _run_event_loop(80)
    forward = js("position")
    assert 1 < forward < 2
    QTest.mouseMove(surface, control_point("scrub", .25))
    _run_event_loop(80)
    assert 0 < js("position") < forward
    QTest.mouseRelease(surface, Qt.MouseButton.LeftButton, pos=control_point("scrub", .25))

    # The earlier two dates are weeks apart; September is eight months away.
    QTest.mousePress(surface, Qt.MouseButton.LeftButton, pos=control_point("chart", .08))
    QTest.mouseMove(surface, control_point("chart", .35))
    _run_event_loop(80)
    assert 1 < js("position") < 2
    QTest.mouseRelease(surface, Qt.MouseButton.LeftButton, pos=control_point("chart", .35))

    js("$('scrub').value=150; $('scrub').dispatchEvent(new Event('input'))")
    assert js("position") == 1.5
    assert js("$('frame-label').textContent") == "2024-01-31 → 2024-09-01 · 50% blend"

    coords = js("""(() => {
        const r = mapCanvas.getBoundingClientRect();
        return [r.x+r.width/2-centerX*scale, r.y+r.height/2+centerY*scale];
    })()""")
    point = QPoint(round(coords[0]), round(coords[1]))
    QTest.mouseMove(view.focusProxy(), point)
    for _ in range(20):
        _run_event_loop(40)
        tooltip = js("$('maptip').textContent")
        if "No reading" in tooltip:
            break
    assert "5 m cell" in tooltip and "100 %" in tooltip and "No reading" in tooltip, (
        tooltip, coords, js("hover && [hover.clientX,hover.clientY,cellAt(hover),position]"),
    )
    assert "12 samples" in tooltip and "1 recording" in tooltip
    QTest.mouseClick(view.focusProxy(), Qt.MouseButton.LeftButton, pos=point)
    _run_event_loop(80)
    assert js("$('clear-pin').hidden") is False
    assert js("window.sloppyReceptionMap.getState().pin") == {"x": 0, "y": 0}
    assert js("series[0].points") == [40, 100, None, None]
    js("$('play').click()")
    _run_event_loop(150)
    assert js("position > 1.5 && window.sloppyReceptionMap.getState().playing")
    js("$('play').click()")
    js("$('prev').click(); $('history').click(); $('half-life').value='30'; $('half-life').onchange()")
    assert js("series[0].points") == [40, 80, 80, 80]
    assert js("position") == 1
    js("$('date').value='2024-01-01'; $('date').onchange()")
    assert js("prepared.frameAt(0).get('0,0').value") == 40
    js("$('history').click(); $('aircraft').value='B'; $('aircraft').onchange()")
    assert js("$('scrub').disabled && $('play').disabled")
    assert js("prepared.chartPeriods.length") == 2
    assert js("prepared.frameAt(0).has('0,0')") is False
    assert js("$('frame-label').textContent") == "2024-09-01"

def check_basemaps(js, run_event_loop, native: bool) -> None:
    # The native controls can change opacity before the first online basemap opens.
    js("sloppyReceptionMap.setImageryOpacity(.38)")
    for _ in range(60):
        if js("Boolean(sloppyReceptionMap.getState().tiles)"):
            break
        run_event_loop(50)
    assert js("sloppyReceptionMap.getState().tiles.imageryOpacity") == .38
    # The CI suite uses the offscreen Qt plugin, which cannot guarantee WebGL.
    # Running this file directly on a desktop also checks both live raster layers.
    if not native:
        return
    js("sloppyReceptionMap.setBasemap('imagery')")
    layers = ("nasa-gibs-raster-base", "usgs-naip-raster-base")
    for _ in range(60):
        if js(f"Boolean(window.__sloppyReceptionDebugMap?.getLayer('{layers[1]}'))"):
            break
        run_event_loop(50)
    for layer in layers:
        assert js(f"window.__sloppyReceptionDebugMap?.getPaintProperty('{layer}','raster-opacity')") == .38
    js("sloppyReceptionMap.setImageryOpacity(.71)")
    for layer in layers:
        assert js(f"window.__sloppyReceptionDebugMap.getPaintProperty('{layer}','raster-opacity')") == .71
    js("sloppyReceptionMap.setBasemap('osm')")
    for layer in layers:
        assert js(f"window.__sloppyReceptionDebugMap.getLayoutProperty('{layer}','visibility')") == "none"
    js("sloppyReceptionMap.setBasemap('offline')")
    assert js("window.__sloppyReceptionDebugMap === null")


if __name__ == "__main__":
    run_viewer(Path(sys.argv[1]))
