const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const SRC_NS = 'pilotgreen48610';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const j = async (r) => { try { return await r.json(); } catch (e) { return {}; } };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// Teslr-YJ 커스텀 캐퍼빌리티 목록
const SRC_CAPS = ['batteryteslr', 'teslrchargestatus', 'tempteslr', 'targettemp', 'fanstat',
  'trunk', 'frunk', 'odometer', 'odoenergy', 'fronttire', 'reartire', 'teslrlocation'];

async function main() {
  // 0) 임시로 만든 캐퍼 삭제
  for (const c of ['optionoption28278.teslatemp', 'optionoption28278.teslatargettemp']) {
    await fetch(API + '/capabilities/' + c + '/1', { method: 'DELETE', headers: H }).catch(() => {});
  }

  const map = {};  // teslr short name -> my new id
  for (const short of SRC_CAPS) {
    const srcId = SRC_NS + '.' + short;
    const def = await j(await fetch(API + `/capabilities/${srcId}/1`, { headers: H }));
    if (!def.name) { console.log('❌ def 못읽음:', srcId, JSON.stringify(def).slice(0, 120)); continue; }
    // 정의 생성 (name/attributes/commands 만)
    const body = { name: def.name, attributes: def.attributes || {}, commands: def.commands || {} };
    let cr = await j(await fetch(API + '/capabilities', { method: 'POST', headers: H, body: JSON.stringify(body) }));
    let id, ver;
    if (cr.id) { id = cr.id; ver = cr.version; }
    else {
      // 이미 있으면 목록에서 찾기 (내 ns)
      const list = await j(await fetch(API + '/capabilities', { headers: H }));
      const f = (list.items || []).find(x => x.id.toLowerCase().endsWith('.' + def.name.toLowerCase()) && !x.id.startsWith(SRC_NS));
      if (f) { id = f.id; ver = f.version; } else { console.log('❌ 생성실패', short, JSON.stringify(cr).slice(0, 140)); continue; }
    }
    // 프리젠테이션 복제
    const pres = await j(await fetch(API + `/capabilities/${srcId}/1/presentation`, { headers: H }));
    if (pres && (pres.dashboard || pres.detailView || pres.automation)) {
      const pbody = { dashboard: pres.dashboard, detailView: pres.detailView, automation: pres.automation, id, version: ver };
      // undefined 제거
      Object.keys(pbody).forEach(k => pbody[k] === undefined && delete pbody[k]);
      const pr = await fetch(API + `/capabilities/${id}/${ver}/presentation`, { method: 'POST', headers: H, body: JSON.stringify(pbody) });
      map[short] = { id, ver, pres: pr.status };
      console.log(short, '→', id, '| pres', pr.status);
    } else {
      map[short] = { id, ver, pres: 'none' };
      console.log(short, '→', id, '| pres 없음');
    }
    await sleep(200);
  }
  console.log('\n=== 매핑 ===');
  console.log(JSON.stringify(Object.fromEntries(Object.entries(map).map(([k, v]) => [k, v.id])), null, 0));
  require('fs').writeFileSync('C:/Users/smile/tesla-bridge/smartthings/_capmap.json', JSON.stringify(map, null, 2));
}
main();
