/**
 * AOHI charger capture server.
 *
 * Stands in for AOHI's cloud so we can learn what the *device* says — we only ever
 * reverse-engineered what the *app* says. Logs every HTTP request and every
 * MQTT-over-WebSocket frame verbatim, and answers the bare minimum of MQTT
 * (CONNACK / SUBACK / PUBACK / PINGRESP) so the charger stays connected long
 * enough to reveal its real traffic.
 *
 * Deliberately permissive: any path, any credentials, any topic is accepted.
 * This is a diagnostic tool, not a server — do not expose it to the internet.
 */
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');
const { WebSocketServer } = require('ws');

const PORT = parseInt(process.env.PORT || '8080', 10);
const LOG_DIR = process.env.LOG_DIR || '/logs';

let logFile = null;
try {
  fs.mkdirSync(LOG_DIR, { recursive: true });
  logFile = path.join(LOG_DIR, 'capture.log');
  fs.appendFileSync(logFile, '');   // fail fast if the directory isn't writable
} catch (err) {
  logFile = null;
  console.warn(`[warn] file logging disabled (${err.message}); stdout only`);
}

function log(...parts) {
  const line = `${new Date().toISOString()} ${parts.join(' ')}`;
  console.log(line);
  // Written synchronously on purpose: a buffered stream loses everything if the
  // container is stopped with a signal, and a capture you can't repeat is worse
  // than a slow write. Traffic volume here is a single charger, so this is cheap.
  if (logFile) {
    try { fs.appendFileSync(logFile, line + '\n'); }
    catch { /* keep serving even if the disk goes away */ }
  }
}

for (const sig of ['SIGINT', 'SIGTERM']) {
  process.on(sig, () => { log(`Shutting down on ${sig}`); process.exit(0); });
}

/** Hex + ASCII dump, 16 bytes per row. */
function hexdump(buf, indent = '    ') {
  const rows = [];
  for (let i = 0; i < buf.length; i += 16) {
    const chunk = buf.subarray(i, i + 16);
    const hex = [...chunk].map(b => b.toString(16).padStart(2, '0')).join(' ').padEnd(47);
    const asc = [...chunk].map(b => (b >= 32 && b < 127 ? String.fromCharCode(b) : '.')).join('');
    rows.push(`${indent}${i.toString(16).padStart(4, '0')}  ${hex}  |${asc}|`);
  }
  return rows.join('\n');
}

/* ---------------------------------------------------------------- MQTT ---- */

const MQTT_TYPES = {
  1: 'CONNECT', 2: 'CONNACK', 3: 'PUBLISH', 4: 'PUBACK', 5: 'PUBREC', 6: 'PUBREL',
  7: 'PUBCOMP', 8: 'SUBSCRIBE', 9: 'SUBACK', 10: 'UNSUBSCRIBE', 11: 'UNSUBACK',
  12: 'PINGREQ', 13: 'PINGRESP', 14: 'DISCONNECT',
};

/** Decode MQTT's variable-length integer. Returns {value, bytes} or null. */
function decodeVarint(buf, offset) {
  let multiplier = 1, value = 0, bytes = 0, byte;
  do {
    if (offset + bytes >= buf.length || bytes >= 4) return null;
    byte = buf[offset + bytes++];
    value += (byte & 0x7f) * multiplier;
    multiplier *= 128;
  } while ((byte & 0x80) !== 0);
  return { value, bytes };
}

/** Read a 2-byte-length-prefixed string. */
function readString(buf, offset) {
  if (offset + 2 > buf.length) return null;
  const len = buf.readUInt16BE(offset);
  if (offset + 2 + len > buf.length) return null;
  return { value: buf.subarray(offset + 2, offset + 2 + len).toString('utf8'), next: offset + 2 + len };
}

/**
 * Pull the interesting fields out of a packet. CONNECT is the prize: it carries
 * the client id and the credentials the *device* uses, which we have never seen.
 */
function describeMqtt(buf) {
  if (buf.length < 2) return null;
  const type = buf[0] >> 4;
  const name = MQTT_TYPES[type] || `UNKNOWN(${type})`;
  const rl = decodeVarint(buf, 1);
  if (!rl) return { name, detail: '(bad remaining-length)' };

  let p = 1 + rl.bytes;
  const details = [];

  try {
    if (type === 1) { // CONNECT
      const proto = readString(buf, p); if (!proto) return { name };
      p = proto.next;
      const level = buf[p++], flags = buf[p++];
      const keepalive = buf.readUInt16BE(p); p += 2;
      details.push(`protocol=${proto.value} level=${level} keepalive=${keepalive}s`);

      const clientId = readString(buf, p); if (!clientId) return { name, detail: details.join(' ') };
      p = clientId.next;
      details.push(`clientId="${clientId.value}"`);

      if (flags & 0x04) { // will
        const wt = readString(buf, p); if (!wt) return { name, detail: details.join(' ') };
        p = wt.next;
        const wm = readString(buf, p); if (!wm) return { name, detail: details.join(' ') };
        p = wm.next;
        details.push(`willTopic="${wt.value}"`);
      }
      if (flags & 0x80) { // username
        const u = readString(buf, p); if (!u) return { name, detail: details.join(' ') };
        p = u.next;
        details.push(`username="${u.value}"`);
      }
      if (flags & 0x40) { // password
        const pw = readString(buf, p); if (!pw) return { name, detail: details.join(' ') };
        p = pw.next;
        details.push(`password="${pw.value}"`);
      }
    } else if (type === 3) { // PUBLISH
      const qos = (buf[0] >> 1) & 0x03;
      const topic = readString(buf, p); if (!topic) return { name };
      p = topic.next;
      if (qos > 0) p += 2;
      const payload = buf.subarray(p, 1 + rl.bytes + rl.value);
      details.push(`topic="${topic.value}" qos=${qos} ${payload.length}B`);
      const text = payload.toString('utf8');
      if (/^[\x09\x0a\x0d\x20-\x7e]*$/.test(text)) details.push(`\n    payload: ${text}`);
    } else if (type === 8) { // SUBSCRIBE
      p += 2; // packet id
      const topics = [];
      while (p < 1 + rl.bytes + rl.value) {
        const t = readString(buf, p); if (!t) break;
        p = t.next;
        topics.push(`${t.value} (qos ${buf[p++]})`);
      }
      details.push(`topics: ${topics.join(', ')}`);
    }
  } catch { /* best-effort decode only */ }

  return { name, detail: details.join(' '), type };
}

