/**
 * Raw TCP listener that logs any connection and the first bytes received.
 *
 * Used to answer a single question: does the charger ever *attempt* a
 * connection to a given host? It does not speak any protocol -- a TLS
 * ClientHello, an HTTP request or random bytes all get logged the same way,
 * which is the point. Silence here means the charger never tried.
 *
 * Usage: node tcp-probe.js <port> [label]
 */
'use strict';

const net = require('net');

const PORT = parseInt(process.argv[2] || '8098', 10);
const LABEL = process.argv[3] || `port ${PORT}`;

const ts = () => new Date().toISOString().slice(11, 23);

function hexdump(buf) {
  const rows = [];
  for (let i = 0; i < Math.min(buf.length, 96); i += 16) {
    const chunk = buf.subarray(i, i + 16);
    const hex = [...chunk].map(b => b.toString(16).padStart(2, '0')).join(' ').padEnd(47);
    const asc = [...chunk].map(b => (b >= 32 && b < 127 ? String.fromCharCode(b) : '.')).join('');
    rows.push(`      ${i.toString(16).padStart(4, '0')}  ${hex}  |${asc}|`);
  }
  return rows.join('\n');
}

/** Name the protocol from its opening bytes, so the log is self-explanatory. */
function sniff(buf) {
  if (buf[0] === 0x16 && buf[1] === 0x03) return 'TLS ClientHello (wss:// attempt)';
  if (/^(GET|POST|PUT|HEAD|OPTIONS) /.test(buf.subarray(0, 8).toString('latin1'))) {
    const line = buf.toString('latin1').split('\r\n')[0];
    return `HTTP request: ${line}`;
  }
  if (buf[0] === 0x10) return 'MQTT CONNECT (raw MQTT, not over WebSocket)';
  return 'unrecognised';
}

net.createServer((sock) => {
  const peer = sock.remoteAddress;
  console.log(`${ts()} *** ${LABEL}: TCP CONNECTION from ${peer} ***`);
  let got = false;
  sock.on('data', (buf) => {
    if (got) return;
    got = true;
    console.log(`${ts()}     ${sniff(buf)}  (${buf.length}B)`);
    console.log(hexdump(buf));
  });
  sock.on('close', () => {
    if (!got) console.log(`${ts()}     ${LABEL}: closed without sending data`);
  });
  sock.on('error', () => { /* ignore resets */ });
}).listen(PORT, '0.0.0.0', () => {
  console.log(`${ts()} ${LABEL}: raw TCP probe listening on 0.0.0.0:${PORT}`);
  console.log(`${ts()} (silence here means the charger never attempted a connection)`);
});
