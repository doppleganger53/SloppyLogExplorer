from __future__ import annotations

import json

import pytest
from PyQt6.QtQml import QJSEngine
from PyQt6.QtWidgets import QApplication

from sloppy_log_explorer.gps_measurement import MEASUREMENT_RUNTIME_JAVASCRIPT


def evaluate_measurement(script: str) -> dict:
    app = QApplication.instance() or QApplication([])
    engine = QJSEngine()
    mocks = """
      const document = {createElement: () => ({setAttribute() {}, addEventListener() {}})};
      const maplibregl = {Marker: class {
        constructor(options) {this.element = options.element;}
        setLngLat(value) {this.coordinate = value; return this;}
        addTo() {return this;}
        on() {return this;}
        remove() {}
        getElement() {return this.element;}
        setDraggable() {}
      }};
      let line = null, zoomEnabled = true;
      const canvas = {style: {cursor: ''}};
      const map = {
        getSource: () => ({setData(value) {line = value;}}),
        getCanvas: () => canvas,
        doubleClickZoom: {
          isEnabled: () => zoomEnabled,
          disable() {zoomEnabled = false;},
          enable() {zoomEnabled = true;}
        }
      };
    """
    result = engine.evaluate(mocks + MEASUREMENT_RUNTIME_JAVASCRIPT + script)
    assert not result.isError(), result.toString()
    assert app is not None
    return json.loads(result.toString())


def test_measurement_geodesic_distance_across_equator_and_antimeridian() -> None:
    result = evaluate_measurement("""
      JSON.stringify({equator: groundDistanceMeters([0,0],[1,0]),
        crossing: groundDistanceMeters([179.999,0],[-179.999,0]),
        identical: groundDistanceMeters([30,60],[30,60])});
    """)
    assert result["equator"] == pytest.approx(111195.08, abs=0.02)
    assert result["crossing"] == pytest.approx(222.39, abs=0.01)
    assert result["identical"] == 0


def test_measurement_accumulates_moves_removes_and_clears_points() -> None:
    result = evaluate_measurement("""
      const disabledAdd = addMeasurementPoint([0,0]);
      setMeasurementActive(true);
      addMeasurementPoint([0,0]); addMeasurementPoint([1,0]); addMeasurementPoint([2,0]);
      const original = measurementState();
      moveMeasurementPoint(1,[0.5,0]);
      const moved = measurementState();
      removeMeasurementPoint(2);
      const removed = measurementState();
      const invalid = moveMeasurementPoint(4,[0,0]);
      setMeasurementActive(false);
      const finished = measurementState();
      const restoredZoom = zoomEnabled;
      clearMeasurement();
      JSON.stringify({disabledAdd,original,moved,removed,invalid,finished,restoredZoom,
        cleared:measurementState(),line});
    """)
    assert result["disabledAdd"] is False
    assert result["original"]["meters"] == pytest.approx(222390.16, abs=0.02)
    assert result["moved"]["meters"] == pytest.approx(result["original"]["meters"])
    assert result["removed"]["meters"] == pytest.approx(55597.54, abs=0.02)
    assert result["invalid"] is False
    assert result["finished"]["active"] is False
    assert len(result["finished"]["points"]) == 2
    assert result["restoredZoom"] is True
    assert result["cleared"] == {"active": False, "points": [], "meters": 0}
    assert result["line"]["features"] == []


def test_measurement_short_line_at_antimeridian_and_preserves_disabled_zoom() -> None:
    result = evaluate_measurement("""
      zoomEnabled = false;
      setMeasurementActive(true);
      addMeasurementPoint([179.999,0]); addMeasurementPoint([-179.999,0]);
      const coordinates = line.features[0].geometry.coordinates;
      setMeasurementActive(false);
      JSON.stringify({coordinates,zoomEnabled});
    """)
    assert result["coordinates"][1][0] - result["coordinates"][0][0] == pytest.approx(0.002)
    assert result["zoomEnabled"] is False


def test_measurement_formats_units_and_rejects_invalid_coordinates() -> None:
    result = evaluate_measurement("""
      setMeasurementActive(true);
      JSON.stringify({short:formatGroundDistance(100),long:formatGroundDistance(2000),
        invalid:addMeasurementPoint([NaN,30]),polar:addMeasurementPoint([0,90]),
        missing:addMeasurementPoint([0]),state:measurementState()});
    """)
    assert result["short"] == "100.0 m / 328.1 ft"
    assert result["long"] == "2.00 km / 1.24 mi"
    assert result["invalid"] is False
    assert result["polar"] is False
    assert result["missing"] is False
    assert result["state"]["points"] == []