/** Minimal replies so the device keeps talking instead of giving up. */
function mqttReply(buf) {
  const type = buf[0] >> 4;
  if (type === 1) return Buffer.from([0x20, 0x02, 0x00, 0x00]);          // CONNACK, accepted
  if (type === 12) return Buffer.from([0xd0, 0x00]);                      // PINGRESP
  if (type === 8) {                                                       // SUBACK
    const rl = decodeVarint(buf, 1);
    if (!rl) return null;
    const packetId = buf.readUInt16BE(1 + rl.bytes);
    // one granted-QoS byte per requested topic
    let p = 1 + rl.bytes + 2, count = 0;
    while (p < 1 + rl.bytes + rl.value) {
      const t = readString(buf, p); if (!t) break;
      p = t.next + 1;
      count++;
    }
    const granted = Buffer.alloc(Math.max(count, 1), 0x00);
    return Buffer.concat([Buffer.from([0x90, 2 + granted.length]),
                          Buffer.from([packetId >> 8, packetId & 0xff]), granted]);
  }
  if (type === 3 && ((buf[0] >> 1) & 0x03) === 1) {                       // PUBACK for QoS 1
    const rl = decodeVarint(buf, 1);
    if (!rl) return null;
    const topic = readString(buf, 1 + rl.bytes);
    if (!topic) return null;
    const packetId = buf.readUInt16BE(topic.next);
    return Buffer.from([0x40, 0x02, packetId >> 8, packetId & 0xff]);
  }
  return null;
}

/* ---------------------------------------------------------------- HTTP ---- */

const server = http.createServer((req, res) => {
  const chunks = [];
  req.on('data', c => chunks.push(c));
  req.on('end', () => {
    const body = Buffer.concat(chunks);
    const headers = Object.entries(req.headers).map(([k, v]) => `    ${k}: ${v}`).join('\n');
    log(`\n=== HTTP ${req.method} ${req.url} from ${req.socket.remoteAddress}\n${headers}` +
        (body.length ? `\n  body (${body.length}B):\n${hexdump(body)}\n    as text: ${body.toString('utf8')}` : '\n  (no body)'));

    // Answer everything with a plausible-looking success envelope, mirroring the
    // {"code":0,"msg":"ok","data":...} shape the AOHI cloud uses.
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ code: 0, msg: 'ok', data: {} }));
  });
});

/* ----------------------------------------------------------- WebSocket ---- */

const wss = new WebSocketServer({ server });

wss.on('connection', (ws, req) => {
  const peer = req.socket.remoteAddress;
  const headers = Object.entries(req.headers).map(([k, v]) => `    ${k}: ${v}`).join('\n');
  log(`\n=== WEBSOCKET OPEN ${req.url} from ${peer}\n${headers}`);

  ws.on('message', (data, isBinary) => {
    const buf = Buffer.isBuffer(data) ? data : Buffer.from(data);
    const m = describeMqtt(buf);
    const header = m ? `MQTT ${m.name}${m.detail ? ' · ' + m.detail : ''}` : `frame (${isBinary ? 'binary' : 'text'})`;
    log(`\n--- WS RX ${buf.length}B from ${peer}: ${header}\n${hexdump(buf)}`);

    const reply = mqttReply(buf);
    if (reply) {
      ws.send(reply);
      const r = describeMqtt(reply);
      log(`--- WS TX ${reply.length}B: MQTT ${r ? r.name : '?'}`);
    }
  });

  ws.on('close', (code, reason) => log(`=== WEBSOCKET CLOSE ${peer} code=${code} reason="${reason}"`));
  ws.on('error', err => log(`=== WEBSOCKET ERROR ${peer}: ${err.message}`));
});

server.listen(PORT, () => {
  log(`AOHI capture server listening on 0.0.0.0:${PORT}`);
  log(`  HTTP  -> point host1 at  http://<this-machine-ip>:${PORT}`);
  log(`  WS    -> point host2 at  ws://<this-machine-ip>:${PORT}/ws/`);
  log(`  Logging to ${logFile || "stdout only"}`);
});
