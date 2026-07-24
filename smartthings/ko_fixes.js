const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const NS = 'optionoption28278';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const j = async (r) => { try { return await r.json(); } catch (e) { return {}; } };

const onoff = { i18n: { value: { on: { label: '켜짐' }, off: { label: '꺼짐' } } } };

const items = [
  { id: NS + '.targettempc', ko: { tag: 'ko', label: '목표 온도', attributes: { temp: { label: '목표 온도' } } } },
  { id: NS + '.seatheatfront', ko: { tag: 'ko', label: '앞좌석 열선', attributes: { fl: Object.assign({ label: '운전석' }, onoff), fr: Object.assign({ label: '조수석' }, onoff) } } },
  { id: NS + '.seatheatrear', ko: { tag: 'ko', label: '뒷좌석 열선', attributes: { rl: Object.assign({ label: '뒤 좌' }, onoff), rc: Object.assign({ label: '뒤 중' }, onoff), rr: Object.assign({ label: '뒤 우' }, onoff) } } },
  { id: NS + '.odometer', ko: { tag: 'ko', label: '주행거리', attributes: { odometerReading: { label: '총주행거리' }, odometerRemain: { label: '주행가능거리' } } } },
];

(async () => {
  for (const it of items) {
    // 먼저 POST 시도, 실패하면 PUT /i18n/ko
    let r = await fetch(API + `/capabilities/${it.id}/1/i18n`, { method: 'POST', headers: H, body: JSON.stringify(it.ko) });
    if (r.status >= 400) {
      const r2 = await fetch(API + `/capabilities/${it.id}/1/i18n/ko`, { method: 'PUT', headers: H, body: JSON.stringify(it.ko) });
      console.log(it.id, 'POST', r.status, '→ PUT', r2.status, r2.status < 300 ? 'OK' : JSON.stringify(await j(r2)).slice(0, 160));
    } else {
      console.log(it.id, 'i18n POST', r.status, 'OK');
    }
  }
})();
