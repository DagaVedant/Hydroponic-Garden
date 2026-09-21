# architecture

what the hat actually is, wired for whoever rewrites `pi/` against it. everything here is pulled
directly from `../PCB/kicad/hat/hat.kicad_sch` (the routed, 0-DRC-error schematic) and
`../BOM.csv`, not reconstructed from memory -- where something isn't labeled in the schematic,
that's called out explicitly rather than guessed.

**the code in this folder right now (`control.py`, `link.py`, `firmware/`) describes a retired
two-board design: a Pi 4B hat plus a separate Pico "pumps" board talking over an isolated
two-wire serial link.** that hardware doesn't exist anymore. this document describes what
replaces it. `link.py` and `firmware/` should be deleted outright -- there is no second
microcontroller in this design, no link protocol to maintain. `control.py`, `config.py`,
`store.py`, and `web.py` are a reasonable *shape* to start from (broker → sqlite → dashboard),
but the hardware access layer inside `control.py` needs a full rewrite.

---

## 1. the one-sentence version

one Raspberry Pi 5, one custom HAT, no microcontroller. everything -- sensors, dosing pumps, LED
strip, fan, a hardware watchdog -- is driven directly over I2C and GPIO from a single control
process on the Pi.

## 2. power architecture

- **the Pi 5 is powered separately, over its own USB-C port.** the HAT does **not** feed the
  Pi's 5V rail, and the Pi does **not** power the HAT.
- **the HAT has its own regulation**, off a single 12V input: a buck module (12V→5V) feeds the
  HAT's own 5V rail, and an AMS1117 module steps that down to 3V3 for the HAT's own logic
  (PCA9685, both/all ADS1115s, INA226, DS3231, the watchdog logic, gate drivers).
- power entry: one 12V terminal block pair, a 10A ATO blade fuse, a schottky diode (SB5H100) in
  series, and a TVS diode (1.5KE15A) across the input for transient clamping.
- the schematic carries **separate power-flag nets** for `+12V` (raw), `+12V_S` (switched?),
  `12V_F` (fused), `+5V`, `+3V3` (the HAT's own), and `+3V3_PI` (a distinct net -- likely a Pi
  rail-presence sense line, not a supply path; confirm its actual use before assuming it powers
  anything). don't collapse these into one net when writing bring-up docs or test code -- they're
  kept separate on purpose.
- **no mains anywhere on this board.** the circulation pump plugs straight into a GFCI outlet and
  is never switched by this hardware. if pump switching is ever added, it goes through an
  external SSR driven by a low-voltage output from the HAT -- never by anything on this board
  directly touching mains.

## 3. I2C bus -- confirmed device map

Pulled directly from the schematic's component labels. Six devices on the bus:

| address | device | purpose |
|---|---|---|
| `0x40` | INA226 | 12V rail voltage + current monitor (default address, unchanged) |
| `0x41` | PCA9685 | 16-channel PWM driver (A0 strapped to +3V3 specifically to avoid colliding with the INA226 default of 0x40) |
| `0x44` | SHT31 | air temperature + humidity |
| `0x48` | ADS1115 #1 | pH (single-ended) + EC (single-ended) + pump 3 current (differential pair) |
| `0x49` | ADS1115 #2 | pump 1 current + pump 2 current (two differential pairs) |
| `0x4A` | ADS1115 #3 | LED strip current (one differential pair; other two pins likely unused) |
| (default) | SCD40 | CO2 + temp + humidity -- standard SCD40 address, not re-strapped |
| (default) | VEML7700 | ambient light -- standard address, not re-strapped |
| (default) | DS3231 | RTC, with its own onboard battery holder |

**correction against earlier project notes:** the BOM previously said "2 ADS1115s used." The
schematic has **three**, at 0x48/0x49/0x4A, as listed above. The BOM.csv line for this part has
been corrected (3-pack, all 3 used, already a zero-waste fit) -- if you're working from an older
copy of this repo or from a summary predating this file, distrust the "2 used" number.

**why three ADS1115s and not one with a mux:** each chip only has 4 analog inputs (2 single-ended
+ 1 differential, or up to 4 single-ended). pH, EC, and 4 independent current-sense differential
reads (3 pumps + LED) don't fit in one chip's channel budget, so the design spreads them across
three chips at three addresses instead of multiplexing.

