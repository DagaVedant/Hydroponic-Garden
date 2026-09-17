#include "pico/stdlib.h"
#include "hardware/pwm.h"
#include "hardware/adc.h"
#include "hardware/i2c.h"
#include "hardware/gpio.h"
#include "hardware/irq.h"
#include "pins.h"
#include "io.h"

// ---------------------------------------------------------------- counters

static volatile uint32_t flow_pulses, fan_pulses;
static uint32_t flow_last, fan_last;
static float flow_hz, fan_hz;
static absolute_time_t counters_at;

static volatile uint64_t echo_rise;
static volatile int echo_us = -1;
static absolute_time_t sonar_at;

static void gpio_irq(uint gpio, uint32_t events) {
    if (gpio == PIN_FLOW) {
        flow_pulses++;
    } else if (gpio == PIN_FAN_TACH) {
        fan_pulses++;
    } else if (gpio == PIN_SONAR_ECHO) {
        if (events & GPIO_IRQ_EDGE_RISE) {
            echo_rise = time_us_64();
        } else if (events & GPIO_IRQ_EDGE_FALL) {
            uint64_t d = time_us_64() - echo_rise;
            echo_us = d < 60000 ? (int)d : -1;
        }
    }
}

// ---------------------------------------------------------------- pwm

static void pwm_pin(uint pin, uint32_t hz) {
    gpio_set_function(pin, GPIO_FUNC_PWM);
    uint slice = pwm_gpio_to_slice_num(pin);
    // 125 MHz / (div * 1000) = hz, keeping 1000 steps of duty
    float div = 125000000.0f / (hz * 1000.0f);
    pwm_set_clkdiv(slice, div);
    pwm_set_wrap(slice, 999);
    pwm_set_gpio_level(pin, 0);
    pwm_set_enabled(slice, true);
}

static void pwm_duty(uint pin, float duty) {
    if (duty < 0) duty = 0;
    if (duty > 1) duty = 1;
    pwm_set_gpio_level(pin, (uint16_t)(duty * 999.0f + 0.5f));
}

static const uint pump_pins[3] = {PIN_PUMP1_EN, PIN_PUMP2_EN, PIN_PUMP3_EN};

void io_init(void) {
    for (int i = 0; i < 3; i++) pwm_pin(pump_pins[i], 100);     // a slow pwm lets a dose be trimmed
    pwm_pin(PIN_LED_PWM_N, 1000);
    // the strip driver is inverted by a 2n7002; invert the pin so duty is duty
    gpio_set_outover(PIN_LED_PWM_N, GPIO_OVERRIDE_INVERT);
    pwm_pin(PIN_FAN_PWM, 25000);

    gpio_init(PIN_ARM); gpio_set_dir(PIN_ARM, GPIO_OUT); gpio_put(PIN_ARM, 0);
    for (uint p = PIN_LED_RAIL; p <= PIN_LED_LINK; p++) { gpio_init(p); gpio_set_dir(p, GPIO_OUT); gpio_put(p, 0); }
    gpio_init(PIN_HB_ALIVE); gpio_set_dir(PIN_HB_ALIVE, GPIO_IN);
    gpio_init(PIN_SONAR_TRIG); gpio_set_dir(PIN_SONAR_TRIG, GPIO_OUT); gpio_put(PIN_SONAR_TRIG, 0);

    gpio_init(PIN_FLOW); gpio_set_dir(PIN_FLOW, GPIO_IN);
    gpio_init(PIN_FAN_TACH); gpio_set_dir(PIN_FAN_TACH, GPIO_IN); gpio_pull_up(PIN_FAN_TACH);
    gpio_init(PIN_SONAR_ECHO); gpio_set_dir(PIN_SONAR_ECHO, GPIO_IN);
    gpio_set_irq_enabled_with_callback(PIN_FLOW, GPIO_IRQ_EDGE_RISE, true, gpio_irq);
    gpio_set_irq_enabled(PIN_FAN_TACH, GPIO_IRQ_EDGE_RISE, true);
    gpio_set_irq_enabled(PIN_SONAR_ECHO, GPIO_IRQ_EDGE_RISE | GPIO_IRQ_EDGE_FALL, true);

    adc_init();
    adc_gpio_init(PIN_LED_ISENSE);
    adc_gpio_init(PIN_RAIL_SENSE);

    i2c_init(i2c0, 100 * 1000);
    gpio_set_function(PIN_SDA, GPIO_FUNC_I2C);
    gpio_set_function(PIN_SCL, GPIO_FUNC_I2C);
    gpio_pull_up(PIN_SDA);
    gpio_pull_up(PIN_SCL);

    counters_at = get_absolute_time();
    sonar_at = counters_at;
}

