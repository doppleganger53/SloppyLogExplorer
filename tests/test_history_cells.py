from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from PyQt6.QtQml import QJSEngine
from PyQt6.QtWidgets import QApplication

from sloppy_log_explorer.history_cells import bin_history_recording
from sloppy_log_explorer.history_sources import build_site_history
from sloppy_log_explorer.parser import load_log
from sloppy_log_explorer.reception import (
    EARTH_RADIUS_METERS,
    build_reception_heatmap,
    index_log,
    reception_cell_key,
)


def test_both_grids_bin_original_paired_rows_like_the_heatmap(tmp_path: Path) -> None:
    path = tmp_path / "recording.csv"
    path.write_text(
        "Date,Time,GPS Lat,GPS Lon,VFR 2.4G(%),VFR 2.4G(%),RSSI(dB),Throttle(%)\n"
        "2025-01-01,10:00:00,40,-75,20,60,,20\n"
        "2025-01-01,10:00:01,40.000016,-75,100,,35,20\n"
        "2025-01-01,10:00:02,40.000016,-75,80,100,45,20\n"
        "2025-01-01,10:00:03,0,0,0,0,99,20\n",
        encoding="utf-8",
    )
    channel = "VFR 2.4G(%)"
    center = (40, -75)
    grids = bin_history_recording(load_log(path), center)
    assert grids == {
        "5": {channel: [[0, 0, 90, 3]], "RSSI(dB)": [[0, 0, 40, 2]]},
        "2": {channel: [[0, 0, 40, 1], [0, 1, 95, 2]], "RSSI(dB)": [[0, 1, 40, 2]]},
    }
    for size, channels in grids.items():
        for name, rows in channels.items():
            static = build_reception_heatmap([index_log(path, tmp_path)], name, center, float(size))
            assert [row[2:] for row in rows] == [
                [cell["value"], cell["sample_count"]] for cell in static["cells"]
            ]


def test_history_filters_the_ten_kilometer_boundary_before_binning(tmp_path: Path) -> None:
    center = (40.0, -75.0)
    origin = (40.001, -75.0)
    edge = center[0] + math.degrees(10_000 / EARTH_RADIUS_METERS)
    outside = edge + math.degrees(0.01 / EARTH_RADIUS_METERS)
    path = tmp_path / "edge.csv"
    path.write_text(
        "Date,Time,GPS Lat,GPS Lon,VFR(%)\n"
        f"2025-01-01,10:00:00,{edge},-75,20\n"
        f"2025-01-01,10:00:01,{outside},-75,80\n"
    )
    grids = bin_history_recording(load_log(path), center, grid_origin=origin)
    for size in (2, 5):
        key = reception_cell_key(edge, -75, origin, size)
        assert key == reception_cell_key(outside, -75, origin, size)
        assert grids[str(size)]["VFR(%)"] == [[*key, 20, 1]]


def test_untimed_fixes_require_conclusive_speed_evidence_to_exclude(tmp_path: Path) -> None:
    site = (40.0, -75.0)
    path = tmp_path / "sparse-clock.csv"
    # Local fixes at 0 and 1 s rule out a 10 km detour. A short detour fits
    # between them. No elapsed-time bound exists at the ends or across a reset.
    samples = [("", 40.03), (0, 40.0), ("", 40.045), ("", 40.0001), (1, 40.0),
               ("", 40.02), (0, 40.0), (1, 40.0), ("", 40.01)]
    path.write_text("Time,GPS Lat,GPS Lon,VFR(%)\n" + "".join(
        f"{time},{latitude},-75,80\n" for time, latitude in samples
    ))
    grids = bin_history_recording(load_log(path), site)
    for size in (2, 5):
        cells = grids[str(size)]["VFR(%)"]
        counts = {(x, y): count for x, y, _, count in cells}
        assert counts == {
            reception_cell_key(latitude, -75.0, site, size): n
            for latitude, n in [(40.03, 1), (40.0, 4), (40.0001, 1), (40.02, 1), (40.01, 1)]
        }


