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

```
120 V AC wall
  └── GFCI outlet, manual reset
        ├── pump, 30 W ................. always on, nothing switches it
        ├── 12 V 100 W PSU  ............ one rail for everything
        │     ├── LED strip, 4 x 800mm .. wired in parallel, 5 A total
        │     └── 3 dosing pumps ........ 12 V
        │           both switched by the 4 mosfet channels on the hat
        └── Pi 5 V supply
              └── raspberry pi 4b
```

led strip and dosers share one 12 v rail, so there's no buck converter and no second mosfet
board. the 12 v never touches the pi. switching is low side, so the pi only ever drives a gate.

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
              ├── gpio x2 ───── mosfets .......... ph and ec probe power
              └── gpio x4 ───── 4-ch board ──┬── ch1 pwm ... led strip
                                             └── ch2 3 4 ... dosing pumps
```

the ads1115 is a hard dependency. the pi has no analog input, so ph and ec reach it only through that
chip. the two gpio switching probe power are the cross-talk fix: ph on, ec off, settle, sample. then
swap. then both off.

## specs

| | |
|---|---|
| plants | 16, four modules of four |
| module | ⌀190 × 200mm, pla/petg. prints turned 45° on the bed, 214 × 214 × 202 |
| tower | ~800mm, ~1.35m with the tank |
| tank | 5 gal, 18.9 L |
| pump | 550 gph, 2.2m lift, throttled to 1-3.5 L/min with a bypass |
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