## 4. PCA9685 channel map -- confirmed

| channel | function |
|---|---|
| ch0 | pump 1 PWM |
| ch1 | pump 2 PWM |
| ch2 | pump 3 PWM |
| ch3 | LED strip PWM |
| ch4 | fan PWM |
| ch5 | rail status LED |
| ch6 | pump status LED |

ch7-15 are unused/spare on this board.

## 5. non-I2C signals (GPIO / 1-Wire / UART)

these exist and are wired, but **exact BCM pin numbers are not labeled as plain text in the
schematic** -- they need to be pulled from the actual netlist/PCB (`hat.kicad_pcb`) or from a
freshly-exported schematic PDF before writing driver code. don't guess pin numbers; verify them
against the board.

confirmed to exist, pin numbers TBD:
- **DS18B20** (water temp), 1-Wire, with a 4.7k pull-up resistor on the HAT itself
- **JSN-SR04T** (tank level), wired in **UART/serial mode** (not the trigger/echo GPIO mode) --
  confirmed by the schematic's own label ("JSN-SR04T (uart mode)")
- **flow sensor** input, cleaned up by two gates of a CD40106 hex Schmitt inverter wired as a
  non-inverting buffer, clamped by a 1N4148 diode. the flow sensor module itself is **not yet in
  the BOM** -- the input circuitry is populated and ready, but nothing is plugged into it yet
- **probe power switching** (the pH/EC cross-talk fix, see section 6) -- two GPIO-driven low-side
  N-FETs (2N7000) each gating a high-side P-FET (BS250) that switches probe VCC
- **watchdog toggle line** -- a GPIO the control process must toggle at a steady rate (see
  section 7)
- **spare GPIO** -- explicitly broken out and unused, per the schematic label "spare gpio"

## 5a. verified pin map -- pulled from hat.kicad_pcb (J_PI, the 2x20 header)

extracted programmatically from the routed board on 2026-09-21, pad by pad. this supersedes
the "pin numbers TBD" caveat above. BCM numbering, i.e. what `gpiozero` / `libgpiod` / the
`/sys/class/gpio` world uses -- not physical header pin numbers.

| BCM | header pin | net | connects to | direction from the Pi |
|---|---|---|---|---|
| 2 | 3 | `/SDA` | I2C bus (all devices in section 3) | I2C |
| 3 | 5 | `/SCL` | I2C bus | I2C |
| 4 | 7 | `/W1` | DS18B20 data, 4k7 pull-up R10 to 3V3 on the HAT | 1-Wire (`dtoverlay=w1-gpio,gpiopin=4`, the default) |
| 14 | 8 | `/SONAR_TX` | JSN-SR04T terminal block J7 pad 3 | UART TX (`/dev/serial0`) |
| 15 | 10 | `/SONAR_RX` | JSN-SR04T terminal block J7 pad 4 | UART RX |
| 17 | 11 | `/FLOW` | CD40106 Schmitt buffer output (U19 pad 4) | input, edge-counting interrupt |
| 18 | 12 | `/HB_ALIVE` | CD4538 monostable trigger (U4 pad 4) | **output, the watchdog heartbeat toggle** |
| 27 | 13 | `/FAN_TACH` | fan tachometer via 10k R25 | input (pulse counting). not mentioned anywhere else in this doc -- it exists |
| 22 | 15 | `/BUZZ` | buzzer driver via 1k R27 | output. footprint not populated (section 9), drive it anyway, harmless |
| 23 | 16 | `/PH_EN` | pH probe power switch, 1k R6 into the 2N7000 gate | output, high = pH probe VCC on |
| 24 | 18 | `/EC_EN` | EC probe power switch, 1k R8 into the 2N7000 gate | output, high = EC probe VCC on |
| 25 | 22 | `/ARM` | **CD4081 AND gate input (U5 pad 2)** | **output, software arm.** see below |
| 0 / 1 | 27 / 28 | `/EEPROM_SDA` `/EEPROM_SCL` | HAT ID EEPROM (unpopulated) | leave alone, reserved by the HAT spec |
| 12 / 13 | 32 / 33 | `/SPARE_GPIO12` `/SPARE_GPIO13` | J2 "spare gpio" header | unused |

