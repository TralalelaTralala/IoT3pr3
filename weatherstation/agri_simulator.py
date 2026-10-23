from __future__ import annotations

import json
import math
import random
import struct
import threading
import time
import zlib
from collections import deque
from datetime import datetime, timezone
from typing import Any


class AgricultureSystem:
    """Repeatable classroom model of a large farm LPWAN + Zigbee deployment.

    Radio range, RSSI, battery life, and mesh recovery are simulated values. This
    model does not claim to emulate LoRaWAN cryptography or a physical radio.
    """

    FIELD = {"lat": 50.4501, "lon": 30.5234, "radius_km": 5.0}
    SENSOR_NAMES = ("temperature", "humidity", "pressure", "wind_speed",
                    "light_level", "reservoir_level")

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.rng = random.Random()
        self.started = time.monotonic()
        self.sequence = 0
        self.interval_seconds = 600
        self.battery_capacity_mah = 2400.0
        self.sleep_current_ma = 0.01
        self.active_current_ma = 18.0
        self.active_seconds = 10.0
        self.measurements: deque[dict[str, Any]] = deque(maxlen=120)
        self.actuators = {"irrigation": False, "ventilation": False, "heating": False}
        self.values: dict[str, Any] = {
            "temperature": 24.2, "humidity": 48.0, "pressure": 1014.0,
            "wind_speed": 1.8, "light_level": 580.0,
            "soil_moisture": [42.0, 38.0, 51.0, 46.0, 40.0],
            "reservoir_level": 78.0, "battery_voltage": 3.96,
            "gps": {"lat": 50.4501, "lon": 30.5234},
        }
        self.lorawan = {
            "region": "EU868", "frequency_mhz": 868.1, "bandwidth_khz": 125,
            "spreading_factor": 9, "tx_power_dbm": 14, "adr_enabled": True,
            "activation": "OTAA demo state", "device_class": "Class A",
            "rx1_delay_s": 1, "rx2_delay_s": 2,
            "gateway": {"id": "farm-gateway-01", "lat": 50.4501, "lon": 30.5234},
            "devices": [], "uplinks": 0, "last_payload_bytes": 0,
            "json_bytes": 0, "batch_compressed_bytes": 0, "last_rssi_dbm": -104,
            "last_snr_db": 6.0, "adr_adjustments": 0,
        }
        self._make_lorawan_devices()
        self.nodes = {
            "coordinator": {"id": "coordinator", "role": "Coordinator", "online": True, "battery": 100},
            "router-north": {"id": "router-north", "role": "Router", "online": True, "battery": 100},
            "router-south": {"id": "router-south", "role": "Router", "online": True, "battery": 100},
            "soil-east": {"id": "soil-east", "role": "End Device", "online": True, "battery": 93},
            "soil-west": {"id": "soil-west", "role": "End Device", "online": True, "battery": 88},
            "greenhouse": {"id": "greenhouse", "role": "End Device", "online": True, "battery": 96},
        }
        self.links = [
            ("coordinator", "router-north", 94), ("coordinator", "router-south", 87),
            ("router-north", "router-south", 75), ("router-north", "soil-east", 91),
            ("router-south", "soil-east", 67), ("router-north", "soil-west", 71),
            ("router-south", "soil-west", 89), ("router-north", "greenhouse", 58),
            ("router-south", "greenhouse", 92),
        ]
        self.recovery_events: list[dict[str, Any]] = []
        self.network = {"pan_id": "0x1A62", "channel": 15, "security": "AES-128 network key (demo)",
                        "routing": "quality-weighted shortest path", "packet_loss_percent": 1.2}
        self._tick_values()

    def _make_lorawan_devices(self) -> None:
        specs = [("soil-01", 1.1, 0), ("soil-02", 2.3, 1), ("soil-03", 3.0, 2),
                 ("soil-04", 4.2, 3), ("soil-05", 4.8, 4), ("meteo-01", 0.5, None),
                 ("reservoir-01", 2.8, None)]
        for i, (name, distance, point) in enumerate(specs):
            lat = self.FIELD["lat"] + (i - 3) * 0.006
            lon = self.FIELD["lon"] + (i - 2) * 0.008
            self.lorawan["devices"].append({"id": name, "kind": "soil" if point is not None else name.split("-")[0],
                "distance_km": distance, "point": point, "online": True,
                "lat": round(lat, 5), "lon": round(lon, 5), "last_seen": None,
                "rssi_dbm": None, "spreading_factor": 9})

    def _tick_values(self) -> None:
        self.sequence += 1
        self.values["temperature"] = round(max(-5, min(45, self.values["temperature"] + self.rng.uniform(-0.35, 0.35))), 2)
        self.values["humidity"] = round(max(10, min(99, self.values["humidity"] + self.rng.uniform(-1.1, 1.1))), 2)
        self.values["pressure"] = round(self.values["pressure"] + self.rng.uniform(-0.8, 0.8), 2)
        self.values["wind_speed"] = round(max(0, self.values["wind_speed"] + self.rng.uniform(-0.3, 0.3)), 2)
        self.values["light_level"] = round(max(0, self.values["light_level"] + self.rng.uniform(-35, 35)), 1)
        self.values["soil_moisture"] = [round(max(5, min(90, v + self.rng.uniform(-0.7, 0.3))), 1)
                                         for v in self.values["soil_moisture"]]
        self.values["reservoir_level"] = round(max(0, min(100, self.values["reservoir_level"] + self.rng.uniform(-0.15, 0.05))), 1)
        self.values["battery_voltage"] = round(max(3.2, self.values["battery_voltage"] - self.rng.uniform(0, 0.00005)), 3)
        sample = {"sequence": self.sequence, "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "values": json.loads(json.dumps(self.values))}
        self.measurements.append(sample)
        for device in self.lorawan["devices"]:
            if device["online"]:
                rssi = self._rssi_for_distance(device["distance_km"])
                device["rssi_dbm"] = rssi
                device["last_seen"] = sample["timestamp"]
                device["spreading_factor"] = self.lorawan["spreading_factor"]
        self._update_uplink_metrics()

    @staticmethod
    def _rssi_for_distance(distance_km: float) -> int:
        # Classroom log-distance estimate with a little fixed variation.
        return round(max(-137, min(-70, -72 - 20 * math.log10(max(0.1, distance_km)) - 12)))

    def sample(self) -> dict[str, Any]:
        with self.lock:
            self._tick_values()
            return self.dashboard()

    def _pack_payload(self) -> bytes:
        soil = self.values["soil_moisture"]
        return struct.pack("<BBhB5BHHHH", 1, self.sequence & 0xFF,
            round(self.values["temperature"] * 100), round(self.values["humidity"]),
            *[round(v) for v in soil], round(self.values["pressure"]),
            round(self.values["light_level"]), round(self.values["reservoir_level"] * 10),
            round(self.values["battery_voltage"] * 100))

    def _update_uplink_metrics(self) -> None:
        payload = self._pack_payload()
        json_payload = json.dumps(self.values, separators=(",", ":")).encode("utf-8")
        # Batch ten compact records to demonstrate delta-friendly compression.
        batch = payload * 10
        compressed = zlib.compress(batch, level=9)
        self.lorawan["uplinks"] += 1
        self.lorawan["last_payload_bytes"] = len(payload)
        self.lorawan["json_bytes"] = len(json_payload)
        self.lorawan["batch_compressed_bytes"] = len(compressed)
        nearest = min(self.lorawan["devices"], key=lambda item: item["distance_km"])
        self.lorawan["last_rssi_dbm"] = nearest["rssi_dbm"] or -104
        self.lorawan["last_snr_db"] = round(max(-20, 12 + self.lorawan["last_rssi_dbm"] / 12), 1)
        if self.lorawan["adr_enabled"]:
            old_sf = self.lorawan["spreading_factor"]
            rssi = self.lorawan["last_rssi_dbm"]
            if rssi > -100:
                self.lorawan["spreading_factor"] = max(7, old_sf - 1)
                self.lorawan["tx_power_dbm"] = max(2, self.lorawan["tx_power_dbm"] - 1)
            elif rssi < -120:
                self.lorawan["spreading_factor"] = min(12, old_sf + 1)
                self.lorawan["tx_power_dbm"] = min(14, self.lorawan["tx_power_dbm"] + 1)
            if old_sf != self.lorawan["spreading_factor"]:
                self.lorawan["adr_adjustments"] += 1
        for device in self.lorawan["devices"]:
            device["spreading_factor"] = self.lorawan["spreading_factor"]

    def _routes(self) -> dict[str, list[str] | None]:
        graph: dict[str, list[tuple[str, float]]] = {key: [] for key in self.nodes}
        for a, b, lqi in self.links:
            if not self.nodes[a]["online"] or not self.nodes[b]["online"]:
                continue
            cost = 1 + (100 - lqi) / 100
            graph[a].append((b, cost)); graph[b].append((a, cost))
        routes: dict[str, list[str] | None] = {}
        for destination in self.nodes:
            dist = {node: math.inf for node in self.nodes}
            prev: dict[str, str | None] = {node: None for node in self.nodes}
            dist["coordinator"] = 0
            todo = set(self.nodes)
            while todo:
                current = min(todo, key=lambda n: dist[n])
                todo.remove(current)
                if dist[current] == math.inf or current == destination:
                    break
                for neighbor, cost in graph[current]:
                    if neighbor in todo and dist[current] + cost < dist[neighbor]:
                        dist[neighbor] = dist[current] + cost
                        prev[neighbor] = current
            if dist[destination] == math.inf:
                routes[destination] = None
                continue
            path = [destination]
            while path[-1] != "coordinator":
                parent = prev[path[-1]]
                if parent is None:
                    path = []
                    break
                path.append(parent)
            routes[destination] = list(reversed(path)) if path else None
        return routes

    def fail_router(self, router_id: str = "router-north") -> dict[str, Any]:
        with self.lock:
            if router_id not in self.nodes or self.nodes[router_id]["role"] != "Router":
                raise ValueError("Unknown Zigbee router")
            if not self.nodes[router_id]["online"]:
                raise ValueError("Router is already offline")
            start = time.monotonic()
            self.nodes[router_id]["online"] = False
            routes = self._routes()
            recovered = all(routes[node] for node in self.nodes if self.nodes[node]["role"] == "End Device")
            elapsed_ms = round(120 + (time.monotonic() - start) * 1000, 1) if recovered else None
            event = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "event": "router_failure", "node": router_id, "recovered": recovered,
                     "recovery_ms": elapsed_ms}
            self.recovery_events.append(event)
            return {"event": event, "routes": routes}

    def recover_router(self, router_id: str = "router-north") -> dict[str, Any]:
        with self.lock:
            if router_id not in self.nodes or self.nodes[router_id]["role"] != "Router":
                raise ValueError("Unknown Zigbee router")
            self.nodes[router_id]["online"] = True
            return {"node": router_id, "online": True, "routes": self._routes()}

    def set_actuator(self, actuator: str, value: bool) -> dict[str, Any]:
        if actuator not in self.actuators:
            raise ValueError("Unknown actuator")
        with self.lock:
            self.actuators[actuator] = bool(value)
            return {"actuator": actuator, "state": self.actuators[actuator]}

    def energy(self) -> dict[str, Any]:
        avg_ma = (self.sleep_current_ma * (1 - self.active_seconds / self.interval_seconds)
                  + self.active_current_ma * (self.active_seconds / self.interval_seconds))
        life_days = self.battery_capacity_mah / max(avg_ma, 0.001) / 24
        return {"interval_seconds": self.interval_seconds,
                "active_seconds": self.active_seconds,
                "sleep_percent": round(100 * (1 - self.active_seconds / self.interval_seconds), 2),
                "average_current_ma": round(avg_ma, 3),
                "battery_capacity_mah": self.battery_capacity_mah,
                "estimated_days": round(life_days, 1),
                "estimated_months": round(life_days / 30.4375, 1),
                "method": "model from assumed active/sleep currents; not a measured battery result"}

    def coverage(self) -> list[dict[str, Any]]:
        result = []
        for d in self.lorawan["devices"]:
            rssi = d["rssi_dbm"] or self._rssi_for_distance(d["distance_km"])
            result.append({**d, "coverage": "good" if rssi > -115 else "marginal" if rssi > -128 else "weak"})
        return result

    def coap_resource(self, path: str) -> Any:
        path = "/" + path.strip("/")
        mapping = {
            "/sensors/temperature": {"value": self.values["temperature"], "unit": "°C"},
            "/sensors/humidity": {"value": self.values["humidity"], "unit": "%"},
            "/sensors/soil-moisture": {"points": self.values["soil_moisture"], "unit": "%"},
            "/sensors/light-level": {"value": self.values["light_level"], "unit": "lx"},
            "/sensors/battery-status": self.energy(),
            "/actuators/irrigation": {"state": self.actuators["irrigation"]},
            "/actuators/ventilation": {"state": self.actuators["ventilation"]},
            "/actuators/heating": {"state": self.actuators["heating"]},
            "/diagnostics/network-stats": self.network_stats(),
            "/diagnostics/error-log": list(self.recovery_events[-20:]),
            "/diagnostics/performance": {"sequence": self.sequence, "uptime_s": int(time.monotonic()-self.started)},
        }
        if path not in mapping:
            raise KeyError(path)
        return mapping[path]

    def network_stats(self) -> dict[str, Any]:
        routes = self._routes()
        return {"lorawan": {k: self.lorawan[k] for k in ("region", "frequency_mhz", "bandwidth_khz", "spreading_factor", "tx_power_dbm", "last_rssi_dbm", "last_snr_db", "uplinks")},
                "zigbee": {"online_nodes": sum(n["online"] for n in self.nodes.values()),
                           "node_count": len(self.nodes), "packet_loss_percent": self.network["packet_loss_percent"],
                           "routes": routes},
                "coap": {"resources": 11, "observe": True, "blockwise": True}}

    def dashboard(self) -> dict[str, Any]:
        with self.lock:
            return {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "site": {"name": "Навчальна ферма", **self.FIELD},
                "sequence": self.sequence,
                "values": json.loads(json.dumps(self.values)),
                "actuators": dict(self.actuators),
                "lorawan": {**self.lorawan, "devices": [dict(d) for d in self.lorawan["devices"]],
                            "payload_hex": self._pack_payload().hex(" "),
                            "compression_ratio": round(self.lorawan["batch_compressed_bytes"] / max(1, self.lorawan["json_bytes"]*10), 3)},
                "zigbee": {"network": dict(self.network), "nodes": [dict(n) for n in self.nodes.values()],
                           "links": [{"from": a, "to": b, "lqi": lqi,
                                      "active": self.nodes[a]["online"] and self.nodes[b]["online"]}
                                     for a,b,lqi in self.links],
                           "routes": self._routes(), "recovery_events": list(self.recovery_events[-10:])},
                "coap": {"resource_paths": ["/sensors/temperature", "/sensors/humidity", "/sensors/soil-moisture",
                        "/sensors/light-level", "/sensors/battery-status", "/actuators/irrigation",
                        "/actuators/ventilation", "/actuators/heating", "/diagnostics/network-stats",
                        "/diagnostics/error-log", "/diagnostics/performance"], "server": "127.0.0.1:5683/UDP"},
                "energy": self.energy(), "history": list(self.measurements)}
