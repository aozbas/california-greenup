const MISSING = 255, Q_SCALE = 250, Q_OFF = 0.2;
export const q2v = (q) => (q / Q_SCALE) * 1.2 - Q_OFF;

async function gunzip(buf) {
  const u8 = new Uint8Array(buf);
  if (u8[0] === 0x1f && u8[1] === 0x8b) {                     // some servers already decompress .gz files
    const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream('gzip'));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }
  return u8;
}

export async function loadCore(setProgress) {
  const base = 'data/web/';
  const meta = await (await fetch(`${base}meta.json`)).json();
  const coreFiles = ['land.bin', 'regions.bin', 'clim8.bin', 'series.json', 'ca_boundary.geojson'];
  const total = coreFiles.reduce((s, f) => s + (meta.files_mb[f] ?? 0), 0) * 1e6 || 1;
  let loaded = 0;
  const get = async (name, kind) => {
    const res = await fetch(base + name);
    if (!res.ok) throw new Error(`${name}: HTTP ${res.status}`);
    const buf = await res.arrayBuffer();
    loaded += buf.byteLength;
    setProgress?.(Math.min(1, loaded / total));
    if (kind === 'json') return JSON.parse(new TextDecoder().decode(buf));
    return new Uint8Array(buf);
  };
  const [land, regionIds, clim, series, boundary] = await Promise.all([
    get('land.bin'), get('regions.bin'), get('clim8.bin'), get('series.json', 'json'),
    get('ca_boundary.geojson', 'json'),
  ]);

  const { width: W, height: H, lon0, lat0, pix } = meta.grid;
  const N = meta.N, T = meta.T;
  const gx = new Uint16Array(N), gy = new Uint16Array(N), pos = new Uint32Array(N), idxOf = new Int32Array(W * H).fill(-1);
  let n = 0;
  for (let p = 0; p < W * H; p++) if (land[p]) { gx[n] = p % W; gy[n] = (p / W) | 0; pos[n] = p; idxOf[p] = n; n++; }
  if (n !== N) throw new Error(`land mask has ${n} cells, expected ${N}`);

  const o = meta.obs;
  const obs = {
    day: Float64Array.from(o.day), doy: Int16Array.from(o.doy), year: Int16Array.from(o.year),
    slot: Uint8Array.from(o.slot), k: Uint16Array.from(o.k), sensor: o.sensor,
  };
  const years = new Map(meta.years.map((y) => [y.year, { ...y, frames: null, promise: null }]));

  const data = {
    meta, N, T, W, H, lon0, lat0, pix, gx, gy, pos, idxOf, clim, regionIds, series, boundary,
    obs, years, yearList: [...years.keys()].sort((a, b) => a - b),
    regions: meta.regions, dayMin: obs.day[0], dayMax: obs.day[T - 1],
    lon: (i) => lon0 + (gx[i] + 0.5) * pix,
    lat: (i) => lat0 - (gy[i] + 0.5) * pix,
    listeners: [],
  };
  data.cellKm2 = (i) => {
    const dy = pix * 111.32;
    return dy * pix * 111.32 * Math.cos((data.lat(i) * Math.PI) / 180);
  };
  data.areaKm2 = (idx) => { let s = 0; for (let j = 0; j < idx.length; j++) s += data.cellKm2(idx[j]); return s; };
  data.onYearLoaded = (fn) => data.listeners.push(fn);

  let inFlight = 0;
  const queue = [];
  data.loadedCount = () => data.yearList.filter((y) => years.get(y).frames).length;
  data.isLoaded = (y) => !!years.get(y)?.frames;

  function loadYear(y) {
    const info = years.get(y);
    if (!info) return Promise.resolve();
    if (info.frames) return Promise.resolve();
    if (info.promise) return info.promise;
    info.promise = (async () => {
      const res = await fetch(base + info.file);
      if (!res.ok) throw new Error(`${info.file}: HTTP ${res.status}`);
      const raw = await gunzip(await res.arrayBuffer());
      // each frame is stored as the difference from the previous one
      for (let k = 1; k < info.n; k++) {
        const a = k * N, b = (k - 1) * N;
        for (let i = 0; i < N; i++) raw[a + i] = (raw[b + i] + raw[a + i]) & 255;
      }
      info.frames = raw;
      data.listeners.forEach((fn) => fn(y));
    })().catch((e) => { info.promise = null; throw e; });
    return info.promise;
  }
  async function pump() {
    while (inFlight < 2 && queue.length) {
      const y = queue.shift();
      if (data.isLoaded(y) || years.get(y)?.promise) continue;
      inFlight++;
      loadYear(y).catch((e) => {
        console.error(e);
        const info = years.get(y);
        info.fails = (info.fails || 0) + 1;
        setTimeout(() => { if (!queue.includes(y)) queue.unshift(y); pump(); }, Math.min(30000, 1000 * 2 ** info.fails));
      }).finally(() => { inFlight--; pump(); });
    }
  }
  data.prioritize = (ys) => {
    const rest = data.yearList.filter((y) => !ys.includes(y));
    const centre = ys[0];
    rest.sort((a, b) => Math.abs(a - centre) - Math.abs(b - centre));
    queue.length = 0;
    queue.push(...ys, ...rest);
    pump();
  };
  data.loadYear = loadYear;

  data.frame = (i) => {
    const info = years.get(obs.year[i]);
    return info?.frames ? { arr: info.frames, base: obs.k[i] * N } : null;
  };
  // the observations on either side of a day, and the weight of the later one
  data.locate = (day) => {
    const d = obs.day;
    let lo = 0, hi = T - 1;
    if (day <= d[0]) return { i: 0, j: 0, w: 0 };
    if (day >= d[T - 1]) return { i: T - 1, j: T - 1, w: 0 };
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (d[mid] <= day) lo = mid; else hi = mid; }
    return { i: lo, j: hi, w: (day - d[lo]) / (d[hi] - d[lo]) };
  };
  data.yearsNeeded = (day) => {
    const { i, j } = data.locate(day);
    return [...new Set([obs.year[i], obs.year[j]])];
  };
  data.climBase = (i) => obs.slot[i] * N;

  data.pixelAt = (p, loc) => {
    const get = (idx) => {
      const f = data.frame(idx);
      if (!f) return { v: NaN, u: NaN };
      const q = f.arr[f.base + p], c = clim[data.climBase(idx) + p];
      return { v: q === MISSING ? NaN : q2v(q), u: c === MISSING ? NaN : q2v(c) };
    };
    const a = get(loc.i), b = get(loc.j);
    const mix = (x, y) => (Number.isNaN(x) ? y : Number.isNaN(y) ? x : x * (1 - loc.w) + y * loc.w);
    return { v: mix(a.v, b.v), u: mix(a.u, b.u) };
  };

  const histMedian = (counts, c) => {
    const half = c / 2;
    let acc = 0, m = 0;
    for (; m <= 250; m++) { acc += counts[m]; if (acc >= half) break; }
    return q2v(m);
  };
  // regions and the whole state are precomputed. Custom areas use the years loaded so far
  data.areaSeries = (area) => {
    if (area.key in series) {
      return { values: Float32Array.from(series[area.key], (v) => (v == null ? NaN : v)) };
    }
    if (!area._cache) area._cache = { values: new Float32Array(T).fill(NaN), done: new Set() };
    const cache = area._cache, idx = area.idx, counts = new Uint32Array(256);
    for (const y of data.yearList) {
      if (cache.done.has(y)) continue;
      const info = years.get(y);
      if (!info.frames) continue;
      for (let i = info.first; i < info.first + info.n; i++) {
        const base = obs.k[i] * N;
        counts.fill(0);
        let c = 0;
        for (let j = 0; j < idx.length; j++) { const q = info.frames[base + idx[j]]; if (q !== MISSING) { counts[q]++; c++; } }
        cache.values[i] = c ? histMedian(counts, c) : NaN;
      }
      cache.done.add(y);
    }
    return { values: cache.values };
  };

  data.typicalOf = (values) => {
    const bySlot = Array.from({ length: 46 }, () => ({ v: [], x: [] }));
    for (let i = 0; i < T; i++) {
      if (obs.year[i] < 2001 || !Number.isFinite(values[i])) continue;
      const b = bySlot[obs.slot[i]];
      b.v.push(values[i]); b.x.push(obs.doy[i]);
    }
    const pts = [];
    const slotMed = new Float32Array(46).fill(NaN);
    bySlot.forEach((b, s) => {
      if (b.v.length < 3) return;
      b.v.sort((x, y) => x - y); b.x.sort((x, y) => x - y);
      const q = (arr, p) => d3.quantileSorted(arr, p);
      slotMed[s] = q(b.v, 0.5);
      pts.push({ slot: s, x: q(b.x, 0.5), v: q(b.v, 0.5), p25: q(b.v, 0.25), p75: q(b.v, 0.75) });
    });
    pts.sort((a, b) => a.x - b.x);
    return { pts, slotMed };
  };
  data.usualAt = (typical, doy) => {
    const p = typical.pts;
    if (!p.length) return NaN;
    if (doy <= p[0].x) return p[0].v;
    if (doy >= p[p.length - 1].x) return p[p.length - 1].v;
    let k = 0;
    while (p[k + 1].x < doy) k++;
    const t = (doy - p[k].x) / (p[k + 1].x - p[k].x);
    return p[k].v * (1 - t) + p[k + 1].v * t;
  };
  data.valueAt = (values, loc) => {
    const a = values[loc.i], b = values[loc.j];
    if (Number.isNaN(a)) return b;
    if (Number.isNaN(b)) return a;
    return a * (1 - loc.w) + b * loc.w;
  };
  data.anomalySeries = (values, typical) => Float32Array.from(values, (v, i) => v - typical.slotMed[obs.slot[i]]);

  data.yearLines = (values) => {
    const lines = new Map();
    for (let i = 0; i < T; i++) {
      if (!Number.isFinite(values[i])) continue;
      const y = obs.year[i];
      if (!lines.has(y)) lines.set(y, []);
      lines.get(y).push({ doy: obs.doy[i], v: values[i], i });
    }
    return lines;
  };
  return data;
}
