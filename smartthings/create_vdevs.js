const TOKEN = process.env.SMARTTHINGS_TOKEN;
const LOC = '5304bc94-d91f-4b89-a09a-d0c093eab7c1';
const ROOM = '6c9494fc-3ecb-40da-8500-1b8401362290';
const API = 'https://api.smartthings.com';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const TEST_ID = '98f47f6c-1325-4370-a22e-076aa8613856';

const devs = [
  { key: 'lock', label: '홍차 잠금' },
  { key: 'climate', label: '홍차 공조' },
  { key: 'charge', label: '홍차 충전' },
  { key: 'sentry', label: '홍차 감시모드' },
];

async function main() {
  // 테스트 기기 삭제
  try { await fetch(API + '/devices/' + TEST_ID, { method: 'DELETE', headers: H }); console.log('테스트기기 삭제'); } catch (e) {}
  const out = {};
  for (const d of devs) {
    const body = { name: d.label, prototype: 'VIRTUAL_SWITCH', executionTarget: 'CLOUD', roomId: ROOM, owner: { ownerType: 'LOCATION', ownerId: LOC } };
    const r = await fetch(API + '/virtualdevices/prototypes', { method: 'POST', headers: H, body: JSON.stringify(body) });
    const j = await r.json();
    if (j.deviceId) { out[d.key] = j.deviceId; console.log('✅', d.label, '→', j.deviceId); }
    else console.log('❌ FAIL', d.label, JSON.stringify(j).slice(0, 200));
  }
  console.log('\n--- .env 용 ---');
  console.log('ST_LOCK=' + (out.lock || ''));
  console.log('ST_CLIMATE=' + (out.climate || ''));
  console.log('ST_CHARGE=' + (out.charge || ''));
  console.log('ST_SENTRY=' + (out.sentry || ''));
}
main();
