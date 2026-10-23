# Part 2 hardware migration plan (ESP32)

The classroom dashboard is a software model. This document records an implementation plan for subsequent hardware validation; it is not a claim that radios were flashed or measured.

## LoRaWAN end devices

- MCU: ESP32; radio: RFM95/SX1276 over SPI. Use a LoRaWAN stack that supports LoRaWAN 1.0.4 and the EU868 regional channel plan.
- Activation: OTAA with DevEUI/JoinEUI/AppKey provisioned per node. Keep keys out of source control and logs.
- Class A, ADR enabled, RX1 after 1 s and RX2 after 2 s per the selected stack/regional configuration.
- Five soil nodes report one moisture value each; weather and reservoir nodes report their own sensor data. A compact application payload uses version + sequence + scaled integer values; do not send JSON over LoRaWAN.
- Wake by RTC, sample, transmit, service downlink windows, persist a small checkpoint, then deep sleep. Feed watchdog during sensor warmup and radio retries. Add bounded retries and a local queue.

## Zigbee mesh

- One powered coordinator, two powered router nodes, and three sleepy end devices.
- Use a commissioned network with unique PAN ID and the network key provisioned outside repository files. Prefer coordinator/router for devices that can remain powered.
- The simulator graph uses LQI as a link cost and recomputes shortest routes after router failure. Hardware test: remove power from one router and record route restoration, packet loss and recovery duration.

## CoAP

Plan a separate RFC 7252 server/stack on constrained nodes or gateway proxy. Resource inventory is listed in `../README.md`. Use CON for commands that require acknowledgment and NON for periodic low-risk telemetry. Observe is a subscription extension (RFC 7641); Block-wise is a transfer extension (RFC 7959). The current Flask interface only represents resources over HTTP/JSON and does not implement CoAP wire format.

## Test checklist for physical build

1. Record RSSI/SNR at marked distances in line-of-sight and behind one wall; repeat for antenna orientations.
2. Compare ADR on/off and record spreading factor, airtime, packet delivery and energy.
3. Remove each router in turn; capture mesh re-route and recovery time.
4. Measure current during deep sleep, sampling and TX with an inline power analyzer; calculate battery life from measured duty cycle.
5. Test watchdog recovery by forcing a controlled sensor timeout.

Hardware connection tables and exact RF power/channel settings must follow the selected board/radio, local regulation, and library version.
