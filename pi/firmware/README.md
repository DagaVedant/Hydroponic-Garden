# pumps board firmware

c, pico sdk. the pico h on the pumps board.

    export PICO_SDK_PATH=~/pico-sdk
    mkdir build && cd build && cmake .. && make
    # hold bootsel, plug the pico in, copy build/pumps.uf2 onto it

the link to the pi is the two lines on the jst: HB carries 9600 baud command frames
from the pi (and keeps the hardware watchdog retriggered), FLT carries telemetry back
and is held low for a hard fault. the frame format is at the top of link.h. pins are
in pins.h. with no frames for a second the pumps stop and the rail is disarmed.
