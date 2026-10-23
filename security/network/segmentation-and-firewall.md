# Network segmentation baseline

- Put IoT devices in a dedicated VLAN/subnet; deny inbound access from user and guest networks by default.
- Permit only gateway-to-broker TLS (8883), device DNS/NTP to approved services, and management from a hardened admin subnet.
- Deny direct internet egress for sensors unless required; allowlist update endpoints through a proxy.
- Place MQTT broker in a service zone; do not expose the broker listener to the public internet.
- Log denied connections and rate-limit new sessions at the gateway. Validate policies in a disposable virtual lab.
