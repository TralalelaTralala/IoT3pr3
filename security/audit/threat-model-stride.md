# STRIDE threat model — навчальна IoT система

| Загроза | Приклад | Захист/перевірка |
|---|---|---|
| Spoofing | Підміна вузла або брокера | mTLS, унікальні X.509 identity, pinning на gateway |
| Tampering | Зміна телеметрії/прошивки | AEAD/HMAC, підпис OTA, Secure Boot |
| Repudiation | Неможливо підтвердити команду | журнал подій з timestamp, sequence, device identity |
| Information disclosure | Перехоплення telemetry/key | TLS 1.3, E2E захист чутливого payload, секрети поза Git |
| Denial of service | MQTT flood / повторні підключення | ACL, rate limit, обмежений retry, watchdog |
| Elevation of privilege | Вузол пише чужі топіки | per-device ACL і сегментація мережі |

Обмеження: модель є навчальною. Ризики та контроль слід уточнити для реального обладнання, процесів і даних.
