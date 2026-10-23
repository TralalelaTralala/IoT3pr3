"""Human-readable GATT profile mirror for the desktop emulator.

The actual over-the-air BLE server and passkey pairing are implemented in firmware/src/main.cpp.
This profile mirror lets a learner inspect and exercise the same GATT reads/writes without a BLE adapter.
"""

WEATHER_SERVICE = "0000181a-0000-1000-8000-00805f9b34fb"
CONFIG_SERVICE = "7a1b0001-3f4d-4c82-9b67-0f12a58c0001"
DIAGNOSTICS_SERVICE = "7a1b0002-3f4d-4c82-9b67-0f12a58c0002"

CHARACTERISTICS = {
    "temperature": {"service": WEATHER_SERVICE, "uuid": "00002a6e-0000-1000-8000-00805f9b34fb", "properties": ["read", "notify"], "unit": "°C"},
    "humidity": {"service": WEATHER_SERVICE, "uuid": "00002a6f-0000-1000-8000-00805f9b34fb", "properties": ["read", "notify"], "unit": "%"},
    "pressure": {"service": WEATHER_SERVICE, "uuid": "00002a6d-0000-1000-8000-00805f9b34fb", "properties": ["read", "notify"], "unit": "hPa"},
    "light": {"service": WEATHER_SERVICE, "uuid": "7a1b0101-3f4d-4c82-9b67-0f12a58c0001", "properties": ["read", "notify"], "unit": "lx"},
    "air_quality": {"service": WEATHER_SERVICE, "uuid": "7a1b0102-3f4d-4c82-9b67-0f12a58c0001", "properties": ["read", "notify"], "unit": "AQI"},
    "wind_speed": {"service": WEATHER_SERVICE, "uuid": "7a1b0103-3f4d-4c82-9b67-0f12a58c0001", "properties": ["read", "notify"], "unit": "m/s"},
    "interval_seconds": {"service": CONFIG_SERVICE, "uuid": "7a1b0201-3f4d-4c82-9b67-0f12a58c0001", "properties": ["read", "write"], "unit": "s"},
    "location": {"service": CONFIG_SERVICE, "uuid": "7a1b0202-3f4d-4c82-9b67-0f12a58c0001", "properties": ["read", "write"], "unit": ""},
    "calibration": {"service": CONFIG_SERVICE, "uuid": "7a1b0203-3f4d-4c82-9b67-0f12a58c0001", "properties": ["read", "write"], "unit": "JSON offsets"},
    "diagnostics": {"service": DIAGNOSTICS_SERVICE, "uuid": "7a1b0301-3f4d-4c82-9b67-0f12a58c0001", "properties": ["read", "notify"], "unit": "JSON"},
}


def get_profile():
    return {
        "mode": "Desktop GATT profile emulator; for radio BLE use firmware on ESP32",
        "security": "ESP32 firmware: LE Secure Connections + MITM + demo passkey 123456",
        "services": [
            {"name": "Weather Service", "uuid": WEATHER_SERVICE},
            {"name": "Configuration Service", "uuid": CONFIG_SERVICE},
            {"name": "Diagnostic Service", "uuid": DIAGNOSTICS_SERVICE},
        ],
        "characteristics": CHARACTERISTICS,
    }
