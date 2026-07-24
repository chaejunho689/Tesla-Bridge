const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const j = async (r) => { try { return await r.json(); } catch (e) { return {}; } };

const seatAttr = { schema: { type: 'object', properties: { value: { type: 'string', enum: ['on', 'off'] } }, additionalProperties: false, required: ['value'] }, enumCommands: [] };
const noArgCmd = (n) => ({ name: n, arguments: [] });
const toggle = (attr, onCmd, offCmd, label) => ({ label, displayType: 'toggleSwitch', toggleSwitch: { command: { on: onCmd, off: offCmd }, state: { value: attr + '.value', on: 'on', off: 'off', valueType: 'string' } } });

const caps = [
  {
    def: { name: 'frontSeats', attributes: { fl: seatAttr, fr: seatAttr }, commands: { flOn: noArgCmd('flOn'), flOff: noArgCmd('flOff'), frOn: noArgCmd('frOn'), frOff: noArgCmd('frOff') } },
    pres: { detailView: [toggle('fl', 'flOn', 'flOff', '운전석 열선'), toggle('fr', 'frOn', 'frOff', '조수석 열선')], dashboard: { states: [], actions: [], panelItems: [] } },
    ko: { tag: 'ko', label: '앞좌석 열선', attributes: { fl: { label: '운전석 열선' }, fr: { label: '조수석 열선' } }, commands: {} },
  },
  {
    def: { name: 'rearSeats', attributes: { rl: seatAttr, rc: seatAttr, rr: seatAttr }, commands: { rlOn: noArgCmd('rlOn'), rlOff: noArgCmd('rlOff'), rcOn: noArgCmd('rcOn'), rcOff: noArgCmd('rcOff'), rrOn: noArgCmd('rrOn'), rrOff: noArgCmd('rrOff') } },
    pres: { detailView: [toggle('rl', 'rlOn', 'rlOff', '뒤좌 열선'), toggle('rc', 'rcOn', 'rcOff', '뒤중 열선'), toggle('rr', 'rrOn', 'rrOff', '뒤우 열선')], dashboard: { states: [], actions: [], panelItems: [] } },
    ko: { tag: 'ko', label: '뒷좌석 열선', attributes: { rl: { label: '뒤좌 열선' }, rc: { label: '뒤중 열선' }, rr: { label: '뒤우 열선' } }, commands: {} },
  },
];

async function main() {
  for (const c of caps) {
    let cr = await j(await fetch(API + '/capabilities', { method: 'POST', headers: H, body: JSON.stringify(c.def) }));
    if (!cr.id) { console.log('❌', c.def.name, JSON.stringify(cr).slice(0, 220)); continue; }
    const pbody = Object.assign({}, c.pres, { id: cr.id, version: cr.version });
    const pr = await fetch(API + `/capabilities/${cr.id}/${cr.version}/presentation`, { method: 'POST', headers: H, body: JSON.stringify(pbody) });
    const ir = await fetch(API + `/capabilities/${cr.id}/${cr.version}/i18n`, { method: 'POST', headers: H, body: JSON.stringify(c.ko) });
    console.log(cr.id, '| pres', pr.status, '| i18n', ir.status);
  }
}
main();
