const TOKEN = process.env.SMARTTHINGS_TOKEN;
const LOC = '5304bc94-d91f-4b89-a09a-d0c093eab7c1';
const ROOM = '6c9494fc-3ecb-40da-8500-1b8401362290';
const API = 'https://api.smartthings.com';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };

async function main() {
  // 1) 디바이스 프로필 (멀티 컴포넌트)
  const profile = {
    name: 'Tesla Control',
    components: [
      { id: 'main',    capabilities: [{ id: 'switch', version: 1 }, { id: 'battery', version: 1 }, { id: 'temperatureMeasurement', version: 1 }], categories: [{ name: 'Others' }] },
      { id: 'climate', capabilities: [{ id: 'switch', version: 1 }], categories: [{ name: 'AirConditioner' }] },
      { id: 'charge',  capabilities: [{ id: 'switch', version: 1 }], categories: [{ name: 'Others' }] },
      { id: 'sentry',  capabilities: [{ id: 'switch', version: 1 }], categories: [{ name: 'Others' }] },
    ],
  };
  let r = await fetch(API + '/deviceprofiles', { method: 'POST', headers: H, body: JSON.stringify(profile) });
  let p = await r.json();
  if (!p.id) { console.log('❌ 프로필 실패:', JSON.stringify(p).slice(0, 400)); return; }
  console.log('✅ 프로필 id:', p.id);

  // 2) 단일 가상 기기 "테슬라"
  const dev = { name: '테슬라', owner: { ownerType: 'LOCATION', ownerId: LOC }, roomId: ROOM, deviceProfileId: p.id, executionTarget: 'CLOUD' };
  r = await fetch(API + '/virtualdevices', { method: 'POST', headers: H, body: JSON.stringify(dev) });
  let d = await r.json();
  if (d.deviceId) {
    console.log('✅ 기기 생성! deviceId:', d.deviceId);
    console.log('컴포넌트:', (d.components || []).map(c => c.id + '[' + (c.capabilities || []).map(x => x.id).join(',') + ']').join(' | '));
    console.log('\nST_DEVICE=' + d.deviceId);
  } else {
    console.log('❌ 기기 실패:', JSON.stringify(d).slice(0, 400));
  }
}
main();
