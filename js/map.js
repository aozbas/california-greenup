import { NDVI_LUT, ANOM_LUT } from './util.js';

const COS = Math.cos((37.3 * Math.PI) / 180);
const MISSING = 255;

export function createMap(host, data, { height = 560 } = {}) {
  const { W, H, N, gx, gy, pos, idxOf, lon0, lat0, pix } = data;
  const cssH = height, cssW = Math.round((height * W * COS) / H);
  host.classList.add('mapwrap');
  host.style.width = `${cssW}px`;
  host.style.height = `${cssH}px`;

  const canvas = document.createElement('canvas');
  canvas.width = W;
  canvas.height = H;
  canvas.setAttribute('aria-hidden', 'true');
  host.appendChild(canvas);
  const ctx = canvas.getContext('2d');
  const img = ctx.createImageData(W, H);
  const buf = new Uint32Array(img.data.buffer);

  const svg = d3.select(host).append('svg').attr('viewBox', `0 0 ${W * COS} ${H}`).attr('preserveAspectRatio', 'none')
    .attr('role', 'img').attr('aria-label', 'Map of California coloured by greenness');
  const path = d3.geoPath(d3.geoTransform({
    point(lon, lat) { this.stream.point(((lon - lon0) / pix) * COS, (lat0 - lat) / pix); },
  }));
  svg.append('path').datum(data.boundary).attr('class', 'outline').attr('d', path);
  const gMarks = svg.append('g').attr('class', 'marks');
  const gLasso = svg.append('g').attr('class', 'lasso');

  const api = {
    svg, gLasso,
    // paints the blend of two frames, dims cells outside selMask and returns the share of land greener than usual
    paintBlend({ A, B, w, mode, cA, cB, selMask }) {
      const clim = data.clim;
      buf.fill(0);
      let greener = 0, valid = 0;
      const aArr = A?.arr, aBase = A?.base ?? 0, bArr = B?.arr, bBase = B?.base ?? 0;
      const w1 = 1 - w;
      for (let i = 0; i < N; i++) {
        const q0 = aArr ? aArr[aBase + i] : MISSING;
        const q1 = bArr ? bArr[bBase + i] : MISSING;
        if (q0 === MISSING && q1 === MISSING) continue;
        const q = q0 === MISSING ? q1 : q1 === MISSING ? q0 : q0 * w1 + q1 * w;
        let c = 0;
        const u0 = clim[cA + i], u1 = clim[cB + i];
        const u = u0 === MISSING ? (u1 === MISSING ? NaN : u1) : u1 === MISSING ? u0 : u0 * w1 + u1 * w;
        if (mode === 'green') {
          c = NDVI_LUT[Math.round(q)];
        } else {
          if (Number.isNaN(u)) continue;
          const d = q - u;
          c = ANOM_LUT[Math.max(0, Math.min(500, Math.round(d) + 250))];
        }
        if (!Number.isNaN(u)) { valid++; if (q > u) greener++; }
        if (selMask && !selMask[i]) c = ((c & 0x00ffffff) | (70 << 24)) >>> 0;
        buf[pos[i]] = c;
      }
      ctx.putImageData(img, 0, 0);
      return valid ? greener / valid : NaN;
    },
    cellAt(evt) {
      const r = svg.node().getBoundingClientRect();
      const x = Math.floor(((evt.clientX - r.left) / r.width) * W);
      const y = Math.floor(((evt.clientY - r.top) / r.height) * H);
      if (x < 0 || y < 0 || x >= W || y >= H) return -1;
      return idxOf[y * W + x];
    },
    svgPoint(evt) {
      const r = svg.node().getBoundingClientRect();
      return [((evt.clientX - r.left) / r.width) * W * COS, ((evt.clientY - r.top) / r.height) * H];
    },
    pixelXY: (i) => [(gx[i] + 0.5) * COS, gy[i] + 0.5],
    setMark(i) {
      gMarks.selectAll('*').remove();
      if (i == null || i < 0) return;
      const [x, y] = api.pixelXY(i);
      gMarks.append('circle').attr('cx', x).attr('cy', y).attr('r', 5).attr('class', 'mark');
    },
    pixelsInPolygon(poly) {
      const xs = poly.map((p) => p[0]), ys = poly.map((p) => p[1]);
      const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
      const out = [];
      for (let i = 0; i < N; i++) {
        const [x, y] = api.pixelXY(i);
        if (x < x0 || x > x1 || y < y0 || y > y1) continue;
        if (d3.polygonContains(poly, [x, y])) out.push(i);
      }
      return Int32Array.from(out);
    },
  };
  return api;
}
