#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pico/stdlib.h"
#include "hardware/pio.h"
#include "hardware/gpio.h"
#include "uart_rx.pio.h"
#include "uart_tx.pio.h"
#include "pins.h"
#include "link.h"

static PIO pio = pio0;
static uint sm_rx = 0, sm_tx = 1;
static char buf[96];
static int n = 0;

uint8_t link_checksum(const char *s, int len) {
    uint8_t x = 0;
    for (int i = 0; i < len; i++) x ^= (uint8_t)s[i];
    return x;
}

void link_init(void) {
    uint off_rx = pio_add_program(pio, &uart_rx_program);
    uart_rx_program_init(pio, sm_rx, off_rx, PIN_HB_IN, LINK_BAUD);
    uint off_tx = pio_add_program(pio, &uart_tx_program);
    uart_tx_program_init(pio, sm_tx, off_tx, PIN_FLT_DRV, LINK_BAUD);
    // the 2n7002 inverts: a high on the pin pulls FLT low. invert the pin so the
    // pio's idle-high uart comes out as an idle-high line at the pi
    gpio_set_outover(PIN_FLT_DRV, GPIO_OVERRIDE_INVERT);
}

static bool parse(const char *line, command_t *c) {
    // line is ">...*XX"
    const char *star = strrchr(line, '*');
    if (!star || line[0] != '>') return false;
    uint8_t want = (uint8_t)strtol(star + 1, NULL, 16);
    if (link_checksum(line + 1, (int)(star - line - 1)) != want) return false;
    command_t out = {{0, 0, 0}, 0, 0, false};
    char body[96];
    int blen = (int)(star - line - 1);
    if (blen <= 0 || blen >= (int)sizeof body) return false;
    memcpy(body, line + 1, blen);
    body[blen] = 0;
    for (char *tok = strtok(body, ","); tok; tok = strtok(NULL, ",")) {
        char *eq = strchr(tok, '=');
        if (!eq) continue;
        *eq = 0;
        float v = strtof(eq + 1, NULL);
        if (!strcmp(tok, "P1")) out.pump[0] = v;
        else if (!strcmp(tok, "P2")) out.pump[1] = v;
        else if (!strcmp(tok, "P3")) out.pump[2] = v;
        else if (!strcmp(tok, "L")) out.led = v;
        else if (!strcmp(tok, "F")) out.fan = v;
        else if (!strcmp(tok, "A")) out.arm = v >= 0.5f;
    }
    for (int i = 0; i < 3; i++) out.pump[i] = out.pump[i] < 0 ? 0 : out.pump[i] > 1 ? 1 : out.pump[i];
    out.led = out.led < 0 ? 0 : out.led > 1 ? 1 : out.led;
    out.fan = out.fan < 0 ? 0 : out.fan > 1 ? 1 : out.fan;
    *c = out;
    return true;
}

bool link_poll(command_t *out) {
    bool got = false;
    int ch;
    while ((ch = uart_rx_program_getc(pio, sm_rx)) >= 0) {
        if (ch == '\n' || ch == '\r') {
            buf[n] = 0;
            if (n > 0 && parse(buf, out)) got = true;
            n = 0;
        } else if (n < (int)sizeof buf - 1) {
            buf[n++] = (char)ch;
        } else {
            n = 0;                      // garbage, start over
        }
    }
    return got;
}

void link_send_status(const status_t *s) {
    char body[128];
    int len = snprintf(body, sizeof body,
                       "I1=%.3f,I2=%.3f,I3=%.3f,IL=%.2f,V=%.1f,FL=%.1f,SN=%d,HB=%d,AR=%d,E=%02X",
                       s->pump_amps[0], s->pump_amps[1], s->pump_amps[2], s->led_amps, s->rail_volts,
                       s->flow_hz, s->sonar_mm, s->hb_alive ? 1 : 0, s->armed ? 1 : 0, s->faults);
    char line[144];
    int ln = snprintf(line, sizeof line, "<%s*%02X\n", body, link_checksum(body, len));
    for (int i = 0; i < ln; i++) uart_tx_program_putc(pio, sm_tx, line[i]);
}
