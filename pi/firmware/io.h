// everything on the board that is not the link: pwm outputs, the adcs, the
// ads1115, the flow counter, the sonar, the fan tach and the status leds.
#pragma once
#include <stdbool.h>
#include <stdint.h>

void io_init(void);
void io_pump(int i, float duty);          // 0..2, 0..1
void io_led(float duty);                  // strip, 0..1
void io_fan(float duty);                  // 0..1
void io_arm(bool on);
void io_fault_line(bool asserted);        // hold FLT low (no telemetry gets through while asserted)
void io_status_leds(bool rail, bool pump, bool link);

float io_led_amps(void);
float io_rail_volts(void);
bool io_pump_amps(float out[3]);          // false if the ads1115 did not answer
float io_flow_hz(void);                   // pulses per second over the last second
int io_sonar_mm(void);                    // -1 while there is no echo yet
float io_fan_hz(void);
bool io_hb_alive(void);
void io_tick(void);                       // call often: sonar trigger, per second counters
