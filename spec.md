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
- `MODULE_COUNT` is one number. a taller tower is a parameter change, not a redesign

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
              │  gutter ──► 4 drip holes ────────────────────────►│  module
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
| **drip holes** | four ⌀10 holes in the gutter, one under each socket, feeding the next module's spreader |
| **joint lip** | step and recess rim where modules stack. catches splash, not a pressure seal |
| **tie rod** | #10-24 zinc rod running the full tower height. clamps the whole stack in compression |
| **rod boss** | ⌀16 pad at each end of every rib that a tie rod passes through |
| **drain base** | bottom part. sends the last module's water back to the tank. four bayonet grooves in its outer wall lock it onto the tank cap's hooks with a 20° twist |
| **tank lid plate** | the printed cap. a shallow cup that screws into the tank ring. carries the pipe, the level sensor pod, the probe cables and the four hooks the drain base locks onto |
| **tank ring** | printed, one piece, snaps over the bucket rim on twelve fingers and carries the internal thread the cap screws into. does what a gamma seal ring does, so no gamma seal is bought |
| **sensor pod** | socket moulded into the tank lid plate, 50mm off the tank axis, that holds the level sensor |
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
anywhere in the design. the stacked tunnels also stiffen the tower against bending.

**four ribs** run up the outside at 45° between the sockets. 14 wide, 8 thick. they take bending,
carry the tie rods, hold the alignment pins, and mount the led channel and cable rail.

each rib has a **⌀16 boss at its top and bottom end**, 20 tall, with a ⌀6.5 hole through it. that's
the only place a rod passes through plastic. in between it runs in open air alongside the rib. a rod
in tension can't buckle, so it needs no support along its length, and 20mm of hole prints straight
where 200mm would wander off axis.

### how modules stack

- **4 × #10-24 tie rods** run the full height of the tower, drain base to tower lid, nyloc nuts and
  washers at both ends. tighten the top nuts and the whole stack goes into compression
- rods sit on a ⌀198 circle at 45°, clear of every socket and outside the wet chamber
- circular lip self centres them, 2 × ⌀5 pins set the rotation
- 2.5mm step and recess joint lip, 0.3mm clearance

**no bolts and no heat set inserts.** bolted joints load plastic in pull-out, which is the direction
it's worst at, and petg creeps under sustained load so they work loose over months. rods load the
whole column in compression instead. 4 rods and 8 nuts replace 32 pieces, there's no soldering iron
in the build, and nothing is melted into a part i might want to recycle later.

**the clearance hole is ⌀6.5 on a ⌀4.83 rod on purpose.** a rod crosses four modules over 800mm and
printed hole positions won't agree that closely. the washers cover the slop.

**no o-rings.** the cascade isn't pressurised. the lip catches splash and that's all it needs to do.

### printing

everything prints upright, gutter flat on the plate, **no supports**. socket angle and both floor
cones are 45° for exactly this reason. if a part needs supports the geometry is wrong.

**print the module turned 45° about the vertical axis.** it stands upright either way, gutter flat on
the plate; turning it only changes which way the sockets point, so not one overhang changes.

the part is four socket lobes at 0/90/180/270 with the ribs at 45°, not a disc, so a circumscribed
circle badly overstates what it needs:

| orientation | bounding box |
|---|---|
| sockets facing the bed edges | 266.7 × 266.7, **will not fit a 256 bed** |
| turned 45°, sockets at the corners | **214.0 × 214.0**, 21mm clear all round |

height is 202.2 either way, `MODULE_HEIGHT` plus the 2.2mm joint spigot. anything from 20° to 70°
fits; 45° is the natural place to land. turned, the widest thing is not the sockets but the rod
bosses at r = 107, and the sockets have room out to r = 151 before they matter again.

measured on the solid: 1272mm² of downward-facing surface is flat, out of 72000mm², and **nothing
falls between 46° and 89°** — no true overhangs at all. the two flat patches are a 1.8mm bridge over
the joint recess and a 4mm ledge under the upper rod boss, both of which bridge unsupported.

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
| socket face | ⌀50.6, 26mm outboard along the axis |
| socket base | ⌀43.9 at 15mm depth |
| taper | 0.443 mm per mm, 25° included |
| clearance bore | ⌀44.9 past the seat |
| tube | ⌀56.6 outside, 3mm wall, 52mm long, from along -26 to +26 |
| hole in the wall | 43.5 × 66.9 oval, z = 94 to 160 |