void io_pump(int i, float duty) { if (i >= 0 && i < 3) pwm_duty(pump_pins[i], duty); }
void io_led(float duty) { pwm_duty(PIN_LED_PWM_N, duty); }
void io_fan(float duty) { pwm_duty(PIN_FAN_PWM, duty); }
void io_arm(bool on) { gpio_put(PIN_ARM, on); }
bool io_hb_alive(void) { return gpio_get(PIN_HB_ALIVE); }

void io_fault_line(bool asserted) {
    // the link's tx state machine owns the pin; a hard fault takes it back and
    // holds the line low (pin high through the inverter override)
    static int state = -1;
    if (state == (int)asserted) return;
    state = asserted;
    if (asserted) {
        gpio_set_function(PIN_FLT_DRV, GPIO_FUNC_SIO);
        gpio_set_dir(PIN_FLT_DRV, GPIO_OUT);
        gpio_put(PIN_FLT_DRV, 0);          // inverted override: 0 here is a high pin, a low line
    } else {
        gpio_set_function(PIN_FLT_DRV, GPIO_FUNC_PIO0);
    }
}

void io_status_leds(bool rail, bool pump, bool link) {
    gpio_put(PIN_LED_RAIL, rail);
    gpio_put(PIN_LED_PUMP, pump);
    gpio_put(PIN_LED_LINK, link);
}

// ---------------------------------------------------------------- analog

static float adc_volts(uint input) {
    adc_select_input(input);
    uint32_t acc = 0;
    for (int i = 0; i < 16; i++) acc += adc_read();
    return (acc / 16.0f) * 3.3f / 4095.0f;
}

float io_led_amps(void) { return adc_volts(0) / 0.5f; }                 // 10 mohm x 50
float io_rail_volts(void) { return adc_volts(1) * (10.0f + 3.3f) / 3.3f; }

static bool ads_read(int ch, float *volts) {
    // single shot, ain ch vs gnd, +-6.144 v, 128 sps
    uint16_t cfg = 0x8000 | ((0x4 | ch) << 12) | (0x0 << 9) | 0x0100 | (0x4 << 5) | 0x3;
    uint8_t w[3] = {0x01, cfg >> 8, cfg & 0xff};
    if (i2c_write_timeout_us(i2c0, ADS1115_ADDR, w, 3, false, 2000) != 3) return false;
    sleep_ms(9);
    uint8_t r = 0x00, d[2];
    if (i2c_write_timeout_us(i2c0, ADS1115_ADDR, &r, 1, true, 2000) != 1) return false;
    if (i2c_read_timeout_us(i2c0, ADS1115_ADDR, d, 2, false, 2000) != 2) return false;
    int16_t raw = (int16_t)((d[0] << 8) | d[1]);
    *volts = raw * 6.144f / 32768.0f;
    return true;
}

bool io_pump_amps(float out[3]) {
    for (int i = 0; i < 3; i++) {
        float v;
        if (!ads_read(i, &v)) return false;
        out[i] = v / 5.0f;                                                // 0.1 ohm x 50
        if (out[i] < 0) out[i] = 0;
    }
    return true;
}

// ---------------------------------------------------------------- per tick

void io_tick(void) {
    absolute_time_t now = get_absolute_time();
    if (absolute_time_diff_us(counters_at, now) >= 1000000) {
        uint32_t f = flow_pulses, t = fan_pulses;
        flow_hz = (float)(f - flow_last);
        fan_hz = (float)(t - fan_last);
        flow_last = f;
        fan_last = t;
        counters_at = now;
    }
    if (absolute_time_diff_us(sonar_at, now) >= 200000) {
        gpio_put(PIN_SONAR_TRIG, 1);
        sleep_us(12);
        gpio_put(PIN_SONAR_TRIG, 0);
        sonar_at = now;
    }
}

float io_flow_hz(void) { return flow_hz; }
float io_fan_hz(void) { return fan_hz; }
int io_sonar_mm(void) { return echo_us < 0 ? -1 : (int)(echo_us / 5.8f); }
