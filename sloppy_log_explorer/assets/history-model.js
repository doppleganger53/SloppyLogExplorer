// A site dataset contains recording medians at each resolution. Prepare period changes
// once per filter selection so playback never reloads a recording or recomputes a trend.
var ReceptionHistoryModel = (() => {
  const ordered = values => Array.from(new Set(values)).sort();

  function quantile(sorted, fraction) {
    if (!sorted.length) return null;
    const position = (sorted.length - 1) * fraction;
    const lower = Math.floor(position);
    return sorted[lower] + (sorted[Math.ceil(position)] - sorted[lower]) * (position - lower);
  }

  function channelNames(records) {
    const names = new Set();
    for (const record of records) {
      for (const name of record.names) names.add(name);
    }
    return Array.from(names).sort();
  }

  function channelInfo(name) {
    const unitMatch = name.match(/\(([^()]*)\)\s*(?:\.\d+)?$/);
    const unit = unitMatch ? unitMatch[1].trim() : "";
    const metric = [
      [/\b(?:rx|tx)?vfr\b/i, "VFR"],
      [/\b(?:(?:rx|tx)?rssi\d*|[12]rss|[rt]rss|signal strength)\b/i, "RSSI"],
      [/\b(?:[rt]?snr)\b/i, "SNR"],
      [/\b(?:[rt]qly|link quality|lq)\b/i, "link quality"],
      [/\bfades\b/i, "fades"],
      [/\bframe losses\b/i, "frame losses"],
      [/\bholds\b/i, "holds"],
    ].find(([pattern]) => pattern.test(name));
    const kind = metric ? metric[1] : name;
    return {
      unit,
      // Without recorded units, compare only identical headers.
      family: unit ? kind + "|" + unit.toLowerCase() : name,
      loss: ["fades", "frame losses", "holds"].includes(kind),
    };
  }

  function recordedLink(names) {
    if (names.some(name => /^Spektrum\s/i.test(name))) return "Spektrum";
    const bands = [
      [/\b(?:2[.,]4(?:\s*g(?:hz)?)?|2g4)\b/i, "2.4 GHz"],
      [/\b900(?:\s*m(?:hz)?)?\b/i, "900 MHz"],
      [/\blo\s*ra\b/i, "LoRa"],
    ].filter(([pattern]) => names.some(name => pattern.test(name))).map(([, label]) => label);
    return bands.length ? bands.join(" + ") : "Unspecified";
  }

  function periodChanges(records, size, channel) {
    const cells = new Map();
    for (const record of records) {
      for (const [x, y, value, samples] of record.grids[size][channel] || []) {
        const key = x + "," + y;
        if (!cells.has(key)) cells.set(key, {x, y, values: [], samples: 0});
        const cell = cells.get(key);
        cell.values.push(value);
        cell.samples += samples;
        cell.lastSeen = record.date;
      }
    }
    for (const [key, cell] of cells) {
      cells.set(key, {
        x: cell.x, y: cell.y, value: quantile(cell.values.sort((a, b) => a - b), 0.5),
        logs: cell.values.length, samples: cell.samples, lastSeen: cell.lastSeen, carried: false,
      });
    }
    return cells;
  }

  function historyChanges(state, records, size, channel, halfLife) {
    const changes = new Map();
    for (const record of records) {
      for (const [x, y, value, samples] of record.grids[size][channel] || []) {
        const key = x + "," + y;
        const old = state.get(key);
        // Weights are relative to the last visit, which keeps isolated cells stable
        // even if the next observation comes years later or the user rewinds.
        const priorWeight = old ? old.weight * 2 ** ((old.day - record.day) / halfLife) : 0;
        const weight = priorWeight + 1;
        const cell = {
          x, y, value: ((old ? old.value : 0) * priorWeight + value) / weight,
          weight, day: record.day, lastSeen: record.date, carried: false,
          logs: (old ? old.logs : 0) + 1, samples: (old ? old.samples : 0) + samples,
        };
        state.set(key, cell);
        changes.set(key, cell);
      }
    }
    return changes;
  }

  function trendsForChannel(groups, size, channel, history, halfLife, loss) {
    const updates = [];
    const field = [];
    const throughDate = new Map();
    for (const records of groups) {
      const updated = history
        ? historyChanges(throughDate, records, size, channel, halfLife)
        : periodChanges(records, size, channel);
      updates.push(updated);
      const grid = history ? throughDate : updated;
      const values = Array.from(grid.values(), cell => cell.value).sort((a, b) => a - b);
      field.push(quantile(values, loss ? 0.9 : 0.1));
    }
    return {name: channel, updates, field};
  }

  class ReceptionHistoryModel {
    constructor(dataset) {
      this.records = dataset.logs.map(log => {
        const names = Object.keys(log.grids["5"]);
        return Object.assign({}, log, {names, link: recordedLink(names), day: Date.parse(log.date) / 86400000});
      }).sort((a, b) => a.date.localeCompare(b.date));
      this.aircraft = ordered(this.records.map(rec => rec.model));
      this.metadata = new Map();
      for (const rec of this.records) {
        for (const name of rec.names) {
          if (!this.metadata.has(name)) this.metadata.set(name, channelInfo(name));
        }
      }
      this.ranges = new Map();
    }

    links(aircraft = "") {
      return ordered(this.records.filter(rec => !aircraft || rec.model === aircraft).map(rec => rec.link));
    }

    matching(aircraft, link) {
      return this.records.filter(rec => (!aircraft || rec.model === aircraft) && (!link || rec.link === link));
    }

    channels(aircraft = "", link = "") {
      return channelNames(this.matching(aircraft, link));
    }

    colorRange(family, size) {
      const key = family + "|" + size;
      if (!this.ranges.has(key)) {
        const values = [];
        for (const rec of this.records) {
          for (const name of rec.names) {
            if (this.metadata.get(name).family === family) {
              for (const row of rec.grids[size][name]) values.push(row[2]);
            }
          }
        }
        values.sort((a, b) => a - b);
        const low = Math.floor(quantile(values, 0.05));
        const high = Math.ceil(quantile(values, 0.95));
        this.ranges.set(key, low === high ? [low - 1, high + 1] : [low, high]);
      }
      return this.ranges.get(key);
    }

    prepare({aircraft = "", link = "", channel, cellSize = 5, granularity = "day",
             history = false, halfLife = 90}) {
      const size = String(cellSize);
      const info = this.metadata.get(channel);
      const records = this.matching(aircraft, link);
      const names = channelNames(records)
        .filter(name => info && this.metadata.get(name).family === info.family);
      const comparable = new Set(names);
      const byPeriod = new Map();
      for (const record of records) {
        if (!record.names.some(name => comparable.has(name))) continue;
        const period = record.date.slice(0, granularity === "month" ? 7 : 10);
        if (!byPeriod.has(period)) byPeriod.set(period, []);
        byPeriod.get(period).push(record);
      }
      const chartPeriods = Array.from(byPeriod.keys());
      const groups = Array.from(byPeriod.values());
      const charts = names.map(name => trendsForChannel(groups, size, name, history, halfLife, info.loss));
      const selected = charts.find(chart => chart.name === channel);
      const changes = [];
      const periods = [];
      const recordingCounts = [];
      let cumulativeCount = 0;
      groups.forEach((group, index) => {
        const count = group.filter(rec => rec.names.includes(channel)).length;
        cumulativeCount += count;
        // A comparison channel may have been flown on another day. Keep its
        // trend point, but offer a map only when the selected channel has data.
        const contributing = history ? cumulativeCount : count;
        if (contributing > 0) {
          periods.push(chartPeriods[index]);
          changes.push(selected.updates[index]);
          recordingCounts.push(contributing);
        }
      });
      // Look up each cell's most recent visit independently. Reverse scrubbing
      // across a long history then costs about as much as going forward.
      const visits = new Map();
      if (history) {
        changes.forEach((updated, index) => {
          for (const [key, cell] of updated) {
            if (!visits.has(key)) visits.set(key, []);
            visits.get(key).push({index, cell});
          }
        });
      }
      // Keep just a few endpoint maps for crossfades, rather than a dense copy
      // of every cell for every day the library has accumulated.
      const cached = new Map();
      const frameAt = index => {
        if (!changes[index]) return new Map();
        if (!history) return changes[index];
        if (cached.has(index)) return cached.get(index);
        const frame = new Map();
        for (const [key, cellVisits] of visits) {
          // First visits were inserted in chronological order.
          if (cellVisits[0].index > index) break;
          let low = 0;
          let high = cellVisits.length;
          while (low + 1 < high) {
            const middle = Math.floor((low + high) / 2);
            if (cellVisits[middle].index <= index) low = middle;
            else high = middle;
          }
          const visit = cellVisits[low];
          frame.set(key, visit.index === index ? visit.cell : Object.assign({}, visit.cell, {carried: true}));
        }
        if (cached.size === 3) cached.delete(cached.keys().next().value);
        cached.set(index, frame);
        return frame;
      };
      const series = (pin = null) => charts.map(chart => {
        if (!pin) return {name: chart.name, points: chart.field};
        const key = pin.x + "," + pin.y;
        let value = null;
        const points = chart.updates.map(updated => {
          if (updated.has(key)) value = updated.get(key).value;
          else if (!history) value = null;
          return value;
        });
        return {name: chart.name, points};
      });
      return {
        periods, chartPeriods, frameAt, recordingCounts, series,
        range: info ? this.colorRange(info.family, size) : [0, 1],
        unit: info ? info.unit : "", loss: info ? info.loss : false,
      };
    }
  }
  return ReceptionHistoryModel;
})();
