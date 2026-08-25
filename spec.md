# spec

everything about the tower. what it is, how it's built, how water moves through it, and what runs it.

numbers live in [parameters.md](parameters.md). this file explains what
they mean.

---

## what it is

a vertical hydroponic tower for indoor leafy greens and herbs. modular, 3d printed, fully parametric.

- 16 plants, 4 modules of 4
- module is ⌀190 × 200mm
- tower is ~800mm, ~1.35m with the tank
- one pump, one pipe, gravity does the rest
- `MODULE_COUNT = 6` gives 24 plants with no redesign

grown bare root. a net pot with a rockwool cube, no clay pebbles or bulk media. roots hang free in the
chamber and get wet as water passes.

---

## how it waters

one pump lifts water to the top. from there the shape of each part does the work.

```
   pump ──► supply pipe ──► jet splitter ──► falling ring of water
                                                  │
              ┌───────────────────────────────────▼───────────────┐
              │  water spreader ──► 4 spouts ──► walls            │
              │  roots ──► root grate ──► sloped floor            │  every
              │  gutter ──► 8 drip holes ────────────────────────►│  module
              └───────────────────────────────────────────────────┘
                                    ▼
                              back to the tank
```

the important bit: **every module re-collects and re-distributes.** a single cone at the top turns
into wall film by module 2 and a couple of streams by module 3. giving each module its own spreader
is what makes the tower scale.

---

## part names

| name | what it is |
|---|---|
| **grow module** | the stackable box. four plant sockets, root chamber, sloped floor |
| **plant socket** | the tapered 45° hole a net pot drops into. four per module |
| **socket tube** | the short tube crossing the wall diagonally that forms the socket |
| **root grate** | mesh shelf near the floor. holds roots up, screens debris |
| **water spreader** | cone near the module top. catches water from above, throws it at the four sockets |
| **jet splitter** | swappable cone in the tower lid. turns the pump's upward jet into a falling ring |
| **tower lid** | caps the tower, holds the pipe end, carries the jet splitter |
| **pipe tunnel** | dry vertical tube up the middle. the supply pipe runs inside it. also the main structural column |
| **sloped floor** | the two 45° cones that make up each module's floor |
| **gutter** | ring channel where the two cones meet. collects everything |
| **drip holes** | eight ⌀8 holes in the gutter that feed the next module's spreader |
| **joint lip** | step and recess rim where modules stack. catches splash, not a pressure seal |
| **drain base** | bottom part. sends the last module's water back to the tank |
| **tank lid plate** | printed disc in the gamma seal lid. carries the pipe, level sensor, temp probe |
| **corner rail** | clip on channel for the led strip and cables |
| **supply pipe** | 1/2" pvc from the pump to the tower lid. bought, not printed |
| **tank** | 5 gal bucket, 18.9 L |

**directions.** outboard means away from the tower centre, the plant side. inboard means toward the
centre, into the root chamber. heights measure up from the bottom of that module. `_DIA` is always a
diameter, never a radius.

---

## structure

### the module

⌀190 cylinder, 200 tall, 3mm wall. chamber is ⌀184 inside.

**pipe tunnel** runs up the middle. ⌀29 outside, ⌀23 bore, full height. the pvc pipe slides through
it and **water never touches the inside**, so there's no pipe-through-water seal to get right
anywhere in the design. the stacked tunnels are also the tower's compression column.

**four ribs** run up the outside at 45° between the sockets. 14 wide, 8 thick. they take bending,
carry the module-to-module bolts and alignment pins, and hold the led channel and cable rail.

### how modules stack

- 4 × m4 bolts into brass heat set inserts, one per rib
- circular lip self centres them, 2 × ⌀5 pins set the rotation
- 2.5mm step and recess joint lip, 0.3mm clearance

**no o-rings.** the cascade isn't pressurised. the lip catches splash and that's all it needs to do.

### printing

everything prints upright, gutter flat on the plate, **no supports**. socket angle and both floor
cones are 45° for exactly this reason. if a part needs supports the geometry is wrong.

bed footprint is ⌀241.4, which is `MODULE_DIA` plus 25.7mm of socket tube reaching past each wall.
that's why the module is 190 and not 200.

---

## the water path

### tower lid and jet splitter

the supply pipe ends pointing **up**, about 30mm below an inverted cone. the jet hits the cone and
turns into a falling ring of water, which is the same thing every module's spreader expects. so the
lid and every module are hydraulically identical.

the splitter is a **separate removable insert**, so drip, spray and other distributor designs can be
swapped in without reprinting the lid. that's the experimentation port.

### water spreader

conical collar that slides over the pipe tunnel near the top of each module.

| | |
|---|---|
| rim | ⌀142, 20° cone, apex at z = 193 |
| spouts | 4, at 90°, reaching r = 85 |
| lands on the wall at | z = 168 |

**four spouts, not a smooth cone.** a smooth ⌀142 cone in a ⌀184 chamber drips off its rim 20mm short
of the wall, and low flow drip is chaotic. directed spouts aim water at the four socket zones on
purpose. print it apex down, as a bowl.

