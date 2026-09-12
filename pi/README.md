# pi

everything runs here. broker, control loop, storage, dashboard, alerts. the pi 4b sits in the control
box at the tower with the hat on its 40 pin header. there is no microcontroller and no firmware.

spec: [spec.md](../spec.md) · board: [PCB/README.md](../PCB/README.md)

## five files

| file | role |
|---|---|
| `config.py` | the numbers you might change. short on purpose |
| `control.py` | reads the hat, drives lights and dosing, writes csv, publishes readings |
| `store.py` | the sqlite schema, the mqtt subscriber that fills it, csv backfill, a report |
| `web.py` | the dashboard server, and the alerts watcher |
| `dashboard.html` | the whole frontend: markup, style and script in one file |

plus `systemd/`, three unit files and an installer, and `requirements.txt`. mosquitto is a
system service, not in this repo.

mqtt is local only, but it stays. it keeps the control loop, the database, and the dashboard from
knowing about each other.

`config.py` is the only file that should need editing: broker host, the two tank numbers, probe
calibration, photoperiod, dosing constants and targets, the healthy bands the dashboard colours by
and the alerts fire on, and the ntfy topic. lines marked MEASURE are guesses until they are
measured. everything the hat fixes, the pin map, i2c addresses, uart and 1-wire paths, the level
sensor's blind zone, the "sensor is broken" ranges, mqtt topic names, is the block at the top of
`control.py`. it should agree with [PCB/README.md](../PCB/README.md) and should not need touching.

## control service

python, systemd. talks to the hat over i2c, 1-wire, uart and gpio.

- sensor drivers behind one interface, each publishing `{value, unit, timestamp, valid}`
- one sweep a minute, in order. a pump pulse blocks the loop for the few seconds it
  runs, on purpose: nothing else should be happening while a pump is on
- systemd restarts on crash, the pi hardware watchdog is the backstop
- **never publish a reading without `valid`.** a failed probe reads as a plausible number, and a
  plausible wrong number is worse than a gap

### running it

```
cd pi
python control.py --simulate --once      one sweep of fake data
python control.py --simulate             loop, no hardware needed
python control.py                        real sensors, on the pi
python control.py --interval 10          override the 60s period
```

on the pi first: `pip install -r requirements.txt`. on a laptop just `flask` and
`paho-mqtt`, and use `--simulate`. hardware mode fails with a clear message instead
of a stack trace if the libraries aren't there.

`control.py` top to bottom: the hat constants, the `Reading` type (value + unit + ts +
valid + note), the four drivers (ds18b20 over 1-wire, sht31 over i2c, jsn-sr04t over
uart with a median of 5, ph and ec through the ads1115 with the supply switched), the
three outputs (a pwm channel, the lights schedule, the dosing state machine), the daily
csv, the mqtt publisher, the loop.

### read order is not arbitrary

water temp is read **first** because ec needs it. a dc excitation conductivity probe
reads high in warm water, so an ec number without a temperature to compensate
against isn't a measurement. if water temp comes back invalid the ec reading is
marked invalid too rather than being quietly wrong.

the probes are read **last** because they're the slowest. two power-on settles at 2s
each means a sweep takes about 4 seconds no matter what else is on the bus.

### the csv

one file per day, `data/readings-YYYY-MM-DD.csv`:

```
ts,iso,sensor,value,unit,valid,note
1788319293,2026-09-02T03:21:32+00:00,water/temp,20.93,C,1,
1788319293,2026-09-02T03:21:32+00:00,water/level,15.83,L,1,distance 278mm
1788319327,2026-09-02T03:22:06+00:00,water/ph,,pH,0,ads1115 read failed
```

columns match the sqlite schema below on purpose, so the migration is an import and
not a rewrite. `iso` and `note` are extra: one so the file is readable without
converting epochs by hand, the other so a failed row says why.

**failed readings are written, not dropped.** that third row is the whole point.

### publishing

readings go to **csv first**, then to the broker. the csv is the durable record and
it must not depend on anything happening on the network. if mosquitto is down, or
paho isn't installed, publishing fails quietly and the loop carries on. a monitoring
transport that can take down the thing it monitors is worse than no transport.

```
python control.py --simulate            publishes to localhost
python control.py --no-mqtt             csv only
python control.py --broker 10.0.0.5     somewhere else
```

**everything is retained.** a dashboard that connects at 3pm should see the current
value of every sensor immediately, not a blank screen until the next sweep lands a
minute later. these are state topics, not an event stream.

**the broker announces our death, we don't.** a last will is registered on connect,
so if the process is killed or the pi loses power the broker publishes
`hydro/state/online {"online": false}` on our behalf. a heartbeat we send ourselves
cannot report that we stopped being able to send heartbeats. on a clean shutdown we
publish `online: false` first so the will never fires spuriously.

