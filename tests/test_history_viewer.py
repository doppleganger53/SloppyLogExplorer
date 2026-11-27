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
    from PyQt6.QtCore import QPoint, Qt
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
    check_fit_site(view, tmp_path)
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
    open_document(view, tmp_path / "view.html", html)

    def js(expression):
        return _run_js(page, expression)

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
    def wait_for_position(low, high):
        for _ in range(50):
            _run_event_loop(40)
            position = js("position")
            if low < position < high:
                return position
        raise AssertionError(f"Expected position between {low} and {high}, got {position}")

    QTest.mousePress(surface, Qt.MouseButton.LeftButton, pos=control_point("scrub", .15))
    move_pointer(surface, control_point("scrub", .7), Qt.MouseButton.LeftButton)
    forward = wait_for_position(1, 2)
    move_pointer(surface, control_point("scrub", .25), Qt.MouseButton.LeftButton)
    wait_for_position(0, forward)
    QTest.mouseRelease(surface, Qt.MouseButton.LeftButton, pos=control_point("scrub", .25))

    # The earlier two dates are weeks apart; September is eight months away.
    QTest.mousePress(surface, Qt.MouseButton.LeftButton, pos=control_point("chart", .08))
    move_pointer(surface, control_point("chart", .35), Qt.MouseButton.LeftButton)
    wait_for_position(1, 2)
    QTest.mouseRelease(surface, Qt.MouseButton.LeftButton, pos=control_point("chart", .35))

    js("$('scrub').value=150; $('scrub').dispatchEvent(new Event('input'))")
    assert js("position") == 1.5
    assert js("$('frame-label').textContent") == "2024-01-31 → 2024-09-01 · 50% blend"

    coords = js("""(() => {
        const r = mapCanvas.getBoundingClientRect();
        return [r.x+r.width/2-centerX*scale, r.y+r.height/2+centerY*scale];
    })()""")
    point = QPoint(round(coords[0]), round(coords[1]))
    move_pointer(surface, point, Qt.MouseButton.NoButton)
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
    automatic = js("getState().range")
    painted = js("mapCanvas.toDataURL()")
    js("$('auto-range').click(); $('range-min').value = 0; $('range-min').oninput();"
       "$('range-max').value = 200; $('range-max').oninput()")
    assert js("getState().range") == [0, 200]
    assert js("mapCanvas.toDataURL()") != painted
    assert js("[$('low').textContent,$('high').textContent]") == ["0 %", "200 %"]
    assert js("series[0].points") == [40, 100, None, None]
    js("$('range-max').value = -10; $('range-max').oninput()")
    assert js("!$('range-error').hidden && getState().range[1] === 200")
    js("$('range-max').value = 200; $('range-max').oninput()")
    js("$('play').click()")
    _run_event_loop(150)
    assert js("position > 1.5 && window.sloppyReceptionMap.getState().playing")
    js("$('play').click()")
    js("$('prev').click(); $('history').click(); $('half-life').value='30'; $('half-life').onchange()")
    assert js("getState().range") == [0, 200]
    assert js("series[0].points") == [40, 80, 80, 80]
    assert js("position") == 1
    js("$('date').value='2024-01-01'; $('date').onchange()")
    assert js("prepared.frameAt(0).get('0,0').value") == 40
    js("$('auto-range').click()")
    assert js("getState().range") == automatic
    js("$('history').click(); $('aircraft').value='B'; $('aircraft').onchange()")
    assert js("$('scrub').disabled && $('play').disabled")
    assert js("prepared.chartPeriods.length") == 2
    assert js("prepared.frameAt(0).has('0,0')") is False
    assert js("$('frame-label').textContent") == "2024-09-01"


def open_document(view, path: Path, html: str) -> None:
    from PyQt6.QtCore import QUrl
    from PyQt6.QtTest import QSignalSpy, QTest
    from tools.validate_gps_map_runtime import _run_js

    path.write_text(html, encoding="utf-8")
    loaded = QSignalSpy(view.loadFinished)
    view.load(QUrl.fromLocalFile(str(path.resolve())))
    assert loaded.wait(10_000) and loaded[0][0]
    for _ in range(100):
        if _run_js(view.page(), "window.sloppyReceptionMap?.getState().ready"):
            return
        QTest.qWait(50)
    raise AssertionError(f"History viewer did not become ready for {path.name}")