### plant sockets

four per module, at 0 / 90 / 180 / 270°, angled 45° up and out.

| | |
|---|---|
| axis height | z = 125 |
| socket face | ⌀50.6, 8mm outboard along the axis |
| socket base | ⌀43.9 at 15mm depth |
| taper | 0.443 mm per mm, 25° included |
| clearance bore | ⌀44.9 past the seat |
| tube | ⌀56.6 outside, 3mm wall, 22mm long |
| hole in the wall | 45 × 63.6 oval, z = 93 to 157 |

**the pot sits on the taper, not on its lip.** the lip is ⌀52.8 and the body just under it is barely
narrower, which leaves about 1.5mm of ledge. that is not a seat, it's a part that falls through.

pot and socket share the same taper, so they meet in **full conical contact**. the pot slides in 5mm
and its lip finishes 5mm proud as a grab handle. a pot 1mm oversize just seats 2mm shallower and still
grips, which matters because the vendor's stated dimensions were wrong by 2.2mm.

**it's a tube through the wall, not a collar on it.** tilted 45°, the lower rim projects about 25.7mm
outboard while the upper rim sits inside the wall. keep `SOCKET_FACE_OFFSET` at 8. bigger and the pot
tip stops short of the chamber, leaving the root ball outside the wet zone.

### root grate

⌀180 disc sitting on a 6mm ledge at z = 34, so it occupies z = 34 to 38.

8mm openings on 3mm ribs. coarse by design, this is root support and a debris screen, not a filter.
⌀52 centre cutout clears the inner floor cone. **lifts out through the module top.**

treat it as a **consumable**. pla, reprinted when biofilm builds up.

### sloped floor, gutter, drip holes

two 45° cones meeting at a ring gutter.

```
   wall                                                  wall
   r=92                                                  r=92
    |  \                                             /   |
    |    \   outer cone, 45° down and in          /      |   z=34
    |      \___  gutter ___/‾\___ gutter ___/            |   z=8
    |            (r=60)     / \                          |
    |                    /       \                       |
    |                 /  inner cone, 45°  \              |   z=47.5
                       pipe tunnel r=14.5
```

| | |
|---|---|
| gutter floor | z = 0, flat on the build plate |
| gutter | ⌀108 inner, ⌀132 outer, walls up to z = 8 |
| outer cone | (r 66, z 8) to (r 92, z 34) |
| inner cone | (r 54, z 8) to (r 14.5, z 47.5) |
| drip holes | 8 × ⌀8 on a ⌀120 circle |

the inner cone prints as a **roof**, each layer smaller than the one below, so it self supports as
easily as the outer one.

the drip holes land on the next spreader at r = 60, near its rim rather than its apex, so water only
travels 60 to 85mm across the cone instead of the full chamber width. drop between modules is about
27mm, so splash stays low.

### supply pipe

1/2" pvc sch40, ⌀21.34 outside. **one 200mm segment per module**, joined inside each pipe tunnel with
an o-ring push coupler. segmenting is what lets you add or remove modules without cutting pipe.

**the joints don't have to be watertight.** static head is only about 0.13 bar and any weep lands in
the root chamber, which is the same nutrient solution it was already carrying. the hardest sealing
problem in the design just isn't one.

a union and a ball valve sit just above the lid plate.

### flow

target **1 to 4 L/min**, continuous while the lights are on.

---

## electronics

the pi reads five sensors and drives two things: the lights and three dosing pumps. everything hangs
off one custom hat on the 40 pin header. there's no microcontroller. the main pump isn't switched at
all, it runs continuously while the lights are on.

```
   raspberry pi 4b        lives in the control box, at the tower
   ├── mosquitto (mqtt broker, local)
   ├── control service ──► reads the hat, drives lights and dosing
   ├── ingest service ──► sqlite
   ├── web dashboard
   ├── phone alerts
   └── custom hat on the 40 pin header
       ├── water level    jsn-sr04t ultrasonic, uart mode, in the tank lid plate
       ├── water temp     ds18b20 waterproof probe, 1-wire
       ├── air temp + rh  sht31 / sht41, i2c
       ├── ph             analog probe ──► ads1115 16 bit adc, i2c
       ├── ec             analog probe ──► ads1115 16 bit adc, i2c
       ├── led control    mosfet on 24 V, pwm and photoperiod timer
       └── dosing         3 peristaltic pumps on 12 V, 4 channel mosfet board

   pump ──► straight into a gfci outlet. nothing switches it.
```

**the ph and ec probes cross-talk.** two powered probes in the same tank leak current through the
solution and corrupt each other's readings. either buy isolated interface boards, or power them
alternately in firmware: read ph, cut power, let it settle, then read ec.

**no camera and no flow sensor.** ph, ec and dosing are all in this build. the camera needs a mount
600mm off the tower axis to clear the foliage and that's a part i haven't designed. flow is set once by
hand at commissioning instead of being measured continuously.

### pump

