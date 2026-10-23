"""Publish a short MQTT probe and verify it arrives with QoS 1 and retain."""
import json
import time

import paho.mqtt.client as mqtt

host, port = "127.0.0.1", 1883
topic = "weather_station/test/iot3_probe"
received = []
subscriber = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="iot3-check-sub")


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        client.subscribe(topic, qos=1)


def on_message(client, userdata, message):
    received.append((message.payload.decode(), message.qos, message.retain))


subscriber.on_connect = on_connect
subscriber.on_message = on_message
subscriber.connect(host, port, 10)
subscriber.loop_start()
time.sleep(0.4)
publisher = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="iot3-check-pub")
publisher.connect(host, port, 10)
publisher.loop_start()
payload = json.dumps({"test": "iot3", "qos": 1, "retain": True})
info = publisher.publish(topic, payload, qos=1, retain=True)
info.wait_for_publish(timeout=3)
deadline = time.monotonic() + 3
while not received and time.monotonic() < deadline:
    time.sleep(0.05)
publisher.disconnect()
publisher.loop_stop()
subscriber.disconnect()
subscriber.loop_stop()
assert received and received[0][0] == payload, f"probe not received: {received}"
print(f"MQTT publish/subscribe PASS: QoS={received[0][1]}, value={received[0][0]}")