**the pot sits on the taper, not on its lip.** the lip is ⌀52.8 and the body just under it is barely
narrower, which leaves about 1.5mm of ledge. that is not a seat, it's a part that falls through.

pot and socket share the same taper, so they meet in **full conical contact**. the pot slides in 5mm
and its lip finishes 5mm proud as a grab handle. a pot 1mm oversize just seats 2mm shallower and still
grips, which matters because the vendor's stated dimensions were wrong by 2.2mm.

**it's a tube through the wall, not a collar on it.** tilted 45°, the lower rim projects 38.4mm
outboard, to r = 133.4.

`SOCKET_FACE_OFFSET` is **26, not 8**, and it is the one number the socket lives or dies on. a 45°
collar's upper rim sits at r = 74.99 + 0.707 × offset, so it only clears the ⌀184 chamber once the
offset passes 24.06. below that the chamber trim shaves the collar's top off and the socket comes out
as an arc rather than a circle — at 8 it was 250° of 360. at 26 the ring closes.

the cost is real and it is the thing to watch. pushing the socket out drags the pot out with it, so
**the root ball now sits 10.7mm inside the chamber wall instead of 23.4mm.** still in the water film,
but with less than half the margin. if the crown dries out, this is why, and the fix is a shorter net
pot rather than winding the offset back.

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
| drip holes | 4 × ⌀10 on a ⌀120 circle, one on each socket bearing |

the inner cone prints as a **roof**, each layer smaller than the one below, so it self supports as
easily as the outer one.

the drip holes land on the next spreader at r = 60, near its rim rather than its apex, so water only
travels 60 to 85mm across the cone instead of the full chamber width. drop between modules is about
27mm, so splash stays low.

**each hole sits on a socket bearing**, so it drops straight onto one of the spreader's four spouts
instead of between two of them. four ⌀10 replaced eight ⌀8: near enough the same open area, half as
many holes, aligned. the gutter is a sump with no other way out, so this is what sets the flow ceiling.

### supply pipe

1/2" pvc sch40, ⌀21.34 outside. **one continuous length** from the pump to the tower top. no
couplers, no joints anywhere along it.

length is the tower plus the tank run. the tower is `#MODULE_COUNT * #MODULE_HEIGHT` = 800mm. below
the lid plate it has to reach down past the tee and valve to the pump outlet, call it 250mm until i
can measure it with the fittings in hand. so roughly **1.05m**, cut from the 10ft stock.

**there is nothing to seal.** the old design segmented the pipe per module and accepted that the
couplers might weep, on the grounds that a leak lands in the root chamber carrying the same solution
it was already there. with one continuous pipe that argument isn't needed at all.

**the cost is that changing module count means cutting a new pipe.** worth it. 10ft of stock is
nearly three times what this tower needs, so it's a cut, not a redesign.

**assembly.** snap the tank ring onto the bucket, screw the cap into it, drop the drain base into it so the four hooks
enter its slots, twist it 20° clockwise until it stops, and it is locked. stack the tower on that,
then drop the pipe down through the aligned pipe tunnels and glue it to the pump below. to service
the pump, twist the tower 20° anticlockwise, lift it off, unscrew the cap and lift cap, pipe and
pump out of the bucket as one unit. it only needs about 400mm of headroom to clear the bucket.

### flow

target **1 to 3.5 L/min**, continuous while the lights are on. the ceiling is set by the gutter,
not the pump: the four ⌀10 drip holes pass 3.7 L/min with the channel brim full, and 3.5 needs
4.6mm of head in a 5mm channel. run it harder and the gutter backs up over the cones.

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
       ├── water level    jsn-sr04t ultrasonic, uart mode, flush in the tank lid plate
       ├── water temp     ds18b20 waterproof probe, 1-wire
       ├── air temp + rh  sht31 / sht41, i2c
       ├── ph             analog probe ──► ads1115 16 bit adc, i2c
       ├── ec             analog probe ──► ads1115 16 bit adc, i2c
       ├── led control    ch1 of the 4 mosfet channels on the hat, pwm and photoperiod
       └── dosing         3 peristaltic pumps on ch2 3 and 4

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

