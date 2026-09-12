"""Everything fixed by the hat, the wiring, the bucket or the protocol.

Nothing here is a preference. The pin map is soldered, the addresses are strapped
on the boards, the blind zone is physics. Tunables live in config.py.
"""

from __future__ import annotations

from .config import MAX_FILL_DEPTH_MM, SENSOR_HEIGHT_MM

# ---------------------------------------------------------------- i2c bus

I2C_BUS = 1                 # /dev/i2c-1 on a pi 4b
ADS1115_ADDR = 0x48         # addr pin to gnd. ph on a0, ec on a1
SHT3X_ADDR = 0x44           # sht31, addr low

ADS_CH_PH = 0
ADS_CH_EC = 1
ADS_FSR_VOLTS = 4.096       # pga setting, +/- 4.096 V

# ---------------------------------------------------------------- gpio (bcm numbering)
# the hat's pin map. PCB/README.md is the other copy; they must agree.

PIN_PH_POWER = 23           # pulls the ph board's high side switch on
PIN_EC_POWER = 24           # same for the ec board. only one is ever on
PIN_LIGHTS = 18             # led mosfet, hardware pwm0
PIN_DOSE_MICRO = 17         # dosing ch2, FloraMicro
PIN_DOSE_GRO = 27           # dosing ch3, FloraGro
PIN_DOSE_PH_DOWN = 22       # dosing ch4, pH Down
LIGHT_PWM_HZ = 1000

# ---------------------------------------------------------------- uart, level sensor

LEVEL_PORT = "/dev/serial0"
LEVEL_BAUD = 9600
LEVEL_TIMEOUT_S = 0.5
LEVEL_SAMPLES = 5           # median of N. a cheap ultrasonic in a narrow bucket
                            # throws the occasional false echo off the pipe or wall

# The JSN-SR04T cannot report anything closer than this. A reading under it is the
# sensor saying "too close", not a distance, so it is marked invalid.
LEVEL_BLIND_ZONE_MM = 200.0
LEVEL_MAX_RANGE_MM = 6000.0

BUCKET_BORE_MM = 290.0                                     # inner diameter

# Plausible window for this tank, derived from the two numbers in config.py.
# Full is only just past the blind zone: overfilling reads as "too close", which
# is invalid, not full.
LEVEL_MIN_VALID_MM = SENSOR_HEIGHT_MM - MAX_FILL_DEPTH_MM  # 202 as configured
LEVEL_MAX_VALID_MM = SENSOR_HEIGHT_MM + 20.0               # a little slack past empty

# ---------------------------------------------------------------- 1-wire, water temp

W1_DIR = "/sys/bus/w1/devices"
W1_PREFIX = "28-"           # ds18b20 family code

# ---------------------------------------------------------------- ph buffers

PH_CAL_7 = 7.00
PH_CAL_4 = 4.00

# ---------------------------------------------------------------- plausible ranges
# Outside these a reading is marked invalid. These are "the sensor is broken or
# unplugged" bounds. The "plants are unhappy" bounds are BANDS in config.py.

RANGES = {
    "water/level":  (0.0, 20.0),      # litres
    "water/temp":   (0.0, 45.0),      # degrees C
    "water/ph":     (0.0, 14.0),
    "water/ec":     (0.0, 5000.0),    # uS/cm
    "air/temp":     (-10.0, 60.0),
    "air/humidity": (0.0, 100.0),
}

# ---------------------------------------------------------------- mqtt

MQTT_PORT = 1883
MQTT_KEEPALIVE_S = 60       # the broker fires our last will about 1.5x this after we die
MQTT_CLIENT_ID = "hydro-control"
MQTT_TOPIC_PREFIX = "hydro"
MQTT_QOS = 1                # at least once. cheap over loopback

# ---------------------------------------------------------------- csv

CSV_HEADER = ["ts", "iso", "sensor", "value", "unit", "valid", "note"]
