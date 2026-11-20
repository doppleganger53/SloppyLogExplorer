"""MapLibre request handling for the two imagery layers in our local map pages."""

IMAGERY_TILE_JAVASCRIPT = r"""
    function installImageryTileLoader() {
      // Keep OSM on its normal HTTP path. Only request imagery for tiles the
      // map actually needs, retaining the exact provider URL as its cache key.
      const prefix = "sle-imagery://";
      const imagerySources = ["usgs-naip-raster-source", "nasa-gibs-raster-source"];
      for (const id of imagerySources) {
        const source = basemapConfig.sources[id];
        source.tiles = source.tiles.map(url => prefix + url);
      }

      // Both local map documents share this profile's IndexedDB origin. The
      // count and per-tile limit bound stored image payloads to 96 MiB.
      const maxTiles = 384;
      const maxTileBytes = 256 * 1024;
      const maxAge = 7 * 24 * 3600 * 1000;
      let opening;
      function database() {
        if (!opening) opening = new Promise((resolve, reject) => {
          const request = indexedDB.open("sloppy-imagery-v1", 1);
          request.onupgradeneeded = () => {
            const tiles = request.result.createObjectStore("tiles", {keyPath: "url"});
            tiles.createIndex("saved", "saved");
          };
          request.onsuccess = () => resolve(request.result);
          request.onerror = request.onblocked = () => reject(new Error("Tile cache unavailable"));
        }).catch(() => null);
        return opening;
      }

      async function lookup(url) {
        const db = await database();
        if (!db) return null;
        return new Promise((resolve, reject) => {
          const request = db.transaction("tiles").objectStore("tiles").get(url);
          request.onsuccess = () => resolve(request.result);
          request.onerror = () => reject(request.error);
        }).catch(() => null);
      }

      async function storeTile(url, entry) {
        const db = await database();
        if (!db) return;
        const store = db.transaction("tiles", "readwrite").objectStore("tiles");
        if (!entry) {
          store.delete(url);
          return;
        }
        store.put(entry);
        const count = store.count();
        count.onsuccess = () => {
          // Writers across both maps are serialized by the transaction. Each
          // put adds at most one entry, so one oldest-key lookup suffices.
          if (count.result <= maxTiles) return;
          const oldest = store.index("saved").openKeyCursor();
          oldest.onsuccess = () => { if (oldest.result) store.delete(oldest.result.primaryKey); };
        };
      }

      function cacheEntry(url, blob, headers) {
        const control = (headers.get("cache-control") || "").toLowerCase();
        const directives = new Set(control.split(",").map(s => s.trim().split("=")[0]));
        if (directives.has("no-store") || directives.has("no-cache")
          || headers.get("vary") === "*" || blob.size > maxTileBytes) return null;
        const saved = Date.now();
        const seconds = control.match(/(?:^|,)\s*max-age\s*=\s*"?(\d+)/);
        const responseDate = Date.parse(headers.get("date")) || saved;
        const age = Math.max(saved - responseDate, (Number(headers.get("age")) || 0) * 1000);
        const expires = seconds ? saved + Number(seconds[1]) * 1000 - age
          : Date.parse(headers.get("expires")) || saved;
        const freshUntil = Math.min(saved + maxAge, expires);
        const staleSeconds = control.match(/(?:^|,)\s*stale-if-error\s*=\s*"?(\d+)/);
        const staleUntil = directives.has("must-revalidate") ? freshUntil
          : Math.min(saved + maxAge, staleSeconds ? freshUntil + Number(staleSeconds[1]) * 1000 : Infinity);
        return {url, blob, saved, freshUntil, staleUntil};
      }

      function retryDelay(response, attempt) {
        if (response && ![408, 429, 500, 502, 503, 504].includes(response.status)) return null;
        const after = response?.headers.get("retry-after");
        if (after) {
          const delay = /^\d+$/.test(after) ? Number(after) * 1000 : Date.parse(after) - Date.now();
          if (Number.isFinite(delay)) return delay <= 3000 ? Math.max(0, delay) : null;
        }
        return attempt === 0 ? 300 : 900;
      }

      async function waitForRetry(delay, signal) {
        await new Promise((resolve, reject) => {
          const abort = () => { clearTimeout(timer); reject(signal.reason); };
          const timer = setTimeout(() => {
            signal.removeEventListener("abort", abort);
            resolve();
          }, delay);
          signal.addEventListener("abort", abort, {once: true});
          if (signal.aborted) abort();
        });
      }

      maplibregl.addProtocol("sle-imagery", async (request, controller) => {
        const url = request.url.slice(prefix.length);
        const signal = controller.signal;
        let cached = await lookup(url);
        signal.throwIfAborted();
        if (cached && cached.freshUntil > Date.now()) {
          try {
            return {data: await createImageBitmap(cached.blob), expires: new Date(cached.freshUntil).toUTCString()};
          } catch {
            cached = null;
            storeTile(url, null).catch(() => {});
          }
        }
        for (let attempt = 0; ; attempt++) {
          let response;
          try {
            const timeout = AbortSignal.timeout(15000);
            response = await fetch(url, {
              signal: AbortSignal.any([signal, timeout]), credentials: "omit",
              headers: request.headers, cache: attempt === 0 ? "default" : "reload"
            });
            if (!response.ok) throw new Error("Imagery HTTP " + response.status);
            const blob = await response.blob();
            // ArcGIS can return a JSON error document with HTTP 200. Decode
            // before persisting and pass the decoded (possibly transparent)
            // bitmap straight to MapLibre.
            const image = await createImageBitmap(blob);
            const entry = cacheEntry(url, blob, response.headers);
            storeTile(url, entry).catch(() => {});
            return {data: image, expires: entry && entry.freshUntil > Date.now()
              ? new Date(entry.freshUntil).toUTCString() : undefined};
          } catch (error) {
            signal.throwIfAborted();
            const delay = retryDelay(response?.ok ? null : response, attempt);
            if (response && [404, 410].includes(response.status)) storeTile(url, null).catch(() => {});
            if ((!response || response.ok || [500, 502, 503, 504].includes(response.status))
              && cached && cached.staleUntil > Date.now()) {
              try { return {data: await createImageBitmap(cached.blob)}; }
              catch { cached = null; storeTile(url, null).catch(() => {}); }
            }
            if (attempt >= 2 || delay === null) throw error;
            await waitForRetry(delay, signal);
          }
        }
      });
    }
"""
