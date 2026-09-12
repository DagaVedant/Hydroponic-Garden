/* Activity: counters, concentrate estimates, a 24h density strip, filtered log. */

const OUT = document.getElementById('events');
const MARKS = document.getElementById('strip-marks');

const KIND = {
  dose:  { label: 'Dose',  cls: 'ok' },
  light: { label: 'Light', cls: 'warn' },
  fault: { label: 'Fault', cls: 'bad' },
};

let allEvents = [];
let filter = 'all';

function dayOf(ts) {
  const d = new Date(ts * 1000);
  const today = new Date();
  const yday = new Date(today.getTime() - 86400000);
  const same = (a, b) => a.toDateString() === b.toDateString();
  if (same(d, today)) return 'Today';
  if (same(d, yday)) return 'Yesterday';
  return d.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' });
}

/* Split "5.0 mL micro, verified" so the outcome reads as secondary to the action. */
function phrase(text) {
  const i = text.indexOf(', ');
  if (i < 0) return text;
  return `${text.slice(0, i)} <em>${text.slice(i + 2)}</em>`;
}

function drawCounters(sum) {
  document.getElementById('c-doses').textContent = sum.doses_24h;
  const f = document.getElementById('c-faults');
  f.textContent = sum.faults_24h;
  f.closest('.ro').classList.toggle('warn', sum.faults_24h > 0);

  document.getElementById('bottles').innerHTML = sum.bottles.map((b) => {
    const low = b.remaining_pct <= 20;
    return `<div class="bottle${low ? ' low' : ''}">
      <div class="bl">
        <span>${b.label}</span>
        <span class="bv">${b.remaining_pct}%</span>
      </div>
      <div class="bar"><div class="fill" style="width:${b.remaining_pct}%"></div></div>
      <div class="bu">${b.used_ml} mL used</div>
    </div>`;
  }).join('');
}

/* Density strip. Where the events sit in the last 24h, so the rhythm is visible
   without reading timestamps. */
function drawStrip(events, sum) {
  const W = 620, span = sum.now - sum.window_start;
  const recent = events.filter((e) => e.ts >= sum.window_start);
  const colour = { dose: 'var(--ok)', light: 'var(--lit)', fault: 'var(--bad)' };

  MARKS.innerHTML = recent.map((e) => {
    const x = ((e.ts - sum.window_start) / span) * W;
    const h = e.kind === 'fault' ? 12 : 8;
    return `<line x1="${x.toFixed(1)}" y1="${17 - h / 2}" x2="${x.toFixed(1)}"
      y2="${17 + h / 2}" stroke="${colour[e.kind] || 'var(--text-3)'}"
      stroke-width="2" stroke-linecap="round"/>`;
  }).join('');
}

function drawLog() {
  const events = filter === 'all'
    ? allEvents : allEvents.filter((e) => e.kind === filter);

  if (!events.length) {
    // an empty result is a real state. say which one it is
    OUT.innerHTML = allEvents.length
      ? `<p class="empty">No ${filter} events recorded.<br>Try a different filter.</p>`
      : '<p class="empty">No activity recorded yet.<br>Events appear here once the control '
        + 'service has run a dose, switched the lighting, or hit a fault.</p>';
    return;
  }

  let lastDay = null;
  OUT.innerHTML = events.map((e) => {
    const day = dayOf(e.ts);
    const header = day === lastDay ? '' : `<div class="day">${day}</div>`;
    lastDay = day;
    const k = KIND[e.kind] || { label: e.kind, cls: 'ok' };
    return header + `<div class="ev">
      <time datetime="${new Date(e.ts * 1000).toISOString()}">${clockOf(e.ts)}</time>
      <span class="tag ${k.cls}">${k.label}</span>
      <span class="what">${phrase(e.text)}</span>
    </div>`;
  }).join('');
}

function render(data) {
  allEvents = data.events || [];
  drawCounters(data.summary);
  drawStrip(allEvents, data.summary);
  drawLog();
}

document.getElementById('filters').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-filter]');
  if (!btn) return;
  filter = btn.dataset.filter;
  document.querySelectorAll('[data-filter]').forEach((b) => {
    b.setAttribute('aria-pressed', String(b === btn));
  });
  drawLog();
});

poll('/api/timeline', render, 20000);
