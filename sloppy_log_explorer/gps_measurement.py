"""Flight-map ground-distance measurement (no external geometry dependency)."""

MEASUREMENT_RUNTIME_JAVASCRIPT = r"""
    let measurementActive = false;
    let measurementPoints = [];
    let measurementMarkers = [];
    let measurementControls = null;
    let measurementDoubleClickZoom = false;

    function groundDistanceMeters(left, right) {
      const radians = Math.PI / 180;
      const deltaLat = (right[1] - left[1]) * radians;
      const deltaLon = (right[0] - left[0]) * radians;
      const value = Math.sin(deltaLat / 2) ** 2
        + Math.cos(left[1] * radians) * Math.cos(right[1] * radians)
        * Math.sin(deltaLon / 2) ** 2;
      return 6371008.8 * 2 * Math.asin(Math.sqrt(Math.max(0, Math.min(1, value))));
    }

    function measurementState() {
      let meters = 0;
      for (let index = 1; index < measurementPoints.length; index += 1) {
        meters += groundDistanceMeters(measurementPoints[index - 1], measurementPoints[index]);
      }
      return {active: measurementActive, points: measurementPoints.map(point => point.slice()), meters};
    }

    function formatGroundDistance(meters) {
      const metric = meters >= 1000 ? `${(meters / 1000).toFixed(2)} km` : `${meters.toFixed(1)} m`;
      const feet = meters / 0.3048;
      const imperial = feet >= 5280 ? `${(feet / 5280).toFixed(2)} mi` : `${feet.toFixed(1)} ft`;
      return `${metric} / ${imperial}`;
    }

    function updateMeasurement() {
      if (!map || !map.getSource("measurement-line")) return;
      const coordinates = [];
      measurementPoints.forEach(point => {
        let longitude = point[0];
        if (coordinates.length) {
          const previous = coordinates[coordinates.length - 1][0];
          while (longitude - previous > 180) longitude -= 360;
          while (longitude - previous < -180) longitude += 360;
        }
        coordinates.push([longitude, point[1]]);
      });
      map.getSource("measurement-line").setData({
        type: "FeatureCollection",
        features: coordinates.length > 1 ? [{type: "Feature", properties: {},
          geometry: {type: "LineString", coordinates}}] : []
      });
      measurementMarkers.forEach((marker, index) => {
        marker.getElement().textContent = String(index + 1);
        marker.getElement().title = `Point ${index + 1}: drag to move, click to remove`;
        marker.setDraggable(measurementActive);
      });
      if (measurementControls) {
        const state = measurementState();
        measurementControls.querySelector("[data-measure='toggle']").setAttribute("aria-pressed", String(state.active));
        measurementControls.querySelector("[data-measure='toggle']").textContent = state.active ? "Done" : "Measure";
        measurementControls.querySelector("[data-measure='undo']").disabled = !state.points.length;
        measurementControls.querySelector("[data-measure='clear']").disabled = !state.points.length;
        measurementControls.querySelector("output").textContent = formatGroundDistance(state.meters);
        measurementControls.querySelector("[data-measure-hint]").textContent = state.active
          ? "Click map to add points; drag to move."
          : "Ground distance";
      }
    }

    function setMeasurementActive(active) {
      const requested = Boolean(active);
      if (requested !== measurementActive) {
        if (requested) {
          measurementDoubleClickZoom = map.doubleClickZoom.isEnabled();
          map.doubleClickZoom.disable();
        } else if (measurementDoubleClickZoom) {
          map.doubleClickZoom.enable();
        }
        measurementActive = requested;
        map.getCanvas().style.cursor = requested ? "crosshair" : "";
      }
      updateMeasurement();
      return measurementState();
    }

    function moveMeasurementPoint(index, coordinate) {
      if (!Number.isInteger(index) || index < 0 || index >= measurementPoints.length) return false;
      if (!Array.isArray(coordinate) || coordinate.length !== 2
          || !coordinate.every(Number.isFinite) || Math.abs(coordinate[1]) > 85.051129) return false;
      measurementPoints[index] = coordinate.slice();
      measurementMarkers[index].setLngLat(coordinate);
      updateMeasurement();
      return true;
    }

    function removeMeasurementPoint(index) {
      if (!Number.isInteger(index) || index < 0 || index >= measurementPoints.length) return false;
      measurementMarkers[index].remove();
      measurementMarkers.splice(index, 1);
      measurementPoints.splice(index, 1);
      updateMeasurement();
      return true;
    }

    function clearMeasurement() {
      measurementMarkers.forEach(marker => marker.remove());
      measurementMarkers = [];
      measurementPoints = [];
      updateMeasurement();
      return measurementState();
    }

    function addMeasurementPoint(coordinate) {
      if (!measurementActive || !Array.isArray(coordinate) || coordinate.length !== 2
          || !coordinate.every(Number.isFinite) || Math.abs(coordinate[1]) > 85.051129) return false;
      const element = document.createElement("button");
      element.type = "button";
      element.className = "measurement-point";
      element.setAttribute("aria-label", "Measurement point: drag to move, click to remove");
      const marker = new maplibregl.Marker({element, draggable: true}).setLngLat(coordinate).addTo(map);
      let dragFinished = 0;
      marker.on("drag", () => {
        const index = measurementMarkers.indexOf(marker);
        if (index >= 0) measurementPoints[index] = marker.getLngLat().toArray();
        updateMeasurement();
      });
      marker.on("dragend", () => {dragFinished = Date.now();});
      element.addEventListener("click", event => {
        event.stopPropagation();
        if (measurementActive && Date.now() - dragFinished > 250) {
          removeMeasurementPoint(measurementMarkers.indexOf(marker));
        }
      });
      measurementPoints.push(coordinate.slice());
      measurementMarkers.push(marker);
      updateMeasurement();
      return true;
    }

    function initializeMeasurement() {
      map.addSource("measurement-line", {type: "geojson", data: {type: "FeatureCollection", features: []}});
      map.addLayer({id: "measurement-halo", type: "line", source: "measurement-line",
        paint: {"line-color": "#111827", "line-width": 5}});
      map.addLayer({id: "measurement-path", type: "line", source: "measurement-line",
        paint: {"line-color": "#fbbf24", "line-width": 2.5}});
      map.addControl({
        onAdd() {
          measurementControls = document.createElement("div");
          measurementControls.id = "measurementControls";
          measurementControls.className = "maplibregl-ctrl";
          measurementControls.innerHTML = '<div><button type="button" data-measure="toggle" aria-pressed="false" title="Click map to add points. Drag points to move, click points to remove. Esc to finish.">Measure</button>'
            + '<button type="button" data-measure="undo" disabled>Undo</button>'
            + '<button type="button" data-measure="clear" disabled>Clear</button></div>'
            + '<output aria-live="polite" aria-label="Measured ground distance">0.0 m / 0.0 ft</output>'
            + '<div data-measure-hint>Ground distance</div>';
          measurementControls.addEventListener("click", event => {
            event.stopPropagation();
            const button = event.target.closest("button[data-measure]");
            if (!button) return;
            const action = button.dataset.measure;
            if (action === "toggle") setMeasurementActive(!measurementActive);
            if (action === "undo") removeMeasurementPoint(measurementPoints.length - 1);
            if (action === "clear") clearMeasurement();
          });
          return measurementControls;
        },
        onRemove() {
          measurementControls.remove();
          measurementControls = null;
          measurementMarkers.forEach(marker => marker.remove());
        }
      }, "top-left");
      map.on("click", event => {
        if (measurementActive && (!event.originalEvent || event.originalEvent.detail < 2)) {
          addMeasurementPoint(event.lngLat.toArray());
        }
      });
      document.addEventListener("keydown", event => {
        if (event.key === "Escape" && measurementActive) {
          setMeasurementActive(false);
          event.preventDefault();
        }
      });
      updateMeasurement();
    }
"""
