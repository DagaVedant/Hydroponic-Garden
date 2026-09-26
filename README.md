# vertical hydroponic garden

[![View PCB on KiCanvas](https://hack.club/pcb-badge)](https://kicanvas.org/?repo=https://github.com/DagaVedant/Hydroponic-Garden/tree/main/PCB/kicad/hat)

a modular 3d printed hydroponic tower, with one pump, one pipe, and no pumps per level. the shape of the part is the entire system

**[onshape](https://cad.onshape.com/documents/e7b652182e17b56d968bf971/w/74d572342f8ebf967bd0880e/e/9edb8eefc30c47e9fd32fa36?renderMode=0&uiState=6aa58dd8d5139ca4163fd859)**

**[KiCanvas](https://kicanvas.org/?repo=https://github.com/DagaVedant/Hydroponic-Garden/tree/main/PCB/kicad/hat)**

## why

i wanted a hydroponic tower thats one efficent, two scalable, and doesnt need tons of tubes to water each layer. this one needs one pump, its modular, and each level gets the same amount of water as all the others

## photos

![full tower assembly](images/tower-assembly.png)

![pcb](images/pcb-board.png)

the hat is a single 4-layer board, 217 x 226mm (down from an original 330 x 283mm). you can check it out in the link here [how it's wired](#how-its-wired)

## non-pcb wiring

```
120 V AC wall
  └── GFCI outlet, manual reset
        ├── pump, 30 W ................. always on
        ├── 12 V 100 W PSU  ............ one rail for everything
        │     ├── LED strip, fan ....... switched via the hat's PCA9685 PWM driver
        │     └── 3 dosing pumps ....... 12 V, same PWM driver
        └── Pi 5, over its own USB-C ... powered separately
```

## repo

| | |
|---|---|
| [BOM.csv](BOM.csv) | just a Bill Of Materials [below](#bill-of-materials) |
| [CAD/](CAD/) | .STEP export + what each file is on the onshape |
| [PCB/](PCB/) | raspberry pi 5 hat (`.kicad_pro`/`.kicad_sch`/`.kicad_pcb`)|
| [pi/](pi/) | the pi firmware `control.py`, `store.py`, `web.py` |
| [twoboard_vertical_garden/](twoboard_vertical_garden/) | the old pi 4b + pico two-board design (kicad source, gerbers, pico firmware)...im not building that but i left it as reference(was able to cut the costs a lot by switching to a single board design|

## bill of materials

just copied from [BOM.csv](BOM.csv)

| Item | Description | Qty | Unit Price (USD) | Total (USD) | Supplier | Status |
|---|---|---|---|---|---|---|
| **ALREADY OWN** |  |  |  |  |  |  |
| 5 gal bucket | 302mm OD, 368mm tall | 1 | 0.00 | 0.00 | N/A | OWNED |
| GROWNEER SML-630 submersible pump | 550 GPH, 30W, 120V AC | 1 | 0.00 | 0.00 | N/A | OWNED |
| 1/2in PVC Schedule 40 pipe 10ft | 21.34mm OD | 1 | 0.00 | 0.00 | N/A | OWNED |
| Analog pH interface board | signal conditioning, BNC socket | 1 | 0.00 | 0.00 | Amazon | OWNED |
| | **SUBTOTAL** | | | **0.00** | | |
| **SELF FUNDED** |  |  |  |  |  |  |
| [PETG filament white 1kg](https://www.amazon.com/ELEGOO-Filament-Dimensional-Accuracy-Printers/dp/B0D41YYVJN) | Elegoo, 1.75mm, 2 spools | 2 | 15.99 | 31.98 | Amazon | SELF-FUNDED |
| [PLA filament light blue 1kg](https://www.amazon.com/ELEGOO-Filament-Dimensional-Accuracy-Cardboard/dp/B0C6QD6456) | Elegoo sky blue, 1.75mm | 2 | 17.09 | 34.18 | Amazon | SELF-FUNDED |
| | **SUBTOTAL** | | | **66.16** | | |
| **PARTS I STILL NEED** |  |  |  |  |  |  |
| [Raspberry Pi 5](https://www.amazon.com/Raspberry-Pi-8GB-SC1112-Quad-core/dp/B0CK2FCG1K) | separate Pi, own power | 1 | 65.00 | 65.00 | N/A | TO BUY |
| [2in slotted mesh net pots (60-pack)](https://www.walmart.com/ip/60-Pack-2-Inch-Net-Cups-Slotted-Mesh-Wide-Lip-Filter-Plant-Net-Pot-Bucket-Basket-for-Hydroponics/917097535?wmlspartner=wlpa&selectedSellerId=102637587&veh=seo_fpl&cn=google) | 60-pack, 55mm lip | 1 | 10.97 | 10.97 | Walmart | TO BUY |
| [1/2in PVC MPT x Slip adapter](https://www.homedepot.com/p/LASCO-Fittings-1-2-in-PVC-Schedule-40-Male-MPT-x-Slip-Adapter-436005BC/317654558) | threaded to slip | 1 | 0.84 | 0.84 | Home Depot | TO BUY |
| [PVC bypass tee / ball valve / fittings](https://www.homedepot.com/p/Everbilt-1-2-in-PVC-Sch-40-Solvent-x-Solvent-Ball-Valve-107-633EB/319340955) | Everbilt 1/2in slip ball valve | 1 | 2.98 | 2.98 | Home Depot | TO BUY |
| [1/2in PVC slip tee](https://www.homedepot.com/p/Charlotte-Pipe-1-2-in-PVC-Schedule-40-S-x-S-x-S-Tee-PVC024000600HD/203812195) | Charlotte Pipe sch40, slip x3 | 1 | 0.87 | 0.87 | Home Depot | TO BUY |
| [1/2in PVC 90 elbow](https://www.homedepot.com/p/Charlotte-Pipe-1-2-in-PVC-Schedule-40-90-Degree-S-x-S-Elbow-Fitting-PVC023000600HD/203812033) | Charlotte Pipe sch40 slip | 1 | 0.78 | 0.78 | Home Depot | TO BUY |
| [PVC primer and cement](https://www.homedepot.com/p/Oatey-8-oz-Purple-CPVC-and-PVC-Primer-and-Regular-Clear-PVC-Cement-Combo-Pack-302483/100151579) | Oatey 8oz combo | 1 | 11.98 | 11.98 | Home Depot | TO BUY |
| [JSN-SR04T ultrasonic sensor](https://www.aliexpress.com/w/wholesale-jsn-sr04t-waterproof-ultrasonic-sensor.html) | non-contact level, 2-pack | 1 | 3.64 | 3.64 | AliExpress | TO BUY |
| [DS18B20 waterproof probe](https://www.amazon.com/DROK-Waterproof-Temperature-Resistors-Raspberry/dp/B0FLDTKW78) | water temp probe, 2-pack | 1 | 6.99 | 6.99 | Amazon | TO BUY |
| [SHT31 temp and humidity module](https://www.aliexpress.com/w/wholesale-sht31-temperature-humidity-module.html) | i2c 0x44, 2-pack | 1 | 3.50 | 3.50 | AliExpress | TO BUY |
| [SCD40 CO2 module](https://www.amazon.com/PAMEENCOS-Detects-Temperature-Humidity-Communication/dp/B0D9WLFWKS) | i2c CO2/temp/RH sensor | 1 | 21.99 | 21.99 | Amazon | TO BUY |
| [VEML7700 light sensor module](https://www.aliexpress.com/w/wholesale-veml7700.html) | i2c ambient light sensor | 1 | 4.70 | 4.70 | AliExpress | TO BUY |
| [12V white LED strip 5m](https://www.amazon.com/XUNATA-SMD5630-Dimmable-Linkable-Flexible/dp/B076HJFR58) | 5630 120led/m, neutral white | 1 | 17.99 | 17.99 | Amazon | TO BUY |
| [Aluminium LED channel + diffuser](https://www.amazon.com/Muzata-Aluminum-Mounting-Installations-Diffuser/dp/B01M09PBYX) | Muzata 17x7mm, 6x1m | 1 | 19.99 | 19.99 | Amazon | TO BUY |
| [12V 100W PSU](https://www.amazon.com/FSJEE-LED-Transformer/dp/B0GSVGLT2N) | IP67, 12V 8.3A, UL listed | 1 | 34.99 | 34.99 | Amazon | TO BUY |
| [Waterproof connectors and wire](https://www.aliexpress.com/w/wholesale-waterproof-connectors-automotive.html) | 26 set, 1-4 pin | 1 | 8.00 | 8.00 | AliExpress | TO BUY |
| [Wire crimping tool](https://www.amazon.com/Crimping-Amliber-Connectors-Electrical-Terminals/dp/B0D1FR76Q7) | ratcheting, 24-14 awg | 1 | 14.99 | 14.99 | Amazon | TO BUY |
| [22awg silicone hookup wire](https://www.amazon.com/Electric-Flexible-Silicone-different-Electronic/dp/B07G2JWYDW) | 6 colors, 8m each | 1 | 15.69 | 15.69 | Amazon | TO BUY |
| [16awg silicone wire 50ft](https://www.amazon.com/Silicone-Flexible-Stranded-Strands-MILAPEAK/dp/B07CMYVF3J) | 25ft red, 25ft black | 1 | 17.89 | 17.89 | Amazon | TO BUY |
| [Drip tray](https://www.amazon.com/Saucers-Extra-Deep-Drainage-Indoors-Plastic/dp/B0CPBC6ML3) | 16in saucer, 2-pack | 1 | 29.99 | 29.99 | Amazon | TO BUY |
| [Rockwool starter cubes 1.5in](https://www.amazon.com/dp/B0DJSXHQJ6) | 1.5in, 56-pack | 1 | 12.59 | 12.59 | Amazon | TO BUY |
| [pH replacement electrode](https://www.amazon.com/Connector-Electrode-Replacement-Aquarium-Hydroponics/dp/B07KG7RVVH) | BNC, 300cm cable | 1 | 17.90 | 17.90 | Amazon | TO BUY |
| [General Hydroponics pH Control Kit](https://www.amazon.com/dp/B000BNKWZY) | pH up/down plus indicator | 1 | 18.89 | 18.89 | Amazon | TO BUY |
| [General Hydroponics Flora Series trio](https://www.amazon.com/Hydroponics-GH-Flora-FloraMicro-FloraBloom/dp/B01HG98GX0) | micro/grow/bloom, 32oz quarts | 1 | 39.86 | 39.86 | Amazon | TO BUY |
| [Analog EC/TDS probe + interface board](https://www.amazon.com/Gravity-Waterproof-Anti-Polarization-0-1000ppm-Hydroponics/dp/B086GX2539) | DFRobot gravity kit, 0-1000ppm | 1 | 11.90 | 11.90 | Amazon | TO BUY |
| [pH and EC calibration solution set](https://www.amazon.com/GIDIGI-Calibration-Calibrating-Multifunction-Hydroponics/dp/B0GD6LMBLB) | 4x50ml, pH 4/7/10 + 1413 | 1 | 12.99 | 12.99 | Amazon | TO BUY |
| [pH probe storage solution](https://www.amazon.com/Biopharm-Electrode-Storage-Solution-Suitable/dp/B074G37V12) | Biopharm 250ml, KCl | 1 | 14.50 | 14.50 | Amazon | TO BUY |
| [ADS1115 16-bit I2C ADC](https://www.aliexpress.com/w/wholesale-16-bit-i2c-ads1115-adc-module.html) | 3-pack, all 3 used | 1 | 2.50 | 2.50 | AliExpress | TO BUY |
| [INA226 breakout module](https://www.aliexpress.com/w/wholesale-ina226.html) | i2c, strapped to 0x41 | 1 | 6.00 | 6.00 | AliExpress | TO BUY |
| [12V peristaltic dosing pump](https://www.amazon.com/INTLLAB-Peristaltic-Liquid-Aquarium-Analytical/dp/B07Q1C3PW2) | 5-100mL/min, 4-pack | 1 | 29.99 | 29.99 | Amazon | TO BUY |
| [Peristaltic pump tubing 10m](https://www.amazon.com/dp/B0DSQLWKFF/) | 3mm ID x 5mm OD | 1 | 9.39 | 9.39 | Amazon | TO BUY |
| [1L HDPE bottles with caps](https://www.amazon.com/United-Scientific-Supplies-33410-Capacity/dp/B07B32GNQ6) | wide mouth, 6-pack | 1 | 22.43 | 22.43 | Amazon | TO BUY |
| [PCB fabrication](https://cart.jlcpcb.com) | JLCPCB, 4-layer, no SMD | 1 | 74.37 | 74.37 | JLCPCB | TO BUY |
| [SB5H100 schottky diode](https://www.digikey.com/en/products/detail/vishay-general-semiconductor-diodes-division/SB5H100-E3-54/2146221) | DO-201AD, 5A/100V | 1 | 1.49 | 1.49 | Digikey | TO BUY |
| [1.5KE15A TVS diode](https://www.digikey.com/en/products/detail/stmicroelectronics/1-5KE15A/497-11371-1-ND/2674521) | DO-201AE axial | 1 | 1.10 | 1.10 | Digikey | TO BUY |
| [5 mOhm 1W axial shunt](https://www.digikey.com/en/products/filter/through-hole-resistors/1w/53) | DO0411, hand-solder | 1 | 2.20 | 2.20 | Digikey | TO BUY |
| [0.01 Ohm 1W axial shunt](https://www.amazon.com/10pcs-0R01%CE%A9J-Ceramic-Cement-Resistor/dp/B07PLSM9KZ) | LED strip current shunt | 1 | 14.49 | 14.49 | Amazon | TO BUY |
| [0.1 Ohm 1W axial shunt](https://www.amazon.com/CHANZON-J0010-RESISTOR-1W/dp/B08X75XKBS) | DO0411, per dosing pump | 3 | 2.00 | 6.00 | Amazon | TO BUY |
| [Buck converter module 12V to 5V](https://www.aliexpress.com/w/wholesale-mp1584.html) | MP1584/LM2596 style, few amps | 1 | 8.30 | 8.30 | AliExpress | TO BUY |
| [AMS1117 3.3V module](https://www.amazon.com/DollaTek-AMS1117-3-3-Voltage-Regulator-4-75V-12V/dp/B07PQPGMZ2) | breakout board, not SOT-223 | 1 | 6.99 | 6.99 | Amazon | TO BUY |
| [BS250 p-fet](https://www.digikey.com/en/products/detail/diodes-incorporated/BS250P/92630) | TO-92, high-side switch | 2 | 1.75 | 3.50 | Digikey | TO BUY |
| [IRLZ44N n-fet](https://www.digikey.com/en/products/detail/infineon-technologies/IRLZ44NPBF/811808) | TO-220, logic-level | 5 | 1.52 | 7.60 | Digikey | TO BUY |
| [2N7000 n-fet](https://www.digikey.com/en/products/detail/onsemi/2N7000G/1475375) | TO-92, gate driver | 2 | 0.65 | 1.30 | Digikey | TO BUY |
| [IRF9540N p-fet](https://www.digikey.com/en/products/detail/infineon-technologies/IRF9540NPBF/812088) | TO-220, interlock switch | 1 | 2.10 | 2.10 | Digikey | TO BUY |
| [DS3231 RTC breakout module](https://www.aliexpress.com/w/wholesale-ds3231-rtc-module.html) | i2c, own battery holder | 1 | 2.50 | 2.50 | AliExpress | TO BUY |
| [1N4148 diode](https://www.digikey.com/en/products/detail/onsemi/1N4148/458603) | DO-35, flow sensor clamp | 1 | 0.10 | 0.10 | Digikey | TO BUY |
| [CD4538BE dual monostable](https://www.digikey.com/en/products/detail/on-semiconductor/CD4538BCN/148229) | DIP-16, watchdog timer | 1 | 0.70 | 0.70 | Digikey | TO BUY |
| [CD4081BE quad AND gate](https://www.digikey.com/en/products/detail/CD4081BE/296-2066-5-ND/67323) | DIP-14, interlock gate | 1 | 1.11 | 1.11 | Digikey | TO BUY |
| [DIP IC socket assortment kit](https://www.aliexpress.com/w/wholesale-socket-dip.html) | 122pcs, 2.54mm pitch | 1 | 5.00 | 5.00 | AliExpress | TO BUY |
| [1N5819 schottky diode](https://www.digikey.com/en/products/detail/stmicroelectronics/1N5819/1037326) | DO-41, pump flyback | 3 | 0.35 | 1.05 | Digikey | TO BUY |
| [1N5822 schottky diode](https://www.digikey.com/en/products/detail/stmicroelectronics/1N5822/1037332) | DO-201AD, LED flyback | 1 | 0.50 | 0.50 | Digikey | TO BUY |
| [Littelfuse 178.6165.0001 blade fuse holder](https://www.digikey.com/en/products/detail/littelfuse-inc/178-6165-0001/2515815) | ATO holder, 12V input | 1 | 5.75 | 5.75 | Digikey | TO BUY |
| [ATO blade fuse 10A](https://www.digikey.com/en/products/detail/littelfuse-inc/0287010-L/3672098) | 12V input fuse | 1 | 7.99 | 7.99 | Amazon | TO BUY |
| [CUI TB007-508-02 terminal block](https://www.digikey.com/en/products/detail/cui-devices/TB007-508-02BE/10064127) | 5.08mm 2-pin | 6 | 0.72 | 4.32 | Digikey | TO BUY |
| [CUI TB007-508-03 terminal block](https://www.digikey.com/en/products/detail/cui-devices/TB007-508-03BE/10064128) | 5.08mm 3-pin | 2 | 0.95 | 1.90 | Digikey | TO BUY |
| [CUI TB007-508-04 terminal block](https://www.digikey.com/en/products/detail/cui-devices/TB007-508-04BE/10064129) | 5.08mm 4-pin | 1 | 1.15 | 1.15 | Digikey | TO BUY |
| [Gravity analog probe terminal block](https://www.digikey.com/en/products/detail/cui-devices/TB007-508-03BE/10064128) | 5.08mm 3-pin | 2 | 0.95 | 1.90 | Digikey | TO BUY |
| [2x20 stacking header for the pi 5](https://www.amazon.com/ELEDIY-20-40-Stacking-Header-Raspberry/dp/B071XCHZNB) | extra tall, clears cooler | 1 | 6.99 | 6.99 | Amazon | TO BUY |
| [2.54mm breakaway pin header strips](https://www.amazon.com/Gikfun-2-54mm-Single-Breakaway-Arduino/dp/B00U8OCENY) | 2.54mm, cut from strips | 2 | 3.00 | 6.00 | Amazon | TO BUY |
| [PCA9685 16-channel PWM breakout module](https://www.aliexpress.com/w/wholesale-pca9685.html) | i2c, strapped to 0x41 | 1 | 3.50 | 3.50 | AliExpress | TO BUY |
| [3mm THT LED assorted](https://www.amazon.com/250pcs-colors-Emitting-Diffused-Assorted/dp/B00XT0P1VQ) | status LEDs, assorted colors | 2 | 5.00 | 10.00 | Amazon | TO BUY |
| [Ceramic capacitor assortment kit](https://www.aliexpress.com/w/wholesale-ceramic-capacitor-kit-assorted.html) | 24 values, 480pcs | 1 | 5.00 | 5.00 | AliExpress | TO BUY |
| [Electrolytic capacitor assortment kit](https://www.aliexpress.com/w/wholesale-electrolytic-capacitors-kit.html) | 24 values, 500pcs | 1 | 7.00 | 7.00 | AliExpress | TO BUY |
| [Axial resistor kit 1% THT](https://www.aliexpress.com/item/32636020144.html) | 30 values, 600pcs | 1 | 3.57 | 3.57 | AliExpress | TO BUY |
| [#10-24 threaded rod 3ft](https://www.homedepot.com/p/Everbilt-10-24-x-3-ft-Zinc-Plated-Steel-Coarse-Threaded-Rod-2300/332733255) | UNC, one per corner | 4 | 2.98 | 11.92 | Home Depot | TO BUY |
| [#10-24 nyloc nuts](https://www.homedepot.com/p/Everbilt-10-24-Stainless-Steel-Nylon-Lock-Nut-4-Pieces-826011/317478988) | stainless, vibration-resistant | 2 | 1.57 | 3.14 | Home Depot | TO BUY |
| [M5 fender washers](https://www.homedepot.com/p/Hillman-Metric-Stainless-Fender-Washer-M5-44995/204786155) | stainless, 12-pack | 1 | 5.25 | 5.25 | Home Depot | TO BUY |
| **TOTAL COST** | sum of TO BUY items | | | **718.49** | | **TO BUY** |
