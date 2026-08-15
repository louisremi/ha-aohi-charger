/**
 * Just enough MQTT 3.1/3.1.1 to broker between an AOHI charger and Home Assistant.
 *
 * The charger connects with protocol name "MQIsdp" (3.1) while the integration
 * uses "MQTT" (3.1.1); both are accepted since the wire format we care about is
 * identical for these packet types.
 */
'use strict';

const TYPES = {
  CONNECT: 1, CONNACK: 2, PUBLISH: 3, PUBACK: 4, PUBREC: 5, PUBREL: 6,
  PUBCOMP: 7, SUBSCRIBE: 8, SUBACK: 9, UNSUBSCRIBE: 10, UNSUBACK: 11,
  PINGREQ: 12, PINGRESP: 13, DISCONNECT: 14,
};
const NAMES = Object.fromEntries(Object.entries(TYPES).map(([k, v]) => [v, k]));

function encodeVarint(n) {
  const out = [];
  do {
    let d = n % 128;
    n = Math.floor(n / 128);
    if (n > 0) d |= 0x80;
    out.push(d);
  } while (n > 0);
  return Buffer.from(out);
}

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

function readString(buf, offset) {
  if (offset + 2 > buf.length) return null;
  const len = buf.readUInt16BE(offset);
  if (offset + 2 + len > buf.length) return null;
  return { value: buf.subarray(offset + 2, offset + 2 + len).toString('utf8'), next: offset + 2 + len };
}

const str = (s) => {
  const b = Buffer.from(s, 'utf8');
  return Buffer.concat([Buffer.from([b.length >> 8, b.length & 0xff]), b]);
};

const packet = (byte1, body) => Buffer.concat([Buffer.from([byte1]), encodeVarint(body.length), body]);

/** Split a stream into whole MQTT packets. Returns {packets, rest}. */
function chunk(buf) {
  const packets = [];
  let off = 0;
  while (off < buf.length) {
    if (off + 2 > buf.length) break;
    const rl = decodeVarint(buf, off + 1);
    if (!rl) break;
    const total = 1 + rl.bytes + rl.value;
    if (off + total > buf.length) break;
    packets.push(buf.subarray(off, off + total));
    off += total;
  }
  return { packets, rest: buf.subarray(off) };
}

function parse(buf) {
  const type = buf[0] >> 4;
  const flags = buf[0] & 0x0f;
  const rl = decodeVarint(buf, 1);
  if (!rl) return null;
  let p = 1 + rl.bytes;
  const end = p + rl.value;
  const out = { type, name: NAMES[type] || `UNKNOWN(${type})`, flags };

  if (type === TYPES.CONNECT) {
    const proto = readString(buf, p); if (!proto) return out;
    p = proto.next;
    out.protocol = proto.value;
    out.level = buf[p++];
    const cf = buf[p++];
    out.keepalive = buf.readUInt16BE(p); p += 2;
    const cid = readString(buf, p); if (!cid) return out;
    p = cid.next;
    out.clientId = cid.value;
    if (cf & 0x04) {
      const wt = readString(buf, p); if (!wt) return out;
      p = wt.next;
      const wm = readString(buf, p); if (!wm) return out;
      p = wm.next;
      out.will = { topic: wt.value, payload: wm.value, qos: (cf >> 3) & 3, retain: Boolean(cf & 0x20) };
    }
    if (cf & 0x80) { const u = readString(buf, p); if (u) { out.username = u.value; p = u.next; } }
    if (cf & 0x40) { const w = readString(buf, p); if (w) { out.password = w.value; p = w.next; } }
  } else if (type === TYPES.PUBLISH) {
    out.qos = (flags >> 1) & 3;
    out.retain = Boolean(flags & 1);
    const t = readString(buf, p); if (!t) return out;
    p = t.next;
    out.topic = t.value;
    if (out.qos > 0) { out.packetId = buf.readUInt16BE(p); p += 2; }
    out.payload = buf.subarray(p, end);
  } else if (type === TYPES.SUBSCRIBE || type === TYPES.UNSUBSCRIBE) {
    out.packetId = buf.readUInt16BE(p); p += 2;
    out.topics = [];
    while (p < end) {
      const t = readString(buf, p); if (!t) break;
      p = t.next;
      const qos = type === TYPES.SUBSCRIBE ? buf[p++] : 0;
      out.topics.push({ topic: t.value, qos });
    }
  } else if (type === TYPES.PUBACK) {
    out.packetId = buf.readUInt16BE(p);
  }
  return out;
}

const build = {
  connack: (returnCode = 0, sessionPresent = false) =>
    Buffer.from([0x20, 0x02, sessionPresent ? 1 : 0, returnCode]),
  suback: (packetId, granted) =>
    packet(0x90, Buffer.concat([Buffer.from([packetId >> 8, packetId & 0xff]), Buffer.from(granted)])),
  unsuback: (packetId) => Buffer.from([0xb0, 0x02, packetId >> 8, packetId & 0xff]),
  puback: (packetId) => Buffer.from([0x40, 0x02, packetId >> 8, packetId & 0xff]),
  pingresp: () => Buffer.from([0xd0, 0x00]),
  publish: (topic, payload, { qos = 0, packetId = 0, retain = false } = {}) => {
    const body = [str(topic)];
    if (qos > 0) body.push(Buffer.from([packetId >> 8, packetId & 0xff]));
    body.push(Buffer.isBuffer(payload) ? payload : Buffer.from(String(payload), 'utf8'));
    return packet(0x30 | (qos << 1) | (retain ? 1 : 0), Buffer.concat(body));
  },
};

/** MQTT topic filter matching, including + and # wildcards. */
function matches(filter, topic) {
  if (filter === topic) return true;
  const f = filter.split('/'), t = topic.split('/');
  for (let i = 0; i < f.length; i++) {
    if (f[i] === '#') return true;
    if (i >= t.length) return false;
    if (f[i] !== '+' && f[i] !== t[i]) return false;
  }
  return f.length === t.length;
}

module.exports = { TYPES, NAMES, chunk, parse, build, matches };
