const fs = require('fs');
const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const NS = 'optionoption28278';
const DEV = 'efaa4190-2147-43a6-8b58-871f21435287';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const PSI = 14.5037738;
const j = async (r) => { try { return await r.json(); } catch (e) { return {}; } };

async function updateTireCap(shortId) {
  const id = NS + '.' + shortId;
  const def = await j(await fetch(API + `/capabilities/${id}/1`, { headers: H }));
  for (const k in def.attributes) {
    const val = def.attributes[k].schema.properties.value;
    val.minimum = 0; val.maximum = 60;
    def.attributes[k].schema.properties.unit = { type: 'string', enum: ['psi'], default: 'psi' };
  }
  for (const cmd in (def.commands || {})) {
    for (const arg of (def.commands[cmd].arguments || [])) {
      if (arg.schema) { arg.schema.minimum = 0; arg.schema.maximum = 60; }
    }
  }
  const body = { name: def.name, attributes: def.attributes, commands: def.commands || {} };
  const r = await fetch(API + `/capabilities/${id}/1`, { method: 'PUT', headers: H, body: JSON.stringify(body) });
  console.log('update', id, '→', r.status, r.status < 300 ? 'OK' : JSON.stringify(await j(r)).slice(0, 180));
}

async function main() {
  await updateTireCap('fronttire');
  await updateTireCap('reartire');

  // 재푸시: 타이어 psi + 위치
  const r = JSON.parse(fs.readFileSync('C:\\Users\\smile\\tesla-bridge\\smartthings\\_state.json', 'utf8')).response || {};
  const vs = r.vehicle_state || {}, ds = r.drive_state || {};
  const cap = (n) => NS + '.' + n;
  const toPsi = (b) => Math.round(b * PSI);
  let addr = null;
  if (ds.latitude != null) {
    try { const g = await (await fetch(`https://nominatim.openstreetmap.org/reverse?format=json&lat=${ds.latitude}&lon=${ds.longitude}&accept-language=ko&zoom=18`, { headers: { 'User-Agent': 'tesla-bridge/1.0' } })).json(); addr = g.display_name; } catch (e) {}
  }
  const evs = [];
  const E = (c, cap2, a, v, u) => { const e = { component: c, capability: cap2, attribute: a, value: v }; if (u) e.unit = u; evs.push(e); };
  if (vs.tpms_pressure_fl != null) E('main', cap('fronttire'), 'FL', toPsi(vs.tpms_pressure_fl), 'psi');
  if (vs.tpms_pressure_fr != null) E('main', cap('fronttire'), 'FR', toPsi(vs.tpms_pressure_fr), 'psi');
  if (vs.tpms_pressure_rl != null) E('main', cap('reartire'), 'RL', toPsi(vs.tpms_pressure_rl), 'psi');
  if (vs.tpms_pressure_rr != null) E('main', cap('reartire'), 'RR', toPsi(vs.tpms_pressure_rr), 'psi');
  if (ds.latitude != null) {
    E('main', cap('teslrlocation'), 'latitude', ds.latitude);
    E('main', cap('teslrlocation'), 'longitude', ds.longitude);
    E('main', cap('teslrlocation'), 'address', addr || (ds.latitude.toFixed(5) + ',' + ds.longitude.toFixed(5)));
    E('main', cap('teslrlocation'), 'lastUpdateTime', new Date().toLocaleString('ko-KR'));
  }
  for (let i = 0; i < evs.length; i += 8) {
    const res = await fetch(API + '/virtualdevices/' + DEV + '/events', { method: 'POST', headers: H, body: JSON.stringify({ deviceEvents: evs.slice(i, i + 8) }) });
    console.log('push batch:', res.status, res.status >= 400 ? JSON.stringify(await res.json()).slice(0, 180) : '');
  }
  console.log('타이어 psi:', [vs.tpms_pressure_fl, vs.tpms_pressure_fr, vs.tpms_pressure_rl, vs.tpms_pressure_rr].map(toPsi).join('/'), '| 주소:', addr);
}
main();
