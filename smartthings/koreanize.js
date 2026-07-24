const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const NS = 'optionoption28278', SRC = 'pilotgreen48610';
const DEV = '2ec0ce84-d043-49c5-b83c-8e108f2ab9cd';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const j = async (r) => { try { return await r.json(); } catch (e) { return {}; } };

// 내 캐퍼 -> Teslr 원본 캐퍼 (i18n 소스)
const MAP = {
  batteryteslr: 'batteryteslr', teslrchargestatus: 'teslrchargestatus', tempteslr: 'tempteslr',
  targettemp: 'targettemp', fanstat: 'fanstat', trunk: 'trunk', frunk: 'frunk',
  odometer: 'odometer', odoenergy: 'odoenergy', fronttirepsi: 'fronttire', reartirepsi: 'reartire',
  teslrlocation: 'teslrlocation',
};

async function main() {
  // 0) 배터리 버그 패치: 표준 battery 값 넣기 (상세화면 100% → 실제값)
  const st = await j(await fetch(API + '/api/state').catch(() => ({})));
  // _state.json에서 배터리 읽기
  const fs = require('fs');
  const state = JSON.parse(fs.readFileSync('C:\\Users\\smile\\tesla-bridge\\smartthings\\_state.json', 'utf8')).response || {};
  const bl = (state.charge_state || {}).battery_level;
  if (bl != null) {
    const r = await fetch(API + '/virtualdevices/' + DEV + '/events', { method: 'POST', headers: H, body: JSON.stringify({ deviceEvents: [{ component: 'main', capability: 'battery', attribute: 'battery', value: Math.round(bl), unit: '%' }] }) });
    console.log('표준 battery 패치:', r.status, '값', bl);
  }

  // 1) i18n 한글 번역 복제
  for (const [mine, src] of Object.entries(MAP)) {
    const ko = await j(await fetch(API + `/capabilities/${SRC}.${src}/1/i18n/ko`, { headers: H }));
    if (!ko.tag) { console.log('  ko 없음:', src); continue; }
    // POST to my cap
    const r = await fetch(API + `/capabilities/${NS}.${mine}/1/i18n`, { method: 'POST', headers: H, body: JSON.stringify(ko) });
    console.log(mine, '한글화:', r.status, r.status < 300 ? 'OK' : JSON.stringify(await j(r)).slice(0, 150));
  }
}
main();
