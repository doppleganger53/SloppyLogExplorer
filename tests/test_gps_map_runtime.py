from pathlib import Path
from typing import Any

from tools import validate_gps_map_runtime as runtime


def test_imagery_zoom_creates_missing_capture_directory(tmp_path: Path, monkeypatch: Any) -> None:
    output = tmp_path / "missing" / "nested" / "map.png"
    assert not output.parent.exists()

    def run_js(page: Any, script: str) -> Any:
        if script == "window.sloppyGpsMap.getState();":
            return {"camera": {"maxZoom": 19}, "basemap": {"provider": "naip", "imageryOpacity": 0.8}}
        if "performance.getEntriesByType" in script:
            return []
        return True

    class Image:
        def save(self, filename: str) -> bool:
            path = Path(filename)
            if not path.parent.is_dir():
                return False
            path.write_bytes(b"test capture")
            return True

    class View:
        def grab(self) -> Image:
            return Image()

    monkeypatch.setattr(runtime, "_run_js", run_js)
    monkeypatch.setattr(runtime, "_wait_for_basemap_tiles", lambda *args: None)
    results = runtime._validate_imagery_zoom(object(), View(), output)

    assert [result["zoom"] for result in results] == [16, 17, 18, 19, 21]
    assert len(list(output.parent.glob("map-zoom-*.png"))) == 5
    assert all(Path(result["screenshot"]).is_file() for result in results)
