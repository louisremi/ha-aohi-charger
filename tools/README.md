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
| [`aohi-ble-provisioning.html`](aohi-ble-provisioning.html) | Web Bluetooth tool to read the charger's config, point it at a custom endpoint, and restore the official one. |
| [`capture-server/`](capture-server/) | Docker container that impersonates the cloud and logs whatever the charger sends. |

To actually *run* a charger locally, use the
**[AOHI Local Server add-on](https://github.com/louisremi/ha-aohi-local-server)**, which installs
into Home Assistant. The capture server here is a diagnostic for protocol work, not something to
depend on.

The provisioning tool is a single static file with no dependencies. Web Bluetooth needs a secure
context, so either open your local copy directly (`file://`) or use the published one:

**https://louisremi.github.io/ha-aohi-charger/tools/aohi-ble-provisioning.html**

It runs entirely in your browser and talks only to the charger over Bluetooth — nothing is sent
anywhere.

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
- **Still unsolved**: the charger never contacts host2 at all, so MQTT has not been reached. The
  likely cause is that host2 is not stored where we think it is. See PROTOCOL.md for the next
  experiments.

Corrections to what these tools originally assumed: the charger advertises as **`AOC-C022-<xx>`**,
not `7231N`; the negotiated **MTU is 517**, so no fragmentation risk; and the write characteristic
is **write-with-response only**.

## Suggested order

Sequenced so reversibility is proven *before* anything depends on it:

1. **Read-only pass** — device info / WiFi config / status. Confirms the tool talks to your
   charger at all. Download the backup.
2. **Probe for a cloud-host read** — worth real effort: if any command returns the current URLs,
   problem 2 above disappears entirely and restore becomes exact.
3. **Write the factory values back** (a no-op in principle), then check the charger still works
   in the official AOHI app. If this fails, stop — you've learned restore is unreliable without
   having lost anything.
4. **Point it at the capture server** and see whether it connects. This answers question 1.
5. **Restore**, and confirm normal operation via the app and the integration.

## Credit

All BLE protocol reverse engineering is [atc1441's][atc]. The tool here is an independent,
purpose-built implementation written from the protocol facts, not a copy of his code (his repo
carries no licence, so it isn't ours to redistribute). If this is useful to you, the credit is his.

[atc]: https://github.com/atc1441/AOHi_280W_Charger_Hacking
