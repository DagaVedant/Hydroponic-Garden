/* Overview: schematic annotations, trend cards, controls, photoperiod strip. */

const CARDS = document.getElementById('cards');
const STALE_AFTER_S = 300;

function setRO(id, text, status) {
  const dd = document.getElementById(id);
  if (!dd) return;
  dd.innerHTML = text;
  const box = dd.closest('.ro');
  if (box) {
    box.classList.remove('warn', 'bad');
    if (status && status !== 'ok') box.classList.add(status);
  }
}

function unit(v, u) { return `${v}<span class="u">${u}</span>`; }

function drawSchematic(data) {
  const by = {};
  data.sensors.forEach((s) => { by[s.sensor] = s; });

  [['water/ph', ''], ['water/ec', ' uS/cm'], ['water/temp', ' C'],
   ['water/level', ' L']].forEach(([key, u]) => {
    const s = by[key];
    if (!s) return;
    setRO('ro-' + key.replace('/', '-'),
      s.valid ? unit(fmt(s.value, key), u) : 'No reading',
      s.status);
  });

  const at = by['air/temp'], rh = by['air/humidity'];
  const airOk = at && at.valid && rh && rh.valid;
  setRO('ro-air',
    airOk ? unit(`${fmt(at.value, 'air/temp')} C / ${fmt(rh.value, 'air/humidity')}%`, '')
          : 'No reading',
    airOk ? (at.status === 'ok' && rh.status === 'ok' ? 'ok' : 'warn') : 'bad');

  /* Tank drawn to its real fill. Full is the fill line from config, not the
     bucket's capacity: the level sensor's blind zone is what sets it. */
  const level = by['water/level'];
  const fill = document.getElementById('tank-fill');
  const line = document.getElementById('tank-line');
  const label = document.getElementById('tank-label');
  const TOP = 160, FULL = 36.5;
  if (level && level.valid && fill) {
    const frac = Math.max(0, Math.min(1, level.value / (data.tank_full_l || 10.6)));
    const top = TOP + FULL * (1 - frac);
    fill.setAttribute('height', (FULL * frac).toFixed(1));
    fill.setAttribute('y', top.toFixed(1));
    line.setAttribute('y1', top.toFixed(1));
    line.setAttribute('y2', top.toFixed(1));
    label.textContent = fmt(level.value, 'water/level') + ' L';
  } else if (fill) {
    fill.setAttribute('height', '0');
    line.setAttribute('y1', '-10'); line.setAttribute('y2', '-10');
    label.textContent = '';
  }

  const lights = data.lights || {};
  const lit = !!lights.on;
  const duty = lights.duty || 0;
  ['rail-l', 'rail-r'].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.setAttribute('fill', lit ? 'var(--lit)' : 'var(--line-mid)');
    el.setAttribute('opacity', lit ? Math.max(0.4, duty).toFixed(2) : '1');
  });
  setRO('ro-lights', lit ? unit(Math.round(duty * 100), '%') : 'Off', 'ok');

  const dosing = data.dosing || {};
  const stateLabel = { idle: 'Idle', dosing: 'Dosing', mixing: 'Mixing',
                       verifying: 'Verifying', fault: 'Fault' }[dosing.state] || '--';
  setRO('ro-dosing', stateLabel, dosing.state === 'fault' ? 'bad' : 'ok');

  /* The pump is never switched, so its state is inferred from the control service
     still reporting, not read from anything. */
  setRO('ro-pump', data.online ? 'Running' : 'Unknown', data.online ? 'ok' : 'warn');
}

