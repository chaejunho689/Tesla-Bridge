const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const NS = 'orangechapter24567';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const j = async (r) => { try { return await r.json(); } catch (e) { return {}; } };

const tempSchema = {
  type: 'object',
  properties: {
    value: { title: 'TemperatureValue', type: 'number', minimum: -460, maximum: 10000 },
    unit: { type: 'string', enum: ['C'], default: 'C' },
  },
  additionalProperties: false,
  required: ['value'],
};

const caps = [
  {
    def: { name: 'teslaTemp', attributes: { temp: { schema: tempSchema, enumCommands: [] } }, commands: {} },
    pres: {
      dashboard: { states: [{ label: '{{temp.value}} {{temp.unit}}', alternatives: [{ key: 'C', value: '°C', type: 'active' }] }], actions: [], panelItems: [] },
      detailView: [{ label: '{{i18n.label}}', displayType: 'numberField', numberField: { value: 'temp.value', valueType: 'number', unit: 'temp.unit', range: [-100.0, 100.0] } }],
      automation: { conditions: [{ label: '{{i18n.label}}', displayType: 'numberField', numberField: { value: 'temp.value', valueType: 'number', unit: 'temp.unit', range: [-100.0, 100.0] } }], actions: [] },
      id: NS + '.teslaTemp', version: 1,
    },
  },
  {
    def: { name: 'teslaTargetTemp', attributes: { temp: { schema: tempSchema, setter: 'setTargetTemp', enumCommands: [] } }, commands: { setTargetTemp: { name: 'setTargetTemp', arguments: [{ name: 'value', optional: false, schema: { type: 'number', minimum: 15, maximum: 28 } }] } } },
    pres: {
      dashboard: { states: [{ label: '{{temp.value}} {{temp.unit}}', alternatives: [{ key: 'C', value: '°C', type: 'active' }] }], actions: [], panelItems: [] },
      detailView: [{ label: '{{i18n.label}}', displayType: 'slider', slider: { range: [15.0, 28.0], step: 1, unit: 'temp.unit', command: 'setTargetTemp', argumentType: 'number', value: 'temp.value', valueType: 'number' } }],
      automation: { conditions: [], actions: [{ label: '{{i18n.label}}', displayType: 'numberField', numberField: { command: 'setTargetTemp', argumentType: 'number', unit: 'temp.unit', range: [15.0, 28.0] } }] },
      id: NS + '.teslaTargetTemp', version: 1,
    },
  },
];

async function main() {
  for (const c of caps) {
    let cr = await j(await fetch(API + '/capabilities', { method: 'POST', headers: H, body: JSON.stringify(c.def) }));
    if (!cr.id) { console.log('❌ 정의 실패:', JSON.stringify(cr).slice(0, 260)); continue; }
    console.log('✅ cap:', cr.id, 'v' + cr.version);
    let pr = await fetch(API + `/capabilities/${cr.id}/${cr.version}/presentation`, { method: 'POST', headers: H, body: JSON.stringify(c.pres) });
    console.log('   presentation:', pr.status, pr.status < 300 ? 'OK' : JSON.stringify(await j(pr)).slice(0, 220));
  }
}
main();
