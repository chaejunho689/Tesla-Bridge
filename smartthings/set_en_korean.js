const TOKEN = process.env.SMARTTHINGS_TOKEN;
const API = 'https://api.smartthings.com';
const NS = 'optionoption28278';
const H = { 'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json' };
const j = async (r) => { try { return await r.json(); } catch (e) { return {}; } };

const caps = ['batteryteslr', 'teslrchargestatus', 'tempsinfo', 'odometer', 'chargespeed', 'odoenergy',
  'chargetimeleft', 'fronttirepsi', 'reartirepsi', 'seatheatfront', 'seatheatrear', 'trunk', 'frunk',
  'targettempc', 'chargelimitpct', 'teslrlocation', 'tempteslr', 'outsidetemp'];

async function getKo(id) {
  for (const tag of ['ko_KR', 'ko']) {
    const r = await fetch(API + `/capabilities/${id}/1/i18n/${tag}`, { headers: H });
    if (r.status < 300) { const b = await j(r); if (b.label) return b; }
  }
  return null;
}

(async () => {
  for (const c of caps) {
    const id = NS + '.' + c;
    const ko = await getKo(id);
    if (!ko) { console.log(c, '(ko 없음, 건너뜀)'); continue; }
    ko.tag = 'en';
    const r = await fetch(API + `/capabilities/${id}/1/i18n/en`, { method: 'PUT', headers: H, body: JSON.stringify(ko) });
    console.log(c, '→ en=한글:', r.status, r.status < 300 ? 'OK' : JSON.stringify(await j(r)).slice(0, 120));
  }
})();
