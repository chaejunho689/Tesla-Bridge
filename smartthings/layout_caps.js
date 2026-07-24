const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const j = async (r) => { try { return await r.json(); } catch (e) { return {}; } };

const numAttr = (min, max, units, defUnit) => ({ schema: { type: 'object', properties: { value: { type: 'number', minimum: min, maximum: max }, unit: { type: 'string', enum: units, default: defUnit } }, additionalProperties: false, required: ['value'] }, enumCommands: [] });
const strAttr = { schema: { type: 'object', properties: { value: { type: 'string' } }, additionalProperties: false, required: ['value'] }, enumCommands: [] };

const caps = [
  { // 외기온도 (표시)
    def: { name: 'outsideTemp', attributes: { temp: numAttr(-100, 100, ['C'], 'C') }, commands: {} },
    pres: { detailView: [{ label: '{{i18n.label}}', displayType: 'state', state: { label: '{{temp.value}} {{temp.unit}}' } }], dashboard: { states: [], actions: [], panelItems: [] } },
    ko: { tag: 'ko', label: '외기 온도', attributes: { temp: { label: '외기 온도' } } },
  },
  { // 충전속도 kW (표시)
    def: { name: 'chargeSpeed', attributes: { power: numAttr(0, 500, ['kW'], 'kW') }, commands: {} },
    pres: { detailView: [{ label: '{{i18n.label}}', displayType: 'state', state: { label: '{{power.value}} {{power.unit}}' } }], dashboard: { states: [], actions: [], panelItems: [] } },
    ko: { tag: 'ko', label: '충전 속도', attributes: { power: { label: '충전 속도' } } },
  },
  { // 완충까지 (표시, 문자열)
    def: { name: 'chargeTimeLeft', attributes: { timeleft: strAttr }, commands: {} },
    pres: { detailView: [{ label: '{{i18n.label}}', displayType: 'state', state: { label: '{{timeleft.value}}' } }], dashboard: { states: [], actions: [], panelItems: [] } },
    ko: { tag: 'ko', label: '완충까지', attributes: { timeleft: { label: '완충까지' } } },
  },
  { // 충전한도 % (제어 슬라이더 50~100)
    def: { name: 'chargeLimitPct', attributes: { limit: Object.assign(numAttr(50, 100, ['%'], '%'), { setter: 'setChargeLimit' }) }, commands: { setChargeLimit: { name: 'setChargeLimit', arguments: [{ name: 'value', optional: false, schema: { type: 'number', minimum: 50, maximum: 100 } }] } } },
    pres: { detailView: [{ label: '{{i18n.label}}', displayType: 'slider', slider: { range: [50, 100], step: 5, unit: 'limit.unit', command: 'setChargeLimit', argumentType: 'number', value: 'limit.value', valueType: 'number' } }], dashboard: { states: [], actions: [], panelItems: [] }, automation: { conditions: [], actions: [{ label: '{{i18n.label}}', displayType: 'numberField', numberField: { command: 'setChargeLimit', argumentType: 'number', unit: 'limit.unit', range: [50, 100] } }] } },
    ko: { tag: 'ko', label: '충전 한도', attributes: { limit: { label: '충전 한도' } }, commands: { setChargeLimit: { label: '충전 한도 설정', arguments: {} } } },
  },
];

async function main() {
  for (const c of caps) {
    let cr = await j(await fetch(API + '/capabilities', { method: 'POST', headers: H, body: JSON.stringify(c.def) }));
    if (!cr.id) { console.log('❌', c.def.name, JSON.stringify(cr).slice(0, 200)); continue; }
    const pbody = Object.assign({}, c.pres, { id: cr.id, version: cr.version });
    const pr = await fetch(API + `/capabilities/${cr.id}/${cr.version}/presentation`, { method: 'POST', headers: H, body: JSON.stringify(pbody) });
    const ir = await fetch(API + `/capabilities/${cr.id}/${cr.version}/i18n`, { method: 'POST', headers: H, body: JSON.stringify(c.ko) });
    console.log(cr.id, '| pres', pr.status, '| i18n', ir.status);
  }
}
main();
