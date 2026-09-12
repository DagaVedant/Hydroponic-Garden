# pi

everything runs on the pi 4b with the hat on its header. no microcontroller.

| file | role |
|---|---|
| `config.py` | the numbers you might change. lines marked MEASURE are guesses |
| `control.py` | reads the hat, drives lights and dosing, writes csv, publishes to mqtt |
| `store.py` | sqlite schema, the mqtt subscriber that fills it, csv backfill, a report |
| `web.py` | the dashboard server and the alerts watcher |
| `dashboard.html` | the frontend, one file |

## run

```
python control.py --simulate        the loop with fake sensors. --once for one sweep
python store.py ingest              broker to sqlite
python store.py backfill data/      csv to sqlite, safe to repeat
python store.py report              what is in there. --faults, --history water/ph
python web.py                       http://0.0.0.0:8080, alerts watching. --demo for a fake day
python web.py --test-alert          one ntfy notification
```

on a laptop `pip install flask paho-mqtt` is enough. on the pi:

```
sudo raspi-config                   enable i2c, 1-wire, serial port hardware (no login shell)
sudo apt install mosquitto python3-venv
sudo usermod -aG gpio,i2c,dialout $USER
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
sudo systemd/install.sh             three units: hydro-control, hydro-store, hydro-web
```

## before it can dose

dosing refuses to run a real pump until the MEASURE blocks in `config.py` are filled in and
`DOSING_CALIBRATED` is flipped: the two ph buffer voltages, each pump's flow rate (60 s into a
cylinder), ec and ph change per mL (dose 5 mL, wait, read the delta). also measure the level
sensor's face height and real blind zone with the cap on, and put an ntfy topic in
`ALERT_NTFY_TOPIC` for phone alerts.

## what it will not do

nothing switches the circulation pump. it plugs into a gfci outlet and runs. the alerts are what
stands between a problem and dead plants: level low, level rose (the pump stopped and the tower
drained back), a dose that did not land, a reading out of band, a sensor failing, the control loop
going quiet.
