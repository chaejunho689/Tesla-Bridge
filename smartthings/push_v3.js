const fs = require('fs');
const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const NS = 'optionoption28278';
const DEV = '665f3d52-9ad8-43ac-b94b-172a81a208c3';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const MI = 1.609344, PSI = 14.5037738;
const j = async (r) => { try { return await r.json(); } catch (e) { return {}; } };
const r = JSON.parse(fs.readFileSync('C:\\Users\\smile\\tesla-bridge\\smartthings\\_state.json', 'utf8')).response || {};
const cs = r.charge_state || {}, cl = r.climate_state || {}, vs = r.vehicle_state || {}, ds = r.drive_state || {};
const cap = (n) => NS + '.' + n;

function timeStr() {
  const m = cs.minutes_to_full_charge != null ? cs.minutes_to_full_charge : (cs.time_to_full_charge ? cs.time_to_full_charge * 60 : 0);
  if (cs.charging_state !== 'Charging' || !m) return cs.charging_state === 'Complete' ? '완충' : '-';
  return Math.floor(m / 60) + '시간 ' + Math.round(m % 60) + '분';
}

async function main() {
  let addr = null;
  if (ds.latitude != null) { try { const g = await (await fetch(`https://nominatim.openstreetmap.org/reverse?format=json&lat=${ds.latitude}&lon=${ds.longitude}&accept-language=ko&zoom=18`, { headers: { 'User-Agent': 'tesla-bridge/1.0' } })).json(); addr = g.display_name; } catch (e) {} }

  const evs = [];
  const E = (c, cp, a, v, u) => { const e = { component: c, capability: cp, attribute: a, value: v }; if (u) e.unit = u; evs.push(e); };
  if (cs.battery_level != null) E('main', cap('batteryteslr'), 'battery', Math.round(cs.battery_level), '%');
  E('main', cap('teslrchargestatus'), 'chargestatus', cs.charging_state === 'Charging' ? '충전 중' : (cs.charging_state === 'Complete' ? '충전 완료' : '충전 안함'));
  if (cl.inside_temp != null) E('main', cap('tempteslr'), 'temp', Math.round(cl.inside_temp * 10) / 10, 'C');
  if (cl.outside_temp != null) E('main', cap('outsidetemp'), 'temp', Math.round(cl.outside_temp * 10) / 10, 'C');
  if (cl.driver_temp_setting != null) E('main', cap('targettempc'), 'temp', Math.round(cl.driver_temp_setting), 'C');
  if (vs.odometer != null) E('main', cap('odometer'), 'odometerReading', Math.round(vs.odometer * MI), 'km');
  if (cs.battery_range != null) E('main', cap('odometer'), 'odometerRemain', Math.round(cs.battery_range * MI), 'km');
  E('main', cap('chargespeed'), 'power', cs.charger_power || 0, 'kW');
  if (cs.charge_limit_soc != null) E('main', cap('chargelimitpct'), 'limit', cs.charge_limit_soc, '%');
  if (cs.charge_energy_added != null) E('main', cap('odoenergy'), 'odometerEnergy', Math.round(cs.charge_energy_added * 10) / 10, 'kWh');
  E('main', cap('chargetimeleft'), 'timeleft', timeStr());
  if (vs.tpms_pressure_fl != null) E('main', cap('fronttirepsi'), 'FL', Math.round(vs.tpms_pressure_fl * PSI), 'psi');
  if (vs.tpms_pressure_fr != null) E('main', cap('fronttirepsi'), 'FR', Math.round(vs.tpms_pressure_fr * PSI), 'psi');
  if (vs.tpms_pressure_rl != null) E('main', cap('reartirepsi'), 'RL', Math.round(vs.tpms_pressure_rl * PSI), 'psi');
  if (vs.tpms_pressure_rr != null) E('main', cap('reartirepsi'), 'RR', Math.round(vs.tpms_pressure_rr * PSI), 'psi');
  const seat = (v) => (v && v > 0) ? 'on' : 'off';
  E('main', cap('seatheatfront'), 'fl', seat(cl.seat_heater_left)); E('main', cap('seatheatfront'), 'fr', seat(cl.seat_heater_right));
  E('main', cap('seatheatrear'), 'rl', seat(cl.seat_heater_rear_left)); E('main', cap('seatheatrear'), 'rc', seat(cl.seat_heater_rear_center)); E('main', cap('seatheatrear'), 'rr', seat(cl.seat_heater_rear_right));
  E('main', 'doorControl', 'door', vs.locked ? 'closed' : 'open');
  E('main', 'switch', 'switch', cl.is_climate_on ? 'on' : 'off');
  E('main', cap('trunk'), 'door', (vs.rt && vs.rt !== 0) ? 'open' : 'closed');
  E('main', cap('frunk'), 'door', (vs.ft && vs.ft !== 0) ? 'open' : 'closed');
  if (ds.latitude != null) { E('main', cap('teslrlocation'), 'latitude', ds.latitude); E('main', cap('teslrlocation'), 'longitude', ds.longitude); E('main', cap('teslrlocation'), 'address', addr || '위치'); E('main', cap('teslrlocation'), 'lastUpdateTime', new Date().toLocaleString('ko-KR')); }
  E('sentry', 'switch', 'switch', vs.sentry_mode ? 'on' : 'off');

  for (let i = 0; i < evs.length; i += 8) {
    const res = await fetch(API + '/virtualdevices/' + DEV + '/events', { method: 'POST', headers: H, body: JSON.stringify({ deviceEvents: evs.slice(i, i + 8) }) });
    console.log('batch', (i / 8 + 1), ':', res.status, res.status >= 400 ? JSON.stringify(await j(res)).slice(0, 200) : '');
  }
  console.log('완료. 이벤트', evs.length, '| 주소:', addr);
}
main();