growneer sml-630. 550 gph, 2.2m max lift, 30 W, **120 V ac**. outlet is **1/2" npt female**.

it plugs into a **gfci outlet**. mains, standing water, indoor floor. not optional.

### bypass

at 1.3m of lift this pump still puts out roughly 900 to 1100 L/h against a target of 60 to 240 L/h.
that's 5 to 15 times too much. choking it with a valve alone makes a small centrifugal pump run hot
and cavitate, so the excess gets **shed instead of throttled**.

```
   inside the tank
     pump (1/2" npt female)
       └── 1/2" mpt × slip pvc adapter
       └── short pvc stub
       └── union                  ← lifts the pump out without cutting pipe
       └── tee
             └── bypass valve ──► open pipe pointing down into the water
       └── ball valve (fine trim)
       ↓ through the bulkhead in the tank lid plate
   above the lid
       └── supply pipe up the tower
```

both valves and the bypass return live inside the tank, so nothing extra passes through the lid. the
returning bypass flow also stirs the tank, which helps keep nutrients mixed.

**tuning.** no flow meter, so set it once at commissioning. disconnect the supply pipe above the lid,
run into a jug for 30 seconds, and adjust until you collect 0.5 to 2 L. open the bypass first, trim
with the ball valve, then leave them alone.

**wrap the mpt in ptfe tape and don't overtighten.** npt is tapered and the pump housing is plastic.

### safety

nothing can stop the main pump automatically. that's deliberate. it runs continuously while the lights
are on, so there's nothing to switch, and a relay on the mains side would add a 120 v subsystem to
guard a failure mode that doesn't exist. the dosing pumps are the opposite case: 12 v, driven directly
by the pi, and a stuck-on doser will happily empty a bottle of ph down into the tank. dose in short
timed bursts with a hard cap on total runtime per hour, enforced in firmware.

| layer | works if software is broken? |
|---|---|
| gfci outlet | **yes**, passive, mandatory |
| drip tray under the whole assembly | **yes**, passive |
| level sensor → low water phone alert | no |
| dosing runtime cap per hour | no |
| mqtt heartbeat → missed → phone alert | no |
| water temp out of range → phone alert | no |

**this makes the phone alerts the actual safety layer**, not a convenience. build them.

**the accepted gap:** with no flow sensor, a pump that dies or clogs isn't detected directly, and with
bare roots that kills plants in hours. partial substitute worth implementing: **when the pump stops,
the 1 to 2 L held in the tower drains back and the tank level rises.** an unexplained level rise is a
strong pump failure signal. it's a heuristic, not a measurement, but it costs nothing and uses a
sensor already fitted.

### lighting

24 V full spectrum led strip in **aluminium channel** clipped to the four ribs. the thermal path isn't
optional.

about 60 W total for 16 leafy plants. 14 to 16 hours a day, pi timed, pwm dimmable. the channel is
bought, printed clips hold it on.

---

## software

### control service, `pi/control/`

python, runs as a systemd service. talks to the hat over i2c, 1-wire, uart and gpio. no separate
firmware, because there's no separate microcontroller.

- sensor drivers behind one interface, each publishing `{value, unit, timestamp, valid}`
- non blocking loop. dosing and pwm run on their own timers
- systemd restarts it on crash, the pi hardware watchdog is the backstop
- **dosing runtime capped per hour, enforced here.** a stuck doser is the one fault that can wreck a
  whole tank of solution

**never publish a reading without `valid`.** a failed probe reads as a plausible number, and a
plausible wrong number is worse than a gap.

mqtt topics:

```
hydro/sensor/water/level      hydro/sensor/water/temp
hydro/sensor/water/ph         hydro/sensor/water/ec
hydro/sensor/air/temp         hydro/sensor/air/humidity
hydro/state/lights            hydro/state/dosing
hydro/state/fault             hydro/cmd/lights
hydro/cmd/dose
```

alerts to implement: level too low, **level rose unexpectedly** (probable pump failure), water temp
out of range, missed heartbeat.

### raspberry pi — `pi/`

- mosquitto broker, runs as a system service
- `ingest/` mqtt subscriber, validates and writes to the db
- `db/` schema and migrations. sqlite for now. written so postgres is a swap not a rewrite
- `web/` dashboard, live tiles and history
- `alerts/` phone notifications

```sql
CREATE TABLE reading (
    ts       INTEGER NOT NULL,   -- unix epoch seconds, utc
    sensor   TEXT    NOT NULL,   -- matches the mqtt topic suffix
    value    REAL    NOT NULL,
    unit     TEXT    NOT NULL,
    valid    INTEGER NOT NULL    -- 0/1, store invalid readings, don't drop them
);
CREATE INDEX reading_sensor_ts ON reading (sensor, ts);
```

**store invalid readings instead of dropping them.** a gap in the data looks identical to "the pi was
off". an explicit `valid = 0` row tells you the probe was failing, which is the thing you actually
want to know six weeks later.

the pi observes and requests. **it never has authority over the pump.**
