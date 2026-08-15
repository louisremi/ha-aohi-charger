/**
 * AOHI local server — run your chargers with no cloud at all.
 *
 * Two listeners:
 *   host1  plain HTTP  — the four bootstrap endpoints the charger needs before
 *                        it will connect to a broker
 *   host2  WSS         — an MQTT broker over TLS WebSocket, which is what the
 *                        charger insists on (it silently ignores plain ws://)
 *
 * A self-signed certificate is fine: the firmware does not verify it. One is
 * generated automatically on first run if none is supplied.
 *
 * Point a charger here with the BLE provisioning tool:
 *   host1  http://<lan-ip>:<HTTP_PORT>
 *   host2  wss://<lan-ip>:<MQTT_PORT>/ws/
 */
'use strict';

const http = require('http');
const https = require('https');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { WebSocketServer } = require('ws');
const mqtt = require('./mqtt');

const HTTP_PORT = parseInt(process.env.HTTP_PORT || '8099', 10);
const MQTT_PORT = parseInt(process.env.MQTT_PORT || '8098', 10);
const LAN_IP = process.env.LAN_IP || '';
const CERT_DIR = process.env.CERT_DIR || path.join(__dirname, 'certs');
const WEATHER_CITY = process.env.WEATHER_CITY || 'Local';
const WEATHER_TZ = process.env.WEATHER_TZ || 'UTC';
const WEATHER_OFFSET = parseInt(process.env.WEATHER_OFFSET || '0', 10);
const VERBOSE = process.env.VERBOSE === '1';

const log = (...a) => console.log(new Date().toISOString(), ...a);
const vlog = (...a) => { if (VERBOSE) log(...a); };

/* ------------------------------------------------------------ certificate -- */

function ensureCert() {
  const cert = path.join(CERT_DIR, 'cert.pem');
  const key = path.join(CERT_DIR, 'key.pem');
  if (fs.existsSync(cert) && fs.existsSync(key)) return { cert, key };

  fs.mkdirSync(CERT_DIR, { recursive: true });
  if (!LAN_IP) {
    throw new Error('LAN_IP must be set so the certificate can carry a matching IP SAN');
  }
  log(`generating a self-signed certificate for ${LAN_IP} (the charger does not verify it)`);
  execFileSync('openssl', [
    'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
    '-keyout', key, '-out', cert, '-days', '3650',
    '-subj', `/CN=${LAN_IP}`, '-addext', `subjectAltName=IP:${LAN_IP}`,
  ], { stdio: 'ignore' });
  return { cert, key };
}

/* ------------------------------------------------------------- the broker -- */

/** Connected MQTT clients, by client id. */
const clients = new Map();
/** Chargers we have seen, by serial — this is how the integration discovers them. */
const devices = new Map();

function route(topic, payload, fromId) {
  let delivered = 0;
  for (const [id, c] of clients) {
    if (id === fromId) continue;
    for (const sub of c.subs) {
      if (mqtt.matches(sub.topic, topic)) {
        c.send(mqtt.build.publish(topic, payload, { qos: 0 }));
        delivered++;
        break;
      }
    }
  }
  vlog(`route ${topic} (${payload.length}B) -> ${delivered} subscriber(s)`);
  return delivered;
}

function handlePacket(client, pkt) {
  const p = mqtt.parse(pkt);
  if (!p) return;

  switch (p.type) {
    case mqtt.TYPES.CONNECT: {
      client.id = p.clientId || `anon_${crypto.randomBytes(4).toString('hex')}`;
      client.will = p.will;
      // Last one in wins, mirroring normal broker behaviour.
      const existing = clients.get(client.id);
      if (existing && existing !== client) {
        log(`client id "${client.id}" reused; dropping the older connection`);
        try { existing.close(); } catch { /* already gone */ }
      }
      clients.set(client.id, client);

      const sn = client.id.startsWith('dev_') ? client.id.slice(4) : null;
      if (sn) {
        devices.set(sn, { sn, clientId: client.id, lastSeen: Date.now(), online: true });
        log(`charger ${sn} connected (${p.protocol} level ${p.level}, keepalive ${p.keepalive}s)`);
      } else {
        log(`client "${client.id}" connected (${p.protocol} level ${p.level})`);
      }
      client.send(mqtt.build.connack(0));
      break;
    }

    case mqtt.TYPES.SUBSCRIBE: {
      for (const t of p.topics) client.subs.push(t);
      vlog(`${client.id} subscribed: ${p.topics.map(t => t.topic).join(', ')}`);
      client.send(mqtt.build.suback(p.packetId, p.topics.map(t => Math.min(t.qos, 1))));
      break;
    }

    case mqtt.TYPES.UNSUBSCRIBE: {
      client.subs = client.subs.filter(s => !p.topics.some(t => t.topic === s.topic));
      client.send(mqtt.build.unsuback(p.packetId));
      break;
    }

    case mqtt.TYPES.PUBLISH: {
      if (p.qos === 1) client.send(mqtt.build.puback(p.packetId));
      const sn = (p.topic.match(/I4SEASON\/([^/]+)/) || [])[1];
      if (sn && devices.has(sn)) devices.get(sn).lastSeen = Date.now();
      route(p.topic, p.payload, client.id);
      break;
    }

    case mqtt.TYPES.PINGREQ:
      client.send(mqtt.build.pingresp());
      break;

    case mqtt.TYPES.DISCONNECT:
      client.graceful = true;
      break;

    default:
      vlog(`ignoring ${p.name} from ${client.id}`);
  }
}

