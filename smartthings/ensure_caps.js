const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const j = async (r) => { try { return await r.json(); } catch (e) { return {}; } };

const tempSchema = (min, max) => ({
  type: 'object',
  properties: { value: { title: 'TemperatureValue', type: 'number', minimum: min, maximum: max }, unit: { type: 'string', enum: ['C'], default: 'C' } },
  additionalProperties: false, required: ['value'],
});

async function ensureCap(def, presFn) {
  let cr = await j(await fetch(API + '/capabilities', { method: 'POST', headers: H, body: JSON.stringify(def) }));
  let id, ver;
  if (cr.id) { id = cr.id; ver = cr.version; console.log('생성:', id); }
  else {
    const list = await j(await fetch(API + '/capabilities', { headers: H }));
    const f = (list.items || []).find(x => x.id.toLowerCase().endsWith('.' + def.name.toLowerCase()));
    if (f) { id = f.id; ver = f.version; console.log('기존:', id); }
    else { console.log('❌ 실패:', JSON.stringify(cr).slice(0, 220)); return null; }
  }
  const pr = await fetch(API + `/capabilities/${id}/${ver}/presentation`, { method: 'POST', headers: H, body: JSON.stringify(presFn(id, ver)) });
  console.log('  presentation:', pr.status, pr.status < 300 ? 'OK' : JSON.stringify(await j(pr)).slice(0, 200));
  return { id, ver };
}

async function main() {
  const t = await ensureCap(
    { name: 'teslaTemp', attributes: { temp: { schema: tempSchema(-460, 10000), enumCommands: [] } }, commands: {} },
    (id, ver) => ({
      dashboard: { states: [{ label: '{{temp.value}} {{temp.unit}}', alternatives: [{ key: 'C', value: '°C', type: 'active' }] }], actions: [], panelItems: [] },
      detailView: [{ label: '{{i18n.label}}', displayType: 'numberField', numberField: { value: 'temp.value', valueType: 'number', unit: 'temp.unit', range: [-100, 100] } }],
      automation: { conditions: [{ label: '{{i18n.label}}', displayType: 'numberField', numberField: { value: 'temp.value', valueType: 'number', unit: 'temp.unit', range: [-100, 100] } }], actions: [] },
      id, version: ver,
    }));
  const tt = await ensureCap(
    { name: 'teslaTargetTemp', attributes: { temp: { schema: tempSchema(15, 28), setter: 'setTargetTemp', enumCommands: [] } }, commands: { setTargetTemp: { name: 'setTargetTemp', arguments: [{ name: 'value', optional: false, schema: { type: 'number', minimum: 15, maximum: 28 } }] } } },
    (id, ver) => ({
      dashboard: { states: [{ label: '{{temp.value}} {{temp.unit}}', alternatives: [{ key: 'C', value: '°C', type: 'active' }] }], actions: [], panelItems: [] },
      detailView: [{ label: '{{i18n.label}}', displayType: 'slider', slider: { range: [15, 28], step: 1, unit: 'temp.unit', command: 'setTargetTemp', argumentType: 'number', value: 'temp.value', valueType: 'number' } }],
      automation: { conditions: [], actions: [{ label: '{{i18n.label}}', displayType: 'numberField', numberField: { command: 'setTargetTemp', argumentType: 'number', unit: 'temp.unit', range: [15, 28] } }] },
      id, version: ver,
    }));
  console.log('\nCAP_TEMP=' + (t && t.id));
  console.log('CAP_TARGET=' + (tt && tt.id));
}
main();
