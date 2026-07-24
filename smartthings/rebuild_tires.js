const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const NS = 'optionoption28278';
const SRC_NS = 'pilotgreen48610';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const OLD_DEV = 'efaa4190-2147-43a6-8b58-871f21435287';
const OLD_PROFILE = '2cac80fe-96a9-4f36-9c6f-b0c2e6d6e79d';
const j = async (r) => { try { return await r.json(); } catch (e) { return {}; } };

async function recreateTire(shortId) {
  // Teslr 원본 def/pres 가져와서 psi로 변형 후 재생성
  const def = await j(await fetch(API + `/capabilities/${SRC_NS}.${shortId}/1`, { headers: H }));
  for (const k in def.attributes) {
    def.attributes[k].schema.properties.value.minimum = 0;
    def.attributes[k].schema.properties.value.maximum = 60;
    def.attributes[k].schema.properties.unit = { type: 'string', enum: ['psi'], default: 'psi' };
  }
  for (const cmd in (def.commands || {})) for (const arg of (def.commands[cmd].arguments || [])) if (arg.schema) { arg.schema.minimum = 0; arg.schema.maximum = 60; }
  const body = { name: def.name, attributes: def.attributes, commands: def.commands || {} };
  let cr = await j(await fetch(API + '/capabilities', { method: 'POST', headers: H, body: JSON.stringify(body) }));
  if (!cr.id) { console.log('  재생성 실패', shortId, JSON.stringify(cr).slice(0, 150)); return null; }
  // 프리젠테이션 복제 (원본에서)
  const pres = await j(await fetch(API + `/capabilities/${SRC_NS}.${shortId}/1/presentation`, { headers: H }));
  const pbody = { dashboard: pres.dashboard, detailView: pres.detailView, automation: pres.automation, id: cr.id, version: cr.version };
  // automation numberField 범위도 psi로
  const fixRange = (arr) => (arr || []).forEach(x => { if (x.numberField && x.numberField.range) x.numberField.range = [0, 60]; });
  fixRange(pbody.automation && pbody.automation.conditions); fixRange(pbody.automation && pbody.automation.actions);
  Object.keys(pbody).forEach(k => pbody[k] === undefined && delete pbody[k]);
  const pr = await fetch(API + `/capabilities/${cr.id}/${cr.version}/presentation`, { method: 'POST', headers: H, body: JSON.stringify(pbody) });
  console.log('  ', cr.id, '재생성 | pres', pr.status);
  return cr.id;
}

async function main() {
  // 1) 기기/프로필 삭제 (캐퍼 삭제 위해)
  await fetch(API + '/devices/' + OLD_DEV, { method: 'DELETE', headers: H });
  await fetch(API + '/deviceprofiles/' + OLD_PROFILE, { method: 'DELETE', headers: H });
  console.log('기기/프로필 삭제');
  // 2) 기존 타이어 캐퍼 삭제
  for (const t of ['fronttire', 'reartire']) {
    const r = await fetch(API + `/capabilities/${NS}.${t}/1`, { method: 'DELETE', headers: H });
    console.log('삭제', t, r.status);
  }
  // 3) psi로 재생성
  await recreateTire('fronttire');
  await recreateTire('reartire');
  console.log('완료 — 이제 build_full.js 재실행');
}
main();
