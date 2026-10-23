import json
import tempfile
import unittest
from pathlib import Path

from weatherstation.app import create_app


class WeatherApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        example = Path(__file__).resolve().parents[1] / "config.example.json"
        self.config = Path(self.tmp.name) / "config.json"
        self.config.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        self.app, self.station, self.mqtt = create_app(self.config)
        self.client = self.app.test_client()

    def tearDown(self):
        self.mqtt.stop()
        self.tmp.cleanup()

    def test_sensor_snapshot_and_history(self):
        response = self.client.get("/api/sensors")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json["sensors"]), {"temperature", "humidity", "pressure", "light", "air_quality", "wind_speed"})
        self.station.sample()
        self.assertEqual(self.client.get("/api/history?minutes=60").json["count"], 1)

    def test_config_updates_and_validation(self):
        ok = self.client.post("/api/config", json={"interval_seconds": 5, "location": "Лабораторія"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json["config"]["interval_seconds"], 5)
        bad = self.client.post("/api/config", json={"interval_seconds": 100})
        self.assertEqual(bad.status_code, 400)

    def test_gatt_mirror_and_write(self):
        profile = self.client.get("/api/ble/profile").json
        self.assertEqual(profile["services"][0]["uuid"], "0000181a-0000-1000-8000-00805f9b34fb")
        response = self.client.post("/api/ble/write/interval_seconds", json={"value": 7})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["config"]["interval_seconds"], 7)

    def test_docs_and_cors(self):
        response = self.client.get("/api/docs")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/api/sensors", response.json["paths"])
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")


if __name__ == "__main__":
    unittest.main()
