# Device-side protocol notes

What the **charger itself** says to the cloud, captured by pointing a real AOC-C022 at the
[capture server](capture-server/) on a LAN address. This is distinct from the *app*-side protocol
the integration speaks, which is documented in the top-level [README](../README.md).

Everything here was observed on real hardware (firmware `1.0.16`, MCU `1.2.0`) unless marked
otherwise.

## BLE provisioning

Corrections to the assumptions the tooling was originally built on:

| | Assumed | Actually observed |
|---|---|---|
| Advertised name | prefix `7231N` | **`AOC-C022-9a`** — model plus the last byte of the WiFi MAC |
| BLE address | — | WiFi MAC **+1** (WiFi `…:71:9a` → BLE `…:71:9B`) |
| ATT MTU | possibly as low as 23, fragmentation feared | **517** negotiated; the 126-byte SetCloudHost frame fits in one write with room to spare |
| Write characteristic | `07af27a5-…` write-without-response | `07af27a5-…` is **write-with-response only**; writing as write-without-response is silently dropped |

Confirmed as documented by [atc1441](https://github.com/atc1441/AOHi_280W_Charger_Hacking): the
`0x55AA` framing, running-sum checksum, big-endian TX / little-endian RX lengths, the GATT UUIDs,
and the `cmd 0x01` / `0x09` payload field offsets.

**The charger only advertises BLE in pairing mode**, which is reached by a factory reset. There is
no way to reach a provisioned, working charger over BLE, so every provisioning experiment costs a
full reset-and-re-add cycle.

**No command reads the current cloud host back.** `0x02, 0x06, 0x07, 0x08, 0x0A, 0x0B, 0x0D, 0x0E,
0x0F, 0x11` were all probed with an empty payload and none replied. Restoring the factory
endpoints is therefore done by factory reset and re-adding in the app, which rewrites them.

## Cloud bootstrap sequence

On boot the charger works through this chain before it will do anything else. Each response shape
below was confirmed by replaying the charger's own captured credentials against AOHI's real cloud —
guessing at the shapes did not work, and a response padded with extra plausible fields was rejected
outright.

### 1. `POST <host1>/iot1/device/login`

```json
{"clientId":"<32 hex>","clientSecret":"<32 hex>",
 "deviceSn":"<32 char serial>","bizuserId":"<digits>"}
```

The charger carries its own baked-in credentials, unrelated to the account login the app uses.

```json
{"code":0,"msg":"ok","data":{"access_token":"<40 hex>","expires_in":604800,
                             "scope":"all","token_type":"Bearer"}}
```

### 2. `GET <host1>/iot1/device/mqtt/info`

Sent with `Authorization: Bearer <access_token>`.

```json
{"code":0,"msg":"ok","data":{"clientId":"dev_<serial>",
                             "username":"<40 hex>","password":"<40 hex>"}}
```

Note the `dev_<serial>` convention: the charger and the app occupy **different** client-id
namespaces (`dev_…` vs `app_<user_id>`), which is why they never evict each other upstream.

Once satisfied, this is requested once and the result is persisted in flash — it is not re-fetched
on subsequent boots.

### 3. `GET <host1>/iot1/time/second`

```json
{"code":0,"msg":"ok","data":1786808622}
```

A bare Unix timestamp, not an object.

### 4. `POST <host1>/iot1/weather/current`

Same body as `device/login`. This is what populates the `city` and `cityTemp` fields that appear in
the app-side `cmd:3` status payload, and what the charger shows on its screen.

```json
{"code":0,"msg":"ok","data":{"city":"France_Lyon",
  "condition":{"text":"Sunny","icon":"//cdn.weatherapi.com/weather/64x64/day/113.png","code":1000},
  "temp_c":37.5,"temp_f":99.5,"time_zone":"Europe/Paris","zone_offset":7200}}
```

Steps 3 and 4 continue on a loop for as long as the charger is running.

## Transport

**The REST leg does not require TLS.** The charger completed the entire chain above against a plain
`http://` server on a LAN IP, with no certificate involved. atc1441's note that a CA-signed
certificate is required applies to serving *HTTPS*; it does not force TLS on you.

Whether the **MQTT leg** requires `wss://` is still unknown — see below.

## Open problem: the charger never contacts host2

With host1 set to `http://<lan-ip>:8099` and host2 to `ws://<lan-ip>:8099/ws/`, the charger:

- completed every HTTP step above, repeatedly and happily;
- **never opened a WebSocket, and never made any non-HTTP connection at all** — no TLS
  `ClientHello`, nothing. Verified with a `clientError` handler that logs anything arriving on the
  port that isn't well-formed HTTP: 309 HTTP requests, zero others.

Since it is not *attempting* the connection, this is not a TLS-rejection problem.

The leading hypothesis is that **host2 was never stored**. The payload layout is assumed to be
3 × 39-byte NUL-padded fields at offsets 0 / 39 / 78; host1 (offset 0) demonstrably takes effect,
but nothing confirms host2 lands at offset 39. If it does not, the charger would hold an empty
broker URL and would never try to connect — which is exactly what we observe.

Ways to test, cheapest first:

1. Provision with host1 pointing at one port and host2 at a **different** port on the same machine.
   If the charger contacts only the first, host2 is definitively not being read from where we think.
2. Try alternative layouts for the second field (different offsets, or a
   length-prefixed rather than fixed-width encoding).
3. Try host2 spellings closer to stock: keep the `:443`, use the `/ws/iot1/` path, or `wss://`.

Each attempt costs a factory reset plus a BLE reprovision, so it is worth batching several
variants into one session rather than iterating one at a time.

## Restoring a charger

Factory reset, then re-add in the AOHI app. The app rewrites the cloud hosts as part of normal
provisioning, so the original URLs never need to be known. Home Assistant entities are keyed on the
serial number and come back by themselves.
