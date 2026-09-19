# pumps board firmware

c, pico sdk. the pico h on the pumps board. builds clean, no warnings from main.c/link.c/io.c,
57 kb flash and 4.5 kb ram out of the rp2040's budget.

    export PICO_SDK_PATH=~/pico-sdk
    mkdir build && cd build && cmake .. && make
    # hold bootsel, plug the pico in, copy build/pumps.uf2 onto it

windows needs two toolchains on PATH: the arm-none-eabi gcc for the target, and a native
gcc/g++ (mingw) for the sdk's host tools (pioasm, picotool). if the native gcc is 13+, pioasm
in pico-sdk 2.1.1 fails to build (`uint8_t`/`uint16_t` not declared) because two of its
headers rely on an implicit `<cstdint>` include that newer gcc no longer provides; add
`#include <cstdint>` near the top of `tools/pioasm/pio_types.h` and `tools/pioasm/output_format.h`
in the sdk checkout and it builds. not a pi/pumps board bug, just an sdk/toolchain mismatch.

the link to the pi is the two lines on the jst: HB carries 9600 baud command frames
from the pi (and keeps the hardware watchdog retriggered), FLT carries telemetry back
and is held low for a hard fault. the frame format is at the top of link.h. pins are
in pins.h. with no frames for a second the pumps stop and the rail is disarmed.
