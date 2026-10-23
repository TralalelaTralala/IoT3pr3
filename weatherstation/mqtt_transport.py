from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable

import paho.mqtt.client as mqtt

LOG = logging.getLogger("weatherstation.mqtt")


class MqttTransport:
    def __init__(self, station, on_config: Callable[[dict[str, Any]], None]):
        self.station = station
        self.on_config = on_config
        cfg = station.config_snapshot()
        self.root = cfg["mqtt"]["topic_root"].strip("/")
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=cfg["device_id"], protocol=mqtt.MQTTv311)
        self.client.will_set(f"{self.root}/status/online", payload="false", qos=1, retain=True)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.connected = threading.Event()
        self.last_heartbeat = None

    def start(self):
        cfg = self.station.config_snapshot()["mqtt"]
        self.client.connect_async(cfg["host"], int(cfg["port"]), keepalive=int(cfg["keepalive_seconds"]))
        self.client.loop_start()

    def stop(self):
        try:
            if self.client.is_connected():
                self._publish(f"{self.root}/status/online", "false", retain=True)
                self.client.disconnect()
            self.client.loop_stop()
        except Exception:
            LOG.exception("MQTT shutdown failed")

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if getattr(reason_code, "value", reason_code) == 0:
            LOG.info("MQTT connected")
            self.connected.set()
            self._publish(f"{self.root}/status/online", "true", retain=True)
            self._publish(f"{self.root}/config/interval", str(self.station.config_snapshot()["interval_seconds"]), retain=True)
            self._publish(f"{self.root}/config/location", self.station.config_snapshot()["location"], retain=True)
            client.subscribe(f"{self.root}/config/#", qos=1)
            client.subscribe(f"{self.root}/commands/config", qos=1)
            self.publish_snapshot(self.station.snapshot())
        else:
            LOG.warning("MQTT connection refused: %s", reason_code)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        self.connected.clear()
        if getattr(reason_code, "value", reason_code) != 0:
            LOG.warning("MQTT disconnected unexpectedly: %s", reason_code)
            with self.station.lock:
                self.station.connection_stats["mqtt_reconnects"] += 1

    def _on_message(self, client, userdata, msg):
        try:
            text = msg.payload.decode("utf-8")
            suffix = msg.topic[len(self.root):].strip("/")
            if suffix == "config/interval":
                self.on_config({"interval_seconds": int(text)})
            elif suffix == "config/location":
                self.on_config({"location": text})
            elif suffix in ("commands/config", "config/calibration"):
                self.on_config(json.loads(text))
            else:
                LOG.info("Ignored unknown MQTT config topic %s", msg.topic)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            LOG.warning("Invalid MQTT configuration message on %s: %s", msg.topic, exc)

    def _publish(self, topic: str, payload: str, retain: bool = True, qos: int = 1):
        if not self.client.is_connected():
            return False
        return self.client.publish(topic, payload, qos=qos, retain=retain).rc == mqtt.MQTT_ERR_SUCCESS

    def publish_snapshot(self, snapshot: dict[str, Any]):
        qos = int(self.station.config_snapshot()["mqtt"].get("qos", 1))
        qos = max(0, min(qos, 2))
        for name, value in snapshot["sensors"].items():
            payload = {"device_id": snapshot["device_id"], "location": snapshot["location"],
                       "timestamp": snapshot["timestamp"], "sequence": snapshot["sequence"], **value}
            self._publish(f"{self.root}/sensors/{name}", json.dumps(payload), qos=qos)
        self._publish(f"{self.root}/status/battery", json.dumps({
            "device_id": snapshot["device_id"], "timestamp": snapshot["timestamp"], "voltage": 3.96, "unit": "V"
        }), qos=qos)
        self._publish(f"{self.root}/status/diagnostics", json.dumps(self.station.diagnostics()), qos=qos)
        self._publish(f"{self.root}/status/heartbeat", json.dumps({
            "device_id": snapshot["device_id"], "timestamp": datetime.now(timezone.utc).isoformat(),
            "sequence": snapshot["sequence"]
        }), retain=False, qos=0)
        self.last_heartbeat = snapshot["timestamp"]