def _model_result(script: str) -> dict | list:
    app = QApplication.instance() or QApplication([])
    engine = QJSEngine()
    model = Path(__file__).resolve().parents[1] / "sloppy_log_explorer/assets/history-model.js"
    dataset = """
        const signal = "VFR 2.4G(%)", other = "VFR 900M(%)", wide = "Rx VFR(%)";
        function recording(date, model, channels) {
          return {date, model, grids: {"5": channels, "2": channels}};
        }
        const model = new ReceptionHistoryModel({logs: [
          recording("2025-01-31", "A", {[signal]: [[0,0,100,1]], [other]: [[0,0,85,3]]}),
          recording("2025-12-01", "B", {[wide]: [[2,0,99,1]], "RSSI(dB)": [[0,0,20,1]]}),
          recording("2025-01-01", "A", {
            [signal]: [[0,0,40,100], [1,0,45,1]], [other]: [[0,0,50,4]],
            "VFR LoRa(%)": [[0,0,75,1]], "RSSI 900M(dB)": [[0,0,22,1]],
          }),
          recording("2025-01-01", "C", {
            [signal]: [[0,0,10,1]], "VFR 900M(dB)": [[0,0,90,2]],
            "Spektrum A fades (/s)": [[0,0,0,1],[1,1,10,40]],
            "Spektrum B fades (/s)": [[0,0,50,3]], "Spektrum holds (/s)": [[0,0,1,1]],
          }),
        ]});
    """
    result = engine.evaluate(model.read_text(encoding="utf-8") + dataset + script)
    assert not result.isError(), (result.toString(), result.property("lineNumber").toInt())
    assert app is not None
    return json.loads(result.toString())


def test_rejected_fixes_leave_history_cells_colors_and_trends_unchanged(tmp_path: Path) -> None:
    center = (40.0, -75.0)
    origin = (40.001, -75.0)
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    header = "Date,Time,GPS Lat,GPS Lon,VFR(%)\n"
    start = "2025-01-01,10:00:00,40,-75,20\n"
    end = "2025-01-01,10:00:02,40,-75,60\n"
    first.write_text(header + start + end)
    second.write_text(header + "2025-01-02,10:00:00,40.0002,-75,80\n"
                               "2025-01-02,10:00:01,40.0002,-75,80\n")

    def views():
        records = [index_log(path, tmp_path) for path in (first, second)]
        payload = build_site_history(records, center, grid_origin=origin)
        assert payload["counts"] == {"included": 2}
        return _model_result("const actual = new ReceptionHistoryModel(" + json.dumps(payload) + """);
            JSON.stringify([2, 5].map(cellSize => {
                const view = actual.prepare({channel: "VFR(%)", cellSize, history: true});
                return {range: view.range, trend: view.series(), cells: Array.from(view.frameAt(1).values())};
            }));
        """)

    baseline = views()
    for size, view in zip((2, 5), baseline):
        carried, current = sorted(view["cells"], key=lambda cell: cell["lastSeen"])
        assert carried["carried"] and (carried["value"], carried["samples"]) == (40, 2)
        assert (carried["x"], carried["y"]) == reception_cell_key(*center, origin, size)
        assert not current["carried"] and (current["value"], current["samples"]) == (80, 2)
        assert view["range"] == [42, 78]
        assert view["trend"] == [{"name": "VFR(%)", "points": [40, 44]}]

    # The first stray implies >300 mph. The second is reachable in two minutes
    # and inside the origin's radius, but beyond 10 km from the current site.
    first.write_text(header + start + "2025-01-01,10:00:01,40.003,-75,99\n" + end
                     + "2025-01-01,10:02:00,40.0903,-75,1\n")
    assert views() == baseline


@pytest.mark.parametrize(("values", "expected"), [
    ([25.2 + n for n in range(24)] + [900], [26, 48]),
    ([40] * 25, [39, 41]),
])
def test_history_and_combined_use_the_same_auto_gradient(tmp_path, values, expected):
    path = tmp_path / "flight.csv"
    path.write_text(
        "Date,Time,GPS Lat,GPS Lon,VFR(%),Offset\n"
        + "".join(f"2025-01-01,10:00:{i:02d},{40+i*.0001:.6f},-75,{value},10\n"
                  for i, value in enumerate(values)),
        encoding="utf-8",
    )
    center = (40, -75)
    records = [index_log(path, tmp_path)]
    combined = build_reception_heatmap(records, "VFR(%)", center)
    grids = bin_history_recording(load_log(path), center)
    history_range = _model_result(
        "const history = new ReceptionHistoryModel({logs:[recording('2025-01-01','A',"
        + json.dumps(grids["5"]) + ")]}); JSON.stringify(history.prepare({channel:'VFR(%)'}).range);"
    )
    assert list(combined["auto_color_range"]) == history_range == expected
    assert max(cell["value"] for cell in combined["cells"]) == max(values)
    normalized = build_reception_heatmap(
        records, "VFR(%)", center, reference_column="Offset", normalization_mode="difference"
    )
    assert list(normalized["auto_color_range"]) == [bound - 10 for bound in expected]