`hydro/state/fault` carries the sensors currently failing, retained:

```
{"failing": ["water/level", "water/ph"], "count": 2, "timestamp": 1788319633}
```

empty list means healthy. this is what the fault log and the alerts read.

### lights

photoperiod with pwm dimming on the hat's led mosfet, gpio 18. no measured constants
needed, so this one just works.

```
LIGHT_ON_HOUR = 6.0        LIGHT_OFF_HOUR = 22.0     a 16 hour photoperiod
LIGHT_BRIGHTNESS = 1.0     LIGHT_RAMP_MINUTES = 10   fade in and out
```

`scheduled_duty()` is a pure function of the clock, so any hour can be tested
without waiting for it. handles a window that crosses midnight, e.g. on 20:00 off
04:00. `hydro/cmd/lights {"duty": 0.5}` forces a level, `{"auto": true}` hands
control back to the schedule.

### dosing

three pumps: floramicro, floragro, ph down. this is the only code in the project
that can destroy a tank, so it is built to refuse rather than to try.

```
IDLE --(out of band)--> DOSING --> MIXING --> VERIFYING --> IDLE
                                                  |
                                                  +--(reading did not move)--> FAULT
```

**it will not dose using guessed numbers.** `DOSING_CALIBRATED` is False and while
it is, the controller runs in simulation and refuses to drive a real pump. three
constants have to be measured first, none of them derivable:

| constant | how to get it |
|---|---|
| `DOSE_FLOW_ML_PER_S` | run each pump 60s into a measuring cylinder |
| `EC_PER_ML` | dose 5 mL, wait for it to mix, read the delta |
| `PH_PER_ML_DOWN` | same, and it is non-linear because the solution buffers |

**one channel per dose event, micro before gro.** micro and gro added together
without mixing between them precipitates calcium phosphate and locks the nutrients
out. the controller alternates to hold the ratio.

**ec is corrected before ph**, because adding nutrients moves ph and doing it the
other way round just means doing it twice.

**ph down has its own much smaller cap.** the generic 5 mL per event is a sensible
nutrient dose and a large ph swing. overshooting ph is easy and hard to walk back.

interlocks, any one of which blocks a dose: dosing disabled, in fault, not
calibrated, ec invalid, ph invalid, no water temp, level invalid, level under
`DOSE_MIN_LEVEL_L` (5 L, about half the tank), too soon since the last dose, hourly
runtime cap reached.

**verification is what replaced the float switches.** after mixing, the reading has
to have moved at least `DOSE_VERIFY_FRACTION` of what was expected, and no more
than `DOSE_VERIFY_MAX_FRACTION` of it. too little is an empty bottle or a slipped
tube or a dead pump; too much is a pump that did not stop or a flow rate measured
wrong. either way the state machine goes to FAULT and stays there until a human
clears it. dosing harder is never the right response to a dose that did not land.

### the two things that can actually cause damage

**dosing runtime is capped per hour, enforced in the control service.** a stuck doser will happily
empty a bottle of ph down into the tank. this is the single worst failure mode in the build and the
only thing standing in front of it is this cap. dose in short timed bursts, never a continuous run.

**the probes have to be read one at a time.** powered ph and ec probes in the same tank leak current
through the solution and corrupt each other. the hat has a mosfet on each probe supply, so the loop
is: ph on, ec off, settle, sample. then swap. then both off. settling time needs measuring, start
around 2 seconds.

## nothing switches the pump

the pump plugs straight into a gfci outlet and runs continuously while the lights are on. there is no
code path that can stop the water, so there's no interlock to get wrong.

the cost of that is the alerts are the *only* thing between a problem and dead plants. build them
properly.

## alerts

```
python web.py                         the dashboard runs the watcher too
python web.py --alerts-only           just the watcher
python web.py --dry-run               print what would be sent, send nothing
python web.py --test-alert            send one test notification and exit
```

