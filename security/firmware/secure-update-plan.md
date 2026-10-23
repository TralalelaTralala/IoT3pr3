# Secure firmware and OTA plan

- Build with ESP-IDF Secure Boot V2 and signed application images; verify the signature before boot.
- Enable anti-rollback only with a tested version scheme and known-good recovery image.
- Encrypt flash in release mode after provisioning and backup/recovery checks. Never paste generated keys into documentation or Git.
- OTA sequence: download to inactive slot, verify signature/hash and version, boot trial image, run health checks, mark valid or roll back automatically.
- Keep serial recovery procedures and signed factory image available for the lab.
- Do not enable/fuse these options on the user's physical device automatically.