at 1.3m of lift this pump still puts out roughly 900 to 1100 L/h against a target of 60 to 210 L/h.
that's 5 to 15 times too much. choking it with a valve alone makes a small centrifugal pump run hot
and cavitate, so the excess gets **shed instead of throttled**.

```
   inside the tank
     pump (1/2" npt female)
       └── 1/2" mpt × slip pvc adapter
       └── short pvc stub
       └── tee
             └── ball valve ──► open pipe pointing down into the water
       ↑ up through the middle of the tank lid plate
   above the lid
       └── supply pipe up the tower
```

**there is no bulkhead.** the tank lid plate is a ring with a ⌀172 opening, and the pipe simply
stands up through it in a ⌀23 collar carried on four ribs. the same opening is how water gets back
down to the tank, so it is deliberately open and there is nothing there to seal. the plate sits well
above the water line in every state.

**the plate is the lid, and the ring is printed too.** the plate sits at the bottom of a 20mm skirt
with an external thread, ⌀236 major, 8mm pitch, single start, 2.5 turns, 45° flanks, and a ⌀252 flange
at the top of the skirt with eight grip ribs on it. it screws into the **tank ring**: a ⌀311 printed
ring that snaps over the bucket's rim on twelve fingers, each hooking 2mm under the rim's bead, with
the matching internal thread hanging into the bucket mouth. the ring's plate stands 12mm above the rim
on a support ring, which is what makes the fingers long enough to flex. both halves of the thread are
ours, so nothing has to be measured on a gamma seal, and the gamma seal is off the bom. the bucket
numbers came from a model of the home depot bucket (thingiverse thing 3688345): rim bead ⌀304.8,
7.2mm tall, wall ⌀291.9 under it; check the bead on the real bucket, the fingers carry a millimetre of
margin each way. the cap's plate top, and so the sensor face, sits 14mm above the bucket rim.

**the tower is held, not just parked.** four hooks stand on the plate between the rod bosses, each a
post with a toe pointing inward. the drain base has four slots up from its bottom edge and a 20°
bayonet groove off each one, cut into its outer wall. drop the base in, twist it 20° clockwise, and
each toe is in its groove with the groove floor under it. it cannot lift off or slide, both twist
directions stop on the groove ends, and the lock twist is the same direction that tightens the cap.
nothing on either part needs support to print: the cap goes plate down with everything pointing up,
the base is only cut.

the valve and the bypass return live inside the tank, so nothing extra passes through the lid. the
returning bypass flow also stirs the tank, which helps keep nutrients mixed.

**servicing the pump.** there's no union. the pipe is glued to the pump and stays on it permanently.
to get at the pump i twist the tower 20° to unlock it, lift it off the cap, unscrew the cap, and the
cap, pipe, tee and pump all come out together as one assembly.

that's one fewer joint, and it was the worst one. a union sitting directly on the pump outlet carries
full pump pressure and is the single most likely thing in the build to weep. lifting the tower off is
a two minute job i'd rather do than own that joint.

**tuning.** no flow meter, so set it once at commissioning. lift the tower off the bucket so the pipe
end is exposed and pointing up, run it into a jug for 30 seconds, and open or close the bypass valve
until you collect 0.5 to 1.75 L. then leave it alone.

**wrap the mpt in ptfe tape and don't overtighten.** npt is tapered and the pump housing is plastic.

### level sensor

the jsn-sr04t has a **200mm blind zone**. it cannot report anything closer than that. mounted flush
in the tank plate it would be useless exactly when the tank is full, which is the reading that
matters most.

the bucket is 368 tall with a bore near ⌀290. that's about 660 cm² of surface, so:

| fill | depth | air gap above water |
|---|---|---|
| 18.9 L brim full | 286mm | **82mm** |
| 16.5 L | 250mm | 118mm |
| 10.9 L | 165mm | **203mm** |

the first two are inside the blind zone. the third is not, and that is the whole design.

**there is no riser.** the original plan was a 150mm post. it does not fit. the plate is the tank
lid, so a post on it rises into the tower, and the best clear run anywhere inside the tower is 63mm
before module 1's floor cone is in the way. the sensor needs 82mm of rise to clear the blind zone at
a 250mm fill line. 82mm of post will not go into 63mm of space, and a shorter post does not help.

so the fill line moved instead of the sensor. **MAX_FILL_DEPTH is 165mm** and the sensor sits flush
in a pod in the tank lid plate:

