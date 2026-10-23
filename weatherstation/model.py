from __future__ import annotations

import json
import random
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SENSOR_UNITS = {
    "temperature": "°C",
    "humidity": "%",
    "pressure": "hPa",
    "light": "lx",
    "air_quality": "AQI",
    "wind_speed": "m/s",
}


class Station:
    """Thread-safe sensor model shared by REST, MQTT, and the BLE profile."""

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.lock = threading.RLock()
        self.config = self._load_config()
        self.samples: deque[dict[str, Any]] = deque(maxlen=3600)
        self.sequence = 0
        self.boot_time = time.monotonic()
        self.connection_stats = {"mqtt_reconnects": 0, "ble_reads": 0, "ble_writes": 0}
        self.sensor_health = {name: True for name in SENSOR_UNITS}
        self.raw = {"temperature": 22.0, "humidity": 48.0, "pressure": 1013.0,
                    "light": 380.0, "air_quality": 72.0, "wind_speed": 2.4}
        self.current = self._format_sample()

    def _load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            source = self.config_path.with_name("config.example.json")
            self.config_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def config_snapshot(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.config))

    def update_config(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {"location", "interval_seconds", "calibration"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Невідомі поля конфігурації: {', '.join(sorted(unknown))}")
        with self.lock:
            if "location" in values:
                location = str(values["location"]).strip()
                if not location or len(location) > 80:
                    raise ValueError("location має містити від 1 до 80 символів")
                self.config["location"] = location
            if "interval_seconds" in values:
                interval = int(values["interval_seconds"])
                if not 1 <= interval <= 60:
                    raise ValueError("interval_seconds має бути від 1 до 60")
                self.config["interval_seconds"] = interval
            if "calibration" in values:
                if not isinstance(values["calibration"], dict):
                    raise ValueError("calibration має бути JSON-об'єктом")
                calibration = self.config["calibration"]
                for key, value in values["calibration"].items():
                    if key not in SENSOR_UNITS:
                        raise ValueError(f"Невідомий канал калібрування: {key}")
                    number = float(value)
                    if not -1000 <= number <= 1000:
                        raise ValueError(f"Калібрування {key} поза дозволеним діапазоном")
                    calibration[key] = number
            self.config_path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
            return self.config_snapshot()

    def _format_sample(self) -> dict[str, Any]:
        calibration = self.config["calibration"]
        values = {key: round(raw + float(calibration.get(key, 0)), 2) for key, raw in self.raw.items()}
        values["humidity"] = max(0.0, min(100.0, values["humidity"]))
        values["pressure"] = max(800.0, min(1200.0, values["pressure"]))
        values["light"] = max(0.0, values["light"])
        values["air_quality"] = max(0.0, min(500.0, values["air_quality"]))
        values["wind_speed"] = max(0.0, values["wind_speed"])
        return {
            "device_id": self.config["device_id"],
            "location": self.config["location"],
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sequence": self.sequence,
            "sensors": {
                name: {"value": value, "unit": SENSOR_UNITS[name], "health": self.sensor_health[name]}
                for name, value in values.items()
            },
        }

    def sample(self) -> dict[str, Any]:
        with self.lock:
            # Smooth, bounded random walk models calibrated sensors plus measurement noise.
            changes = {"temperature": (-0.35, 0.35), "humidity": (-1.2, 1.2),
                       "pressure": (-0.8, 0.8), "light": (-18.0, 18.0),
                       "air_quality": (-3.0, 3.0), "wind_speed": (-0.25, 0.25)}
            for name, (lo, hi) in changes.items():
                self.raw[name] = max(0.0, self.raw[name] + random.uniform(lo, hi))
            self.sequence += 1
            self.current = self._format_sample()
            self.samples.append(self.current)
            return json.loads(json.dumps(self.current))

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.current))

    def history(self, minutes: int = 60) -> list[dict[str, Any]]:
        if not 1 <= minutes <= 1440:
            raise ValueError("minutes має бути від 1 до 1440")
        cutoff = time.time() - minutes * 60
        with self.lock:
            return [
                json.loads(json.dumps(row)) for row in self.samples
                if datetime.fromisoformat(row["timestamp"]).timestamp() >= cutoff
            ]

    def diagnostics(self) -> dict[str, Any]:
        with self.lock:
            sample = self.current["sensors"]
            return {
                "overall": "OK" if all(self.sensor_health.values()) else "DEGRADED",
                "sensor_health": dict(self.sensor_health),
                "calibration_offsets": dict(self.config["calibration"]),
                "sampling_interval_seconds": self.config["interval_seconds"],
                "samples_in_memory": len(self.samples),
                "uptime_seconds": round(time.monotonic() - self.boot_time),
                "last_sequence": self.sequence,
                "ble_profile": "simulated GATT profile (radio server is in ESP32 firmware)",
                "last_values_finite": all(isinstance(v["value"], (int, float)) for v in sample.values()),
            }

    def record_ble_read(self) -> None:
        with self.lock:
            self.connection_stats["ble_reads"] += 1

    def record_ble_write(self) -> None:
        with self.lock:
            self.connection_stats["ble_writes"] += 1
