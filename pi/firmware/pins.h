// the pumps board, rev d. one line per pico pin, same names as the schematic.
#pragma once

#define PIN_LED_PWM_N    0   // led strip gate driver, inverted: a high here turns the strip off
#define PIN_PUMP1_EN     2   // micro
#define PIN_PUMP2_EN     3   // gro
#define PIN_PUMP3_EN     4   // ph down
#define PIN_HB_ALIVE     5   // the monostable's Q: high while the pi keeps sending
#define PIN_FLOW         6   // yf-s201 pulses, buffered
#define PIN_SONAR_TRIG   7
#define PIN_SONAR_ECHO   8   // through the 2k2 / 3k3 divider
#define PIN_FAN_PWM      9
#define PIN_FAN_TACH    10
#define PIN_FLT_DRV     11   // open drain to the pi through a 2n7002: high here pulls FLT low
#define PIN_HB_IN       12   // the pi's heartbeat line, which carries the command frames
#define PIN_ARM         13   // and-ed with HB_ALIVE in hardware to switch the dosing rail
#define PIN_LED_RAIL    14
#define PIN_LED_PUMP    15
#define PIN_LED_LINK    16
#define PIN_SDA         20
#define PIN_SCL         21
#define PIN_LED_ISENSE  26   // adc0: ina180a2 (x50) over 10 mohm, 0.5 v per amp
#define PIN_RAIL_SENSE  27   // adc1: 12 v rail through 10k / 3k3

#define ADS1115_ADDR    0x49 // addr pin high. ain0..2 = pump 1..3 current, ina180a2 (x50) over 0.1 ohm, 5 v per amp

#define LINK_BAUD       9600
#define FRAME_TIMEOUT_MS 1000   // no valid frame for this long: pumps off, disarm
#define TELEMETRY_MS     250
