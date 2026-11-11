import { ACCENT, HILITE, doyLabel, fmt, clamp, tip } from './util.js';

const MONTH_STARTS = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335];
const LETTERS = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];

export function createSeasonal(host, { width = 640, height = 340, onJump } = {}) {
  const m = { t: 26, r: 14, b: 26, l: 44 };
  const svg = d3.select(host).append('svg').attr('class', 'chart').attr('viewBox', `0 0 ${width} ${height}`)
    .attr('role', 'img').attr('aria-label', 'Greenness through the year, one line per year');
  const x = d3.scaleLinear([1, 366], [m.l, width - m.r]);
  const y = d3.scaleLinear([0, 1], [height - m.b, m.t]);

  const gGrid = svg.append('g');
  MONTH_STARTS.forEach((d, i) => {
    gGrid.append('line').attr('x1', x(d)).attr('x2', x(d)).attr('y1', m.t).attr('y2', height - m.b).attr('class', 'gridline');
    gGrid.append('text').attr('x', x(d + 14)).attr('y', height - 8).attr('class', 'tick').attr('text-anchor', 'middle').text(LETTERS[i]);
  });
  const gY = svg.append('g').attr('transform', `translate(${m.l},0)`);
  svg.append('text').attr('x', 4).attr('y', 12).attr('class', 'axislabel').text('greenness (NDVI)');

  const band = svg.append('path').attr('class', 'band');
  const gOthers = svg.append('g');
  const typ = svg.append('path').attr('class', 'typical');
  const hov = svg.append('path').attr('class', 'hoverline');
  const futurePath = svg.append('path').attr('class', 'cur future').attr('stroke', ACCENT);
  const pastPath = svg.append('path').attr('class', 'cur').attr('stroke', ACCENT);
  const cursor = svg.append('line').attr('class', 'cursor');
  const dot = svg.append('circle').attr('r', 5).attr('class', 'dot').attr('fill', ACCENT);
  const overlay = svg.append('rect').attr('x', m.l).attr('y', m.t).attr('width', width - m.l - m.r)
    .attr('height', height - m.t - m.b).attr('fill', 'transparent').style('cursor', 'pointer');

  const line = d3.line().x((d) => x(d.doy)).y((d) => y(d.v)).curve(d3.curveMonotoneX);
  const area = d3.area().x((d) => x(d.x)).y0((d) => y(d.p25)).y1((d) => y(d.p75)).curve(d3.curveMonotoneX);

  let lines = new Map(), typical = null, curYear = null, hoverYear = null;
  const valueAt = (pts, doy) => {
    if (!pts.length) return NaN;
    if (doy <= pts[0].doy) return pts[0].v;
    if (doy >= pts[pts.length - 1].doy) return pts[pts.length - 1].v;
    let k = 0;
    while (pts[k + 1].doy < doy) k++;
    const t = (doy - pts[k].doy) / (pts[k + 1].doy - pts[k].doy);
    return pts[k].v * (1 - t) + pts[k + 1].v * t;
  };

  const api = {
    setData(newLines, newTypical) {
      lines = newLines; typical = newTypical;
      let lo = Infinity, hi = -Infinity;
      for (const pts of lines.values()) for (const p of pts) { if (p.v < lo) lo = p.v; if (p.v > hi) hi = p.v; }
      if (!Number.isFinite(lo)) { lo = 0; hi = 1; }
      const pad = Math.max(0.02, (hi - lo) * 0.06);
      let a = Math.max(0, lo - pad), b = Math.min(1, hi + pad);
      if (b - a < 0.25) { const c = (a + b) / 2; a = Math.max(0, c - 0.125); b = Math.min(1, a + 0.25); }
      y.domain([a, b]);
      gY.call(d3.axisLeft(y).ticks(5).tickSize(-(width - m.l - m.r)).tickFormat(d3.format('.2f')));
      gY.select('.domain').remove();
      gY.selectAll('line').attr('class', 'gridline');
      gY.selectAll('text').attr('class', 'ytick').attr('dx', -2);
      const ys = [...lines.keys()].filter((yr) => yr !== curYear);
      gOthers.selectAll('path').data(ys, (d) => d).join('path').attr('class', 'yr').attr('d', (yr) => line(lines.get(yr)));
      band.attr('d', typical?.pts.length ? area(typical.pts) : null);
      typ.attr('d', typical?.pts.length ? line(typical.pts.map((p) => ({ doy: p.x, v: p.v }))) : null);
      api.setNow(curYear, last.doy);
    },
    setNow(year, doy) {
      last = { year, doy };
      if (year !== curYear) {
        curYear = year;
        const ys = [...lines.keys()].filter((yr) => yr !== curYear);
        gOthers.selectAll('path').data(ys, (d) => d).join('path').attr('class', 'yr').attr('d', (yr) => line(lines.get(yr)));
      }
      const pts = lines.get(year) || [];
      const past = pts.filter((p) => p.doy <= doy), future = pts.filter((p) => p.doy > doy);
      const v = valueAt(pts, doy);
      const edge = Number.isFinite(v) ? { doy, v } : null;
      pastPath.attr('d', past.length && edge ? line([...past, edge]) : null);
      futurePath.attr('d', future.length && edge ? line([edge, ...future]) : null);
      cursor.attr('x1', x(doy)).attr('x2', x(doy)).attr('y1', m.t).attr('y2', height - m.b);
      dot.style('display', edge ? null : 'none');
      if (edge) dot.attr('cx', x(doy)).attr('cy', y(v));
    },
  };
  let last = { year: null, doy: 1 };

  overlay.on('pointermove', (evt) => {
    const [px, py] = d3.pointer(evt, svg.node());
    const doy = clamp(x.invert(px), 1, 366);
    let best = null, bd = 14;
    for (const [yr, pts] of lines) {
      const v = valueAt(pts, doy);
      if (!Number.isFinite(v)) continue;
      const dd = Math.abs(y(v) - py);
      if (dd < bd) { bd = dd; best = yr; }
    }
    hoverYear = best;
    hov.attr('d', best != null ? line(lines.get(best)) : null).attr('stroke', HILITE);
    if (best != null) {
      const v = valueAt(lines.get(best), doy);
      tip.show(`<b>${best}</b> <span class="muted">${doyLabel(doy)}</span><div>greenness <b>${fmt(v)}</b></div><div class="muted">click to jump here</div>`, evt);
    } else tip.hide();
  }).on('pointerleave', () => { hoverYear = null; hov.attr('d', null); tip.hide(); })
    .on('click', (evt) => {
      if (hoverYear == null) return;
      const [px] = d3.pointer(evt, svg.node());
      onJump?.(hoverYear, Math.round(clamp(x.invert(px), 1, 366)));
    });
  return api;
}
