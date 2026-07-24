const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const LOC = '5304bc94-d91f-4b89-a09a-d0c093eab7c1';
const ROOM = '6c9494fc-3ecb-40da-8500-1b8401362290';
const OLD_DEV = 'bbc2731f-cb00-4b71-b459-ea64eb28037c';
const OLD_PROFILE = '2cc27d2b-c888-4b4d-a026-7b05911870df';

const j = async (r) => { try { return await r.json(); } catch (e) { return {}; } };

// 컴포넌트 정의
const components = [
  { id: 'main',    caps: ['lock', 'battery', 'temperatureMeasurement'], cat: 'Others' },
  { id: 'outside', caps: ['temperatureMeasurement'], cat: 'Others' },
  { id: 'climate', caps: ['switch', 'thermostatCoolingSetpoint'], cat: 'AirConditioner' },
  { id: 'charge',  caps: ['switch', 'switchLevel', 'powerMeter'], cat: 'Others' },
  { id: 'sentry',  caps: ['switch'], cat: 'Others' },
  { id: 'trunk',   caps: ['switch'], cat: 'Others' },
  { id: 'frunk',   caps: ['switch'], cat: 'Others' },
  { id: 'seatFL',  caps: ['switch'], cat: 'Others' },
  { id: 'seatFR',  caps: ['switch'], cat: 'Others' },
  { id: 'seatRL',  caps: ['switch'], cat: 'Others' },
  { id: 'seatRC',  caps: ['switch'], cat: 'Others' },
  { id: 'seatRR',  caps: ['switch'], cat: 'Others' },
];

async function main() {
  // 1) 프리젠테이션(deviceconfig) 생성 — 온도 °C 명시
  const detailView = [];
  for (const c of components)
    for (const cap of c.caps) {
      const item = { component: c.id, capability: cap, version: 1 };
      detailView.push(item);
    }
  let pr = await j(await fetch(API + '/presentation/deviceconfig', { method: 'POST', headers: H, body: JSON.stringify({ detailView }) }));
  const VID = pr.presentationId || pr.vid;
  const MNMN = pr.manufacturerName || pr.mnmn;
  console.log('프리젠테이션 vid:', VID, '| mnmn:', MNMN);
  if (!VID) { console.log('  실패:', JSON.stringify(pr).slice(0, 300)); return; }

  // 2) 프로필 생성 (vid 메타데이터 포함)
  const profBody = {
    name: 'Tesla Control v2',
    metadata: { vid: VID, mnmn: MNMN },
    components: components.map(c => ({ id: c.id, capabilities: c.caps.map(x => ({ id: x, version: 1 })), categories: [{ name: c.cat }] })),
  };
  let p = await j(await fetch(API + '/deviceprofiles', { method: 'POST', headers: H, body: JSON.stringify(profBody) }));
  console.log('프로필 id:', p.id);
  if (!p.id) { console.log('  실패:', JSON.stringify(p).slice(0, 300)); return; }

  // 3) 기존 기기/프로필 삭제 후 새 기기 생성
  await fetch(API + '/devices/' + OLD_DEV, { method: 'DELETE', headers: H });
  const dev = { name: '테슬라', owner: { ownerType: 'LOCATION', ownerId: LOC }, roomId: ROOM, deviceProfileId: p.id, executionTarget: 'CLOUD' };
  let d = await j(await fetch(API + '/virtualdevices', { method: 'POST', headers: H, body: JSON.stringify(dev) }));
  console.log('새 기기 deviceId:', d.deviceId, '| presentationId:', d.presentationId);
  await fetch(API + '/deviceprofiles/' + OLD_PROFILE, { method: 'DELETE', headers: H }).catch(() => {});
  if (d.deviceId) console.log('\nST_DEVICE=' + d.deviceId);
}
main();