```
   sensor ─────────────────────  368mm above the bucket floor, flush in the plate
     │
     │  203mm  ← full tank reading. only 3mm clear of a 200mm blind zone
     │
   ══╪══════════════════════════  165mm max fill line, about 10.9 L
     │
     │  368mm  ← empty tank reading
   ──┴──────────────────────────  0mm, bucket floor
```

usable span is 203 to 368mm, which maps the whole working range of the tank. the cost is volume:
10.9 L of usable tank instead of 16.5.

**3mm of margin is thin and the blind zone number is not settled.** spec, part-links and
next-session all say 200mm, but build-log has it at 25 cm off the datasheet and plenty of jsn-sr04t
listings say 25 cm too. if it is really 250mm then the fill line has to come down to 118mm at the
absolute best, 7.8 L, and less than that for any margin. measure the real blind zone on the bench
before committing the fill line.

**overfilling reads as empty.** closer than the blind zone does not give a short reading, it gives
garbage, and garbage can look like a far wall. the control service has to treat out of band as a
fault rather than as a level.

**it sits 50mm off the tank axis**, as close to centre as the pipe collar allows. dead centre is
where the supply pipe goes.

**open pod, not a tube.** the beam angle is 75°, so a narrow stilling well would just echo off
its own walls. in open air the water surface is the nearest and flattest reflector, so its echo
returns first and strongest. the pipe and the bucket wall return later and weaker.

**filter it in software anyway.** take a median of several readings and reject anything outside the
203 to 368 band. a cheap ultrasonic in a narrow bucket will throw the occasional false echo.

### safety

nothing can stop the main pump automatically. that's deliberate. it runs continuously while the lights
are on, so there's nothing to switch, and a relay on the mains side would add a 120 v subsystem to
guard a failure mode that doesn't exist. the dosing pumps are the opposite case: 12 v, driven directly
by the pi, and a stuck-on doser will happily empty a bottle of ph down into the tank. dose in short
timed bursts with a hard cap on total runtime per hour, enforced in the control service.

| layer | works if software is broken? |
|---|---|
| gfci outlet | **yes**, passive, mandatory |
| drip tray under the whole assembly | **yes**, passive |
| level sensor → low water phone alert | no |
| dosing runtime cap per hour | no |
| ec doesn't rise after a nutrient dose → alert | no |
| ph doesn't move after a ph down dose → alert | no |
| mqtt heartbeat → missed → phone alert | no |
| water temp out of range → phone alert | no |

**this makes the phone alerts the actual safety layer**, not a convenience. build them.

**the accepted gap:** with no flow sensor, a pump that dies or clogs isn't detected directly, and with
bare roots that kills plants in hours. partial substitute worth implementing: **when the pump stops,
the 1 to 2 L held in the tower drains back and the tank level rises.** an unexplained level rise is a
strong pump failure signal. it's a heuristic, not a measurement, but it costs nothing and uses a
sensor already fitted.

the dosers get the same treatment. there are no float switches in the concentrate bottles, so an
empty bottle is caught by watching whether ec and ph actually respond to a dose. that catches a
slipped tube and a dead pump too, which a float switch never would.

### lighting

12 V white led strip in **aluminium channel** clipped to the four ribs. the thermal path isn't
optional, and it's the reason the strip is **not** an ip65 one. a silicone jacket sits between the
pcb and the aluminium and insulates it. at 17 W/m that heat has to go somewhere, so a bare strip in a
capped channel is both cooler and better protected than waterproof tape lying exposed.

**17 W/m**, so the 3.2m across four rails draws about **54 W**. 14 to 16 hours a day, pi timed, pwm
dimmable. the channel is bought, printed clips hold it on.

**buy on watts per metre, never on led count.** the first strip i picked advertised 1200 leds and
turned out to be 29 W for the whole 5m reel, which is 5.8 W/m. that's 24 milliwatts per led. it would
have delivered 19 W across 16 plants and grown pale leggy lettuce.

**12 v, not 24.** grow strips are almost all 12 v, and the dosing pumps are 12 v too, so the whole
build runs off one rail. no buck converter, and the four mosfet channels on the hat drive the lights and
all three dosers. the cost is 5 a instead of 2.5 a, which only matters if the runs get long. they don't.

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