everything else on the header (BCM 5, 6, 7, 8, 9, 10, 11, 16, 19, 20, 21, 26) is **not connected**.

**`/ARM` is not in sections 5-8 above and it matters.** the pump/LED enable line is the AND
(CD4081) of two things: the watchdog's "heartbeat is alive" output *and* this GPIO. so the Pi
has to do two things to enable dosing: keep toggling BCM18 (section 7) **and** hold BCM25
high. dropping BCM25 low is the software's own clean, deliberate way to disarm the dosing rail
without waiting for the watchdog to time out -- use it on shutdown, on any fault, and as the
default state at boot until the control loop is healthy.

`+3V3_PI` (header pins 1/17) feeds the I2C pull-ups R4/R5 (10k) and the unpopulated EEPROM's
VDD. so it *is* a supply, just a tiny one -- the earlier "confirm its actual use" note is
resolved.

## 5b. ADS1115 channels and shunts -- verified

the "differential pair" wording in section 3 is not how the board is wired. every current
sense is a **single-ended read against GND across a low-side shunt**: the sense net is the
top of the shunt, the shunt's other end is GND. read the channel single-ended (or as a
differential pair against a grounded neighbor, same number), then `I = V / R_shunt`.

module pinout on all three (ADS1115 breakout): pad 1 = ADDR, pad 2 = ALRT, pads 3-6 = A0-A3,
pad 7 = VDD, pad 8 = SDA, pad 9 = SCL. address comes from where ADDR is strapped
(GND -> 0x48, VDD -> 0x49, SDA -> 0x4A), which matches the labels.

| chip | A0 | A1 | A2 | A3 |
|---|---|---|---|---|
| 0x48 | `/PH_S` (pH board analog out) | `/EC_S` (EC board analog out) | `/PUMP3_S` | GND |
| 0x49 | `/PUMP1_S` | GND | `/PUMP2_S` | GND |
| 0x4A | `/LED_S` | GND | GND | GND |

| sense net | shunt | value | full-scale hint |
|---|---|---|---|
| `/PUMP1_S` `/PUMP2_S` `/PUMP3_S` | R13 R16 R19 | 0.1 ohm | 1 A of pump current = 100 mV; use the +-256 mV or +-512 mV PGA range |
| `/LED_S` | R22 | 0.01 ohm | 5 A of strip current = 50 mV; +-256 mV range |
| INA226 | `R_SHUNT`, between `+12V` and `+12V_S` | 5 milliohm | matches `INA226_SHUNT_OHMS = 0.005` already in the old control.py |

so `+12V_S` is simply the downstream (load) side of the INA226 shunt, not a switched rail.

## 6. the pH/EC cross-talk fix

two powered probes sitting in the same tank leak current through the nutrient solution and
corrupt each other's readings. the fix, inherited unchanged in concept from the original
single-hat design that predates even the two-board version:

```
read pH  -> pH probe VCC on, EC probe VCC off, wait to settle, sample ADS1115 0x48 channel
read EC  -> EC probe VCC on, pH probe VCC off, wait to settle, sample ADS1115 0x48 (other channel)
idle     -> both off
```

each probe's VCC is switched **high side** (P-FET, not low-side/ground switching) --
switching the ground side would lift the probe board's local ground reference and put its
analog output at an undefined level relative to the ADC. this is why the schematic uses a
P-FET (BS250) per probe rather than a simpler low-side N-FET.

**settling time is not yet measured.** the original single-hat design notes said "start around 2
seconds" as a bench-measured starting point, not a firm spec -- confirm this empirically once
real hardware exists, don't hardcode 2 seconds as gospel.

## 7. the watchdog -- hard safety requirement, not a nice-to-have

built from a CD4538 dual monostable + a CD4081 quad AND gate. the Pi toggles one GPIO at a
steady rate as part of the normal control loop. if that toggling stops for any reason -- the
control process hangs, the OS locks up, the process crashes without cleanup -- the CD4538's
monostable times out and the CD4081 gate drops the pump/LED enable line, physically disarming
the dosing pumps and LED strip regardless of what any other GPIO or software state claims.

