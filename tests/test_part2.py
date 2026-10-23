from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from weatherstation.agri_simulator import AgricultureSystem
from weatherstation.app import create_app


class AgricultureModelTests(unittest.TestCase):
    def setUp(self):
        self.system = AgricultureSystem()

    def test_lorawan_payload_and_sensor_nodes(self):
        data = self.system.sample()
        self.assertEqual(len(data["lorawan"]["devices"]), 7)
        self.assertEqual(data["lorawan"]["last_payload_bytes"], 18)
        self.assertEqual(len(data["values"]["soil_moisture"]), 5)
        self.assertEqual(data["lorawan"]["region"], "EU868")

    def test_zigbee_self_heals_and_recovers(self):
        result = self.system.fail_router("router-north")
        self.assertTrue(result["event"]["recovered"])
        self.assertIsNotNone(result["routes"]["soil-east"])
        restored = self.system.recover_router("router-north")
        self.assertIsNotNone(restored["routes"]["soil-west"])

    def test_coap_resource_catalog(self):
        self.assertIn("value", self.system.coap_resource("/sensors/temperature"))
        self.assertEqual(len(self.system.dashboard()["coap"]["resource_paths"]), 11)

    def test_energy_sleep_target_and_actuator(self):
        self.assertGreaterEqual(self.system.energy()["sleep_percent"], 98)
        self.assertTrue(self.system.set_actuator("irrigation", True)["state"])

    def test_http_page_and_api(self):
        app, _, _ = create_app(ROOT / "test-config.json")
        app.config["TESTING"] = True
        client = app.test_client()
        response = client.get("/part2")
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertEqual(client.get("/api/part2/dashboard").status_code, 200)
        self.assertEqual(client.get("/api/part2/coap/sensors/temperature").status_code, 200)
        self.assertEqual(client.post("/api/part2/zigbee/fail/router-north").status_code, 200)
        self.assertEqual(client.post("/api/part2/zigbee/recover/router-north").status_code, 200)
        test_config = ROOT / "test-config.json"
        if test_config.exists():
            test_config.unlink()


if __name__ == "__main__":
    unittest.main()


