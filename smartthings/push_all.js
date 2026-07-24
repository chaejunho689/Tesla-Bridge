const fs = require('fs');
const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const NS = 'optionoption28278';
const DEV = '2ec0ce84-d043-49c5-b83c-8e108f2ab9cd';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const MI = 1.609344, PSI = 14.5037738;
const j = async (r) => { try { return await r.json(); } catch (e) { return {}; } };
const r = JSON.parse(fs.readFileSync('C:\\Users\\smile\\tesla-bridge\\smartthings\\_state.json', 'utf8')).response || {};
const cs = r.charge_state || {}, cl = r.climate_state || {}, vs = r.vehicle_state || {}, ds = r.drive_state || {};
const cap = (n) => NS + '.' + n;

async function main() {
  // 이름 변경
  await fetch(API + '/devices/' + DEV, { method: 'PUT', headers: H, body: JSON.stringify({ label: '테슬라 모델3' }) });
  console.log('이름 → 테슬라 모델3');

  let addr = null;
  if (ds.latitude != null) { try { const g = await (await fetch(`https://nominatim.openstreetmap.org/reverse?format=json&lat=${ds.latitude}&lon=${ds.longitude}&accept-language=ko&zoom=18`, { headers: { 'User-Agent': 'tesla-bridge/1.0' } })).json(); addr = g.display_name; } catch (e) {} }

  const evs = [];
  const E = (c, cp, a, v, u) => { const e = { component: c, capability: cp, attribute: a, value: v }; if (u) e.unit = u; evs.push(e); };
  if (cs.battery_level != null) E('main', cap('batteryteslr'), 'battery', Math.round(cs.battery_level), '%');
  E('main', cap('teslrchargestatus'), 'chargestatus', cs.charging_state === 'Charging' ? '충전 중' : (cs.charging_state === 'Complete' ? '충전 완료' : '충전 안함'));
  if (cl.inside_temp != null) E('main', cap('tempteslr'), 'temp', Math.round(cl.inside_temp * 10) / 10, 'C');
  if (cl.driver_temp_setting != null) E('main', cap('targettemp'), 'temp', Math.round(cl.driver_temp_setting), 'C');
  E('main', cap('fanstat'), 'status', cl.is_climate_on ? '작동 중' : '대기');
  E('main', 'switch', 'switch', cl.is_climate_on ? 'on' : 'off');
  E('main', 'doorControl', 'door', vs.locked ? 'closed' : 'open');
  E('main', cap('trunk'), 'door', (vs.rt && vs.rt !== 0) ? 'open' : 'closed');
  E('main', cap('frunk'), 'door', (vs.ft && vs.ft !== 0) ? 'open' : 'closed');
  if (vs.odometer != null) E('main', cap('odometer'), 'odometerReading', Math.round(vs.odometer * MI), 'km');
  if (cs.battery_range != null) E('main', cap('odometer'), 'odometerRemain', Math.round(cs.battery_range * MI), 'km');
  if (cs.charge_energy_added != null) E('main', cap('odoenergy'), 'odometerEnergy', Math.round(cs.charge_energy_added * 10) / 10, 'kWh');
  if (vs.tpms_pressure_fl != null) E('main', cap('fronttirepsi'), 'FL', Math.round(vs.tpms_pressure_fl * PSI), 'psi');
  if (vs.tpms_pressure_fr != null) E('main', cap('fronttirepsi'), 'FR', Math.round(vs.tpms_pressure_fr * PSI), 'psi');
  if (vs.tpms_pressure_rl != null) E('main', cap('reartirepsi'), 'RL', Math.round(vs.tpms_pressure_rl * PSI), 'psi');
  if (vs.tpms_pressure_rr != null) E('main', cap('reartirepsi'), 'RR', Math.round(vs.tpms_pressure_rr * PSI), 'psi');
  if (ds.latitude != null) { E('main', cap('teslrlocation'), 'latitude', ds.latitude); E('main', cap('teslrlocation'), 'longitude', ds.longitude); E('main', cap('teslrlocation'), 'address', addr || (ds.latitude.toFixed(5) + ',' + ds.longitude.toFixed(5))); E('main', cap('teslrlocation'), 'lastUpdateTime', new Date().toLocaleString('ko-KR')); }
  E('sentry', 'switch', 'switch', vs.sentry_mode ? 'on' : 'off');

  for (let i = 0; i < evs.length; i += 8) {
    const res = await fetch(API + '/virtualdevices/' + DEV + '/events', { method: 'POST', headers: H, body: JSON.stringify({ deviceEvents: evs.slice(i, i + 8) }) });
    console.log('batch', (i / 8 + 1), ':', res.status, res.status >= 400 ? JSON.stringify(await j(res)).slice(0, 160) : '');
  }
  console.log('타이어 psi:', [vs.tpms_pressure_fl, vs.tpms_pressure_fr, vs.tpms_pressure_rl, vs.tpms_pressure_rr].map(b => Math.round(b * PSI)).join('/'), '| 주소:', addr);
}
main();
