/**
 * Check a local server end to end: is it reachable, which chargers have
 * registered, and does a real command round-trip work?
 *
 * Usage: node check.js [host] [httpPort] [mqttPort]
 *        node check.js 192.168.1.166 8099 8098
 */
'use strict';

const WebSocket = require('ws');

const HOST = process.argv[2] || '127.0.0.1';
const HTTP_PORT = parseInt(process.argv[3] || '8099', 10);
const MQTT_PORT = parseInt(process.argv[4] || '8098', 10);

const str = (s) => {
  const b = Buffer.from(s, 'utf8');
  return Buffer.concat([Buffer.from([b.length >> 8, b.length & 0xff]), b]);
};
const varint = (n) => {
  const o = [];
  do { let d = n % 128; n = Math.floor(n / 128); if (n > 0) d |= 0x80; o.push(d); } while (n > 0);
  return Buffer.from(o);
};
const pkt = (b, x) => Buffer.concat([Buffer.from([b]), varint(x.length), x]);
const readStr = (b, o) => {
  const l = b.readUInt16BE(o);
  return { v: b.subarray(o + 2, o + 2 + l).toString(), n: o + 2 + l };
};

async function main() {
  console.log(`checking local server at ${HOST} (http ${HTTP_PORT}, mqtt ${MQTT_PORT})\n`);

  let devices;
  try {
    const res = await fetch(`http://${HOST}:${HTTP_PORT}/local/devices`);
    const json = await res.json();
    devices = json.data || [];
  } catch (err) {
    console.log(`FAIL: cannot reach the server — ${err.message}`);
    console.log('      is it running, and is the port open in your firewall?');
    process.exit(1);
  }

  if (!devices.length) {
    console.log('server is reachable, but no chargers have registered.');
    console.log('provision one to it with the BLE tool:');
    console.log(`   host1  http://${HOST}:${HTTP_PORT}`);
    console.log(`   host2  wss://${HOST}:${MQTT_PORT}/ws/`);
    process.exit(1);
  }

  console.log(`${devices.length} charger(s) known to the server:`);
  for (const d of devices) {
    const age = d.lastSeen ? `${Math.round((Date.now() - d.lastSeen) / 1000)}s ago` : 'never';
    console.log(`   ${d.sn}  online=${d.online}  last seen ${age}`);
  }

  const target = devices.find((d) => d.online);
  if (!target) {
    console.log('\nno charger is currently connected, so the round-trip cannot be tested.');
    process.exit(1);
  }

  console.log(`\nasking ${target.sn.slice(-6)} for status over the local broker...`);
  const sn = target.sn;
  const ws = new WebSocket(`wss://${HOST}:${MQTT_PORT}/ws/`, ['mqtt'], { rejectUnauthorized: false });
  let pid = 1;

  const done = new Promise((resolve) => {
    ws.on('open', () => ws.send(pkt(0x10, Buffer.concat([
      str('MQTT'), Buffer.from([4, 0xC2, 0, 60]), str(`check-${Date.now() % 100000}`), str('u'), str('p'),
    ]))));
    ws.on('message', (data) => {
      const b = Buffer.from(data), type = b[0] >> 4;
      if (type === 2) {
        ws.send(pkt(0x82, Buffer.concat([
          Buffer.from([0, pid++]), str(`dev/I4SEASON/${sn}/command/reply`), Buffer.from([1])])));
      } else if (type === 9) {
        ws.send(pkt(0x30, Buffer.concat([
          str(`dev/I4SEASON/${sn}/command/request`),
          Buffer.from(JSON.stringify({ cmd: 3, user: 'check' }))])));
      } else if (type === 3) {
        let o = 1; while (b[o] & 0x80) o++; o++;
        const topic = readStr(b, o);
        let msg; try { msg = JSON.parse(b.subarray(topic.n).toString()); } catch { return; }
        if (msg.cmd === 3) resolve(msg.result);
      }
    });
    ws.on('error', (err) => { console.log(`FAIL: ${err.message}`); process.exit(1); });
  });

  const timeout = new Promise((_, rej) => setTimeout(() => rej(new Error('no reply in 15s')), 15000));
  let result;
  try {
    result = await Promise.race([done, timeout]);
  } catch (err) {
    console.log(`FAIL: ${err.message} — the charger is registered but not answering.`);
    process.exit(1);
  }

  console.log('\nPASS: full round-trip over the local network, no cloud involved');
  console.log(`   power on   : ${result.poweron}`);
  console.log(`   total draw : ${result.allPower} W`);
  console.log(`   temperature: ${result.batTemp} C`);
  for (const key of ['cPorts', 'aPorts']) {
    for (const p of result[key] || []) {
      console.log(`   ${p.name}: ${(p.power / 100).toFixed(2)} W  on=${p.poweron}`);
    }
  }
  ws.close();
  process.exit(0);
}

main();
