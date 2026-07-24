const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const NS = 'optionoption28278';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const LOC = '5304bc94-d91f-4b89-a09a-d0c093eab7c1';
const ROOM = '6c9494fc-3ecb-40da-8500-1b8401362290';
const OLD_DEV = 'efaa4190-2147-43a6-8b58-871f21435287';
const j = async (r) => { try { return await r.json(); } catch (e) { return {}; } };

// main 컴포넌트 캐퍼 (Teslr 구조, 표준 temperatureMeasurement 제외 → 커스텀 tempteslr °C 사용)
const mainCaps = ['switch', NS + '.batteryteslr', 'battery', NS + '.teslrchargestatus', NS + '.tempteslr',
  NS + '.targettemp', NS + '.fanstat', 'doorControl', NS + '.trunk', NS + '.frunk',
  NS + '.odometer', 'energyMeter', NS + '.odoenergy', NS + '.fronttirepsi', NS + '.reartirepsi', NS + '.teslrlocation'];

async function main() {
  // 1) device 프리젠테이션(deviceconfig) - 전 캐퍼 나열 (각 캐퍼 프리젠테이션이 표시 담당)
  const detailView = mainCaps.map(c => ({ component: 'main', capability: c, version: 1 }));
  detailView.push({ component: 'sentry', capability: 'switch', version: 1 });
  const dashboard = { states: [{ component: 'main', capability: NS + '.batteryteslr', version: 1 }], actions: [{ component: 'main', capability: 'switch', version: 1 }] };
  let cfg = await j(await fetch(API + '/presentation/deviceconfig', { method: 'POST', headers: H, body: JSON.stringify({ dashboard, detailView }) }));
  const VID = cfg.presentationId, MNMN = cfg.manufacturerName;
  console.log('deviceconfig vid:', VID, '| mnmn:', MNMN);
  if (!VID) { console.log('  실패:', JSON.stringify(cfg).slice(0, 300)); return; }

  // 2) 프로필
  const profBody = {
    name: 'Tesla Full',
    metadata: { vid: VID, mnmn: MNMN, ocfDeviceType: 'oic.d.vehicle' },
    components: [
      { id: 'main', capabilities: mainCaps.map(c => ({ id: c, version: 1 })), categories: [{ name: 'Others' }] },
      { id: 'sentry', capabilities: [{ id: 'switch', version: 1 }], categories: [{ name: 'Others' }] },
    ],
  };
  let p = await j(await fetch(API + '/deviceprofiles', { method: 'POST', headers: H, body: JSON.stringify(profBody) }));
  if (!p.id) {
    console.log('프로필 실패(ocfDeviceType 빼고 재시도):', JSON.stringify(p).slice(0, 200));
    delete profBody.metadata.ocfDeviceType;
    p = await j(await fetch(API + '/deviceprofiles', { method: 'POST', headers: H, body: JSON.stringify(profBody) }));
  }
  console.log('프로필 id:', p.id);
  if (!p.id) { console.log('  ', JSON.stringify(p).slice(0, 300)); return; }

  // 3) 기존 기기 삭제 + 새 기기
  await fetch(API + '/devices/' + OLD_DEV, { method: 'DELETE', headers: H });
  const dev = { name: '테슬라', owner: { ownerType: 'LOCATION', ownerId: LOC }, roomId: ROOM, deviceProfileId: p.id, executionTarget: 'CLOUD' };
  let d = await j(await fetch(API + '/virtualdevices', { method: 'POST', headers: H, body: JSON.stringify(dev) }));
  console.log('새 기기 deviceId:', d.deviceId, '| presentationId:', d.presentationId);
  if (d.deviceId) console.log('\nST_DEVICE=' + d.deviceId);
}
main();
