// the pumps board. takes command frames from the pi over the heartbeat line,
// drives the three dosing pumps, the led strip and the fan, and reports back
// over the fault line. if the frames stop the pumps stop and the rail is
// disarmed, and the hardware monostable does the same on its own.
#include <string.h>
#include "pico/stdlib.h"
#include "pins.h"
#include "link.h"
#include "io.h"

#define PUMP_OVER_AMPS   1.5f
#define LED_OVER_AMPS    6.0f
#define RAIL_MIN_VOLTS  10.0f
#define STALL_AMPS       0.02f
#define STALL_AFTER_MS   400

int main(void) {
    stdio_init_all();
    io_init();
    link_init();

    command_t cmd = {{0, 0, 0}, 0, 0, false};
    status_t st;
    memset(&st, 0, sizeof st);
    absolute_time_t last_frame = nil_time;
    absolute_time_t last_tx = get_absolute_time();
    absolute_time_t pump_on_since[3] = {nil_time, nil_time, nil_time};
    bool hard_fault = false;
    uint8_t latched = 0;
    bool blink = false;

    while (true) {
        io_tick();
        absolute_time_t now = get_absolute_time();

        command_t fresh;
        if (link_poll(&fresh)) {
            cmd = fresh;
            last_frame = now;
            blink = !blink;
        }
        bool frames_ok = !is_nil_time(last_frame) && absolute_time_diff_us(last_frame, now) < FRAME_TIMEOUT_MS * 1000;
        if (!frames_ok) {
            for (int i = 0; i < 3; i++) cmd.pump[i] = 0;
            cmd.arm = false;
        }

        // measure
        st.led_amps = io_led_amps();
        st.rail_volts = io_rail_volts();
        if (!io_pump_amps(st.pump_amps)) {
            st.pump_amps[0] = st.pump_amps[1] = st.pump_amps[2] = -1;
        }
        st.flow_hz = io_flow_hz();
        st.sonar_mm = io_sonar_mm();
        st.hb_alive = io_hb_alive();

        // judge
        uint8_t faults = 0;
        bool anything = cmd.led > 0 || cmd.pump[0] > 0 || cmd.pump[1] > 0 || cmd.pump[2] > 0;
        if (anything && st.rail_volts < RAIL_MIN_VOLTS) faults |= FAULT_RAIL_LOW;
        if (st.led_amps > LED_OVER_AMPS) faults |= FAULT_LED_OVER;
        for (int i = 0; i < 3; i++) {
            if (st.pump_amps[i] > PUMP_OVER_AMPS) faults |= FAULT_PUMP_OVER;
            if (cmd.pump[i] > 0) {
                if (is_nil_time(pump_on_since[i])) pump_on_since[i] = now;
                else if (absolute_time_diff_us(pump_on_since[i], now) > STALL_AFTER_MS * 1000
                         && st.pump_amps[i] >= 0 && st.pump_amps[i] < STALL_AMPS)
                    faults |= FAULT_PUMP_STALL;
            } else {
                pump_on_since[i] = nil_time;
            }
        }
        if (!frames_ok) faults |= FAULT_NO_FRAMES;
        // over current is latched until the pi stops asking for the load
        if (faults & (FAULT_PUMP_OVER | FAULT_LED_OVER)) { hard_fault = true; latched = faults & (FAULT_PUMP_OVER | FAULT_LED_OVER); }
        if (hard_fault && !anything) { hard_fault = false; latched = 0; }
        st.faults = faults | latched;

        // act
        bool arm = cmd.arm && frames_ok && !hard_fault && !(faults & FAULT_RAIL_LOW);
        io_arm(arm);
        st.armed = arm && st.hb_alive;
        for (int i = 0; i < 3; i++) io_pump(i, (arm && !hard_fault) ? cmd.pump[i] : 0);
        io_led(hard_fault ? 0 : cmd.led);
        io_fan(cmd.fan);
        io_status_leds(st.armed, cmd.pump[0] > 0 || cmd.pump[1] > 0 || cmd.pump[2] > 0, frames_ok && blink);

        // report. a hard fault holds the line low instead, which the pi reads as a break
        io_fault_line(hard_fault);
        if (!hard_fault && absolute_time_diff_us(last_tx, now) >= TELEMETRY_MS * 1000) {
            link_send_status(&st);
            last_tx = now;
        }
        sleep_ms(5);
    }
}
