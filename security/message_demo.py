"""Small, dependency-free lab demo of authenticated, replay-resistant telemetry."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any


class TelemetryVerifier:
    """In-memory verifier. The random key intentionally changes on every run."""

    def __init__(self, key: bytes | None = None) -> None:
        self._key = key or secrets.token_bytes(32)
        self._seen: set[tuple[str, str]] = set()
        self._last_sequence: dict[str, int] = {}

    @staticmethod
    def canonical(message: dict[str, Any]) -> bytes:
        return json.dumps(message, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")

    def sign(self, message: dict[str, Any]) -> str:
        return hmac.new(self._key, self.canonical(message), hashlib.sha256).hexdigest()

    def verify(self, message: dict[str, Any], signature: str,
               now: float | None = None) -> tuple[bool, str]:
        expected = self.sign(message)
        if not hmac.compare_digest(expected, str(signature)):
            return False, "signature_invalid"
        device = str(message.get("device_id", ""))
        nonce = str(message.get("nonce", ""))
        sequence = message.get("sequence")
        timestamp = message.get("timestamp")
        if not device or not nonce or not isinstance(sequence, int) or not isinstance(timestamp, (int, float)):
            return False, "message_fields_invalid"
        if abs((time.time() if now is None else now) - timestamp) > 60:
            return False, "timestamp_outside_window"
        replay_key = (device, nonce)
        if replay_key in self._seen or sequence <= self._last_sequence.get(device, -1):
            return False, "replay_rejected"
        self._seen.add(replay_key)
        self._last_sequence[device] = sequence
        return True, "accepted"


def run_demo() -> dict[str, Any]:
    verifier = TelemetryVerifier()
    now = time.time()
    message = {"device_id": "lab-sensor-01", "sequence": 1,
               "timestamp": now, "nonce": secrets.token_hex(12),
               "temperature_c": 22.4}
    signature = verifier.sign(message)
    accepted, valid_result = verifier.verify(message, signature, now)
    replayed, replay_result = verifier.verify(message, signature, now)
    altered = dict(message, temperature_c=99.9)
    tampered, tamper_result = verifier.verify(altered, signature, now)
    return {
        "algorithm": "HMAC-SHA-256 + timestamp + nonce + monotonic sequence",
        "checks": [
            {"name": "Підписане повідомлення", "passed": accepted, "result": valid_result},
            {"name": "Повтор того самого повідомлення", "passed": not replayed, "result": replay_result},
            {"name": "Зміна даних після підпису", "passed": not tampered, "result": tamper_result},
        ],
        "key_lifetime": "тільки пам'ять процесу; випадковий ключ щоразу",
        "scope": "локальна навчальна демонстрація; не замінює TLS/mTLS або керування ключами",
    }
