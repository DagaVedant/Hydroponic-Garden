"""Publish readings to the local mosquitto broker.

The broker is on localhost. It is not there to get data off the pi, it is there so
the control loop, the database and the dashboard don't have to know about each
other.

Two rules this module follows:

1. **Publishing never breaks logging.** The csv is the durable record. If the broker
   is down, or paho isn't installed, or the network stack is having a moment, every
   call here fails quietly and the caller carries on. A monitoring transport that
   can take down the thing it monitors is worse than no transport.

2. **The broker announces our death, we don't.** A last will is registered on
   connect, so if this process is killed or the pi loses power the broker publishes
   `offline` on our behalf. A heartbeat we send ourselves cannot report that we
   stopped being able to send heartbeats.
"""

from __future__ import annotations

import json
import queue
import time
from typing import Iterable, List

from .config import MQTT_HOST
from .hardware import (MQTT_CLIENT_ID, MQTT_KEEPALIVE_S, MQTT_PORT, MQTT_QOS,
                       MQTT_TOPIC_PREFIX)
from .reading import Reading

TOPIC_ONLINE = f"{MQTT_TOPIC_PREFIX}/state/online"
TOPIC_FAULT = f"{MQTT_TOPIC_PREFIX}/state/fault"
TOPIC_CMD = f"{MQTT_TOPIC_PREFIX}/cmd/#"
CMD_PREFIX = f"{MQTT_TOPIC_PREFIX}/cmd/"


def sensor_topic(sensor: str) -> str:
    """water/temp -> hydro/sensor/water/temp"""
    return f"{MQTT_TOPIC_PREFIX}/sensor/{sensor}"


def payload_for(r: Reading) -> str:
    """The shape the spec fixed: value, unit, timestamp, valid.

    `note` is an addition. An invalid reading that doesn't say why is only half a
    fault report, and the dashboard's fault log needs the reason.
    """
    body = {
        "value": None if r.value != r.value else round(r.value, 4),
        "unit": r.unit,
        "timestamp": int(r.ts),
        "valid": r.valid,
    }
    if r.note:
        body["note"] = r.note
    return json.dumps(body, separators=(",", ":"))


class Publisher:
    """A publisher that is safe to call when there is no broker.

    `enabled` goes false if paho is missing. Everything still runs; the readings
    just go to csv only.
    """

    def __init__(self, host: str = MQTT_HOST, port: int = MQTT_PORT) -> None:
        self.host = host
        self.port = port
        self.enabled = False
        self.connected = False
        self._client = None
        self._warned = False
        # commands arrive on paho's thread but touch gpio, so they are queued
        # and applied by the main loop. same rule as the ingest service
        self.commands: 'queue.Queue[tuple]' = queue.Queue(maxsize=100)

        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            print("  mqtt: paho-mqtt not installed, publishing disabled")
            return

        # paho 2.x requires an explicit callback api version, 1.x has no such arg
        try:
            self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                       client_id=MQTT_CLIENT_ID)
        except AttributeError:
            self._client = mqtt.Client(client_id=MQTT_CLIENT_ID)

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        # the will. retained, so a dashboard connecting later sees we are gone
        self._client.will_set(TOPIC_ONLINE,
                              json.dumps({"online": False}),
                              qos=MQTT_QOS, retain=True)
        self.enabled = True

    # ------------------------------------------------------------------ lifecycle

    def _on_connect(self, _client, _userdata, _flags, reason_code, *_args) -> None:
        ok = getattr(reason_code, "is_failure", None)
        self.connected = (not ok()) if callable(ok) else (reason_code == 0)
        if self.connected:
            print(f"  mqtt: connected to {self.host}:{self.port}")
            self._publish(TOPIC_ONLINE,
                          json.dumps({"online": True, "timestamp": int(time.time())}),
                          retain=True)
            self._client.subscribe(TOPIC_CMD, qos=MQTT_QOS)
        else:
            print(f"  mqtt: connect refused, {reason_code}")

    def _on_disconnect(self, _client, _userdata, *args) -> None:
        self.connected = False
        print("  mqtt: disconnected, will retry in the background")

    def start(self) -> None:
        """Connect without blocking. paho retries on its own from here on."""
        if not self.enabled:
            return
        try:
            self._client.connect_async(self.host, self.port, MQTT_KEEPALIVE_S)
            self._client.loop_start()
        except Exception as exc:                              # noqa: BLE001
            print(f"  mqtt: {exc}, publishing disabled")
            self.enabled = False

    def close(self) -> None:
        if not self.enabled or self._client is None:
            return
        try:
            # say goodbye properly, so the will does not fire on a clean shutdown
            self._publish(TOPIC_ONLINE,
                          json.dumps({"online": False, "timestamp": int(time.time())}),
                          retain=True)
            time.sleep(0.1)
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:                                     # noqa: BLE001
            pass

    def _on_message(self, _client, _userdata, msg) -> None:
        """Queue a command. Never act on it here; this is paho's thread."""
        if not msg.topic.startswith(CMD_PREFIX):
            return
        try:
            body = json.loads(msg.payload.decode("utf-8", "replace"))
        except (json.JSONDecodeError, ValueError):
            print(f"  mqtt: bad command payload on {msg.topic}")
            return
        if not isinstance(body, dict):
            return
        try:
            self.commands.put_nowait((msg.topic[len(CMD_PREFIX):], body))
        except queue.Full:
            pass

    # ------------------------------------------------------------------ publish

    def _publish(self, topic: str, payload: str, retain: bool = False) -> bool:
        if not self.enabled:
            return False
        try:
            info = self._client.publish(topic, payload, qos=MQTT_QOS, retain=retain)
            return info.rc == 0
        except Exception:                                     # noqa: BLE001
            return False

    def publish(self, readings: Iterable[Reading]) -> int:
        """Push a sweep. Retained, so a dashboard that connects late still sees
        the current value of everything rather than an empty screen until the next
        sweep lands a minute later.
        """
        rows: List[Reading] = list(readings)
        if not self.enabled:
            return 0

        if not self.connected and not self._warned:
            print("  mqtt: broker unreachable, csv only until it comes back")
            self._warned = True
        elif self.connected:
            self._warned = False

        sent = sum(1 for r in rows
                   if self._publish(sensor_topic(r.sensor), payload_for(r), retain=True))

        # fault state is the list of sensors currently failing. empty means healthy.
        # retained, so this is a state topic and not an event stream
        failing = sorted(r.sensor for r in rows if not r.valid)
        self._publish(TOPIC_FAULT,
                      json.dumps({"failing": failing,
                                  "count": len(failing),
                                  "timestamp": int(time.time())}),
                      retain=True)
        return sent

    def publish_state(self, name: str, body: dict) -> bool:
        """hydro/state/<name>, retained. lights and dosing use this."""
        payload = dict(body)
        payload.setdefault("timestamp", int(time.time()))
        return self._publish(f"{MQTT_TOPIC_PREFIX}/state/{name}",
                             json.dumps(payload, separators=(",", ":")), retain=True)

    def heartbeat(self) -> None:
        """Refresh the online topic so a consumer can spot a stalled loop.

        The will covers a dead process. This covers a process that is alive but has
        stopped sweeping, which the will cannot see.
        """
        self._publish(TOPIC_ONLINE,
                      json.dumps({"online": True, "timestamp": int(time.time())}),
                      retain=True)

    @property
    def status(self) -> str:
        if not self.enabled:
            return "off"
        return "connected" if self.connected else "retrying"