function attachBroker(wss) {
  wss.on('connection', (ws, req) => {
    const client = {
      id: null,
      subs: [],
      will: null,
      graceful: false,
      send: (buf) => { if (ws.readyState === 1) ws.send(buf); },
      close: () => ws.close(),
    };
    let buffer = Buffer.alloc(0);
    vlog(`websocket open ${req.url} from ${req.socket.remoteAddress}`);

    ws.on('message', (data) => {
      buffer = Buffer.concat([buffer, Buffer.isBuffer(data) ? data : Buffer.from(data)]);
      const { packets, rest } = mqtt.chunk(buffer);
      buffer = rest;
      for (const pkt of packets) {
        try { handlePacket(client, pkt); }
        catch (err) { log(`error handling packet from ${client.id}: ${err.message}`); }
      }
    });

    ws.on('close', () => {
      if (client.id && clients.get(client.id) === client) clients.delete(client.id);
      const sn = client.id && client.id.startsWith('dev_') ? client.id.slice(4) : null;
      if (sn && devices.has(sn)) devices.get(sn).online = false;
      // An unexpected drop publishes the will, which is how the charger's
      // offline state reaches anyone watching.
      if (!client.graceful && client.will) {
        vlog(`publishing will for ${client.id}`);
        route(client.will.topic, Buffer.from(client.will.payload), client.id);
      }
      if (client.id) log(`${client.id} disconnected${client.graceful ? '' : ' (unexpected)'}`);
    });

    ws.on('error', (err) => vlog(`websocket error: ${err.message}`));
  });
}

/* --------------------------------------------------- bootstrap endpoints -- */

const tokens = new Map();
let lastSn = '';

function bootstrapHandler(req, res) {
  const chunks = [];
  req.on('data', (c) => chunks.push(c));
  req.on('end', () => {
    const body = Buffer.concat(chunks);
    let sent = {};
    try { sent = JSON.parse(body.toString('utf8')); } catch { /* not all calls have bodies */ }
    let payload = { code: 0, msg: 'ok', data: {} };
    const url = req.url || '';

    if (url.includes('/device/login')) {
      const sn = sent.deviceSn || '';
      const token = crypto.createHash('sha1')
        .update(`${sn}:${sent.clientSecret || ''}`).digest('hex');
      tokens.set(token, sn);
      if (sn) {
        lastSn = sn;
        if (!devices.has(sn)) devices.set(sn, { sn, clientId: `dev_${sn}`, online: false, lastSeen: 0 });
      }
      payload = { code: 0, msg: 'ok', data: {
        access_token: token, expires_in: 604800, scope: 'all', token_type: 'Bearer' } };
      vlog(`device/login from ${sn}`);

    } else if (url.includes('/device/mqtt/info')) {
      const auth = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
      const sn = tokens.get(auth) || lastSn || '';
      // We are the broker and accept anything, so these only need to be stable.
      payload = { code: 0, msg: 'ok', data: {
        clientId: `dev_${sn}`,
        username: crypto.createHash('sha1').update(`u:${sn}`).digest('hex'),
        password: crypto.createHash('sha1').update(`p:${sn}`).digest('hex') } };
      log(`issued broker credentials to ${sn}`);

    } else if (url.includes('/time/second')) {
      payload = { code: 0, msg: 'ok', data: Math.floor(Date.now() / 1000) };

    } else if (url.includes('/weather/current')) {
      payload = { code: 0, msg: 'ok', data: {
        city: WEATHER_CITY,
        condition: { text: 'Sunny', icon: '//cdn.weatherapi.com/weather/64x64/day/113.png', code: 1000 },
        temp_c: 20, temp_f: 68, time_zone: WEATHER_TZ, zone_offset: WEATHER_OFFSET } };

    } else if (url.startsWith('/local/devices')) {
      // Not part of AOHI's API: how the Home Assistant integration discovers
      // which chargers are attached to this server, replacing the cloud's
      // /iot1/device/list.
      payload = { code: 0, msg: 'ok', data: [...devices.values()] };

    } else {
      vlog(`unhandled ${req.method} ${url}`);
    }

    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify(payload));
  });
}

/* ------------------------------------------------------------------ boot -- */

const { cert, key } = ensureCert();
const tls = { cert: fs.readFileSync(cert), key: fs.readFileSync(key) };

const httpServer = http.createServer(bootstrapHandler);
httpServer.listen(HTTP_PORT, '0.0.0.0', () =>
  log(`bootstrap (host1) on http://0.0.0.0:${HTTP_PORT}`));

// The broker port also answers the bootstrap endpoints over HTTPS, so a charger
// pointed entirely at this port still works.
const mqttServer = https.createServer(tls, bootstrapHandler);
attachBroker(new WebSocketServer({
  server: mqttServer,
  handleProtocols: (protocols) => (protocols && protocols.has('mqtt') ? 'mqtt' : undefined),
}));
mqttServer.on('tlsClientError', (err, sock) =>
  vlog(`TLS handshake failed from ${sock.remoteAddress}: ${err.code || err.message}`));
mqttServer.listen(MQTT_PORT, '0.0.0.0', () => {
  log(`broker  (host2) on wss://0.0.0.0:${MQTT_PORT}/ws/`);
  if (LAN_IP) {
    log('');
    log('provision a charger with these values:');
    log(`   host1   http://${LAN_IP}:${HTTP_PORT}`);
    log(`   host2   wss://${LAN_IP}:${MQTT_PORT}/ws/`);
    log('');
  }
});

for (const sig of ['SIGINT', 'SIGTERM']) {
  process.on(sig, () => { log(`shutting down on ${sig}`); process.exit(0); });
}
