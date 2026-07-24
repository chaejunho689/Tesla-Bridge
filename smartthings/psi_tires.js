const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const j = async (r) => { try { return await r.json(); } catch (e) { return {}; } };

const psiVal = { type: 'number', minimum: 0, maximum: 60 };
const psiUnit = { type: 'string', enum: ['psi'], default: 'psi' };
const attrSchema = { type: 'object', properties: { value: psiVal, unit: psiUnit }, additionalProperties: false, required: ['value'] };

const defs = [
  {
    name: 'frontTirePsi',
    def: { name: 'frontTirePsi', attributes: { FL: { schema: attrSchema, setter: 'setFL', enumCommands: [] }, FR: { schema: attrSchema, setter: 'setFR', enumCommands: [] } }, commands: { setFL: { name: 'setFL', arguments: [{ name: 'value', optional: false, schema: { type: 'number', minimum: 0, maximum: 60 } }] }, setFR: { name: 'setFR', arguments: [{ name: 'value', optional: false, schema: { type: 'number', minimum: 0, maximum: 60 } }] } } },
    pres: { detailView: [{ label: '앞 좌', displayType: 'state', state: { label: '{{FL.value}} {{FL.unit}}' } }, { label: '앞 우', displayType: 'state', state: { label: '{{FR.value}} {{FR.unit}}' } }] },
  },
  {
    name: 'rearTirePsi',
    def: { name: 'rearTirePsi', attributes: { RL: { schema: attrSchema, setter: 'setRL', enumCommands: [] }, RR: { schema: attrSchema, setter: 'setRR', enumCommands: [] } }, commands: { setRL: { name: 'setRL', arguments: [{ name: 'value', optional: false, schema: { type: 'number', minimum: 0, maximum: 60 } }] }, setRR: { name: 'setRR', arguments: [{ name: 'value', optional: false, schema: { type: 'number', minimum: 0, maximum: 60 } }] } } },
    pres: { detailView: [{ label: '뒤 좌', displayType: 'state', state: { label: '{{RL.value}} {{RL.unit}}' } }, { label: '뒤 우', displayType: 'state', state: { label: '{{RR.value}} {{RR.unit}}' } }] },
  },
];

async function main() {
  for (const c of defs) {
    let cr = await j(await fetch(API + '/capabilities', { method: 'POST', headers: H, body: JSON.stringify(c.def) }));
    if (!cr.id) { console.log('❌', c.name, JSON.stringify(cr).slice(0, 200)); continue; }
    const pbody = Object.assign({}, c.pres, { id: cr.id, version: cr.version });
    const pr = await fetch(API + `/capabilities/${cr.id}/${cr.version}/presentation`, { method: 'POST', headers: H, body: JSON.stringify(pbody) });
    // verify
    const v = await j(await fetch(API + `/capabilities/${cr.id}/1`, { headers: H }));
    const fk = Object.keys(v.attributes)[0];
    const sch = v.attributes[fk].schema.properties;
    console.log(cr.id, '| pres', pr.status, '| unit:', JSON.stringify(sch.unit.enum), 'max:', sch.value.maximum);
  }
}
main();
