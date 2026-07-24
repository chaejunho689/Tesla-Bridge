const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const NS = 'optionoption28278';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const LOC = '5304bc94-d91f-4b89-a09a-d0c093eab7c1';
const ROOM = '6c9494fc-3ecb-40da-8500-1b8401362290';
const OLD_DEV = '665f3d52-9ad8-43ac-b94b-172a81a208c3';
const j = async (r) => { try { return await r.json(); } catch (e) { return {}; } };

const mainCaps = [
  NS + '.batteryteslr', NS + '.teslrchargestatus',
  NS + '.tempsinfo',            // 온도 3묶음 (내부/외기/목표)
  NS + '.odometer',             // 주행거리 (총/주행가능)
  NS + '.chargespeed', NS + '.odoenergy', NS + '.chargetimeleft',
  NS + '.fronttirepsi', NS + '.reartirepsi',
  NS + '.seatheatfront', NS + '.seatheatrear',
  'doorControl', 'switch', NS + '.trunk', NS + '.frunk',
  NS + '.targettempc', NS + '.chargelimitpct',   // 제어 슬라이더
  NS + '.teslrlocation',
];

async function main() {
  const detailView = mainCaps.map(c => ({ component: 'main', capability: c, version: 1 }));
  detailView.push({ component: 'sentry', capability: 'switch', version: 1 });
  const dashboard = { states: [{ component: 'main', capability: NS + '.batteryteslr', version: 1 }], actions: [{ component: 'main', capability: 'switch', version: 1 }] };
  let cfg = await j(await fetch(API + '/presentation/deviceconfig', { method: 'POST', headers: H, body: JSON.stringify({ dashboard, detailView }) }));
  const VID = cfg.presentationId, MNMN = cfg.manufacturerName;
  console.log('vid:', VID);
  if (!VID) { console.log(JSON.stringify(cfg).slice(0, 300)); return; }
  const profBody = {
    name: 'Tesla Full4', metadata: { vid: VID, mnmn: MNMN, ocfDeviceType: 'oic.d.vehicle' },
    components: [
      { id: 'main', capabilities: mainCaps.map(c => ({ id: c, version: 1 })), categories: [{ name: 'Others' }] },
      { id: 'sentry', capabilities: [{ id: 'switch', version: 1 }], categories: [{ name: 'Others' }] },
    ],
  };
  let p = await j(await fetch(API + '/deviceprofiles', { method: 'POST', headers: H, body: JSON.stringify(profBody) }));
  console.log('profile:', p.id);
  if (!p.id) { console.log(JSON.stringify(p).slice(0, 250)); return; }
  await fetch(API + '/devices/' + OLD_DEV, { method: 'DELETE', headers: H });
  const dev = { name: '테슬라 모델3', owner: { ownerType: 'LOCATION', ownerId: LOC }, roomId: ROOM, deviceProfileId: p.id, executionTarget: 'CLOUD' };
  let d = await j(await fetch(API + '/virtualdevices', { method: 'POST', headers: H, body: JSON.stringify(dev) }));
  console.log('새 기기:', d.deviceId);
  if (d.deviceId) {
    await fetch(API + '/devices/' + d.deviceId, { method: 'PUT', headers: H, body: JSON.stringify({ label: '테슬라 모델3', components: [{ id: 'main', label: '차량' }, { id: 'sentry', label: '센트리 모드' }] }) });
    console.log('\nST_DEVICE=' + d.deviceId);
  }
}
main();
