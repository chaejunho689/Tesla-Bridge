const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const j = async (r) => { try { return await r.json(); } catch (e) { return {}; } };

const seatAttr = (onCmd, offCmd) => ({
  schema: { type: 'object', properties: { value: { type: 'string', enum: ['on', 'off'] } }, additionalProperties: false, required: ['value'] },
  enumCommands: [{ command: onCmd, value: 'on' }, { command: offCmd, value: 'off' }],
});
const noArg = (n) => ({ name: n, arguments: [] });
const toggle = (attr, onCmd, offCmd, label) => ({ label, displayType: 'toggleSwitch', toggleSwitch: { command: { on: onCmd, off: offCmd }, state: { value: attr + '.value', on: 'on', off: 'off', valueType: 'string' } } });

const caps = [
  {
    def: { name: 'seatHeatFront', attributes: { fl: seatAttr('flOn', 'flOff'), fr: seatAttr('frOn', 'frOff') }, commands: { flOn: noArg('flOn'), flOff: noArg('flOff'), frOn: noArg('frOn'), frOff: noArg('frOff') } },
    pres: { detailView: [toggle('fl', 'flOn', 'flOff', '운전석 열선'), toggle('fr', 'frOn', 'frOff', '조수석 열선')], dashboard: { states: [], actions: [], panelItems: [] } },
  },
  {
    def: { name: 'seatHeatRear', attributes: { rl: seatAttr('rlOn', 'rlOff'), rc: seatAttr('rcOn', 'rcOff'), rr: seatAttr('rrOn', 'rrOff') }, commands: { rlOn: noArg('rlOn'), rlOff: noArg('rlOff'), rcOn: noArg('rcOn'), rcOff: noArg('rcOff'), rrOn: noArg('rrOn'), rrOff: noArg('rrOff') } },
    pres: { detailView: [toggle('rl', 'rlOn', 'rlOff', '뒤좌 열선'), toggle('rc', 'rcOn', 'rcOff', '뒤중 열선'), toggle('rr', 'rrOn', 'rrOff', '뒤우 열선')], dashboard: { states: [], actions: [], panelItems: [] } },
  },
  {
    def: {
      name: 'targetTempC',
      attributes: { temp: { schema: { type: 'object', properties: { value: { type: 'number', minimum: 15, maximum: 28 }, unit: { type: 'string', enum: ['C'], default: 'C' } }, additionalProperties: false, required: ['value'] }, setter: 'setTargetTemp', enumCommands: [] } },
      commands: { setTargetTemp: { name: 'setTargetTemp', arguments: [{ name: 'value', optional: false, schema: { type: 'number', minimum: 15, maximum: 28 } }] } },
    },
    pres: {
      dashboard: { states: [{ label: '{{temp.value}} {{temp.unit}}', alternatives: [{ key: 'C', value: '°C', type: 'active' }] }], actions: [], panelItems: [] },
      detailView: [{ label: '목표 온도', displayType: 'slider', slider: { range: [15, 28], step: 1, unit: 'temp.unit', command: 'setTargetTemp', argumentType: 'number', value: 'temp.value', valueType: 'number' } }],
      automation: { conditions: [], actions: [{ label: '목표 온도', displayType: 'numberField', numberField: { command: 'setTargetTemp', argumentType: 'number', unit: 'temp.unit', range: [15, 28] } }] },
    },
  },
];

(async () => {
  for (const c of caps) {
    let cr = await j(await fetch(API + '/capabilities', { method: 'POST', headers: H, body: JSON.stringify(c.def) }));
    if (!cr.id) { console.log('❌', c.def.name, JSON.stringify(cr).slice(0, 220)); continue; }
    const pr = await fetch(API + `/capabilities/${cr.id}/${cr.version}/presentation`, { method: 'POST', headers: H, body: JSON.stringify(Object.assign({}, c.pres, { id: cr.id, version: cr.version })) });
    console.log(cr.id, '| pres', pr.status);
  }
})();
