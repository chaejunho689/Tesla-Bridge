const fs = require('fs');
const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const NS = 'optionoption28278';
const DEV = 'efaa4190-2147-43a6-8b58-871f21435287';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const MI = 1.609344;
const r = JSON.parse(fs.readFileSync('C:\\Users\\smile\\tesla-bridge\\smartthings\\_state.json', 'utf8')).response || {};
const cs = r.charge_state || {}, cl = r.climate_state || {}, vs = r.vehicle_state || {}, ds = r.drive_state || {};

const evs = [];
const E = (component, capability, attribute, value, unit) => { const e = { component, capability, attribute, value }; if (unit) e.unit = unit; evs.push(e); };
const cap = (n) => NS + '.' + n;

if (cs.battery_level != null) E('main', cap('batteryteslr'), 'battery', Math.round(cs.battery_level), '%');
const chg = cs.charging_state === 'Charging' ? '충전 중' : (cs.charging_state === 'Complete' ? '충전 완료' : '충전 안함');
E('main', cap('teslrchargestatus'), 'chargestatus', chg);
if (cl.inside_temp != null) E('main', cap('tempteslr'), 'temp', Math.round(cl.inside_temp * 10) / 10, 'C');
if (cl.driver_temp_setting != null) E('main', cap('targettemp'), 'temp', Math.round(cl.driver_temp_setting), 'C');
E('main', cap('fanstat'), 'status', cl.is_climate_on ? '작동 중' : '대기');
E('main', 'switch', 'switch', cl.is_climate_on ? 'on' : 'off');           // 공조
E('main', 'doorControl', 'door', vs.locked ? 'closed' : 'open');          // 잠금(closed=잠김)
E('main', cap('trunk'), 'door', (vs.rt && vs.rt !== 0) ? 'open' : 'closed');
E('main', cap('frunk'), 'door', (vs.ft && vs.ft !== 0) ? 'open' : 'closed');
if (vs.odometer != null) E('main', cap('odometer'), 'odometerReading', Math.round(vs.odometer * MI), 'km');
if (cs.battery_range != null) E('main', cap('odometer'), 'odometerRemain', Math.round(cs.battery_range * MI), 'km');
if (cs.charge_energy_added != null) E('main', cap('odoenergy'), 'odometerEnergy', Math.round(cs.charge_energy_added * 10) / 10, 'kWh');
if (vs.tpms_pressure_fl != null) E('main', cap('fronttire'), 'FL', Math.round(vs.tpms_pressure_fl * 100) / 100, 'bar');
if (vs.tpms_pressure_fr != null) E('main', cap('fronttire'), 'FR', Math.round(vs.tpms_pressure_fr * 100) / 100, 'bar');
if (vs.tpms_pressure_rl != null) E('main', cap('reartire'), 'RL', Math.round(vs.tpms_pressure_rl * 100) / 100, 'bar');
if (vs.tpms_pressure_rr != null) E('main', cap('reartire'), 'RR', Math.round(vs.tpms_pressure_rr * 100) / 100, 'bar');
if (ds.latitude != null) { E('main', cap('teslrlocation'), 'latitude', ds.latitude); E('main', cap('teslrlocation'), 'longitude', ds.longitude); E('main', cap('teslrlocation'), 'address', ds.latitude.toFixed(5) + ', ' + ds.longitude.toFixed(5)); }
E('sentry', 'switch', 'switch', vs.sentry_mode ? 'on' : 'off');

(async () => {
  for (let i = 0; i < evs.length; i += 8) {
    const chunk = evs.slice(i, i + 8);
    const res = await fetch(API + '/virtualdevices/' + DEV + '/events', { method: 'POST', headers: H, body: JSON.stringify({ deviceEvents: chunk }) });
    console.log('batch', i / 8 + 1, 'status:', res.status, '| count:', chunk.length);
    if (res.status >= 400) console.log('  ', JSON.stringify(await res.json()).slice(0, 250));
  }
})();
