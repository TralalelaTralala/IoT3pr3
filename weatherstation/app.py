from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

from .ble_profile import CHARACTERISTICS, get_profile
from .model import SENSOR_UNITS, Station
from .mqtt_transport import MqttTransport
from .agri_simulator import AgricultureSystem
from security.message_demo import run_demo

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger("weatherstation")


def create_app(config_path: Path | None = None):
    station = Station(config_path or ROOT / "config.json")
    transport = MqttTransport(station, station.update_config)
    app = Flask(__name__, static_folder=None)
    app.config["STATION"] = station
    app.config["MQTT"] = transport
    app.config["RATE_BUCKETS"] = {}
    app.config["RATE_LIMIT_PER_MINUTE"] = 60
    agri = AgricultureSystem()
    app.config["AGRI"] = agri
    app.config["RATE_LOCK"] = threading.Lock()

    @app.before_request
    def rate_limit():
        if not request.path.startswith("/api/"):
            return None
        now = time.monotonic()
        key = request.remote_addr or "unknown"
        with app.config["RATE_LOCK"]:
            count, start = app.config["RATE_BUCKETS"].get(key, (0, now))
            if now - start >= 60:
                count, start = 0, now
            if count >= app.config["RATE_LIMIT_PER_MINUTE"]:
                return jsonify({"error": "rate limit exceeded", "limit_per_minute": 60}), 429
            app.config["RATE_BUCKETS"][key] = (count + 1, start)
        return None

    @app.after_request
    def cors(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Max-Age"] = "600"
        return response

    @app.route("/api/<path:_path>", methods=["OPTIONS"])
    def options(_path):
        return ("", 204)

    @app.get("/")
    def index():
        return send_from_directory(ROOT / "web", "index.html")

    @app.get("/part2")
    def part2_page():
        return send_from_directory(ROOT / "web", "part2.html")

    @app.get("/security")
    def security_page():
        return send_from_directory(ROOT / "web", "security.html")

    @app.get("/api/part3/security")
    def part3_security_status():
        files = {
            "pki": ROOT / "security/pki/openssl-ca.cnf.example",
            "mqtt": ROOT / "security/mqtt/mosquitto-tls.conf.example",
            "secure_boot": ROOT / "security/firmware/sdkconfig.defaults.example",
            "vpn": ROOT / "security/network/ipsec.conf.example",
            "threat_model": ROOT / "security/audit/threat-model-stride.md",
        }
        controls = [
            {"id": key, "template_present": path.is_file(), "active": False,
             "state": "Підготовлено" if path.is_file() else "Файл відсутній"}
            for key, path in files.items()
        ]
        controls.append({"id": "message_security", "template_present": True,
                         "active": True, "state": "HMAC і захист від повтору працюють у локальному демо"})
        return jsonify({
            "project": "IoTpr3", "mode": "design-and-local-lab",
            "notice": "Демонстрація HMAC та відхилення повторів працює локально. TLS/mTLS, VPN і Secure Boot описані конфігураціями, але не активовані на брокері чи фізичному ESP32.",
            "controls": controls,
            "private_keys_present": any((ROOT / "security/keys").glob("*")),
            "scope": "Лише локальна демонстрація власного стенду",
        })

    @app.post("/api/part3/demo")
    def part3_security_demo():
        return jsonify(run_demo())

    @app.get("/api/part3/audit")
    def part3_audit_plan():
        paths = ["security/audit/authorized-lab-test-plan.md",
                 "security/audit/threat-model-stride.md",
                 "security/incident-response.md"]
        mqtt = (ROOT / "security/mqtt/mosquitto-tls.conf.example").read_text(encoding="utf-8")
        acl = (ROOT / "security/mqtt/acl.example").read_text(encoding="utf-8")
        boot = (ROOT / "security/firmware/sdkconfig.defaults.example").read_text(encoding="utf-8")
        private_files = list((ROOT / "security/keys").glob("**/*")) if (ROOT / "security/keys").exists() else []
        checks = [
            {"name": "MQTT вимагає клієнтський сертифікат", "passed": "require_certificate true" in mqtt},
            {"name": "Анонімний MQTT вимкнений", "passed": "allow_anonymous false" in mqtt},
            {"name": "Мінімальна версія TLS задана", "passed": "tls_version tlsv1.3" in mqtt},
            {"name": "ACL обмежує топіки вузлів", "passed": "topic write" in acl and "topic read" in acl},
            {"name": "Опції Secure Boot підготовлені", "passed": "CONFIG_SECURE_BOOT=y" in boot and "CONFIG_SECURE_BOOT_V2_ENABLED=y" in boot},
            {"name": "Приватних ключів у каталозі проєкту немає", "passed": not any(path.is_file() for path in private_files)},
        ]
        return jsonify({"audit_executed": True, "audit_type": "static-configuration-review",
                        "message": "Виконано статичну перевірку локальних шаблонів. Мережевий penetration test і перевірка фізичного ESP32 не виконувалися.",
                        "checks": checks,
                        "artifacts": [{"path": name, "present": (ROOT / name).is_file()} for name in paths]})

    @app.get("/api/part2/dashboard")
    def part2_dashboard():
        return jsonify(agri.dashboard())

    @app.post("/api/part2/sample")
    def part2_sample():
        return jsonify(agri.sample())

    @app.get("/api/part2/coverage")
    def part2_coverage():
        return jsonify({"devices": agri.coverage(), "note": "Модель покриття; не результат вимірювання радіо"})

    @app.post("/api/part2/zigbee/fail/<router_id>")
    def part2_fail_router(router_id):
        try:
            return jsonify(agri.fail_router(router_id))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/part2/zigbee/recover/<router_id>")
    def part2_recover_router(router_id):
        try:
            return jsonify(agri.recover_router(router_id))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/part2/actuator/<actuator>")
    def part2_actuator(actuator):
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(agri.set_actuator(actuator, bool(body.get("state"))))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/part2/coap/<path:resource>")
    def part2_coap_resource(resource):
        try:
            return jsonify({"code": "2.05 Content", "content_format": "application/json",
                            "path": "/" + resource, "value": agri.coap_resource(resource)})
        except KeyError:
            return jsonify({"code": "4.04 Not Found", "path": "/" + resource}), 404

    @app.get("/api/sensors")
    def get_sensors():
        return jsonify(station.snapshot())

    @app.get("/api/sensors/<sensor_type>")
    def get_sensor(sensor_type: str):
        if sensor_type not in SENSOR_UNITS:
            return jsonify({"error": "unknown sensor type", "available": list(SENSOR_UNITS)}), 404
        sample = station.snapshot()
        return jsonify({"device_id": sample["device_id"], "timestamp": sample["timestamp"],
                        "sequence": sample["sequence"], "sensor": sensor_type,
                        **sample["sensors"][sensor_type]})

    @app.get("/api/history")
    def get_history():
        try:
            minutes = int(request.args.get("minutes", "60"))
            return jsonify({"minutes": minutes, "count": len(station.history(minutes)),
                            "samples": station.history(minutes)})
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/config")
    def post_config():
        values = request.get_json(silent=True)
        if not isinstance(values, dict):
            return jsonify({"error": "request body must be a JSON object"}), 400
        try:
            config = station.update_config(values)
            mqtt = app.config["MQTT"]
            if "interval_seconds" in values:
                mqtt._publish(f"{mqtt.root}/config/interval", str(config["interval_seconds"]), retain=True)
            if "location" in values:
                mqtt._publish(f"{mqtt.root}/config/location", config["location"], retain=True)
            if "calibration" in values:
                mqtt._publish(f"{mqtt.root}/config/calibration", json.dumps(values["calibration"]), retain=True)
            return jsonify({"ok": True, "config": config})
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/status")
    def get_status():
        cfg = station.config_snapshot()
        return jsonify({"device_id": cfg["device_id"], "location": cfg["location"],
                        "uptime_seconds": station.diagnostics()["uptime_seconds"],
                        "mqtt_connected": transport.connected.is_set(),
                        "mqtt_broker": f"{cfg['mqtt']['host']}:{cfg['mqtt']['port']}",
                        "ble": "GATT profile available; physical radio provided by ESP32 firmware",
                        "last_sensor_update": station.snapshot()["timestamp"]})

    @app.get("/api/diagnostics")
    def get_diagnostics():
        result = station.diagnostics()
        result["connections"] = station.connection_stats.copy()
        result["protocols"] = {"mqtt": "connected" if transport.connected.is_set() else "reconnecting",
                               "http": "online", "ble": "profile emulator (no host BLE adapter required)"}
        return jsonify(result)

    @app.get("/api/dashboard")
    def dashboard_data():
        """One-call dashboard snapshot so polling stays under the demo rate limit."""
        cfg = station.config_snapshot()
        diagnostic = station.diagnostics()
        return jsonify({
            "sensors": station.snapshot(),
            "status": {
                "device_id": cfg["device_id"], "location": cfg["location"],
                "uptime_seconds": diagnostic["uptime_seconds"],
                "mqtt_connected": transport.connected.is_set(),
                "mqtt_broker": f"{cfg['mqtt']['host']}:{cfg['mqtt']['port']}",
                "last_sensor_update": station.snapshot()["timestamp"],
            },
            "diagnostics": {**diagnostic, "connections": station.connection_stats.copy()},
            "history": station.history(60),
            "config": cfg,
            "ble_profile": get_profile(),
        })

    @app.get("/api/ble/profile")
    def ble_profile():
        return jsonify(get_profile())

    @app.get("/api/ble/read/<characteristic>")
    def ble_read(characteristic: str):
        if characteristic not in CHARACTERISTICS:
            return jsonify({"error": "unknown characteristic", "available": list(CHARACTERISTICS)}), 404
        station.record_ble_read()
        cfg = station.config_snapshot()
        snapshot = station.snapshot()
        if characteristic in SENSOR_UNITS:
            value = snapshot["sensors"][characteristic]
        elif characteristic == "interval_seconds":
            value = {"value": cfg["interval_seconds"], "unit": "s"}
        elif characteristic == "location":
            value = {"value": cfg["location"], "unit": ""}
        elif characteristic == "calibration":
            value = {"value": cfg["calibration"], "unit": "JSON offsets"}
        else:
            value = {"value": station.diagnostics(), "unit": "JSON"}
        return jsonify({"characteristic": characteristic, **value})

    @app.post("/api/ble/write/<characteristic>")
    def ble_write(characteristic: str):
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or "value" not in body:
            return jsonify({"error": "body must contain value"}), 400
        try:
            if characteristic == "interval_seconds":
                station.update_config({"interval_seconds": body["value"]})
            elif characteristic == "location":
                station.update_config({"location": body["value"]})
            elif characteristic == "calibration":
                station.update_config({"calibration": body["value"]})
            else:
                return jsonify({"error": "characteristic is read-only or unknown"}), 400
            station.record_ble_write()
            config = station.config_snapshot()
            return jsonify({"ok": True, "config": config, "transport": "desktop GATT-profile emulator"})
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/docs")
    def api_docs():
        # Generate the endpoint list from Flask's registered routes on each request.
        paths: dict[str, Any] = {}
        for rule in app.url_map.iter_rules():
            if rule.rule.startswith("/api/") and rule.rule != "/api/<path:_path>":
                methods = sorted(method for method in rule.methods if method in {"GET", "POST"})
                paths[rule.rule] = {method.lower(): {"summary": rule.endpoint.replace("_", " ")} for method in methods}
        return jsonify({"openapi": "3.0.3", "info": {"title": "Weather Station API", "version": "1.0.0"}, "paths": paths})

    return app, station, transport


def run_server():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app, station, transport = create_app()
    stop = threading.Event()

    def sampling_loop():
        while not stop.is_set():
            sample = station.sample()
            transport.publish_snapshot(sample)
            LOG.info("T %.1f°C | RH %.1f%% | P %.1f hPa | light %.0f lx | AQI %.0f | wind %.1f m/s | MQTT %s",
                     sample["sensors"]["temperature"]["value"], sample["sensors"]["humidity"]["value"],
                     sample["sensors"]["pressure"]["value"], sample["sensors"]["light"]["value"],
                     sample["sensors"]["air_quality"]["value"], sample["sensors"]["wind_speed"]["value"],
                     "online" if transport.connected.is_set() else "retrying")
            stop.wait(station.config_snapshot()["interval_seconds"])

    transport.start()
    worker = threading.Thread(target=sampling_loop, name="weather-sampler", daemon=True)
    worker.start()
    cfg = station.config_snapshot()
    LOG.info("REST dashboard: http://%s:%s", cfg.get("http_host", "127.0.0.1"), cfg.get("http_port", 5000))
    try:
        app.run(host=cfg.get("http_host", "127.0.0.1"), port=int(cfg.get("http_port", 5000)), threaded=True, use_reloader=False)
    finally:
        stop.set()
        transport.stop()

