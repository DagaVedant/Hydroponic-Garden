"""The numbers you might have to change.

Everything fixed by the hat, the wiring or the bucket is in hardware.py and should
not need touching. Everything here is a measurement, a target or a preference.

Lines marked MEASURE are guesses until they are measured on the bench, and the
dosing ones are refused on hardware until DOSING_CALIBRATED is flipped.
"""

from __future__ import annotations

# ---------------------------------------------------------------- where things are

MQTT_HOST = "localhost"     # the broker. it runs on this pi
SAMPLE_INTERVAL_S = 60.0    # seconds between sweeps
CSV_DIR = "data"            # daily csv files, relative to pi/

# ---------------------------------------------------------------- tank

# Both in mm from the bucket floor. The sensor face is the level sensor's transducer
# in the cap's pod, which the cad puts 6 below the bucket rim of a 368 tall bucket.
# Measure it with the ring and cap on: a tape from the floor to the pod opening.
SENSOR_HEIGHT_MM = 362.0    # MEASURE
MAX_FILL_DEPTH_MM = 160.0   # the fill line, about 10.6 L. leaves 202 mm of air above
                            # a full tank, just past the sensor's 200 mm blind zone

# ---------------------------------------------------------------- probe calibration

# pH, two point. Probe in the 7.00 buffer, note the volts. Then the 4.00 buffer.
PH_CAL_V7 = 2.500           # MEASURE, volts in the pH 7.00 buffer
PH_CAL_V4 = 3.040           # MEASURE, volts in the pH 4.00 buffer

# TDS to EC. The Gravity board is dc excitation, so it needs the water temp.
TDS_TEMP_COEFF = 0.02       # per degree C, referenced to 25 C
TDS_TO_EC = 2.0             # ec (uS/cm) = tds (ppm) * 2
PROBE_SETTLE_S = 2.0        # wait after powering a probe before sampling. MEASURE

# ---------------------------------------------------------------- lights

LIGHT_ON_HOUR = 6.0         # 06:00
LIGHT_OFF_HOUR = 22.0       # 22:00, a 16 hour photoperiod. may cross midnight
LIGHT_BRIGHTNESS = 1.0      # 1.0 is the full 54 W. pwm only dims
LIGHT_RAMP_MINUTES = 10.0   # fade in and out. 0 snaps

# ---------------------------------------------------------------- dosing

DOSING_ENABLED = True
DOSING_CALIBRATED = False   # flip to True only after the three MEASURE blocks below

# 1. run each pump 60 s into a measuring cylinder, divide by 60
DOSE_FLOW_ML_PER_S = {
    "micro":   1.5,         # MEASURE
    "gro":     1.5,         # MEASURE
    "ph_down": 1.5,         # MEASURE
}

# 2. dose 5 mL, wait for the tank to mix, read the ec delta, divide by 5
EC_PER_ML = {
    "micro": 22.0,          # MEASURE, uS/cm per mL in a full tank
    "gro":   18.0,          # MEASURE
}

# 3. same for ph. non-linear because the solution buffers, so measure near target
PH_PER_ML_DOWN = 0.06       # MEASURE, ph units per mL

# targets for leafy greens
DOSE_EC_TARGET = 1200.0     # uS/cm
DOSE_EC_DEADBAND = 100.0    # do nothing inside target +/- this
DOSE_PH_TARGET = 5.8
DOSE_PH_DEADBAND = 0.3
MICRO_GRO_RATIO = 1.0       # gro per micro. micro always goes in first

# safety. these stop a stuck pump emptying a bottle
DOSE_MAX_ML_PER_EVENT = 5.0         # nutrients
DOSE_MAX_ML_PH_EVENT = 1.0          # ph down, far stronger per mL
DOSE_MAX_SECONDS_PER_HOUR = 30.0    # the hard cap from the spec
DOSE_MIN_INTERVAL_S = 600.0         # 10 min between doses
DOSE_MIX_WAIT_S = 300.0             # 5 min for the tank to mix before checking
DOSE_MIN_LEVEL_L = 5.0              # never dose into a nearly empty tank
DOSE_VERIFY_FRACTION = 0.4          # the reading must move at least this much of
                                    # what was expected, or the dose did not land
DOSE_VERIFY_MAX_FRACTION = 4.0      # and no more than this, or the pump did not stop

# ---------------------------------------------------------------- healthy bands

# Where each reading should sit. The dashboard colours by these and the alerts
# fire on them. Outside these is "the plants are unhappy"; hardware.py has the
# much wider "the sensor is broken" limits.
BANDS = {
    "water/level":  (6.0,  11.0),     # litres. full is the fill line
    "water/ph":     (5.5,   6.1),
    "water/ec":     (1100, 1300),     # uS/cm
    "water/temp":   (18.0, 24.0),     # C
    "air/temp":     (18.0, 28.0),
    "air/humidity": (40.0, 70.0),     # %RH
}

# ---------------------------------------------------------------- alerts

# Phone notifications go through ntfy. Install the app, subscribe to a topic name
# nobody would guess, and put it here. Blank means log to the terminal only.
ALERT_NTFY_TOPIC = ""
ALERT_NTFY_SERVER = "https://ntfy.sh"

ALERT_LEVEL_LOW_L = 6.0             # top up the tank
ALERT_LEVEL_RISE_L = 1.0            # a rise this big with no top up means the pump
ALERT_LEVEL_RISE_WINDOW_S = 900.0   # stopped and the tower drained back, 15 min
ALERT_OUT_OF_BAND_S = 1800.0        # a reading has to stay outside its band this
                                    # long before it is worth a notification
ALERT_SENSOR_FAIL_S = 600.0         # same for a sensor that keeps failing
ALERT_HEARTBEAT_S = 300.0           # no heartbeat for this long means the pi is down
ALERT_REPEAT_S = 6 * 3600.0         # remind about a problem that persists this often
