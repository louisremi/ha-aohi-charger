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

The provisioning tool is a single static file with no dependencies. Web Bluetooth needs a secure
context, so either open your local copy directly (`file://`) or use the published one:

**https://louisremi.github.io/ha-aohi-charger/tools/aohi-ble-provisioning.html**

It runs entirely in your browser and talks only to the charger over Bluetooth — nothing is sent
anywhere.

## Status: unverified

Neither tool has been run against real hardware yet. Two things are known and unresolved:

1. **Plain `ws://` may not work.** atc1441 reports the firmware requires a CA-signed certificate
   and rejects self-signed ones. Whether it accepts unencrypted transport is untested. If it
   doesn't, local control needs a real domain and a valid certificate — which makes it much less
   attractive. The capture server exists to settle this question.
2. **Restore is best-effort.** No known command reads the current endpoint back, so "restore"
   writes our best-known factory values rather than a verified backup. Worse, the real factory
   host2 (`wss://iotservice.iaohi.com:443/ws/iot1/`) is 39 characters and the field only holds
   38 — so the stored value must differ from the documented one, and we don't know how.

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