def move_pointer(surface, point, buttons) -> None:
    from PyQt6.QtCore import QEvent, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtWidgets import QApplication

    # Target the browser surface directly. OS cursor moves can land in
    # another window while this test runs in the desktop suite.
    event = QMouseEvent(
        QEvent.Type.MouseMove, QPointF(point), QPointF(surface.mapToGlobal(point)),
        Qt.MouseButton.NoButton, buttons, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(surface, event)


def check_fit_site(view, tmp_path: Path) -> None:
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtTest import QTest
    from sloppy_log_explorer.history_sources import build_site_history
    from sloppy_log_explorer.reception import index_log
    from sloppy_log_explorer.reception_history_renderer import build_reception_history_html
    from tools.validate_gps_map_runtime import _run_js

    center, origin = (40, -75), (40.009, -75)
    local = ["12:00:00,40,-75,70", "12:00:01,40.0002,-74.9998,74",
             "12:00:02,40.0004,-74.9996,78", "12:00:04,40.0006,-74.9994,82"]
    # The first stray requires an impossible jump. The second is reachable
    # at the recorded time but is over 10 km from the current site center.
    strays = ["12:00:03,40.01,-75,0", "12:04:00,40.0915,-75,0"]

    def js(code):
        return _run_js(view.page(), code)

    def midpoint(element):
        x, y = js(f"(() => {{ const r = $('{element}').getBoundingClientRect(); "
                  "return [r.x + r.width / 2, r.y + r.height / 2]; })()")
        return QPoint(round(x), round(y))

    def wait_for_camera(matches):
        for _ in range(60):
            camera = js("sloppyReceptionMap.getState().camera")
            if matches(camera):
                return
            QTest.qWait(20)
        raise AssertionError(f"Camera did not reach the expected position: {camera}")

    def change_height(delta):
        before = js("mapCanvas.height")
        view.resize(view.width(), view.height() + delta)
        for _ in range(60):
            if js("mapCanvas.height") != before:
                return
            QTest.qWait(20)
        raise AssertionError("History map did not resize")

    cameras = []
    for name, rows in (("local", local), ("with-strays", sorted(local + strays))):
        library = tmp_path / name
        library.mkdir(exist_ok=True)
        path = library / "Glider-2025-01-01.csv"
        header = "Date,Time,GPS Lat,GPS Lon,VFR(%)\n"
        path.write_text(header +
                        "".join(f"2025-01-01,{row}\n" for row in rows), encoding="utf-8")
        # A prior flight also reported plausible fixes about 6 km away. Keep
        # those observations available while framing the field on opening.
        older = library / "Glider-2024-12-31.csv"
        earlier = list(local)
        if name == "with-strays":
            earlier.extend(["12:04:00,40.055,-75,82", "12:04:01,40.0552,-75,84"])
        older.write_text(header + "".join(f"2024-12-31,{row}\n" for row in earlier), encoding="utf-8")
        records = [index_log(p, library) for p in (older, path)]
        payload = build_site_history(records, center, grid_origin=origin)
        html = build_reception_history_html({**payload, "center": origin, "basemap": "offline"})
        open_document(view, tmp_path / f"{name}.html", html)
        state = js("sloppyReceptionMap.getState()")
        assert js("prepared.frameAt(0).size") == len(earlier)
        assert state["cellCount"] == len(local)
        opening = state["camera"]
        assert 0 < js("mapCanvas.clientHeight") / opening["scale"] < 200
        assert js("""[...prepared.frameAt(Math.round(position)).values()].every(cell =>
            Math.abs(cell.x * selection.cellSize - centerX) * scale < mapCanvas.clientWidth / 2 &&
            Math.abs(cell.y * selection.cellSize - centerY) * scale < mapCanvas.clientHeight / 2)""")
        cameras.append(opening)

        change_height(80)
        wait_for_camera(lambda p: p["scale"] > opening["scale"])
        change_height(-80)
        wait_for_camera(lambda p: p == opening)

        start = midpoint("map")
        end = start + QPoint(65, 35)
        surface = view.focusProxy()
        QTest.mousePress(surface, Qt.MouseButton.LeftButton, pos=start)
        move_pointer(surface, end, Qt.MouseButton.LeftButton)
        QTest.mouseRelease(surface, Qt.MouseButton.LeftButton, pos=end)
        wait_for_camera(lambda p: p["x"] != opening["x"] and p["y"] != opening["y"])
        QTest.mouseClick(surface, Qt.MouseButton.LeftButton, pos=midpoint("zoom-out"))
        wait_for_camera(lambda p: p["scale"] < opening["scale"])
        manual = js("sloppyReceptionMap.getState().camera")
        change_height(80)
        js("sloppyReceptionMap.refresh({fit:true})")
        assert js("sloppyReceptionMap.getState().camera") == manual
        change_height(-80)
        QTest.mouseClick(surface, Qt.MouseButton.LeftButton, pos=midpoint("fit"))
        wait_for_camera(lambda p: p == opening)
    assert cameras[0] == cameras[1], cameras


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
