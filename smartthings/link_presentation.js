const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const PROFILE = '2cc27d2b-c888-4b4d-a026-7b05911870df';
const VID = 'dc0a02c6-8acf-360b-b6c0-b78a06e1afef';
const MNMN = 'SmartThingsCommunity';
const OLD_DEV = 'bbc2731f-cb00-4b71-b459-ea64eb28037c';
const LOC = '5304bc94-d91f-4b89-a09a-d0c093eab7c1';
const ROOM = '6c9494fc-3ecb-40da-8500-1b8401362290';

async function j(r) { try { return await r.json(); } catch (e) { return {}; } }

async function main() {
  // 1) 프로필에 vid/mnmn 메타데이터 추가 (PUT 전체 갱신)
  const p = await j(await fetch(API + '/deviceprofiles/' + PROFILE, { headers: H }));
  const body = {
    name: p.name,
    components: (p.components || []).map(c => ({ id: c.id, capabilities: c.capabilities.map(x => ({ id: x.id, version: x.version || 1 })), categories: c.categories })),
    metadata: Object.assign({}, p.metadata, { vid: VID, mnmn: MNMN }),
  };
  let r = await fetch(API + '/deviceprofiles/' + PROFILE, { method: 'PUT', headers: H, body: JSON.stringify(body) });
  console.log('프로필 업데이트:', r.status);
  if (r.status >= 400) { console.log('  ', JSON.stringify(await j(r)).slice(0, 300)); }

  // 2) 기존 기기 presentationId 확인
  let d = await j(await fetch(API + '/devices/' + OLD_DEV, { headers: H }));
  console.log('기존 기기 presentationId:', d.presentationId);

  // 3) 반영 안 됐으면 기기 재생성
  if (!d.presentationId) {
    console.log('→ 기기 재생성 (프리젠테이션 반영 위해)');
    await fetch(API + '/devices/' + OLD_DEV, { method: 'DELETE', headers: H });
    const dev = { name: '테슬라', owner: { ownerType: 'LOCATION', ownerId: LOC }, roomId: ROOM, deviceProfileId: PROFILE, executionTarget: 'CLOUD' };
    let nr = await fetch(API + '/virtualdevices', { method: 'POST', headers: H, body: JSON.stringify(dev) });
    let nd = await j(nr);
    console.log('새 기기 deviceId:', nd.deviceId, '| presentationId:', nd.presentationId);
    if (nd.deviceId) console.log('\nST_DEVICE=' + nd.deviceId);
  }
}
main();