this is the **only** thing standing between a hung control process and pumps stuck dosing
indefinitely into the tank. any rewrite of `control.py` must toggle this line as one of its most
reliable, most-frequently-executed pieces of code -- ideally from something that keeps running
even if higher-level logic (dosing decisions, web server, etc.) throws an exception. don't let a
try/except around business logic accidentally also swallow the watchdog toggle.

## 8. actuators and their drive path

- **3x peristaltic dosing pumps** -- PCA9685 ch0/1/2 -> gate drivers (IRLZ44N logic-level
  N-FETs) -> 12V, each with a 1N5819 flyback diode
- **LED strip** -- PCA9685 ch3 -> IRLZ44N -> 12V, with a 1N5822 flyback diode (a beefier part
  than the pump flybacks, sized for the LED strip's higher current)
- **fan** -- PCA9685 ch4 -> IRLZ44N -> 12V
- **dosing rail interlock** -- one IRF9540N P-FET as a master cutoff for the dosing rail, gated
  by the watchdog's enable line (section 7) -- this is the physical enforcement point, not just
  a software check
- **status LEDs** -- PCA9685 ch5 (rail status), ch6 (pump status) -- through-hole LEDs, current
  limited by 100R resistors

## 9. what's on the board but intentionally not populated yet

these footprints exist on the PCB and are routed, but were deliberately left off the purchase
list (see `BOM.csv` and the project's own commit history for the reasoning):

- **OLED (SSD1306)** -- local status display. cut because the web dashboard + phone alerts
  already cover this.
- **buzzer** -- audible fault alert. cut for the same reason (phone alerts via `ntfy` are the
  real notification path already planned in `web.py`).
- **24LC32A/P HAT ID EEPROM** -- would let the Pi auto-identify the board per the official
  Raspberry Pi HAT spec. cut in favor of hardcoding the board config in software -- **whoever
  rewrites `config.py` needs to know there is no EEPROM to read at boot; all board identity is
  hardcoded.**

if any of these get added back later, the PCB doesn't need to change -- just populate the
footprint and add the part back to the BOM.

## 10. what's explicitly gone from the old two-board design

don't reintroduce these while adapting the old `control.py`/`link.py` shape:

- **no Pico, no second MCU, no link/frame protocol.** `link.py` and `firmware/` are dead code,
  not a reference implementation to preserve compatibility with.
- **no UPS.** no 18650 cell, no charger, no boost converter, no ideal-diode OR-ing, no
  mains-presence sensing. a power outage simply stops the tower until mains returns -- there is
  no ride-through, and no code should assume there is.
- **no isolated RS-485.** it had no defined second device to talk to even in the two-board
  design; there's nothing on this board for it to connect to.

## 11. software task, concretely

- rewrite the hardware-access layer of `control.py` for: 3x ADS1115 (0x48/0x49/0x4A) via
  differential + single-ended reads, PCA9685 PWM output (7 channels used, see section 4), SHT31,
  SCD40, VEML7700, INA226, DS3231, DS18B20 (1-Wire), JSN-SR04T (UART mode, not GPIO
  trigger/echo), a flow-sensor GPIO interrupt (once the sensor itself is sourced), the two
  probe-power-switch GPIOs, and the watchdog toggle GPIO.
- delete `link.py` and `firmware/` entirely.
- `config.py`'s MEASURE-tagged constants (probe settling time, per-pump flow rate, ph/ec
  change-per-mL, level sensor height) still need real values from a physical build -- none of
  this can be filled in from the schematic alone.
- `store.py`, `web.py`, `dashboard.html` are broker/storage/UI layers relatively decoupled from
  the hardware change -- lower priority to touch, but check for any assumption that a second
  board/process exists.

## 12. sources of truth, in order of trust

1. `../PCB/kicad/hat/hat.kicad_sch` and `hat.kicad_pcb` -- the actual routed board. if a
   document (including this one) disagrees with the schematic, the schematic wins.
2. `../BOM.csv` -- what's actually being bought, and why (each line has a reasoning note).
3. `../spec.md`'s "## electronics" section -- prose overview, kept in sync with the schematic
   as of this writing but not itself authoritative.
4. this file -- a snapshot interpretation of the above two, written to save a fresh reader from
   re-deriving it. re-verify against the schematic if anything here looks off.
