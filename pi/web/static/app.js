/* Shared: polling, system status, command buttons, formatting. */

const LINK = document.getElementById('link');
const STATUS = document.getElementById('status');

function setLink(ok, note) {
  if (!LINK) return;
  LINK.textContent = note;
  LINK.classList.toggle('down', !ok);
}

function ago(seconds) {
  if (seconds == null) return 'never';
  if (seconds < 60) return 'just now';
  if (seconds < 5400) return Math.round(seconds / 60) + ' min ago';
  if (seconds < 172800) return Math.round(seconds / 3600) + ' h ago';
  return Math.round(seconds / 86400) + ' d ago';
}

function clockOf(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function fmt(value, sensor) {
  if (value == null) return '--';
  if (sensor === 'water/ec') return Math.round(value).toLocaleString();
  if (sensor === 'air/humidity') return Math.round(value).toString();
  return value.toFixed(2).replace(/\.?0+$/, '');
}

/* ---------------------------------------------------------------------------
   System status. One line answering "is anything wrong", so the six cards below
   do not have to be read to find out.
   ------------------------------------------------------------------------ */
function setStatus(data) {
  if (!STATUS) return;
  const head = document.getElementById('status-head');
  const detail = document.getElementById('status-detail');
  const when = document.getElementById('status-when');

  const problems = [];
  let worst = 'ok';

  if (!data.online) {
    problems.push('control service is not reporting');
    worst = 'bad';
  }
  data.sensors.forEach((s) => {
    if (!s.valid) { problems.push(`${s.label} sensor failing`); worst = 'bad'; }
    else if (s.status === 'warn') {
      problems.push(`${s.label} outside target`);
      if (worst === 'ok') worst = 'warn';
    }
  });
  const dosing = data.dosing || {};
  if (dosing.state === 'fault') {
    problems.push('dosing halted on a fault');
    worst = 'bad';
  }
  // the alerts service watches for the things a single reading cannot show: a
  // level that rose, a heartbeat that stopped. whatever it has active goes here
  (data.alerts || []).forEach((a) => {
    if (a.key === 'dosing_fault' || (a.key || '').startsWith('fail:')) return;  // already listed
    problems.push(a.text);
    worst = 'bad';
  });

  STATUS.classList.remove('ok', 'warn', 'bad');
  STATUS.classList.add(worst);

  if (!problems.length) {
    head.textContent = 'All systems nominal';
    detail.textContent = dosing.calibrated === false
      ? 'Dosing pumps are not calibrated, so no dose will run.' : '';
  } else {
    head.textContent = problems.length === 1
      ? 'One issue needs attention' : `${problems.length} issues need attention`;
    // sentence case, joined, so it reads as a sentence and not a log dump
    detail.textContent = problems.join('. ');
  }
  when.textContent = data.last_seen
    ? 'Last reading ' + ago(data.now - data.last_seen) : '';
}

/* ---------------------------------------------------------------------------
   Polling. Pauses while the tab is hidden so a dashboard left open on a phone
   does not keep waking the pi.
   ------------------------------------------------------------------------ */
function poll(url, onData, intervalMs) {
  let timer = null;
  let failures = 0;

  async function tick() {
    try {
      const res = await fetch(url, { cache: 'no-store' });
      if (!res.ok) throw new Error(res.status);
      onData(await res.json());
      failures = 0;
      setLink(true, 'Live');
    } catch (err) {
      failures += 1;
      // one dropped request is a blip, not an outage. say so only when it persists
      if (failures > 1) setLink(false, 'Reconnecting');
    }
  }
  function start() { if (!timer) { tick(); timer = setInterval(tick, intervalMs); } }
  function stop() { clearInterval(timer); timer = null; }
  document.addEventListener('visibilitychange', () => (document.hidden ? stop() : start()));
  start();
  return tick;
}

/* ---------------------------------------------------------------------------
   One snapshot poll serves every page. The status header lives in the shared
   layout, so it has to be filled whichever page is open; the page script picks
   the same payload up off an event rather than issuing a second request.
   ------------------------------------------------------------------------ */
let refreshSnapshot = () => {};

document.addEventListener('DOMContentLoaded', () => {
  refreshSnapshot = poll('/api/snapshot', (data) => {
    setStatus(data);
    document.dispatchEvent(new CustomEvent('hydro:snapshot', { detail: data }));
  }, 10000);
});

/* ---------------------------------------------------------------------------
   Command buttons. Any element with data-cmd posts its data-body.
   ------------------------------------------------------------------------ */
function wireCommands() {
  document.querySelectorAll('[data-cmd]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (btn.dataset.confirm && !window.confirm(btn.dataset.confirm)) return;

      const label = btn.textContent;
      btn.disabled = true;
      btn.classList.add('busy');
      btn.textContent = 'Sending';
      try {
        const res = await fetch('/api/cmd/' + btn.dataset.cmd, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: btn.dataset.body,
        });
        const body = await res.json();
        if (!body.ok) setLink(false, body.error === 'no broker' ? 'No broker' : 'Command failed');
      } catch (err) {
        setLink(false, 'Command failed');
      } finally {
        btn.textContent = label;
        btn.classList.remove('busy');
        btn.disabled = false;
        // the button reports that the message left. whether it worked shows up on
        // the next sweep, when the control service republishes its state
        setTimeout(refreshSnapshot, 500);
      }
    });
  });
}

/* ---------------------------------------------------------------------------
   Sparkline with the target band drawn behind it, so "is this drifting out of
   range" is answerable without reading the axis.
   ------------------------------------------------------------------------ */
function sparkline(points, status, lo, hi) {
  const W = 160, H = 30, PAD = 3;
  if (!points || points.length < 2) {
    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"></svg>`;
  }

  // scale to the data and the target band together, so the band is always visible
  let min = Math.min(...points, lo == null ? Infinity : lo);
  let max = Math.max(...points, hi == null ? -Infinity : hi);
  if (!isFinite(min) || !isFinite(max)) { min = Math.min(...points); max = Math.max(...points); }
  const span = (max - min) || 1;
  const y = (v) => PAD + (H - PAD * 2) * (1 - (v - min) / span);
  const step = W / (points.length - 1);

  let band = '';
  if (lo != null && hi != null) {
    const top = y(hi), bottom = y(lo);
    band = `<rect x="0" y="${top.toFixed(1)}" width="${W}"
      height="${Math.max(1, bottom - top).toFixed(1)}" fill="var(--accent)" opacity="0.10"/>`;
  }

  const d = points.map((v, i) => `${(i * step).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const stroke = status === 'ok' ? 'var(--text-2)'
    : status === 'warn' ? 'var(--warn)' : 'var(--bad)';
  const last = points[points.length - 1];

  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
    ${band}
    <polyline fill="none" stroke="${stroke}" stroke-width="1.25" vector-effect="non-scaling-stroke"
      stroke-linejoin="round" stroke-linecap="round" points="${d}"/>
    <circle cx="${W - 1}" cy="${y(last).toFixed(1)}" r="1.75" fill="${stroke}"
      vector-effect="non-scaling-stroke"/>
  </svg>`;
}
