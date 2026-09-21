# vertical hydroponic garden

a modular 3d printed hydroponic tower, with one pump, one pipe, and no pumps per level. the shape of the part is the entire system

> **[onshape](https://cad.onshape.com/documents/e7b652182e17b56d968bf971/w/74d572342f8ebf967bd0880e/e/9edb8eefc30c47e9fd32fa36?renderMode=0&uiState=6aa58dd8d5139ca4163fd859)** · **[spec](spec.md)** · **[parameters](parameters.md)**

## why

i wanted a hydroponic setup i actually designed, not a kit off amazon. every commercial tower either runs tubing to each level or the bottom plants get little to no water. my goal is to make a tower that is one functional, two moduler, and three actually waters all the layers. instead of buying tons of piping and stuff, im using just angles, geometry, and shapes to help distribute the water evenly.

## how it works

the pump only does one job: get water to the top. after that it's all shape.

```
   pump ──► pipe ──► jet splitter ──► falling ring of water
                                          │
            ┌─────────────────────────────▼─────────────┐
            │  spreader ──► 4 spouts ──► walls          │  repeats,
            │  roots ──► grate ──► sloped floor         │  identically,
            │  gutter ──► 4 drip holes ────────────────►│  every module
            └───────────────────────────────────────────┘
                              ▼
                         back to the tank
```

1. the pipe fires a jet **upward** into a cone, which turns it into a falling ring of water
2. the ring lands on a **spreader**, a cone with four spouts aimed at the four plant sockets
3. water runs down the walls, past the hanging roots, through a **grate**
4. the floor is **two 45° cones meeting at a ring gutter**. everything collects there and drops
   through 4 holes, one under each socket, onto the next module's spreader
5. because every module re-collects and re-distributes, **module 4 gets watered like module 1**

## how it's wired

one board, one computer -- a raspberry pi 5 and a custom hat, no second microcontroller. an earlier
two-board revision (pi 4b hat + pico pumps board over an isolated link) is fully routed and archived
at [twoboard_vertical_garden/](twoboard_vertical_garden/); see [spec.md](spec.md) for why this one
replaced it.

```
120 V AC wall
  └── GFCI outlet, manual reset
        ├── pump, 30 W ................. always on, nothing switches it
        ├── 12 V 100 W PSU  ............ one rail for everything
        │     ├── LED strip, fan ....... switched via the hat's PCA9685 PWM driver
        │     └── 3 dosing pumps ....... 12 V, same PWM driver
        └── Pi 5, over its own USB-C ... powered separately, per the Pi 5's own guidance
```

the hat regulates its own 12 V-to-5 V and 5 V-to-3.3 V for its own load. none of that comes from
the Pi's 5 V/3.3 V pins, and the hat never feeds the Pi's rail either.

```
raspberry pi 5
  └── 40 pin header, tall standoffs to clear the active cooler and side ports
        └── hat   (the custom pcb)
              │
              ├── i2c ────┬── ads1115 #1 ──┬── ph board ◄── ph probe
              │           │                └── ec board ◄── ec probe
              │           ├── ads1115 #2 ──── 3x pump current + LED current (differential shunts)
              │           ├── sht31 .......... air temp + humidity
              │           ├── scd40 .......... co2
              │           ├── ina226 ......... 12V rail voltage + current
              │           ├── pca9685 ........ 3 pumps, LED, fan -- all PWM
              │           └── oled ........... local status
              │
              ├── 1-wire ──── ds18b20 ............ water temp, 4.7k pull-up
              ├── uart ────── jsn-sr04t .......... tank level
              ├── gpio (int)── flow sensor ........ pulse output
              ├── gpio x2 ──── mosfets ............ ph and ec probe power
              ├── gpio ─────── buzzer
              └── gpio (toggle) ── watchdog monostable ──► pump/LED enable line
                                   hung control process ⇒ line drops on its own
```

the ads1115 is a hard dependency. the pi has no analog input, so ph, ec, and both current reads reach
it only through those two chips. the two gpio switching probe power are the cross-talk fix: ph on, ec
off, settle, sample. then swap. then both off.

## specs

| | |
|---|---|
| plants | 16, four modules of four |
| module | ⌀190 × 200mm, pla/petg. prints turned 45° on the bed, 214 × 214 × 202 |
| tower | ~800mm, ~1.35m with the tank |
| tank | 5 gal, 18.9 L |
| pump | 550 gph, 2.2m lift, throttled to 1-3.5 L/min with a bypass |
| sensors | water level, water temp, air temp + humidity, co2, ph, ec, water flow, pump/LED current, 12V rail |
| dosing | 3 peristaltic pumps, nutrient a/b and ph down |
| control | raspberry pi 5 + custom hat → mqtt → sqlite + dashboard |
| scaling | `MODULE_COUNT` is one number. a taller tower is a parameter change, not a redesign |

## repo

| | |
|---|---|
| [spec.md](spec.md) | the design. structure, water path, electronics, software |
| [parameters.md](parameters.md) | every dimension, straight from the onshape variable studio |
| [CAD/](CAD/) | step exports and the part list |
| `PCB/` | raspberry pi 5 hat, routed, 0 DRC errors, gerbers exported |
| [twoboard_vertical_garden/](twoboard_vertical_garden/) | the archived pi 4b + pico two-board design, superseded but kept for reference |