the watcher is a thread in `web.py`, because the dashboard and the phone are the two ways the
system talks to a human and one process for both is enough. notifications go through
[ntfy](https://ntfy.sh): install the app, subscribe to a topic name nobody would guess, put it in
`ALERT_NTFY_TOPIC`. no account, no key, one http post. with no topic set it prints to the journal.

| condition | key | meaning |
|---|---|---|
| level below `ALERT_LEVEL_LOW_L` | `level_low` | top up the tank |
| **level rises `ALERT_LEVEL_RISE_L` inside 15 min** | `level_rise` | **probable pump failure.** the 1 to 2 L held up in the tower has drained back down. a top up trips it too, and the message says so. this is the substitute for the flow sensor i cut |
| dosing in fault | `dosing_fault` | the dose didn't land, or overshot. empty bottle, slipped tube, dead pump, wrong flow rate. the control service already stopped dosing |
| reading outside its band for 30 min | `band:<sensor>` | ph, ec, water temp, air temp, humidity. bands are `BANDS` in config.py, the same ones the dashboard colours by |
| sensor invalid for 10 min | `fail:<sensor>` | one bad sweep is a blip. ten minutes is a probe |
| last will fired, or no heartbeat for 5 min | `offline` | control service or pi is down |

every condition is a state, not an event: one message when it starts, a reminder every
`ALERT_REPEAT_S` while it lasts, one when it clears. the level rise is the exception, it is an
event and gets no "cleared". the rules are a pure class fed with timestamps, so a day of them
can be run through in a millisecond.

what is active is also published retained on `hydro/state/alerts`, and the dashboard's status
line shows it.

the level-rise one is the important one. it's a heuristic not a measurement, but it uses a sensor
that's already fitted and it catches the failure mode that kills plants fastest.

the dose-verification alert is why i dropped the float switches. a float switch only catches an
empty bottle. watching ec and ph respond catches an empty bottle *and* a slipped tube *and* a dead
pump, for free.

## mqtt topics

```
hydro/sensor/water/level      hydro/sensor/water/temp
hydro/sensor/water/ph         hydro/sensor/water/ec
hydro/sensor/air/temp         hydro/sensor/air/humidity
hydro/state/lights            hydro/state/dosing
hydro/state/fault             hydro/state/online
hydro/state/alerts            hydro/cmd/lights
hydro/cmd/dose
```

payload: `{value, unit, timestamp, valid}`, plus `note` when something failed:

```json
{"value":20.93,"unit":"C","timestamp":1788319293,"valid":true}
{"value":null,"unit":"pH","timestamp":1788319520,"valid":false,"note":"ads1115 read failed"}
```

an invalid reading that doesn't say why is only half a fault report.

## ingest

```
python store.py ingest                     subscribe and write
python store.py ingest --verbose           print every row
python store.py backfill data/             load the csv files
python store.py report                     what is in there
python store.py report --faults            what has been failing
python store.py report --history water/temp --hours 24
```

runs as its own process, not inside the control service. that separation is the
whole reason mqtt exists on a single machine: the control loop must not stop
reading sensors because the database is locked, and the database must not miss a
day because the control loop crashed.

**the network callback never touches the database.** paho calls `on_message` from
its own thread, and a sqlite connection belongs to the thread that opened it.
messages are parsed and queued there; the main thread owns the database and drains
the queue in batches every 2s. found this the hard way, it throws
`SQLite objects created in a thread can only be used in that same thread`.

**the broker is not trusted.** anything can publish to it: a test script, a half
finished dashboard, something bolted on later. every payload is validated again
here and a malformed one is logged and dropped, never written. a payload that is
nearly right is still wrong, and guessing what it meant is how bad data gets into
the table that is supposed to be the record.

### backfill

`store.py backfill` loads the control loop's csv straight into the table. the csv
columns were picked to match the schema, so it is a load and not a transformation.
safe to run twice.

## dashboard

```
python web.py                          http://0.0.0.0:8080
python web.py --demo                   a fake day to look at, no tower needed
python web.py --port 8099 --db somewhere.sqlite
```

`dashboard.html` is the whole frontend, one file, served as is: two views switched on
the url hash, the status header shared between them.

**overview** is the tower drawn as the machine: four modules with their sockets,
the corner rails lit when the strip is on, the supply pipe running through, and the
tank filled to its real level with a waterline. every reading sits where it is
actually measured. under it, six trend cards with a 24h sparkline each, because the
question is usually "is it drifting", not "what is it right now".

**activity** is what the system has been doing, and it answers its own question
before the log is read: **is anything running out, and is dosing healthy.**

- doses and faults in the last 24 h as counters
- **concentrate remaining per bottle.** there is no level sensor in them by design,
  because dose verification catches an empty bottle *and* a slipped tube *and* a
  dead pump where a float switch only catches the first. so this is arithmetic on
  what was dosed, labelled as an estimate, reset by hand on a refill
- a 24 h density strip showing when things happened, so the rhythm is visible
  without reading timestamps
- the log itself, filterable to doses, lighting or faults, grouped by day, with each
  dose paired to whether the reading actually moved afterwards

### the status line

the header carries one line answering "is anything wrong", so the six cards below
never have to be read to find out. it aggregates every sensor being out of band or
failing, the dosing state, and whether the control service is reporting at all, then
says either **all systems nominal** or **n issues need attention** with the list.

one snapshot poll feeds it and the overview; the activity log polls on its own,
slower. both pause while the tab is hidden so a phone left on the dashboard does not
keep waking the pi.

### design

everything comes from tokens at the top of `dashboard.html`: one colour set redefined for
dark, a 6 step type scale, a 4px spacing scale. nothing below that block hardcodes a
value, so the whole app retints from one place.

- **stale readings dim and say so** rather than presenting an old number as current
- **the target band is drawn behind each sparkline**, so "is this drifting out of
  range" is answerable without reading an axis
- **clear fault is disabled unless there is a fault.** a control that does nothing is
  worse than no control
- numbers are tabular throughout, so columns of readings do not jitter as they update

### it reads sqlite and writes mqtt, nothing else

it never touches a sensor and never touches a gpio. display comes out of the
database the ingest service fills. a button publishes on `hydro/cmd/...` and the
control service picks it up on its next sweep. so the dashboard can crash, restart,
or be open in six tabs and none of it reaches the hardware.

**a button reports that the message left, not that it worked.** with no broker the
api returns `503 {"ok": false, "error": "no broker"}` and the header says so. the
real answer arrives on the next sweep when the state topic changes.

### the pump button is disabled on purpose

there is no relay, no ssr, nothing on a gpio. the pump plugs straight into the gfci
outlet. the button is rendered disabled with an explanation rather than left out,
because a missing control looks like an oversight and a fake one is a lie.

if i ever want it: an mqtt smart plug is about $12 and keeps mains switching inside
a device certified to do it. an ssr on a gpio means building a 120 v circuit next to
an open tank, and the hat's four load mosfets are already fully committed.

### derived, not stored

the state topics are republished every sweep, so the raw `event` table is mostly the
same row over and over. the timeline only keeps transitions. consecutive identical
faults collapse into one line carrying how long it went on, otherwise a sensor that
fails for ten minutes buries everything else.

## storage

sqlite. schema written so postgres is a swap and not a rewrite. no sqlite specific types, no implicit
`rowid` dependence.

migrations are the `SCHEMA` list in `store.py`, applied in order and recorded in
`schema_version`. `Store.migrate()` runs on every startup and does nothing if there
is nothing new.

```sql
CREATE TABLE reading (
    ts      INTEGER NOT NULL,          -- unix epoch seconds, utc
    sensor  TEXT    NOT NULL,          -- matches the mqtt topic suffix
    value   REAL,                      -- NULL when the reading failed
    unit    TEXT    NOT NULL,
    valid   INTEGER NOT NULL,          -- 0/1
    note    TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (sensor, ts)
);
```

two things changed from the draft above, both for a reason:

**`value` is nullable now.** an invalid reading has no value by definition, and
sqlite silently converts a stored NaN to NULL anyway, so `NOT NULL` would have
rejected every single fault row. the whole point of keeping invalid rows is to
distinguish "the probe was failing" from "the pi was off".

**the key is `(sensor, ts)`, not a surrogate id.** that is what makes ingest
idempotent. mqtt qos 1 is *at least once* and every topic is retained, so a
reconnecting subscriber is handed the last value of everything again. tested: two
ingest runs against the same retained set leave 6 rows, not 12.

there is also an `event` table holding the raw `hydro/state/#` payloads, which is
where the fault log reads from.

**store invalid readings instead of dropping them.** a gap in the data looks identical to "the pi was
off". an explicit `valid = 0` row tells me the probe was failing, which is the thing i actually want
to know six weeks later.

## on the pi

raspberry pi os bookworm. once:

```
sudo raspi-config        # interface options: enable i2c, 1-wire, serial port
                         # (login shell over serial: no. serial port hardware: yes)
sudo apt install mosquitto python3-venv
sudo systemctl enable --now mosquitto
sudo usermod -aG gpio,i2c,dialout $USER      # then log out and in

cd pi
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
sudo systemd/install.sh
```

the installer writes three units, `hydro-control`, `hydro-store` and `hydro-web`, with the repo
path and your user filled in, enables them and starts them. `journalctl -u hydro-control -f` to
watch. the control unit stops with SIGINT and a 15 s grace so the pumps are switched off in the
`finally` on the way down; the loop sleeps in half second slices for the same reason.

then, in this order:

1. `mosquitto_sub -t 'hydro/#' -v` showing traffic
2. bench each sensor alone against a known reference. the probes need the two buffer readings in
   config.py before ph means anything
3. measure the level sensor's real blind zone and the sensor face height with the cap on, and put
   them in config.py
4. run each pump 60 s into a cylinder, dose 5 mL and read the delta, fill in the three MEASURE
   blocks, flip `DOSING_CALIBRATED`
5. set the ntfy topic, `python web.py --test-alert`
