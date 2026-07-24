const fs = require('fs');
const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const DEV = '6944ff23-1dd2-4b55-9ecd-cfbe293101da';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const SF = 'C:\\Users\\smile\\tesla-bridge\\smartthings\\_state.json';

const j = JSON.parse(fs.readFileSync(SF, 'utf8'));
const r = j.response || {}, cs = r.charge_state || {}, cl = r.climate_state || {}, vs = r.vehicle_state || {};
const evs = [];
const E = (component, capability, attribute, value, unit) => { const e = { component, capability, attribute, value }; if (unit) e.unit = unit; evs.push(e); };

if (cs.battery_level != null) E('main', 'battery', 'battery', Math.round(cs.battery_level), '%');
if (cl.inside_temp != null) E('main', 'temperatureMeasurement', 'temperature', Math.round(cl.inside_temp * 10) / 10, 'C');
if (cl.outside_temp != null) E('outside', 'temperatureMeasurement', 'temperature', Math.round(cl.outside_temp * 10) / 10, 'C');
E('main', 'lock', 'lock', vs.locked ? 'locked' : 'unlocked');
E('climate', 'switch', 'switch', cl.is_climate_on ? 'on' : 'off');
if (cl.driver_temp_setting != null) E('climate', 'thermostatCoolingSetpoint', 'coolingSetpoint', cl.driver_temp_setting, 'C');
E('charge', 'switch', 'switch', cs.charging_state === 'Charging' ? 'on' : 'off');
if (cs.charge_limit_soc != null) E('charge', 'switchLevel', 'level', cs.charge_limit_soc);
if (cs.charger_power != null) E('charge', 'powerMeter', 'power', cs.charger_power * 1000);
E('sentry', 'switch', 'switch', vs.sentry_mode ? 'on' : 'off');
E('trunk', 'switch', 'switch', (vs.rt && vs.rt !== 0) ? 'on' : 'off');
E('frunk', 'switch', 'switch', (vs.ft && vs.ft !== 0) ? 'on' : 'off');
[['seatFL', 'seat_heater_left'], ['seatFR', 'seat_heater_right'], ['seatRL', 'seat_heater_rear_left'], ['seatRC', 'seat_heater_rear_center'], ['seatRR', 'seat_heater_rear_right']]
  .forEach(([c, f]) => E(c, 'switch', 'switch', (cl[f] && cl[f] > 0) ? 'on' : 'off'));

(async () => {
  for (let i = 0; i < evs.length; i += 8) {
    const chunk = evs.slice(i, i + 8);
    const res = await fetch(API + '/virtualdevices/' + DEV + '/events', { method: 'POST', headers: H, body: JSON.stringify({ deviceEvents: chunk }) });
    console.log('batch', i / 8 + 1, 'status:', res.status, '| count:', chunk.length);
    if (res.status >= 400) console.log('  ', JSON.stringify(await res.json()).slice(0, 200));
  }
})();
