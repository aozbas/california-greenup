import { MUTED, dateToDay, clamp, fmtDay, signed, tip } from './util.js';

export function createTimeline(host, data, { onSeek, onGrab } = {}) {
  const W = 760, H = 64, padL = 10, padR = 10, sy = 8, sh = 26;
  const x = d3.scaleLinear([data.dayMin, data.dayMax], [padL, W - padR]);
  const T = data.T, dd = data.obs.day;

  const svg = d3.select(host).append('svg').attr('class', 'tl').attr('viewBox', `0 0 ${W} ${H}`)
    .attr('role', 'slider').attr('tabindex', 0).attr('aria-label', 'Time, 2000 to 2025')
    .attr('aria-valuemin', data.dayMin).attr('aria-valuemax', data.dayMax);

  // each cell spans halfway to its neighbours
  const cells = svg.append('g').selectAll('rect').data(d3.range(T)).join('rect')
    .attr('y', sy).attr('height', sh)
    .attr('x', (i) => x(i === 0 ? dd[0] : (dd[i - 1] + dd[i]) / 2))
    .attr('width', (i) => Math.max(0.9, x(i === T - 1 ? dd[i] : (dd[i] + dd[i + 1]) / 2) - x(i === 0 ? dd[0] : (dd[i - 1] + dd[i]) / 2) + 0.35))
    .attr('fill', '#e6e8e1');

  const axis = svg.append('g').attr('transform', `translate(0,${sy + sh})`);
  for (let y = 2000; y <= 2026; y++) {
    const d = dateToDay(y, 1);
    if (d < data.dayMin - 1 || d > data.dayMax + 1) continue;
    const major = y % 5 === 0;
    const label = major && y !== 2000 && y !== 2025;
    axis.append('line').attr('x1', x(d)).attr('x2', x(d)).attr('y1', 0).attr('y2', major ? 9 : 5).attr('stroke', major ? MUTED : '#aab3ac');
    if (label) axis.append('text').attr('x', x(d)).attr('y', 20).attr('text-anchor', 'middle').attr('class', 'tick').text(y);
  }
  axis.append('text').attr('x', x(data.dayMin)).attr('y', 20).attr('text-anchor', 'start').attr('class', 'tick').text('Mar 2000');
  axis.append('text').attr('x', x(data.dayMax)).attr('y', 20).attr('text-anchor', 'end').attr('class', 'tick').text('Dec 2025');

  const head = svg.append('g').attr('class', 'playhead');
  head.append('line').attr('y1', sy - 6).attr('y2', sy + sh + 6);
  head.append('path').attr('d', `M-7,${sy - 8} L7,${sy - 8} L0,${sy + 2} Z`);
  head.append('path').attr('d', `M-7,${sy + sh + 8} L7,${sy + sh + 8} L0,${sy + sh - 2} Z`);

  const bis = d3.bisector((d) => d).left;
  const nearest = (day) => {
    const j = clamp(bis(dd, day), 0, T - 1);
    return j > 0 && day - dd[j - 1] < dd[j] - day ? j - 1 : j;
  };
  const dayAt = (evt) => {
    const r = svg.node().getBoundingClientRect();
    return clamp(x.invert(((evt.clientX - r.left) / r.width) * W), data.dayMin, data.dayMax);
  };

  let anom = null;
  const api = {
    setAnomaly(values, lim) {
      anom = values;
      const col = d3.scaleSequential(d3.interpolateBrBG).domain([-lim, lim]).clamp(true);
      cells.attr('fill', (i) => (Number.isFinite(values[i]) ? col(values[i]) : '#e6e8e1'));
    },
    setLoaded(isLoaded) {
      cells.attr('opacity', (i) => (isLoaded(data.obs.year[i]) ? 1 : 0.28));
    },
    setDay(day) {
      head.attr('transform', `translate(${x(day)},0)`);
      svg.attr('aria-valuenow', Math.round(day)).attr('aria-valuetext', fmtDay(day));
    },
  };

  let dragging = false;
  svg.on('pointerdown', (evt) => {
    if (evt.button !== 0) return;
    dragging = true;
    // fails for synthetic events
    try { svg.node().setPointerCapture(evt.pointerId); } catch (e) {}
    onGrab?.();
    onSeek?.(dayAt(evt));
    svg.node().focus({ preventScroll: true });
  });
  svg.on('pointermove', (evt) => {
    if (dragging) { onSeek?.(dayAt(evt)); tip.hide(); return; }
    const j = nearest(dayAt(evt));
    const a = anom?.[j];
    tip.show(`<b>${fmtDay(dd[j])}</b>${Number.isFinite(a) ? `<div>${signed(a)} vs usual</div><div class="muted">${a >= 0 ? 'greener' : 'browner'} than usual here</div>` : ''}`, evt);
  });
  svg.on('pointerup pointercancel', () => { dragging = false; });
  svg.on('pointerleave', () => tip.hide());
  svg.on('keydown', (evt) => {
    const cur = +svg.attr('aria-valuenow');
    const step = evt.shiftKey ? 16 : 1;
    let next = null;
    if (evt.key === 'ArrowRight') next = cur + step;
    else if (evt.key === 'ArrowLeft') next = cur - step;
    else if (evt.key === 'Home') next = data.dayMin;
    else if (evt.key === 'End') next = data.dayMax;
    if (next == null) return;
    evt.preventDefault();
    onGrab?.();
    onSeek?.(clamp(next, data.dayMin, data.dayMax));
  });
  return api;
}
