# vertical hydroponic garden

a modular 3d printed hydroponic tower, with one pump, one pipe, and no pumps per level. the shape of the part is the entire system

> **onshape cad when i finish making it** · **[spec](spec.md)** · **[parameters](parameters.md)**

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
            │  gutter ──► 8 drip holes ────────────────►│  every module
            └───────────────────────────────────────────┘
                              ▼
                         back to the tank
```

1. the pipe fires a jet **upward** into a cone, which turns it into a falling ring of water
2. the ring lands on a **spreader**, a cone with four spouts aimed at the four plant sockets
3. water runs down the walls, past the hanging roots, through a **grate**
4. the floor is **two 45° cones meeting at a ring gutter**. everything collects there and drops
   through 8 holes onto the next module's spreader
5. because every module re-collects and re-distributes, **module 4 gets watered like module 1**

## design decisions worth knowing

| | |
|---|---|
| **a spreader in every module** | one cone at the top becomes wall film by module 2. |
| **net pots sit on a taper, not their lip** | the lip gives ~1.5mm of ledge, less than the error in the vendor's own spec sheet |
| **the supply pipe has no joints at all** | one continuous length from the pump to the top, running in a dry tunnel through every module. nothing to seal, nothing to weep |
| **no o-rings between modules** | nothing is pressurised. a step and recess lip catches splash and that's all it needs to do |
| **every overhang is exactly 45°** | sockets, both floor cones, the grate ledge. nothing needs support material |

## how it's wired

```
120 V AC wall
  └── GFCI outlet
        ├── pump, 30 W ................. always on, nothing switches it
        ├── 24 V 100 W PSU
        │     ├── LED strip, 4 x 800mm .. parallel, switched by a mosfet on the hat
        │     └── 24 -> 12 V buck
        │           └── 3 dosing pumps ... switched by the 4 channel mosfet board
        └── Pi 5 V supply
              └── raspberry pi 4b
```

the 24 v never touches the pi. both mosfets are low side, so the pi only ever drives a gate.

```
raspberry pi 4b
  └── 40 pin header
        └── hat   (the custom pcb)
              │
              ├── i2c ────┬── ads1115  0x48 ──┬── a0 ◄── ph board ◄── ph probe
              │           │                   └── a1 ◄── ec board ◄── ec probe
              │           └── sht31    0x44 ...... air temp + humidity
              │
              ├── 1-wire ──── ds18b20 ............ water temp, 4.7k pull-up
              ├── uart ────── jsn-sr04t .......... tank level
              │
              ├── gpio18 pwm ── mosfet ........... led strip
              ├── gpio x2 ───── mosfets .......... ph and ec probe power
              └── gpio x3 ───── 4-ch board ....... dosing pumps 1 2 3
```

the ads1115 is a hard dependency. the pi has no analog input, so ph and ec reach it only through that
chip. the two gpio switching probe power are the cross-talk fix: ph on, ec off, settle, sample. then
swap. then both off.

## specs

| | |
|---|---|
| plants | 16, four modules of four |
| module | ⌀190 × 200mm, pla/petg |
| tower | ~800mm, ~1.35m with the tank |
| tank | 5 gal, 18.9 L |
| pump | 550 gph, 2.2m lift, throttled to 1-4 L/min with a bypass |
| sensors | water level, water temp, air temp + humidity, ph, ec |
| dosing | 3 peristaltic pumps, nutrient a/b and ph down |
| control | raspberry pi 4b + custom hat → mqtt → sqlite + dashboard |
| scaling | `MODULE_COUNT` is one number. a taller tower is a parameter change, not a redesign |

## repo

| | |
|---|---|
| [spec.md](spec.md) | the design. structure, water path, electronics, software |
| [parameters.md](parameters.md) | every dimension, straight from the onshape variable studio |
| [CAD/](CAD/) | step exports and the part list |
| `PCB/` | raspberry pi hat, not started |