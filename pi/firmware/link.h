// the frames between the pi and this board. ascii, one per line, xor checksum.
//   pi -> pico   >P1=0.00,P2=0.00,P3=0.00,L=0.62,F=0.50,A=1*5B
//   pico -> pi   <I1=0.000,I2=0.000,I3=0.000,IL=2.35,V=12.1,FL=12.3,SN=345,HB=1,AR=1,E=00*3C
// P1..3 pump duty 0..1, L led duty, F fan duty, A arm request.
// I1..3 pump amps, IL led amps, V rail volts, FL flow pulses per second, SN sonar mm,
// HB monostable alive, AR armed, E fault bits in hex.
#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef struct {
    float pump[3];
    float led;
    float fan;
    bool arm;
} command_t;

typedef struct {
    float pump_amps[3];
    float led_amps;
    float rail_volts;
    float flow_hz;
    int sonar_mm;
    bool hb_alive;
    bool armed;
    uint8_t faults;
} status_t;

// fault bits
#define FAULT_RAIL_LOW    0x01   // 12 v rail under 10 v while something is commanded
#define FAULT_PUMP_OVER   0x02   // a pump over 1.5 a
#define FAULT_LED_OVER    0x04   // the strip over 6 a
#define FAULT_PUMP_STALL  0x08   // a pump commanded on but drawing nothing (report only)
#define FAULT_NO_FRAMES   0x10   // the pi went quiet (report only, the rail is already off)

void link_init(void);
// feed received bytes; returns true when a complete valid command frame has arrived
bool link_poll(command_t *out);
void link_send_status(const status_t *s);
uint8_t link_checksum(const char *s, int n);
