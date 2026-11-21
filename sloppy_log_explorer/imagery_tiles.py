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
          const request = indexedDB.open("sloppy-imagery-v1", 2);
          request.onupgradeneeded = event => {
            if (event.oldVersion) {
              // Older entries did not distinguish hidden response age from zero.
              request.transaction.objectStore("tiles").clear();
              return;
            }
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

      function cacheEntry(url, blob, response, requestedAt) {
        const headers = response.headers;
        const control = (headers.get("cache-control") || "").toLowerCase();
        const directives = new Set(control.split(",").map(s => s.trim().split("=")[0]));
        if (directives.has("no-store") || directives.has("no-cache")
          || headers.get("vary") === "*" || blob.size > maxTileBytes) return null;
        const saved = Date.now();
        const seconds = control.match(/(?:^|,)\s*max-age\s*=\s*"?(\d+)/);
        let expires = Date.parse(headers.get("expires"));
        if (seconds) {
          // Date and Age can both be hidden by CORS, even for a near-expired
          // CDN or HTTP-cache hit. Let Chromium decide freshness in that case.
          // A visible max-age takes precedence over any Expires header.
          const originDate = Date.parse(headers.get("date"));
          const ageHeader = headers.get("age");
          const ageVisible = ageHeader !== null || response.type !== "cors";
          const lifetime = Number(seconds[1]) * 1000;
          // These are independent upper bounds: Age is already the time spent
          // upstream. The request timestamp also accounts for time in transit.
          expires = ageVisible ? Math.min(originDate + lifetime,
            requestedAt + lifetime - Number(ageHeader) * 1000) : NaN;
        }
        const freshUntil = Number.isFinite(expires) ? Math.min(saved + maxAge, expires) : 0;
        const staleSeconds = control.match(/(?:^|,)\s*stale-if-error\s*=\s*"?(\d+)/);
        let staleUntil = saved + maxAge;
        if (directives.has("must-revalidate")) staleUntil = freshUntil;
        else if (staleSeconds) staleUntil = Number.isFinite(expires)
          ? Math.min(staleUntil, expires + Number(staleSeconds[1]) * 1000) : 0;
        return {url, blob, saved, freshUntil, staleUntil};
      }

      function retryDelay(response, attempt) {
        if (response && ![408, 429, 500, 502, 503, 504].includes(response.status)) return null;
        const after = response?.headers.get("retry-after");
        if (after) {
          const delay = /^\d+$/.test(after) ? Number(after) * 1000 : Date.parse(after) - Date.now();
          if (Number.isFinite(delay)) return delay <= 3000 ? Math.max(0, delay) : null;
        }
        // CORS may hide the provider's Retry-After. Don't turn a rate limit
        // into a burst of retries unless the provider permits a short wait.
        if (response?.status === 429) return null;
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
            const requestedAt = Date.now();
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
            const entry = cacheEntry(url, blob, response, requestedAt);
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