function drawCards(data) {
  CARDS.innerHTML = data.sensors.map((s) => {
    const stale = s.age != null && s.age > STALE_AFTER_S;
    const cls = [s.status === 'ok' ? '' : s.status, stale ? 'stale' : ''].join(' ').trim();
    const value = s.valid ? fmt(s.value, s.sensor) : '--';

    const tag = !s.valid ? '<span class="tag bad">Fault</span>'
      : s.status === 'warn' ? '<span class="tag warn">Out of band</span>' : '';

    const trend = s.delta == null ? ''
      : (s.delta > 0 ? '+' : '') + s.delta + ' / 24h';
    const range = s.lo != null ? `${s.lo}-${s.hi}` : '';

    return `<article class="card ${cls}">
      <div class="top"><span class="name">${s.label}</span>${tag}</div>
      <div class="val">${value}<span class="u">${s.unit}</span></div>
      <div class="spark">${sparkline(s.series, s.status, s.lo, s.hi)}</div>
      <div class="foot">
        <span>${s.valid ? trend : (s.note || 'No reading')}</span>
        <span>${s.valid ? range : ''}</span>
      </div>
    </article>`;
  }).join('');
}

function drawControls(data) {
  const lights = data.lights || {};
  const mode = lights.mode === 'manual' ? (lights.on ? 'on' : 'off') : 'schedule';
  document.querySelectorAll('[data-mode]').forEach((b) => {
    b.setAttribute('aria-pressed', String(b.dataset.mode === mode));
  });
  const pad = (h) => String(h).padStart(2, '0') + ':00';
  document.getElementById('lights-note').innerHTML = lights.on_hour != null
    ? `<strong>${pad(lights.on_hour)}-${pad(lights.off_hour)}</strong>, currently ${Math.round((lights.duty || 0) * 100)}%`
    : 'No state reported yet';

  const dosing = data.dosing || {};
  document.querySelectorAll('[data-dose]').forEach((b) => {
    b.setAttribute('aria-pressed', String(b.dataset.dose === (dosing.enabled ? 'on' : 'off')));
  });
  // clearing a fault that does not exist is a no-op dressed up as a control
  const clear = document.getElementById('clear-fault');
  if (clear) clear.disabled = dosing.state !== 'fault';

  const note = document.getElementById('dose-note');
  if (dosing.state === 'fault') {
    note.innerHTML = `<strong>Halted.</strong> ${dosing.fault || ''}`;
  } else if (dosing.calibrated === false) {
    note.innerHTML = '<strong>Not calibrated.</strong> Pumps will not run until flow rates are measured';
  } else {
    note.innerHTML = `${dosing.runtime_last_hour_s || 0}s of <strong>${dosing.cap_s || 0}s</strong> hourly limit used`;
  }

  document.getElementById('pump-meta').textContent =
    data.online ? 'Running continuously, not switchable' : 'Control service offline';
}

/* Two rectangles so a window crossing midnight draws as two segments rather than
   one impossible negative-width bar. */
function drawSchedule(data) {
  const lights = data.lights || {};
  const on = lights.on_hour, off = lights.off_hour;
  if (on == null || off == null) return;

  const W = 620, x = (h) => (h / 24) * W;
  const a = document.getElementById('sched-lit');
  const b = document.getElementById('sched-lit2');

  if (on <= off) {
    a.setAttribute('x', x(on)); a.setAttribute('width', x(off) - x(on));
    b.setAttribute('width', 0);
  } else {
    a.setAttribute('x', x(on)); a.setAttribute('width', W - x(on));
    b.setAttribute('x', 0); b.setAttribute('width', x(off));
  }

  const now = new Date();
  const nowX = x(now.getHours() + now.getMinutes() / 60);
  const line = document.getElementById('sched-now');
  line.setAttribute('x1', nowX); line.setAttribute('x2', nowX);

  const hours = on <= off ? off - on : (24 - on) + off;
  document.getElementById('sched-note').textContent =
    `${hours} hours of light per day. The marker is now.`;
}

function render(data) {
  drawSchematic(data);
  drawCards(data);
  drawControls(data);
  drawSchedule(data);
}

document.addEventListener('hydro:snapshot', (e) => render(e.detail));
wireCommands();
