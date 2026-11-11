export const ACCENT = '#1f7a6d';
export const HILITE = '#c4631a';
export const MUTED = '#5b6b61';

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const EPOCH = Date.UTC(2000, 0, 1);
const DAY = 86400000;

export const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
export const fmt = (x, d = 2) => (Number.isFinite(x) ? x.toFixed(d) : 'n/a');
export const signed = (x, d = 2) => (Number.isFinite(x) ? (x >= 0 ? '+' : '−') + Math.abs(x).toFixed(d) : 'n/a');

// day = days since 2000-01-01 (may be fractional)
export function dayToDate(day) {
  const dt = new Date(EPOCH + Math.floor(day) * DAY);
  const y = dt.getUTCFullYear();
  const doy = Math.round((dt - Date.UTC(y, 0, 1)) / DAY) + 1;
  return { y, m: dt.getUTCMonth(), d: dt.getUTCDate(), doy };
}
export function dateToDay(y, doy) { return (Date.UTC(y, 0, 1) - EPOCH) / DAY + (doy - 1); }

export function fmtDay(day, withYear = true) {
  const { y, m, d } = dayToDate(day);
  return `${MON[m]} ${d}${withYear ? `, ${y}` : ''}`;
}
export function doyLabel(doy) {
  const dt = new Date(Date.UTC(2001, 0, 1) + (Math.round(doy) - 1) * DAY);
  return `${MON[dt.getUTCMonth()]} ${dt.getUTCDate()}`;
}

const packRGBA = (r, g, b, a = 255) => ((a << 24) | (b << 16) | (g << 8) | r) >>> 0;

// q = round(250 × (ndvi + 0.2) / 1.2), coloured over NDVI 0 to 0.9
export const NDVI_LUT = (() => {
  const l = new Uint32Array(256);
  for (let q = 0; q <= 250; q++) {
    const v = (q / 250) * 1.2 - 0.2;
    const c = d3.rgb(d3.interpolateYlGn(clamp(v / 0.9, 0, 1)));
    l[q] = packRGBA(c.r, c.g, c.b);
  }
  return l;
})();

// index = q - usual q + 250, coloured over ±0.25 NDVI
const ANOM_LIM = 0.25;
export const ANOM_LUT = (() => {
  const l = new Uint32Array(501);
  for (let i = 0; i <= 500; i++) {
    const v = ((i - 250) / 250) * 1.2;
    const c = d3.rgb(d3.interpolateBrBG(clamp((v + ANOM_LIM) / (2 * ANOM_LIM), 0, 1)));
    l[i] = packRGBA(c.r, c.g, c.b);
  }
  return l;
})();

const tipEl = document.createElement('div');
tipEl.className = 'tip';
tipEl.setAttribute('role', 'tooltip');
document.addEventListener('DOMContentLoaded', () => document.body.appendChild(tipEl));
export const tip = {
  show(html, evt) {
    tipEl.innerHTML = html;
    tipEl.style.display = 'block';
    const pad = 14, r = tipEl.getBoundingClientRect();
    let x = evt.clientX + pad, y = evt.clientY + pad;
    if (x + r.width > window.innerWidth - 8) x = evt.clientX - r.width - pad;
    if (y + r.height > window.innerHeight - 8) y = evt.clientY - r.height - pad;
    tipEl.style.left = `${x}px`;
    tipEl.style.top = `${y}px`;
  },
  hide() { tipEl.style.display = 'none'; },
};

export function createState(initial) {
  const target = new EventTarget();
  const state = { ...initial };
  return {
    get: () => state,
    set(patch) {
      const keys = Object.keys(patch).filter((k) => state[k] !== patch[k]);
      Object.assign(state, patch);
      if (keys.length) target.dispatchEvent(new CustomEvent('change', { detail: keys }));
    },
    on(fn) { target.addEventListener('change', (e) => fn(e.detail, state)); },
  };
}