def test_current_period_equal_recording_weight_comparable_trends_and_missing_cells() -> None:
    result = _model_result("""
        const view = model.prepare({channel: signal});
        const losses = model.prepare({channel: "Spektrum A fades (/s)"});
        const month = model.prepare({channel: signal, granularity: "month"});
        const wideView = model.prepare({channel: wide});
        JSON.stringify({
          periods: view.periods, chartPeriods: view.chartPeriods, cell: view.frameAt(0).get("0,0"),
          recordings: view.recordingCounts, series: view.series(),
          pin: view.series({x: 0,y: 0}), month: month.frameAt(0).get("0,0").value,
          loss: losses.loss, lossSeries: losses.series(), links: model.links("A"),
          widePeriods: wideView.periods, wideChart: wideView.chartPeriods,
          single: model.prepare({channel: signal, aircraft:"A", link:"2.4 GHz + 900 MHz"}).periods,
        });
    """)
    assert result["periods"] == ["2025-01-01", "2025-01-31"]
    assert result["chartPeriods"] == ["2025-01-01", "2025-01-31", "2025-12-01"]
    assert result["cell"]["value"] == 25
    assert (result["cell"]["logs"], result["cell"]["samples"]) == (2, 101)
    assert result["recordings"] == [2, 1]
    assert result["month"] == 40
    trends = {item["name"]: item["points"] for item in result["series"]}
    assert set(trends) == {"VFR 2.4G(%)", "VFR 900M(%)", "Rx VFR(%)", "VFR LoRa(%)"}
    assert trends["VFR 2.4G(%)"] == [27, 100, None]
    assert {i["name"]: i["points"] for i in result["pin"]}["VFR 2.4G(%)"] == [25, 100, None]
    assert result["loss"] is True
    assert result["lossSeries"] == [
        {"name": "Spektrum A fades (/s)", "points": [9]},
        {"name": "Spektrum B fades (/s)", "points": [50]},
    ]
    assert result["links"] == ["2.4 GHz + 900 MHz", "2.4 GHz + 900 MHz + LoRa"]
    assert result["single"] == ["2025-01-31"]
    assert result["widePeriods"] == ["2025-12-01"] and result["wideChart"] == result["chartPeriods"]


def test_elapsed_day_half_life_rewind_and_filters() -> None:
    result = _model_result("""
        const filters = {channel: signal, aircraft:"A", history:true, halfLife:30};
        const view = model.prepare(filters);
        const last = view.frameAt(1);
        const month = model.prepare(Object.assign({}, filters, {granularity:"month"}));
        const switched = model.prepare(Object.assign({}, filters, {channel:other}));
        JSON.stringify({
          observed: last.get("0,0"), carried: last.get("1,0"), rewind: view.frameAt(0).get("0,0"),
          pin: view.series({x:0,y:0}), monthly: month.frameAt(0).get("0,0").value,
          switched: switched.frameAt(1).get("0,0").value,
          cached: last === view.frameAt(1), range: view.range,
          unfilteredRange: model.prepare({channel:signal}).range,
        });
    """)
    assert result["observed"]["value"] == 80
    assert result["observed"]["samples"] == 101
    assert result["observed"]["logs"] == 2
    assert result["carried"]["value"] == 45 and result["carried"]["carried"]
    assert result["carried"]["lastSeen"] == "2025-01-01"
    assert result["rewind"]["value"] == 40 and not result["rewind"]["carried"]
    assert result["monthly"] == 80
    assert result["switched"] == pytest.approx((50 * .5 + 85) / 1.5)
    assert result["cached"] and result["range"] == result["unfilteredRange"]
    assert {item["name"]: item["points"] for item in result["pin"]}["VFR 2.4G(%)"] == [40, 80]
