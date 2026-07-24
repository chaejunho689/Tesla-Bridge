const fs = require('fs');
const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const NS = 'optionoption28278';
const DEV = 'efaa4190-2147-43a6-8b58-871f21435287';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const PSI = 14.5037738;
const r = JSON.parse(fs.readFileSync('C:\\Users\\smile\\tesla-bridge\\smartthings\\_state.json', 'utf8')).response || {};
const vs = r.vehicle_state || {}, ds = r.drive_state || {};
const cap = (n) => NS + '.' + n;

async function geocode(lat, lon) {
  try {
    const u = `https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lon}&accept-language=ko&zoom=18`;
    const res = await fetch(u, { headers: { 'User-Agent': 'tesla-bridge/1.0' } });
    const j = await res.json();
    return j.display_name || null;
  } catch (e) { return null; }
}

async function pushEvents(evs) {
  for (let i = 0; i < evs.length; i += 8) {
    const chunk = evs.slice(i, i + 8);
    const res = await fetch(API + '/virtualdevices/' + DEV + '/events', { method: 'POST', headers: H, body: JSON.stringify({ deviceEvents: chunk }) });
    console.log('  batch status:', res.status);
    if (res.status >= 400) console.log('   ', JSON.stringify(await res.json()).slice(0, 200));
  }
}

(async () => {
  const evs = [];
  const E = (component, capability, attribute, value, unit) => { const e = { component, capability, attribute, value }; if (unit) e.unit = unit; evs.push(e); };
  // 타이어 psi
  const toPsi = (bar) => Math.round(bar * PSI);
  if (vs.tpms_pressure_fl != null) E('main', cap('fronttire'), 'FL', toPsi(vs.tpms_pressure_fl), 'psi');
  if (vs.tpms_pressure_fr != null) E('main', cap('fronttire'), 'FR', toPsi(vs.tpms_pressure_fr), 'psi');
  if (vs.tpms_pressure_rl != null) E('main', cap('reartire'), 'RL', toPsi(vs.tpms_pressure_rl), 'psi');
  if (vs.tpms_pressure_rr != null) E('main', cap('reartire'), 'RR', toPsi(vs.tpms_pressure_rr), 'psi');
  // 위치 (역지오코딩)
  if (ds.latitude != null) {
    const addr = await geocode(ds.latitude, ds.longitude);
    console.log('주소:', addr);
    E('main', cap('teslrlocation'), 'latitude', ds.latitude);
    E('main', cap('teslrlocation'), 'longitude', ds.longitude);
    E('main', cap('teslrlocation'), 'address', addr || (ds.latitude.toFixed(5) + ', ' + ds.longitude.toFixed(5)));
    E('main', cap('teslrlocation'), 'lastUpdateTime', new Date().toLocaleString('ko-KR'));
  }
  console.log('타이어 psi:', [vs.tpms_pressure_fl, vs.tpms_pressure_fr, vs.tpms_pressure_rl, vs.tpms_pressure_rr].map(toPsi).join('/'));
  await pushEvents(evs);
})();
