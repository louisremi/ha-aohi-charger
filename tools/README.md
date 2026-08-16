# Tools — experimental local-control research

> **None of this is needed to use the integration.** The integration talks to AOHI's cloud and
> works fine on its own. Everything here is opt-in research towards *optional* local control,
> and it involves reconfiguring your hardware. Skip it unless that's what you're after.

## Why

The integration depends on AOHI's cloud: if their servers or your charger's internet connection
are down, it can't do anything. [atc1441's reverse engineering][atc] found the charger accepts a
BLE command (`0x10`, SetCloudHost) that rewrites the two server URLs it talks to — so in
principle it could be pointed at a server on your own LAN instead.

"In principle" is doing real work in that sentence. See the status section below.

## What's here

| | |
|---|---|
| [`PROTOCOL.md`](PROTOCOL.md) | What the charger says to the cloud, recovered from real hardware. |
| [`capture-server/`](capture-server/) | Docker container that impersonates the cloud and logs whatever the charger sends. |

**The BLE provisioning tool has moved** to the add-on repository, which is the only place it is
useful: **https://louisremi.github.io/ha-aohi-local-server/**

To actually *run* a charger locally, use the
**[AOHI Local Server add-on](https://github.com/louisremi/ha-aohi-local-server)**, which installs
into Home Assistant. The capture server here is a diagnostic for protocol work, not something to
depend on.

## Status: partially verified against real hardware

Both tools have now been run against a real AOC-C022. See **[PROTOCOL.md](PROTOCOL.md)** for the
full findings. Headlines:

- **The REST leg needs no TLS.** The charger completed its entire cloud bootstrap against a plain
  `http://` server on a LAN IP. No domain, no certificate.
- **The device-side bootstrap chain is mapped**: `device/login` → `device/mqtt/info` →
  `time/second` + `weather/current`, with every response shape confirmed against AOHI's own cloud.
- **Restore is straightforward after all**: factory reset and re-add in the app, which rewrites the
  cloud hosts for you. The exact factory URLs never need to be known — which is just as well, since
  no command reads them back.
- **host2 must be `wss://`.** This was the last unknown, and it cost the most time: given a plain
  `ws://` address the firmware opens no socket at all, silently, which looks identical to the URL
  never having been stored. It was stored correctly the whole time. A **self-signed certificate is
  accepted**, so no domain and no certificate authority are needed.
- **Local control works end to end** and is packaged as the add-on linked above.

Corrections to what these tools originally assumed: the charger advertises as **`AOC-C022-<xx>`**,
not `7231N`; the negotiated **MTU is 517**, so no fragmentation risk; and the write characteristic
is **write-with-response only**.

## Suggested order

If you are doing protocol work rather than just using the add-on:

1. **Read-only pass** — device info / WiFi config / status. Confirms the tool talks to your
   charger at all. Download the backup.
2. **Point it at the capture server** and read the log to see the bootstrap chain first-hand.
3. **Restore** by factory resetting and re-adding in the app.

A cloud-host *read* is not possible: `0x02, 0x06, 0x07, 0x08, 0x0A, 0x0B, 0x0D, 0x0E, 0x0F, 0x11`
were all probed with empty payloads and none of them ever replied. Don't spend time on it.

## Credit

All BLE protocol reverse engineering is [atc1441's][atc]. The tool here is an independent,
purpose-built implementation written from the protocol facts, not a copy of his code (his repo
carries no licence, so it isn't ours to redistribute). If this is useful to you, the credit is his.

[atc]: https://github.com/atc1441/AOHi_280W_Charger_Hacking
