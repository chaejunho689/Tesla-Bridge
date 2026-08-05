"""
Tesla ↔ SmartThings 브릿지.

- OAuth (Authorization Code) 로그인/토큰 저장/자동 갱신
- 읽기(vehicles, vehicle_data)는 Fleet API로 직접
- 명령(lock/climate/charge...)은 tesla-http-proxy 경유(서명 필요)
- SmartThings Edge 드라이버가 호출할 간단한 REST 엔드포인트 제공
  (BRIDGE_TOKEN 으로 보호 — LAN이라도 아무나 차 제어 못 하게)
"""
import asyncio
import json
import math
import os
import time
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx
try:
    import asyncpg
except Exception:
    asyncpg = None
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse, FileResponse, Response

# ── 설정 (환경변수) ──────────────────────────────────────────────
CLIENT_ID = os.environ["TESLA_CLIENT_ID"]
CLIENT_SECRET = os.environ["TESLA_CLIENT_SECRET"]
DOMAIN = os.environ.get("TESLA_DOMAIN", "chaejunho689.asuscomm.com")
REDIRECT_URI = os.environ.get("TESLA_REDIRECT_URI", f"https://{DOMAIN}/callback")
FLEET_BASE = os.environ.get("FLEET_BASE", "https://fleet-api.prd.na.vn.cloud.tesla.com")
AUTH_BASE = os.environ.get("TESLA_AUTH_BASE", "https://auth.tesla.com")
PROXY_BASE = os.environ.get("PROXY_BASE", "https://tesla-proxy:4443")
PROXY_CA = os.environ.get("PROXY_CA", "/certs/cert.pem")
SCOPES = os.environ.get(
    "TESLA_SCOPES",
    "openid offline_access vehicle_device_data vehicle_cmds vehicle_charging_cmds vehicle_location",
)
BRIDGE_TOKEN = os.environ["BRIDGE_TOKEN"]
DEFAULT_VIN = os.environ.get("TESLA_VIN", "")

# TeslaMate DB (충전 세션 조회)
TM_DB_HOST = os.environ.get("TESLAMATE_DB_HOST", "")
TM_DB_PORT = int(os.environ.get("TESLAMATE_DB_PORT", "5432"))
TM_DB_USER = os.environ.get("TESLAMATE_DB_USER", "teslamate")
TM_DB_PASS = os.environ.get("TESLAMATE_DB_PASS", "")
TM_DB_NAME = os.environ.get("TESLAMATE_DB_NAME", "teslamate")
TM_CAR_ID  = int(os.environ.get("TESLAMATE_CAR_ID", "1"))

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
TOKENS_FILE = DATA_DIR / "tokens.json"

# 명령 이름 → Fleet API command 엔드포인트 매핑 (허용 목록)
COMMANDS = {
    "lock": ("door_lock", None),
    "unlock": ("door_unlock", None),
    "climate_on": ("auto_conditioning_start", None),
    "climate_off": ("auto_conditioning_stop", None),
    "charge_start": ("charge_start", None),
    "charge_stop": ("charge_stop", None),
    "charge_port_open": ("charge_port_door_open", None),
    "charge_port_close": ("charge_port_door_close", None),
    "flash": ("flash_lights", None),
    "honk": ("honk_horn", None),
    "wake": ("__wake__", None),  # 특수: wake_up 엔드포인트
}

app = FastAPI(title="tesla-bridge")
_oauth_states: dict[str, float] = {}


# ── 토큰 저장/로드 ───────────────────────────────────────────────
def load_tokens() -> dict:
    if TOKENS_FILE.exists():
        return json.loads(TOKENS_FILE.read_text())
    return {}


def save_tokens(tok: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TOKENS_FILE.write_text(json.dumps(tok, indent=2))


async def get_access_token() -> str:
    tok = load_tokens()
    if not tok.get("refresh_token"):
        raise HTTPException(401, "로그인 안 됨. /login 먼저 진행하세요.")
    # 만료 60초 전이면 갱신
    if tok.get("expires_at", 0) - time.time() < 60:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                f"{AUTH_BASE}/oauth2/v3/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "refresh_token": tok["refresh_token"],
                },
            )
        if r.status_code != 200:
            raise HTTPException(401, f"토큰 갱신 실패: {r.text}")
        new = r.json()
        tok["access_token"] = new["access_token"]
        tok["refresh_token"] = new.get("refresh_token", tok["refresh_token"])
        tok["expires_at"] = time.time() + new.get("expires_in", 28800)
        save_tokens(tok)
    return tok["access_token"]


def require_bridge_auth(request: Request) -> None:
    auth = request.headers.get("authorization", "")
    key = request.query_params.get("key", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else key
    if not secrets.compare_digest(token, BRIDGE_TOKEN):
        raise HTTPException(403, "브릿지 인증 실패")


# ── 헬스체크 ─────────────────────────────────────────────────────
@app.get("/health")
async def health():
    tok = load_tokens()
    return {
        "ok": True,
        "logged_in": bool(tok.get("refresh_token")),
        "token_expires_in": int(tok.get("expires_at", 0) - time.time()) if tok else None,
    }


# ── 모바일 제어 대시보드 ─────────────────────────────────────────
@app.get("/manifest.webmanifest")
async def manifest():
    m = {
        "name": "테슬라 모델3", "short_name": "홍차",
        "start_url": f"/?key={BRIDGE_TOKEN}", "scope": "/",
        "display": "standalone", "orientation": "portrait",
        "background_color": "#0e0f11", "theme_color": "#0e0f11",
        "icons": [
            {"src": "/icon.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/icon.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/icon.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    return JSONResponse(m, media_type="application/manifest+json")


@app.get("/icon.png")
async def icon_png():
    p = DATA_DIR / "icon.png"
    if p.exists():
        return FileResponse(p, media_type="image/png")
    raise HTTPException(404, "icon.png 없음 — bridge/data/icon.png 에 저장하세요")


@app.get("/sw.js")
async def service_worker():
    return Response("self.addEventListener('fetch',()=>{});", media_type="application/javascript")


@app.get("/app.apk")
async def app_apk(request: Request):
    require_bridge_auth(request)  # APK 안에 key가 박혀 있으므로 공개 노출 방지
    p = DATA_DIR / "tesla.apk"
    if p.exists():
        return FileResponse(p, media_type="application/vnd.android.package-archive",
                            filename="테슬라모델3.apk")
    raise HTTPException(404, "apk 없음")


@app.get("/watch.apk")
async def watch_apk(request: Request):
    require_bridge_auth(request)  # 워치 APK도 key 포함
    p = DATA_DIR / "tesla-watch.apk"
    if p.exists():
        return FileResponse(p, media_type="application/vnd.android.package-archive",
                            filename="테슬라워치.apk")
    raise HTTPException(404, "watch apk 없음")


# ── 갤럭시워치(Wear OS) 대시보드 ─────────────────────────────────
_WATCH_ASSETS = {
    "bg.png": "image/png", "s1.png": "image/png", "s2.png": "image/png", "s3.png": "image/png",
    "arrow.svg": "image/svg+xml", "seat.svg": "image/svg+xml", "wheel.svg": "image/svg+xml",
}


@app.get("/w/{name}")
async def watch_asset(name: str):
    mt = _WATCH_ASSETS.get(name)
    if not mt:
        raise HTTPException(404, "asset 없음")
    p = DATA_DIR / "watch" / name
    if p.exists():
        return FileResponse(p, media_type=mt)
    raise HTTPException(404, f"{name} 없음")


@app.get("/watch")
async def watch():
    return HTMLResponse(WATCH_HTML)


@app.get("/")
async def dashboard():
    return HTMLResponse(DASHBOARD_HTML)


# ── OAuth 로그인 흐름 ────────────────────────────────────────────
@app.get("/login")
async def login(key: str = Query("")):
    if not secrets.compare_digest(key, BRIDGE_TOKEN):
        return HTMLResponse("<h3>접근 거부: ?key=BRIDGE_TOKEN 필요</h3>", status_code=403)
    state = secrets.token_urlsafe(16)
    _oauth_states[state] = time.time()
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "prompt": "login",  # 기존 동의 재사용 방지 → 새 스코프(위치) 강제 재동의
    }
    return RedirectResponse(f"{AUTH_BASE}/oauth2/v3/authorize?{urlencode(params)}")


@app.get("/callback")
async def callback(code: str = Query(""), state: str = Query("")):
    if state not in _oauth_states:
        return HTMLResponse("<h3>state 불일치/만료. /login 다시 시도.</h3>", status_code=400)
    _oauth_states.pop(state, None)
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(
            f"{AUTH_BASE}/oauth2/v3/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "audience": FLEET_BASE,
            },
        )
    if r.status_code != 200:
        return HTMLResponse(f"<h3>토큰 교환 실패</h3><pre>{r.text}</pre>", status_code=400)
    t = r.json()
    save_tokens({
        "access_token": t["access_token"],
        "refresh_token": t["refresh_token"],
        "expires_at": time.time() + t.get("expires_in", 28800),
    })
    return HTMLResponse("<h2>✅ Tesla 로그인 완료! 이제 이 창은 닫아도 됩니다.</h2>")


# ── 파트너 등록 (일회성, 도메인 등록) ────────────────────────────
@app.post("/admin/register-partner")
async def register_partner(request: Request):
    require_bridge_auth(request)
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(
            f"{AUTH_BASE}/oauth2/v3/token",
            data={
                "grant_type": "client_credentials",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "scope": SCOPES,
                "audience": FLEET_BASE,
            },
        )
        if r.status_code != 200:
            raise HTTPException(400, f"파트너 토큰 실패: {r.text}")
        partner_token = r.json()["access_token"]
        r2 = await c.post(
            f"{FLEET_BASE}/api/1/partner_accounts",
            headers={"Authorization": f"Bearer {partner_token}"},
            json={"domain": DOMAIN},
        )
    return JSONResponse({"status": r2.status_code, "body": _safe_json(r2)})


# ── 차량 목록 / 상태 (읽기: Fleet API 직접) ──────────────────────
@app.get("/api/vehicles")
async def vehicles(request: Request):
    require_bridge_auth(request)
    at = await get_access_token()
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(
            f"{FLEET_BASE}/api/1/vehicles",
            headers={"Authorization": f"Bearer {at}"},
        )
    return JSONResponse(_safe_json(r), status_code=r.status_code)


@app.get("/api/state")
async def state(request: Request, vin: str = Query("")):
    require_bridge_auth(request)
    vin = vin or DEFAULT_VIN
    if not vin:
        raise HTTPException(400, "vin 필요")
    at = await get_access_token()
    base = "charge_state;climate_state;drive_state;vehicle_state;gui_settings;vehicle_config"
    url = f"{FLEET_BASE}/api/1/vehicles/{vin}/vehicle_data"
    hdr = {"Authorization": f"Bearer {at}"}
    async with httpx.AsyncClient(timeout=25) as c:
        # 위치 포함 시도 → 위치 권한 없으면 전체가 실패하므로 위치 빼고 재시도
        r = await c.get(url, headers=hdr, params={"endpoints": base + ";location_data"})
        if r.status_code != 200 and "vehicle_location" in r.text:
            r = await c.get(url, headers=hdr, params={"endpoints": base})

    cache_file = DATA_DIR / "last_state.json"
    if r.status_code == 200:
        data = _safe_json(r)
        if isinstance(data, dict) and data.get("response"):
            now = time.time()
            resp = data["response"]
            try:
                cache_file.write_text(json.dumps({"response": resp, "cached_at": now}))
            except Exception:
                pass
            cost = None
            try:
                cost = await _compute_charge_cost(resp.get("charge_state") or {}, resp.get("drive_state") or {})
            except Exception:
                pass
            return JSONResponse({"response": resp, "cached": False, "cached_at": now,
                                 "charge_cost": cost})
    # 실패(잠자는 중 등) → 마지막 캐시 반환
    if cache_file.exists():
        try:
            c = json.loads(cache_file.read_text())
            cost = None
            try:
                resp = c["response"]
                cost = await _compute_charge_cost(resp.get("charge_state") or {}, resp.get("drive_state") or {})
            except Exception:
                pass
            return JSONResponse({"response": c["response"], "cached": True,
                                 "cached_at": c.get("cached_at"), "charge_cost": cost})
        except Exception:
            pass
    return JSONResponse(_safe_json(r), status_code=r.status_code)


# ── 명령 (프록시 경유, 서명됨) ───────────────────────────────────
async def _cmd_post(vin: str, at: str, url: str, body: dict, wake: bool = True):
    """명령 전송. 차가 절전/오프라인이면 깨운 뒤 1회 재시도."""
    hdr = {"Authorization": f"Bearer {at}"}

    async def once():
        async with httpx.AsyncClient(timeout=30, verify=PROXY_CA) as c:
            return await c.post(url, headers=hdr, json=body)

    r = await once()
    txt = ((r.text if r is not None else "") or "").lower()
    asleep = (r is None or r.status_code != 200
              or "asleep" in txt or "offline" in txt or "unavailable" in txt)
    if wake and asleep:
        try:
            async with httpx.AsyncClient(timeout=60, verify=PROXY_CA) as c:
                await c.post(f"{PROXY_BASE}/api/1/vehicles/{vin}/wake_up", headers=hdr)
                # 깨어날 때까지 최대 30초 대기 (2초 간격 폴링)
                for _ in range(15):
                    await asyncio.sleep(2)
                    try:
                        s = await c.get(f"{PROXY_BASE}/api/1/vehicles/{vin}", headers=hdr)
                        if (s.json().get("response") or {}).get("state") == "online":
                            break
                    except Exception:
                        pass
        except Exception:
            pass
        r = await once()

    # 명령이 통했으면 폰 앱에 "지금 상태 다시 읽어라" 신호를 넣는다.
    # (앱은 notify_pending을 10초마다 확인 → 상태 칩이 2분 기다리지 않고 바로 갱신됨)
    if r is not None and r.status_code == 200:
        _nudge_phone()
        # 차량이 명령을 반영하는 데 몇 초 걸리므로 한 번 더
        asyncio.create_task(_nudge_phone_later(15))
    return r


def _nudge_phone():
    """폰 앱 갱신 신호를 큐에 넣는다(중복 방지)."""
    if "refresh" not in _notify_queue:
        _notify_queue.append("refresh")


async def _nudge_phone_later(delay: float):
    await asyncio.sleep(delay)
    _nudge_phone()


@app.post("/api/command/{action}")
async def command(action: str, request: Request, vin: str = Query("")):
    require_bridge_auth(request)
    if action not in COMMANDS:
        raise HTTPException(400, f"허용되지 않은 명령: {action}. 가능: {list(COMMANDS)}")
    vin = vin or DEFAULT_VIN
    if not vin:
        raise HTTPException(400, "vin 필요")
    at = await get_access_token()
    endpoint, _ = COMMANDS[action]

    # wake_up 은 command가 아니라 별도 엔드포인트 (프록시가 그대로 전달)
    if endpoint == "__wake__":
        url = f"{PROXY_BASE}/api/1/vehicles/{vin}/wake_up"
    else:
        url = f"{PROXY_BASE}/api/1/vehicles/{vin}/command/{endpoint}"

    try:
        body = await request.json()
    except Exception:
        body = {}

    # wake 명령 자체는 재시도 불필요
    r = await _cmd_post(vin, at, url, body, wake=(endpoint != "__wake__"))
    return JSONResponse(_safe_json(r), status_code=r.status_code)


# 온도/충전한도처럼 파라미터 있는 명령
@app.post("/api/set_temp")
async def set_temp(request: Request, vin: str = Query(""), celsius: float = Query(21.0)):
    require_bridge_auth(request)
    vin = vin or DEFAULT_VIN
    at = await get_access_token()
    url = f"{PROXY_BASE}/api/1/vehicles/{vin}/command/set_temps"
    r = await _cmd_post(vin, at, url, {"driver_temp": celsius, "passenger_temp": celsius})
    return JSONResponse(_safe_json(r), status_code=r.status_code)


@app.post("/api/charge_limit")
async def charge_limit(request: Request, vin: str = Query(""), percent: int = Query(80)):
    require_bridge_auth(request)
    vin = vin or DEFAULT_VIN
    at = await get_access_token()
    url = f"{PROXY_BASE}/api/1/vehicles/{vin}/command/set_charge_limit"
    r = await _cmd_post(vin, at, url, {"percent": percent})
    return JSONResponse(_safe_json(r), status_code=r.status_code)


@app.post("/api/trunk")
async def trunk(request: Request, vin: str = Query(""), which: str = Query("rear")):
    require_bridge_auth(request)
    vin = vin or DEFAULT_VIN
    at = await get_access_token()
    url = f"{PROXY_BASE}/api/1/vehicles/{vin}/command/actuate_trunk"
    body = {"which_trunk": "front" if which == "front" else "rear"}
    r = await _cmd_post(vin, at, url, body)
    return JSONResponse(_safe_json(r), status_code=r.status_code)


@app.post("/api/seat")
async def seat_heater(request: Request, vin: str = Query(""),
                      seat: int = Query(0), level: int = Query(3)):
    require_bridge_auth(request)
    vin = vin or DEFAULT_VIN
    at = await get_access_token()
    url = f"{PROXY_BASE}/api/1/vehicles/{vin}/command/remote_seat_heater_request"
    body = {"heater": seat, "level": max(0, min(3, level))}
    r = await _cmd_post(vin, at, url, body)
    return JSONResponse(_safe_json(r), status_code=r.status_code)


@app.post("/api/steering")
async def steering_heater(request: Request, vin: str = Query(""), on: bool = Query(True)):
    require_bridge_auth(request)
    vin = vin or DEFAULT_VIN
    at = await get_access_token()
    url = f"{PROXY_BASE}/api/1/vehicles/{vin}/command/remote_steering_wheel_heater_request"
    r = await _cmd_post(vin, at, url, {"on": on})
    return JSONResponse(_safe_json(r), status_code=r.status_code)


@app.post("/api/sentry")
async def sentry(request: Request, vin: str = Query(""), on: bool = Query(True)):
    require_bridge_auth(request)
    vin = vin or DEFAULT_VIN
    at = await get_access_token()
    url = f"{PROXY_BASE}/api/1/vehicles/{vin}/command/set_sentry_mode"
    r = await _cmd_post(vin, at, url, {"on": on})
    return JSONResponse(_safe_json(r), status_code=r.status_code)


# ── 폰 알림 원격 테스트 (adb 불필요) ────────────────────────────
_notify_queue: list[str] = []

@app.get("/api/notify_test")
async def notify_test(request: Request, which: str = Query(...)):
    require_bridge_auth(request)
    _notify_queue.append(which)
    return JSONResponse({"ok": True, "queued": which})

@app.get("/api/notify_pending")
async def notify_pending(request: Request):
    require_bridge_auth(request)
    items = _notify_queue[:]
    _notify_queue.clear()
    return JSONResponse({"which": items})

@app.get("/notifytest")
async def notifytest_page(key: str = Query("")):
    return HTMLResponse("""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>알림 테스트</title><style>
body{margin:0;background:#0e0f11;color:#eee;font-family:-apple-system,Roboto,sans-serif;padding:16px}
h2{font-size:18px;margin:8px 0 4px} .muted{color:#8a8f96;font-size:12px;margin-bottom:14px}
.sec{color:#8a8f96;font-size:13px;margin:16px 0 8px}
button{display:block;width:100%;padding:16px;margin:8px 0;border:0;border-radius:12px;
  background:#1e1f22;color:#fff;font-size:16px;font-weight:600;cursor:pointer}
button:active{background:#2a2c30}
.chip{display:block;margin-top:4px;color:#8a8f96;font-size:12px;font-weight:400}
#t{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#e82127;color:#fff;
  padding:10px 18px;border-radius:20px;opacity:0;transition:opacity .2s;font-size:14px}
#t.on{opacity:1}
</style></head><body>
<h2>🔔 폰 알림 테스트</h2>
<div class="muted">버튼을 누르면 폰 앱이 최대 10초 안에 해당 알림을 띄웁니다.</div>
<div class="sec">이벤트 알림</div>
<button onclick="s('sw')">신규 소프트웨어</button>
<button onclick="s('chgdone')">충전 완료 (시간·요금)</button>
<button onclick="s('driveend')">주행 종료</button>
<div class="sec">상태 칩 / 나우바</div>
<button onclick="s('drive')">운전 중<span class="chip">칩: 02:50 (경과)</span></button>
<button onclick="s('charge')">충전 중<span class="chip">칩: 00:45 (남은시간)</span></button>
<button onclick="s('hvac')">에어컨<span class="chip">칩: 00:50 (경과)</span></button>
<button onclick="s('trunk')">트렁크 열림<span class="chip">칩: 트렁크</span></button>
<button onclick="s('frunk')">프렁크 열림<span class="chip">칩: 프렁크</span></button>
<button onclick="s('sentry')">센트리모드<span class="chip">칩: 감시중</span></button>
<button onclick="s('off')">칩 끄기</button>
<div id="t"></div>
<script>
const KEY=new URLSearchParams(location.search).get('key')||'';
async function s(w){ try{ await fetch('/api/notify_test?which='+w+'&key='+encodeURIComponent(KEY));
  toast('전송됨: '+w); }catch(e){ toast('실패'); } }
let tt; function toast(m){ const t=document.getElementById('t'); t.textContent=m; t.classList.add('on');
  clearTimeout(tt); tt=setTimeout(()=>t.classList.remove('on'),1500); }
</script></body></html>""")


# ── 충전 요금 관리 (Claude가 등록/수정) ────────────────────────────
@app.get("/api/charge/info")
async def charge_info(request: Request):
    require_bridge_auth(request)
    rows = await _tm_fetch(
        """SELECT cp.id, (cp.start_date + interval '9 hours') AS start_kst,
                  g.name AS loc, cp.charge_energy_added AS kwh, cp.cost AS tm_cost
             FROM charging_processes cp
             LEFT JOIN geofences g ON g.id = cp.geofence_id
            WHERE cp.car_id = $1 AND cp.charge_energy_added > 0
            ORDER BY cp.start_date DESC LIMIT 30""",
        TM_CAR_ID)
    sessions = [{"id": r["id"], "start": str(r["start_kst"]), "loc": r["loc"],
                 "kwh": float(r["kwh"] or 0), "tm_cost": (float(r["tm_cost"]) if r["tm_cost"] is not None else None)}
                for r in rows]
    return JSONResponse({"rates": _load_rates(), "teslamate_sessions": sessions})

@app.post("/api/charge/location")
async def charge_location(request: Request, name: str = Query(...),
                          lat: float = Query(...), lon: float = Query(...),
                          radius_m: int = Query(100), cycle_day: int = Query(0)):
    """위치 추가/수정. cycle_day>0이면 청구주기 시작일(집 전용)."""
    require_bridge_auth(request)
    d = _load_rates()
    loc = next((x for x in d["locations"] if x["name"] == name), None)
    if not loc:
        loc = {"name": name, "rates": []}
        d["locations"].append(loc)
    loc.update({"lat": lat, "lon": lon, "radius_m": radius_m})
    if cycle_day > 0:
        loc["cycle_day"] = cycle_day
    _save_rates(d)
    return JSONResponse({"ok": True, "location": loc})

@app.post("/api/charge/rate")
async def charge_rate(request: Request, name: str = Query(...),
                      won: float = Query(...), from_date: str = Query(..., alias="from")):
    """위치의 요금(원/kWh)을 특정 시점부터 적용."""
    require_bridge_auth(request)
    d = _load_rates()
    loc = next((x for x in d["locations"] if x["name"] == name), None)
    if not loc:
        raise HTTPException(404, f"위치 '{name}' 없음 — /api/charge/location 먼저")
    loc.setdefault("rates", [])
    loc["rates"] = [r for r in loc["rates"] if r.get("from") != from_date]
    loc["rates"].append({"from": from_date, "won": won})
    loc["rates"].sort(key=lambda x: x.get("from", ""))
    _save_rates(d)
    return JSONResponse({"ok": True, "rates": loc["rates"]})


# ══════════ 분석용 시계열 API ══════════
@app.get("/api/analytics/battery")
async def analytics_battery(request: Request, mode: str = Query("hour")):
    """배터리% 시계열. mode=hour(24시간, 매 시간) / day(30일, 매 일). 빈 버킷은 직전값 유지."""
    require_bridge_auth(request)
    if mode == "day":
        rows = await _tm_fetch(
            """WITH days AS (
                 SELECT generate_series(
                   (date_trunc('day', now() + interval '9 hours') - interval '29 days')::timestamp,
                   (date_trunc('day', now() + interval '9 hours'))::timestamp,
                   interval '1 day') AS bucket)
               SELECT d.bucket,
                      round(avg(p.battery_level)::numeric, 1) AS pct
                 FROM days d
                 LEFT JOIN positions p
                   ON date_trunc('day', p.date + interval '9 hours') = d.bucket
                  AND p.car_id = $1 AND p.battery_level IS NOT NULL
                GROUP BY d.bucket ORDER BY d.bucket""", TM_CAR_ID)
    else:
        # 시간별: 최근 14일, 각 시간의 '마지막' 배터리값(그 시간 내 여러 번 변해도 끝값)
        rows = await _tm_fetch(
            """SELECT DISTINCT ON (date_trunc('hour', date + interval '9 hours'))
                      date_trunc('hour', date + interval '9 hours') AS bucket,
                      battery_level AS pct
                 FROM positions
                WHERE car_id = $1 AND battery_level IS NOT NULL
                  AND date >= now() - interval '14 days'
                ORDER BY date_trunc('hour', date + interval '9 hours'), date DESC""",
            TM_CAR_ID)

    if mode == "day":
        # 빈 날은 직전 값 유지, 첫 실측 전 구간 제거
        filled = []
        last = None
        for r in rows:
            v = float(r["pct"]) if r["pct"] is not None else last
            if v is not None: last = v
            filled.append({"t": str(r["bucket"]), "v": v})
        series = [p for p in filled if p["v"] is not None]
    else:
        # 데이터 있는 시간만, 값이 '변한 시점'만 남김 (연속 동일값 collapse)
        series = []
        for r in rows:
            v = float(r["pct"])
            if not series or series[-1]["v"] != v:
                series.append({"t": str(r["bucket"]), "v": v})
    return JSONResponse({"mode": mode, "series": series})


@app.get("/api/analytics/home_charge")
async def analytics_home_charge(request: Request):
    """집 충전비 월별 (9~8일 사이클, 이번 사이클은 오늘까지). 사용자 지정 override 반영."""
    require_bridge_auth(request)
    home = _get_location("집")
    cd = home.get("cycle_day", 9) if home else 9
    rows = await _tm_fetch(
        """SELECT (cp.start_date + interval '9 hours')::date AS kst_date,
                  cp.charge_energy_added AS kwh
             FROM charging_processes cp
             LEFT JOIN geofences g ON g.id = cp.geofence_id
            WHERE cp.car_id = $1 AND cp.charge_energy_added > 0 AND g.name = '집'
            ORDER BY cp.start_date""", TM_CAR_ID)
    # 각 세션을 청구 사이클(cycle_day 기준 월)로 매핑
    from collections import defaultdict
    def cycle_label(d: date):
        if d.day >= cd:
            m0 = d.replace(day=cd)
        else:
            first = d.replace(day=1)
            m0 = (first - timedelta(days=1)).replace(day=cd)
        return m0.strftime("%Y-%m")   # 사이클 시작 월 (예 7/9~8/8 = 2026-07)
    agg = defaultdict(lambda: {"kwh": 0.0})
    for r in rows:
        lbl = cycle_label(r["kst_date"])
        agg[lbl]["kwh"] += float(r["kwh"] or 0)
    # 사용자 지정 override
    OVERRIDE = {
        "2026-02": {"kwh": 170.9,  "cost": 48831},
        "2026-05": {"kwh": 62.524, "cost": 16496},
        "2026-06": {"kwh": 125.718, "cost": 33963},
        "2026-07": {"kwh": 90.614, "cost": 25726},
    }
    # 3~4월: 사용자 미기록 → 평균 요금/kWh로 계산
    known_rates = [16496/62.524, 33963/125.718, 25726/90.614, 48831/170.9]
    avg_rate = sum(known_rates)/len(known_rates)  # ~271
    series = []
    for lbl in sorted(agg.keys()):
        if lbl in OVERRIDE:
            series.append({"month": lbl, "kwh": OVERRIDE[lbl]["kwh"], "cost": OVERRIDE[lbl]["cost"]})
        else:
            kwh = round(agg[lbl]["kwh"], 3)
            series.append({"month": lbl, "kwh": kwh, "cost": int(round(kwh * avg_rate))})
    # OVERRIDE에 있는데 TeslaMate엔 없는 달도 표시
    for lbl, v in OVERRIDE.items():
        if lbl not in agg:
            series.append({"month": lbl, "kwh": v["kwh"], "cost": v["cost"]})
    series.sort(key=lambda x: x["month"])
    return JSONResponse({"cycle_day": cd, "series": series,
                         "note": "매월 %d일~다음달 %d일 사이클" % (cd, cd-1)})


@app.get("/api/analytics/distance")
async def analytics_distance(request: Request, mode: str = Query("day")):
    """주행거리 시계열. mode=day(30일) / week(12주) / month(12개월)"""
    require_bridge_auth(request)
    if mode == "week":
        sql = """SELECT date_trunc('week', start_date + interval '9 hours') AS bucket,
                        sum(distance) AS km
                   FROM drives WHERE car_id=$1 AND distance IS NOT NULL
                    AND start_date >= now() - interval '84 days'
                  GROUP BY 1 ORDER BY 1"""
    elif mode == "month":
        sql = """SELECT date_trunc('month', start_date + interval '9 hours') AS bucket,
                        sum(distance) AS km
                   FROM drives WHERE car_id=$1 AND distance IS NOT NULL
                    AND start_date >= now() - interval '12 months'
                  GROUP BY 1 ORDER BY 1"""
    else:  # day
        sql = """SELECT date_trunc('day', start_date + interval '9 hours') AS bucket,
                        sum(distance) AS km
                   FROM drives WHERE car_id=$1 AND distance IS NOT NULL
                    AND start_date >= now() - interval '30 days'
                  GROUP BY 1 ORDER BY 1"""
    rows = await _tm_fetch(sql, TM_CAR_ID)
    return JSONResponse({"mode": mode,
        "series": [{"t": str(r["bucket"]), "v": round(float(r["km"] or 0), 1)} for r in rows]})


def _safe_json(r: httpx.Response):
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}


# ── SmartThings 가상 디바이스 양방향 동기화 ─────────────────────
ST_TOKEN = os.environ.get("SMARTTHINGS_TOKEN", "")
ST_API = "https://api.smartthings.com"
ST_DEVICE = os.environ.get("ST_DEVICE", "")       # 단일 "테슬라" 기기
_NS = "optionoption28278"   # 커스텀 캐퍼빌리티 네임스페이스
_sync_target = {}   # 컨트롤키 -> 마지막 동기화 값
_sync_changed = {}  # 컨트롤키 -> 마지막 변경 시각
_last_push_ts = 0.0  # 표시값 마지막 반영 캐시 시각
_last_hb = 0.0       # 마지막 하트비트 시각


async def _proxy_cmd(vin, at, endpoint, body=None):
    url = f"{PROXY_BASE}/api/1/vehicles/{vin}/command/{endpoint}"
    async with httpx.AsyncClient(timeout=30, verify=PROXY_CA) as c:
        return await c.post(url, headers={"Authorization": f"Bearer {at}"}, json=body or {})


# 컨트롤 정의: key -> (component, capability, attribute)
_CTRLS = {
    "climate":  ("main", "switch", "switch"),
    "lock":     ("main", "doorControl", "door"),          # closed=잠김
    "trunk":    ("main", _NS + ".trunk", "door"),
    "frunk":    ("main", _NS + ".frunk", "door"),
    "sentry":   ("sentry", "switch", "switch"),
    "settemp":  ("main", _NS + ".targettempc", "temp"),
    "chglimit": ("main", _NS + ".chargelimitpct", "limit"),
    "seat_fl":  ("main", _NS + ".seatheatfront", "fl"),
    "seat_fr":  ("main", _NS + ".seatheatfront", "fr"),
    "seat_rl":  ("main", _NS + ".seatheatrear", "rl"),
    "seat_rc":  ("main", _NS + ".seatheatrear", "rc"),
    "seat_rr":  ("main", _NS + ".seatheatrear", "rr"),
}
_SEAT_IDX = {"seat_fl": 0, "seat_fr": 1, "seat_rl": 2, "seat_rc": 4, "seat_rr": 5}


async def _apply_control(key, value):
    """SmartThings에서 바뀐 컨트롤 값을 실제 Tesla 명령으로. 자는 차는 깨우고 재시도."""
    vin = DEFAULT_VIN
    at = await get_access_token()

    async def run():
        if key == "climate":
            return await _proxy_cmd(vin, at, "auto_conditioning_start" if value == "on" else "auto_conditioning_stop")
        if key == "lock":
            return await _proxy_cmd(vin, at, "door_lock" if value == "closed" else "door_unlock")
        if key == "trunk":
            return await _proxy_cmd(vin, at, "actuate_trunk", {"which_trunk": "rear"})
        if key == "frunk":
            return await _proxy_cmd(vin, at, "actuate_trunk", {"which_trunk": "front"})
        if key == "sentry":
            return await _proxy_cmd(vin, at, "set_sentry_mode", {"on": value == "on"})
        if key == "settemp":
            t = float(value)
            return await _proxy_cmd(vin, at, "set_temps", {"driver_temp": t, "passenger_temp": t})
        if key == "chglimit":
            return await _proxy_cmd(vin, at, "set_charge_limit", {"percent": int(float(value))})
        if key in _SEAT_IDX:
            return await _proxy_cmd(vin, at, "remote_seat_heater_request",
                                    {"heater": _SEAT_IDX[key], "level": 3 if value == "on" else 0})
        return None

    r = await run()
    txt = ((r.text if r is not None else "") or "").lower()
    if r is None or r.status_code != 200 or "asleep" in txt or "offline" in txt or "unavailable" in txt:
        try:
            async with httpx.AsyncClient(timeout=30, verify=PROXY_CA) as c:
                await c.post(f"{PROXY_BASE}/api/1/vehicles/{vin}/wake_up",
                             headers={"Authorization": f"Bearer {at}"})
        except Exception:
            pass
        await asyncio.sleep(12)
        r = await run()
    return r


def _desired_all(resp):
    """차량 실제 상태 -> 각 컨트롤이 가져야 할 값"""
    cs = resp.get("charge_state") or {}
    cl = resp.get("climate_state") or {}
    vs = resp.get("vehicle_state") or {}
    seat = lambda v: "on" if (v or 0) > 0 else "off"
    out = {
        "climate": "on" if cl.get("is_climate_on") else "off",
        "lock": "closed" if vs.get("locked") else "open",
        "trunk": "open" if (vs.get("rt") or 0) != 0 else "closed",
        "frunk": "open" if (vs.get("ft") or 0) != 0 else "closed",
        "sentry": "on" if vs.get("sentry_mode") else "off",
        "seat_fl": seat(cl.get("seat_heater_left")),
        "seat_fr": seat(cl.get("seat_heater_right")),
        "seat_rl": seat(cl.get("seat_heater_rear_left")),
        "seat_rc": seat(cl.get("seat_heater_rear_center")),
        "seat_rr": seat(cl.get("seat_heater_rear_right")),
    }
    if cl.get("driver_temp_setting") is not None:
        out["settemp"] = max(15, min(28, round(cl["driver_temp_setting"])))
    if cs.get("charge_limit_soc") is not None:
        out["chglimit"] = max(50, min(100, int(cs["charge_limit_soc"])))
    return out


def _read_cache():
    f = DATA_DIR / "last_state.json"
    if f.exists():
        try:
            c = json.loads(f.read_text())
            return c.get("response"), c.get("cached_at", 0)
        except Exception:
            pass
    return None, 0


async def _st_get_status(device_id):
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{ST_API}/devices/{device_id}/status",
                            headers={"Authorization": f"Bearer {ST_TOKEN}"})
        return r.json()
    except Exception:
        return None


async def _st_event(device_id, component, capability, attribute, value, unit=None):
    ev = {"component": component, "capability": capability, "attribute": attribute, "value": value}
    if unit is not None:
        ev["unit"] = unit
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            await c.post(f"{ST_API}/virtualdevices/{device_id}/events",
                         headers={"Authorization": f"Bearer {ST_TOKEN}"},
                         json={"deviceEvents": [ev]})
    except Exception:
        pass


# ══════════ 충전 요금 계산 (위치별 요금표 + TeslaMate 세션 집계) ══════════
RATES_FILE  = DATA_DIR / "charge_rates.json"

def _seed_rates() -> dict:
    return {"locations": [
        {"name": "집", "lat": 37.59262, "lon": 127.08531, "radius_m": 100,
         "cycle_day": 9, "rates": [{"from": "2026-01-01", "won": 0}]}
    ]}

def _load_rates() -> dict:
    if RATES_FILE.exists():
        try:
            return json.loads(RATES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    d = _seed_rates()
    _save_rates(d)
    return d

def _save_rates(d: dict) -> None:
    try:
        RATES_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def _haversine(lat1, lon1, lat2, lon2) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))

def _match_location(lat, lon):
    if lat is None or lon is None:
        return None
    for loc in _load_rates().get("locations", []):
        try:
            if _haversine(lat, lon, loc["lat"], loc["lon"]) <= loc.get("radius_m", 100):
                return loc
        except Exception:
            continue
    return None

def _get_location(name):
    for loc in _load_rates().get("locations", []):
        if loc.get("name") == name:
            return loc
    return None

def _rate_for(name, date_str) -> float:
    loc = _get_location(name)
    if not loc:
        return 0.0
    best = 0.0
    for r in sorted(loc.get("rates", []), key=lambda x: x.get("from", "")):
        if r.get("from", "0000-01-01") <= date_str:
            best = r.get("won", 0)
    return best

def _home_cycle_bounds(cycle_day=9, today=None):
    """집 요금 청구 주기: 매월 cycle_day(9일) ~ 다음달 (cycle_day-1)일(8일)"""
    today = today or date.today()
    if today.day >= cycle_day:
        start = today.replace(day=cycle_day)
    else:
        first = today.replace(day=1)
        start = (first - timedelta(days=1)).replace(day=cycle_day)
    nxt = start.replace(year=start.year+1, month=1) if start.month == 12 \
        else start.replace(month=start.month+1)
    end = nxt - timedelta(days=1)
    return start, end

def _kst_today() -> date:
    return (datetime.utcnow() + timedelta(hours=9)).date()

# ── TeslaMate DB 연결 풀 ──
_tm_pool = None
async def _tm_fetch(sql, *args):
    global _tm_pool
    if asyncpg is None or not TM_DB_HOST:
        return []
    try:
        if _tm_pool is None:
            _tm_pool = await asyncpg.create_pool(
                host=TM_DB_HOST, port=TM_DB_PORT, user=TM_DB_USER,
                password=TM_DB_PASS, database=TM_DB_NAME, min_size=1, max_size=3)
        async with _tm_pool.acquire() as con:
            return await con.fetch(sql, *args)
    except Exception:
        return []

async def _compute_charge_cost(cs, ds):
    """이번 충전 = 차량 실시간 값 × 현재 위치 요금.
       이번달 = TeslaMate charging_processes 집계 (집: 9~8일 주기, 집외: 당월)."""
    lat, lon = ds.get("latitude"), ds.get("longitude")
    lm = _match_location(lat, lon)
    locname = lm["name"] if lm else "기타"
    energy = cs.get("charge_energy_added") or 0
    rate_now = _rate_for(locname, _kst_today().isoformat()) if lm else 0
    session_won = round(energy * rate_now)

    home = _get_location("집")
    cd = home.get("cycle_day", 9) if home else 9
    cstart, cend = _home_cycle_bounds(cd, _kst_today())
    tod = _kst_today()
    mstart = tod.replace(day=1)

    lower = min(cstart, mstart)
    rows = await _tm_fetch(
        """SELECT (cp.start_date + interval '9 hours')::date AS kst_date,
                  g.name AS loc, cp.charge_energy_added AS kwh, cp.cost AS tm_cost
             FROM charging_processes cp
             LEFT JOIN geofences g ON g.id = cp.geofence_id
            WHERE cp.car_id = $1 AND cp.charge_energy_added > 0
              AND (cp.start_date + interval '9 hours')::date >= $2
            ORDER BY cp.start_date""",
        TM_CAR_ID, lower)

    month_won = 0
    month_kwh = 0.0
    for r in rows:
        sd = r["kst_date"]
        kwh = float(r["kwh"] or 0)
        loc = r["loc"]
        if loc == "집":
            if cstart <= sd <= cend:
                month_won += round(kwh * _rate_for("집", sd.isoformat()))
                month_kwh += kwh
        else:
            if sd.year == tod.year and sd.month == tod.month:
                month_won += int(round(float(r["tm_cost"]))) if r["tm_cost"] is not None else 0
                month_kwh += kwh

    return {"location": locname, "session_won": session_won,
            "session_kwh": round(energy, 2), "month_won": month_won,
            "month_kwh": round(month_kwh, 2),
            "cycle": f"{cstart.isoformat()}~{cend.isoformat()}"}


async def _vehicle_online(vin) -> str:
    """차량 요약 엔드포인트로 상태만 조회 (online/asleep/offline) — 차를 깨우지 않음."""
    try:
        at = await get_access_token()
        url = f"{FLEET_BASE}/api/1/vehicles/{vin}"
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url, headers={"Authorization": f"Bearer {at}"})
        if r.status_code == 200:
            d = _safe_json(r)
            resp = d.get("response") if isinstance(d, dict) else None
            if isinstance(resp, dict):
                return resp.get("state", "")
    except Exception:
        pass
    return ""


async def _fetch_and_cache(vin):
    at = await get_access_token()
    base = "charge_state;climate_state;drive_state;vehicle_state;gui_settings;vehicle_config"
    url = f"{FLEET_BASE}/api/1/vehicles/{vin}/vehicle_data"
    hdr = {"Authorization": f"Bearer {at}"}
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.get(url, headers=hdr, params={"endpoints": base + ";location_data"})
        if r.status_code != 200 and "vehicle_location" in r.text:
            r = await c.get(url, headers=hdr, params={"endpoints": base})
    if r.status_code == 200:
        data = _safe_json(r)
        if isinstance(data, dict) and data.get("response"):
            (DATA_DIR / "last_state.json").write_text(
                json.dumps({"response": data["response"], "cached_at": time.time()}))


async def _delayed_fetch():
    await asyncio.sleep(10)
    try:
        await _fetch_and_cache(DEFAULT_VIN)
    except Exception:
        pass


async def _st_events(evs):
    """이벤트 목록을 8개씩 나눠 전송 (API 제한)"""
    for i in range(0, len(evs), 8):
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                await c.post(f"{ST_API}/virtualdevices/{ST_DEVICE}/events",
                             headers={"Authorization": f"Bearer {ST_TOKEN}"},
                             json={"deviceEvents": evs[i:i + 8]})
        except Exception:
            pass


_geo_cache = {"lat": None, "lon": None, "addr": None}


async def _geocode(lat, lon):
    if _geo_cache["addr"] and _geo_cache["lat"] is not None and \
       abs(lat - _geo_cache["lat"]) < 0.0005 and abs(lon - _geo_cache["lon"]) < 0.0005:
        return _geo_cache["addr"]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://nominatim.openstreetmap.org/reverse",
                            params={"format": "json", "lat": lat, "lon": lon,
                                    "accept-language": "ko", "zoom": 18},
                            headers={"User-Agent": "tesla-bridge/1.0"})
        addr = r.json().get("display_name")
        if addr:
            _geo_cache.update({"lat": lat, "lon": lon, "addr": addr})
        return addr
    except Exception:
        return _geo_cache["addr"]


def _kst_str():
    return time.strftime("%m/%d %H:%M", time.gmtime(time.time() + 32400))


def _display_events(resp):
    cs = resp.get("charge_state") or {}
    cl = resp.get("climate_state") or {}
    vs = resp.get("vehicle_state") or {}
    MI, PSI = 1.609344, 14.5037738
    cap = lambda n: _NS + "." + n
    evs = []

    def E(c, cp, a, v, u=None):
        e = {"component": c, "capability": cp, "attribute": a, "value": v}
        if u:
            e["unit"] = u
        evs.append(e)

    if cs.get("battery_level") is not None:
        E("main", cap("batteryteslr"), "battery", int(cs["battery_level"]), "%")
    st = cs.get("charging_state")
    E("main", cap("teslrchargestatus"), "chargestatus",
      "충전 중" if st == "Charging" else ("충전 완료" if st == "Complete" else "충전 안함"))
    if cl.get("inside_temp") is not None:
        E("main", cap("tempsinfo"), "inside", round(cl["inside_temp"], 1), "C")
    if cl.get("outside_temp") is not None:
        E("main", cap("tempsinfo"), "outside", round(cl["outside_temp"], 1), "C")
    if cl.get("driver_temp_setting") is not None:
        E("main", cap("tempsinfo"), "target", round(cl["driver_temp_setting"]), "C")
    if vs.get("odometer") is not None:
        E("main", cap("odometer"), "odometerReading", round(vs["odometer"] * MI), "km")
    if cs.get("battery_range") is not None:
        E("main", cap("odometer"), "odometerRemain", round(cs["battery_range"] * MI), "km")
    E("main", cap("chargespeed"), "power", cs.get("charger_power") or 0, "kW")
    if cs.get("charge_energy_added") is not None:
        E("main", cap("odoenergy"), "odometerEnergy", round(cs["charge_energy_added"], 1), "kWh")
    m = cs.get("minutes_to_full_charge") or (cs.get("time_to_full_charge") or 0) * 60
    if st == "Charging" and m:
        E("main", cap("chargetimeleft"), "timeleft", f"{int(m // 60)}시간 {int(m % 60)}분")
    else:
        E("main", cap("chargetimeleft"), "timeleft", "완충" if st == "Complete" else "-")
    for a, f in (("FL", "tpms_pressure_fl"), ("FR", "tpms_pressure_fr")):
        if vs.get(f) is not None:
            E("main", cap("fronttirepsi"), a, round(vs[f] * PSI), "psi")
    for a, f in (("RL", "tpms_pressure_rl"), ("RR", "tpms_pressure_rr")):
        if vs.get(f) is not None:
            E("main", cap("reartirepsi"), a, round(vs[f] * PSI), "psi")
    return evs


async def _sync_once():
    global _last_push_ts, _last_hb
    if not ST_DEVICE:
        return
    status = await _st_get_status(ST_DEVICE)
    if status is None:
        return
    comps = status.get("components", {})

    def read(key):
        c, cp, a = _CTRLS[key]
        try:
            return comps[c][cp][a]["value"]
        except Exception:
            return None

    resp, cache_ts = _read_cache()
    des = _desired_all(resp) if resp else {}
    now = time.time()

    for key in _CTRLS:
        actual = read(key)
        if actual is None:
            # SmartThings에 값이 아직 없음 → 차량 상태로 초기화 (시트 null→off 등)
            if key in des:
                c, cp, a = _CTRLS[key]
                unit = "C" if key == "settemp" else ("%" if key == "chglimit" else None)
                await _st_event(ST_DEVICE, c, cp, a, des[key], unit)
                _sync_target[key] = des[key]
            continue
        tgt = _sync_target.get(key)
        if tgt is None:
            _sync_target[key] = actual
            continue
        if str(actual) != str(tgt):
            # 사용자가 SmartThings에서 조작 → Tesla 명령
            _sync_target[key] = actual
            _sync_changed[key] = now
            try:
                await _apply_control(key, actual)
            except Exception:
                pass
            asyncio.create_task(_delayed_fetch())
        elif key in des and cache_ts > _sync_changed.get(key, 0):
            # 차량 실제 상태 → SmartThings 반영
            want = des[key]
            if str(want) != str(actual):
                c, cp, a = _CTRLS[key]
                unit = "C" if key == "settemp" else ("%" if key == "chglimit" else None)
                await _st_event(ST_DEVICE, c, cp, a, want, unit)
                _sync_target[key] = want
                _sync_changed[key] = now

    # 표시값 반영 (새 캐시일 때만)
    if resp is not None and cache_ts > _last_push_ts:
        await _st_events(_display_events(resp))
        ds = resp.get("drive_state") or {}
        if ds.get("latitude") is not None:
            addr = await _geocode(ds["latitude"], ds["longitude"])
            await _st_events([
                {"component": "main", "capability": _NS + ".teslrlocation", "attribute": "latitude", "value": ds["latitude"]},
                {"component": "main", "capability": _NS + ".teslrlocation", "attribute": "longitude", "value": ds["longitude"]},
                {"component": "main", "capability": _NS + ".teslrlocation", "attribute": "address",
                 "value": addr or f'{ds["latitude"]:.5f}, {ds["longitude"]:.5f}'},
            ])
        _last_push_ts = cache_ts
        _last_hb = now

    # 하트비트 (기기 오프라인 방지, ~12초마다)
    if now - _last_hb > 12:
        await _st_event(ST_DEVICE, "main", _NS + ".teslrlocation", "lastUpdateTime", _kst_str())
        _last_hb = now


_last_fetch = 0.0


async def _sync_loop():
    global _last_fetch
    await asyncio.sleep(5)
    while True:
        try:
            # 자동 vehicle_data 폴링 제거: TeslaMate가 이미 폴링/수면관리 중이므로
            # 브릿지가 추가로 차를 건드리면 잠들기를 방해함. 캐시는 앱(온디맨드)·명령 후에만 갱신.
            await _sync_once()
        except Exception:
            pass
        await asyncio.sleep(6)


@app.on_event("startup")
async def _start_sync():
    if ST_TOKEN and ST_DEVICE:
        asyncio.create_task(_sync_loop())


DASHBOARD_HTML = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="theme-color" content="#0e0f11">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="테슬라 모델3">
<meta name="application-name" content="테슬라 모델3">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icon.png">
<link rel="icon" type="image/png" href="/icon.png">
<title>테슬라 모델3</title>
<style>
  :root { --bg:#0e0f11; --card:#1a1c1f; --card2:#232629; --txt:#f2f3f5; --sub:#9aa0a6; --red:#e82127; --grn:#2ecc71; --blu:#3b9dff; --amber:#f5a623; --line:#2c2f33; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  body { margin:0; background:var(--bg); color:var(--txt); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; padding:16px; padding-bottom:44px; max-width:520px; margin:0 auto; }
  h1 { font-size:22px; margin:4px 0 2px; display:flex; align-items:center; gap:8px; }
  .sub { color:var(--sub); font-size:13px; margin-bottom:16px; }
  .card { background:var(--card); border-radius:16px; padding:16px; margin-bottom:14px; }
  .cardh { font-size:13px; color:var(--sub); text-transform:uppercase; letter-spacing:.5px; margin-bottom:12px; display:flex; justify-content:space-between; align-items:center; gap:8px; }
  .stats { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
  .stat { background:var(--card2); border-radius:12px; padding:12px; }
  .stat .k { color:var(--sub); font-size:12px; }
  .stat .v { font-size:20px; font-weight:600; margin-top:3px; }
  .stat .v small { font-size:12px; color:var(--sub); font-weight:400; }
  .bigsoc { font-size:44px; font-weight:700; line-height:1; }
  .bigsoc small { font-size:18px; color:var(--sub); font-weight:500; }
  .barwrap { position:relative; margin:14px 0 6px; }
  .bar { height:10px; background:var(--card2); border-radius:6px; overflow:hidden; margin:0; position:relative; }
  .bar > i { display:block; height:100%; background:linear-gradient(90deg,#2ecc71,#27ae60); width:0%; transition:width .5s; }
  /* 충전 중: 회색(빈) 구간의 100%지점 → 현재잔량 경계까지 초록 세로선이 우→좌로 이동 (패스 사이 정지 간격) */
  #chgspark { position:absolute; top:0; bottom:0; right:0; left:0; display:none; pointer-events:none; }
  .bar.charging > #chgspark { display:block; }
  #chgspark::before {
    content:''; position:absolute; top:0; bottom:0; width:3px; background:#2ecc71;
    animation:chgspark 2.2s linear infinite;
  }
  @keyframes chgspark {
    0%   { left:100%; opacity:1; animation-timing-function: cubic-bezier(.6,0,.95,.25); }
    55%  { left:0%;   opacity:1; }
    56%  { opacity:0; }
    100% { left:0%;   opacity:0; }
  }
  /* 충전한도 드래그 노브 (충전기 연결 시에만) */
  #limknob { position:absolute; top:5px; left:80%; transform:translate(-50%,-50%);
    width:20px; height:20px; border-radius:50%; background:#fff; box-shadow:0 1px 4px rgba(0,0,0,.5);
    display:none; cursor:grab; touch-action:none; z-index:3; }
  #limknob.on { display:block; }
  #limknob:active { cursor:grabbing; }
  .barfoot { position:relative; color:var(--sub); font-size:12px; min-height:16px; }
  .eta { position:absolute; top:0; transform:translateX(-50%); white-space:nowrap; color:#f5c518; font-weight:600; }
  .row { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:10px; }
  button { border:0; border-radius:12px; padding:15px 10px; font-size:15px; font-weight:600; color:#fff; background:var(--card2); cursor:pointer; transition:transform .05s, opacity .2s; }
  button:active { transform:scale(.96); }
  button.red { background:var(--red); }
  button.wide { width:100%; }
  button.active { box-shadow:inset 0 0 0 2px var(--grn); color:var(--grn); }
  /* 기능별 활성 색상 */
  #btnLock.active   { box-shadow:inset 0 0 0 2px #f5c518; color:#f5c518; }  /* 잠금해제 = 노랑 */
  #btnClim.active   { box-shadow:inset 0 0 0 2px #38bdf8; color:#38bdf8; }  /* 공조 = 밝은 파랑 */
  #btnChg.active    { box-shadow:inset 0 0 0 2px var(--grn); color:var(--grn); } /* 충전구 열림 = 초록 */
  #btnSentry.active { box-shadow:inset 0 0 0 2px #ff4d4d; color:#ff4d4d; }  /* 감시 = 밝은 빨강 */
  #btnFrunk.active, #btnTrunk.active { box-shadow:inset 0 0 0 2px #fff; color:#fff; } /* 열림 = 흰색 */
  /* 롱프레스 홀드 채움 효과 (왼→오른쪽) */
  @property --fill { syntax:'<percentage>'; initial-value:0%; inherits:false; }
  #btnFrunk, #btnTrunk { --fill:0%;
    background-image:linear-gradient(to right, rgba(56,189,248,.6) var(--fill), transparent var(--fill));
    transition:--fill .15s linear, transform .05s;
    -webkit-touch-callout:none; -webkit-user-select:none; user-select:none; }
  #conn { cursor:pointer; position:relative; display:inline-block; }
  @property --ang { syntax:'<angle>'; initial-value:0deg; inherits:false; }
  #conn.sleeping::before { content:""; position:absolute; inset:-3px; border-radius:20px; padding:3px;
    background:conic-gradient(from var(--ang), #ff0059,#ff8a00,#ffe600,#2ecc71,#3b9dff,#a34bff,#ff0059);
    -webkit-mask:linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0); -webkit-mask-composite:xor;
    mask:linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0); mask-composite:exclude;
    animation:angspin 3s linear infinite; pointer-events:none; }
  @keyframes angspin { to { --ang:360deg; } }
  .slider { margin:4px 0 2px; }
  .slider label { display:flex; justify-content:space-between; color:var(--sub); font-size:13px; margin-bottom:8px; }
  .slider label b { color:var(--txt); font-size:16px; }
  input[type=range] { width:100%; accent-color:var(--red); height:28px; }
  #tempslider { accent-color:var(--blu); }
  #limslider  { accent-color:var(--grn); }
  .updlink { display:block; text-align:right; color:var(--sub); font-size:11px; margin:20px 4px 8px; text-decoration:none; opacity:.7; }
  .vintext { text-align:left; color:var(--sub); font-size:10px; opacity:.5; letter-spacing:.3px; margin:5px 4px 0; -webkit-user-select:none; user-select:none; -webkit-touch-callout:none; }
  .odotext { text-align:left; color:var(--sub); font-size:13px; opacity:.5; letter-spacing:.3px; margin:20px 4px 0; -webkit-user-select:none; user-select:none; -webkit-touch-callout:none; }
  .tabbtn { padding:4px 10px; font-size:12px; border-radius:14px; background:var(--card2); color:var(--sub); border:0; cursor:pointer; }
  .tabbtn.active { background:var(--red); color:#fff; }
  .scrollx { overflow-x:auto; overflow-y:hidden; -webkit-overflow-scrolling:touch; }
  .scrollx canvas { display:block; height:180px; }
  /* 모던 확인 모달 */
  #modal { position:fixed; inset:0; background:rgba(0,0,0,.6); display:none; align-items:center; justify-content:center; z-index:100; }
  #modal.on { display:flex; animation:fade .15s ease; }
  @keyframes fade { from{opacity:0} to{opacity:1} }
  .modalbox { background:var(--card); border:1px solid var(--line); border-radius:20px; padding:24px 20px 16px; width:80%; max-width:340px; box-shadow:0 16px 48px rgba(0,0,0,.55); animation:pop .18s cubic-bezier(.2,.9,.3,1.2); }
  @keyframes pop { from{transform:scale(.9);opacity:.5} to{transform:scale(1);opacity:1} }
  .modalmsg { font-size:17px; color:var(--txt); text-align:center; margin-bottom:22px; line-height:1.5; }
  .modalbtns { display:flex; gap:10px; }
  .modalbtns button { flex:1; padding:14px; border-radius:14px; font-size:15px; font-weight:700; border:0; }
  .mcancel { background:var(--card2); color:var(--txt); }
  .mok { background:var(--blu); color:#fff; }
  .sec { color:var(--sub); font-size:12px; text-transform:uppercase; letter-spacing:.5px; margin:18px 4px 8px; }
  #toast { position:fixed; left:50%; bottom:24px; transform:translateX(-50%) translateY(90px); background:#000; color:#fff; padding:12px 18px; border-radius:12px; font-size:14px; opacity:0; transition:.3s; max-width:90%; text-align:center; z-index:9; box-shadow:0 6px 24px rgba(0,0,0,.5); }
  #toast.show { transform:translateX(-50%) translateY(0); opacity:1; }
  .pill { font-size:12px; padding:3px 9px; border-radius:20px; background:var(--card2); color:var(--sub); }
  .pill.on { background:rgba(46,204,113,.15); color:var(--grn); }
  .muted { color:var(--sub); font-size:12px; text-align:center; margin-top:8px; }
  /* 온도 3박스 */
  .temps { display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; }
  .tb { background:var(--card2); border-radius:12px; padding:12px 6px; text-align:center; }
  .tb .k { color:var(--sub); font-size:12px; }
  .tb .v { font-size:22px; font-weight:600; margin-top:4px; }
  /* SVG 다이어그램 (타이어/시트 실루엣) */
  .diagram { display:block; width:100%; max-width:220px; margin:2px auto; }
  .diagram-wide { max-width:100%; }
  .psi { fill:var(--txt); font:700 17px sans-serif; }
  .psi.warn { fill:var(--amber); }
  .lab { fill:var(--sub); font:400 10px sans-serif; }
  .seat { cursor:pointer; }
  .seat rect { transition:fill .2s; }
  /* 지도 */
  #map { width:100%; height:170px; border:0; border-radius:12px; background:var(--card2); }
  a.btn { display:block; text-align:center; text-decoration:none; }
  /* 기능 아이콘 */
  .ic { width:1.15em; height:1.15em; fill:currentColor; vertical-align:-.2em; margin-right:6px; }
  .cih { width:1.05em; height:1.05em; fill:currentColor; vertical-align:-.16em; margin-right:5px; }
  .logo { width:1.3em; height:1.3em; fill:var(--red,#e82127); vertical-align:-.22em; margin-right:5px; }
</style>
</head>
<body>
  <h1><svg class="logo" viewBox="0 0 24 24"><path d="m12 5.362l2.475-3.026s4.245.09 8.471 2.054c-1.082 1.636-3.231 2.438-3.231 2.438c-.146-1.439-1.154-1.79-4.354-1.79L12 24L8.619 5.034c-3.18 0-4.188.354-4.335 1.792c0 0-2.146-.795-3.229-2.43C5.28 2.431 9.525 2.34 9.525 2.34L12 5.362l-.004.002H12v-.002zm0-3.899c3.415-.03 7.326.528 11.328 2.28c.535-.968.672-1.395.672-1.395C19.625.612 15.528.015 12 0C8.472.015 4.375.61 0 2.349c0 0 .195.525.672 1.396C4.674 1.989 8.585 1.435 12 1.46v.003z"/></svg>홍차 <span id="conn" class="pill" onclick="wake()">…</span></h1>
  <div class="sub">Tesla Model 3 AWD · <a href="#" onclick="refresh();return false" style="color:var(--blu)">새로고침</a></div>

  <!-- 배터리/주행 -->
  <div class="card" id="hero">
    <div style="display:flex; justify-content:space-between; align-items:flex-end">
      <div class="bigsoc"><span id="soc">–</span><small>%</small></div>
      <div style="text-align:right">
        <div class="v" style="font-size:22px; font-weight:600"><span id="range">–</span> <small style="color:var(--sub);font-size:13px">km 주행가능</small></div>
        <div style="color:var(--sub); font-size:13px; margin-top:2px" id="chgstate">–</div>
      </div>
    </div>
    <div class="barwrap">
      <div class="bar"><i id="socbar"></i><b id="chgspark"></b></div>
      <div id="limknob"></div>
    </div>
    <div class="barfoot">
      <span id="limlabel" style="display:none">목표 <span id="limtxt">–</span>%</span>
      <span id="eta" class="eta"></span>
    </div>
    <span id="updated" style="display:none"></span>
  </div>

  <!-- 목표 온도 (내기/외기 위) -->
  <div class="card" id="tempCard">
    <div class="slider">
      <label>목표 온도 <b><span id="tempval">21</span>°C</b></label>
      <input type="range" min="15" max="28" value="21" id="tempslider" oninput="tempval.textContent=this.value">
      <button class="wide" style="margin-top:10px" onclick="setTemp()">온도 설정</button>
    </div>
  </div>

  <!-- 온도 3박스 -->
  <div class="card">
    <div class="temps">
      <div class="tb"><div class="k">내기</div><div class="v"><span id="itemp">–</span>°</div></div>
      <div class="tb"><div class="k">외기</div><div class="v"><span id="otemp">–</span>°</div></div>
      <div class="tb"><div class="k">설정 목표</div><div class="v"><span id="ttemp">–</span>°</div></div>
    </div>
  </div>


  <div class="sec">제어 (탭 = ON/OFF, 현재 상태 표시)</div>
  <div class="row">
    <button id="btnLock" onclick="tgl('lock')">잠금</button>
    <button id="btnClim" onclick="tgl('clim')">공조</button>
  </div>
  <div class="row">
    <button id="btnChg" onclick="tgl('chg')">충전</button>
    <button id="btnSentry" onclick="tgl('sentry')">감시</button>
  </div>
  <div class="row">
    <button id="btnFrunk">프렁크</button>
    <button id="btnTrunk">트렁크</button>
  </div>

  <div class="sec">기타</div>
  <div class="row">
    <button onclick="cmd('flash','라이트 깜빡')"><svg class="ic" viewBox="0 0 24 24"><path d="M15.911 5.852h-1.953A1.644 1.644 0 0 0 12.314 7.5v9.438a1.5 1.5 0 0 0 1.5 1.5H15.5a6.5 6.5 0 0 0 6.5-6.5a6.09 6.09 0 0 0-6.089-6.086M2.814 17.931a.692.692 0 1 0 .162 1.374l8.142-.946l-.145-1.372zM2.721 6.069l8.158.944l.145-1.372L2.882 4.7a.692.692 0 1 0-.161 1.374Zm0 8.848l7.956-.316v-1.38l-8.011.319a.689.689 0 1 0 .055 1.377m-.055-4.48l8.011.319v-1.38L2.721 9.06a.689.689 0 1 0-.055 1.377"/></svg>라이트</button>
    <button onclick="cmd('honk','경적')"><svg class="ic" viewBox="0 0 24 24"><path d="M21.184 10.073c-.45 0-.815.255-.815.569v.373h-.957c.019 0-.023-.01-.075 0h-8.31c-1.2 0-4.2-3.746-5.5-5.443a2.04 2.04 0 0 0-1.662-.8A1.9 1.9 0 0 0 2 6.677v10.856a1.693 1.693 0 0 0 1.693 1.693a2.26 2.26 0 0 0 1.875-.986c1.122-1.644 3.4-4.793 4.608-5.058a3.6 3.6 0 0 0-.2 1.289a3.153 3.153 0 0 0 2.8 3.42l4.3.046c.661.007 1.208-.576 1.7-1.059a3.12 3.12 0 0 0 .679-2.361a6.8 6.8 0 0 0-.4-1.76h1.313v.45c.026.315.412.556.862.538s.8-.287.771-.6v-2.5c-.001-.317-.367-.572-.817-.572m-5.171 6.068H13.18c-.449-.058-1.025.184-1.428-1.555a1.3 1.3 0 0 1 .012-.84c.061-.237.236-.875.714-.875H16.7c.69.046 1.151.909 1.151 1.738c.004 1.55-.998 1.522-1.838 1.532"/></svg>경적</button>
  </div>

  <!-- 충전 상세 -->
  <div class="card" id="chgcard">
    <div class="cardh"><svg class="cih" viewBox="0 0 24 24"><path d="m17.635 9.991l-4.938-.1l-.064-7.01c.067-.958-.633-1.4-1.366.059l-5.629 9.53c-.648 1.046-.557 1.41.625 1.524h5l.131 6.573c-.017 1.41.574 2.16 1.438.432l5.686-9.735c.482-.728.282-1.251-.883-1.273"/></svg>충전 <span id="chgbadge" class="pill">–</span> <span id="chgloc" class="pill" style="margin-left:auto">–</span></div>
    <div class="stats">
      <div class="stat"><div class="k">충전 속도</div><div class="v"><span id="ckw">–</span> <small>kW</small> <span id="camp" style="color:var(--sub);font-size:13px"></span></div></div>
      <div class="stat"><div class="k">완충까지</div><div class="v" style="font-size:16px"><span id="cfull">–</span></div></div>
      <div class="stat"><div class="k" id="addedk">이번 충전</div><div class="v" style="font-size:16px"><span id="cthis">–</span></div></div>
      <div class="stat"><div class="k" id="cmonthk">이번달 충전</div><div class="v" style="font-size:16px"><span id="cmonwon">–</span></div></div>
    </div>
  </div>

  <!-- 타이어 (가로형: 좌=앞, 우=뒤) -->
  <div class="card" id="tireCard">
    <div class="cardh">🛞 타이어 공기압 <span class="pill">PSI</span></div>
    <svg class="diagram diagram-wide" viewBox="0 0 320 130">
      <rect x="40" y="13" width="240" height="104" rx="34" fill="#33373c"/>
      <rect id="wtfl" x="54" y="8"  width="54" height="26" rx="12" fill="#4a4f55"/>
      <rect id="wtfr" x="54" y="96" width="54" height="26" rx="12" fill="#4a4f55"/>
      <rect id="wtrl" x="212" y="8"  width="54" height="26" rx="12" fill="#4a4f55"/>
      <rect id="wtrr" x="212" y="96" width="54" height="26" rx="12" fill="#4a4f55"/>
      <text id="tfr" class="psi" x="81" y="19" text-anchor="middle">–</text><text class="lab" x="81" y="30" text-anchor="middle">앞우</text>
      <text id="tfl" class="psi" x="81" y="107" text-anchor="middle">–</text><text class="lab" x="81" y="118" text-anchor="middle">앞좌</text>
      <text id="trr" class="psi" x="239" y="19" text-anchor="middle">–</text><text class="lab" x="239" y="30" text-anchor="middle">뒤우</text>
      <text id="trl" class="psi" x="239" y="107" text-anchor="middle">–</text><text class="lab" x="239" y="118" text-anchor="middle">뒤좌</text>
    </svg>
  </div>

  <!-- 열선 시트 · 핸들 열선 (접기 가능) -->
  <div class="card" id="heatCard">
    <div class="cardh" style="cursor:pointer; user-select:none" onclick="toggleHeatCollapse()">
      🔥 열선 시트 · 핸들
      <span class="pill" id="heatBadge">탭 = ON/OFF</span>
      <span id="heatChevron" style="margin-left:auto; font-size:16px; color:var(--sub); transition:transform .25s">▾</span>
    </div>
    <div id="heatBody">
      <svg class="diagram" viewBox="0 0 220 290">
        <defs>
          <linearGradient id="heat" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stop-color="#ff8a4c"/><stop offset="1" stop-color="#e82127"/>
          </linearGradient>
          <!-- 시트 열선 웨이브 (3줄, 세로) -->
          <symbol id="heatWave" viewBox="-60 -60 120 120">
            <g fill="none" stroke="currentColor" stroke-width="10" stroke-linecap="round">
              <path d="M-22 -38 Q-38 -22 -22 0 T-22 38"/>
              <path d="M0   -38 Q-16 -22 0   0 T0   38"/>
              <path d="M22  -38 Q6   -22 22  0 T22  38"/>
            </g>
          </symbol>
        </defs>
        <rect x="30" y="16" width="160" height="258" rx="54" fill="#1a1c1f" stroke="#2c2f33" stroke-width="2"/>
        <path d="M46 58 Q110 32 174 58" fill="none" stroke="#2c2f33" stroke-width="2"/>
        <!-- 핸들 열선 (탭 = ON/OFF) — 아이콘 중앙정렬 -->
        <g class="seat" onclick="toggleWheel()">
          <circle id="sWheel" cx="70" cy="50" r="16" fill="#3a3f45" stroke="#5a5f65" stroke-width="2"/>
          <circle cx="70" cy="50" r="7" fill="none" stroke="#20232a" stroke-width="2"/>
          <use href="#heatWave" x="57" y="18" width="26" height="14" style="color:#8a8f96"/>
        </g>
        <!-- 시트 5개 — 각 좌석 정중앙에 열선 아이콘 -->
        <g class="seat" onclick="toggleSeat(0)">
          <rect id="sFL" x="48" y="72" width="46" height="58" rx="14" fill="#3a3f45"/>
          <use href="#heatWave" x="56" y="86" width="30" height="30" style="color:#8a8f96" id="hFL"/>
        </g>
        <text class="lab" x="71" y="146" text-anchor="middle">운전석</text>
        <g class="seat" onclick="toggleSeat(1)">
          <rect id="sFR" x="128" y="72" width="46" height="58" rx="14" fill="#3a3f45"/>
          <use href="#heatWave" x="136" y="86" width="30" height="30" style="color:#8a8f96" id="hFR"/>
        </g>
        <text class="lab" x="151" y="146" text-anchor="middle">조수석</text>
        <g class="seat" onclick="toggleSeat(2)">
          <rect id="sRL" x="44" y="186" width="40" height="56" rx="12" fill="#3a3f45"/>
          <use href="#heatWave" x="51" y="201" width="26" height="26" style="color:#8a8f96" id="hRL"/>
        </g>
        <text class="lab" x="64" y="256" text-anchor="middle">뒤좌</text>
        <g class="seat" onclick="toggleSeat(4)">
          <rect id="sRC" x="91" y="190" width="40" height="52" rx="12" fill="#3a3f45"/>
          <use href="#heatWave" x="98" y="203" width="26" height="26" style="color:#8a8f96" id="hRC"/>
        </g>
        <text class="lab" x="111" y="256" text-anchor="middle">뒤중</text>
        <g class="seat" onclick="toggleSeat(5)">
          <rect id="sRR" x="138" y="186" width="40" height="56" rx="12" fill="#3a3f45"/>
          <use href="#heatWave" x="145" y="201" width="26" height="26" style="color:#8a8f96" id="hRR"/>
        </g>
        <text class="lab" x="158" y="256" text-anchor="middle">뒤우</text>
      </svg>
    </div>
  </div>

  <!-- 위치 -->
  <div class="card">
    <div class="cardh">📍 위치 <span id="locbadge" class="pill">–</span></div>
    <iframe id="map" src="about:blank" loading="lazy"></iframe>
    <div class="muted" id="loctxt" style="margin:8px 0 10px">위치 불러오는 중…</div>
    <a class="btn" id="maplink" href="#" target="_blank"><button class="wide">🗺️ 지도 앱에서 열기</button></a>
  </div>

  <!-- 배터리 시계열 -->
  <div class="card">
    <div class="cardh">📈 배터리 <span class="pill" id="battRangeLbl">시간별</span>
      <span style="margin-left:auto; display:flex; gap:6px">
        <button class="tabbtn active" id="tabBattHour" onclick="loadBatt('hour')">시간</button>
        <button class="tabbtn" id="tabBattDay" onclick="loadBatt('day')">일</button>
      </span>
    </div>
    <div class="scrollx"><canvas id="cBatt" height="180"></canvas></div>
  </div>

  <!-- 집 월 충전비 -->
  <div class="card">
    <div class="cardh">🏠 월별 집 충전비 <span class="pill">9~8일 사이클</span></div>
    <div class="scrollx"><canvas id="cHome" height="180"></canvas></div>
  </div>

  <!-- 주행거리 -->
  <div class="card">
    <div class="cardh">🚗 주행거리
      <span style="margin-left:auto; display:flex; gap:6px">
        <button class="tabbtn active" id="tabDistDay" onclick="loadDist('day')">일</button>
        <button class="tabbtn" id="tabDistWeek" onclick="loadDist('week')">주</button>
        <button class="tabbtn" id="tabDistMonth" onclick="loadDist('month')">월</button>
      </span>
    </div>
    <div class="scrollx"><canvas id="cDist" height="180"></canvas></div>
  </div>

  <!-- 제어 -->
  <div class="odotext">총 주행거리 <span id="odo">–</span> km</div>
  <div id="vin" class="vintext"></div>
  <a class="updlink" id="updlink_app" href="#">대시보드 apk 업데이트</a>
  <a class="updlink" id="updlink_watch" href="#">워치 apk 업데이트</a>
  <a class="updlink" id="updlink_notify" href="#">알림 테스트</a>

  <div id="modal">
    <div class="modalbox">
      <div class="modalmsg" id="modalmsg"></div>
      <div class="modalbtns">
        <button class="mcancel" id="mcancel">취소</button>
        <button class="mok" id="mok">확인</button>
      </div>
    </div>
  </div>

  <div id="toast"></div>

<script>
if('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(function(){});
const KEY = new URLSearchParams(location.search).get('key') || '';
(function(){
  var a=document.getElementById('updlink_app'); if(a) a.href='/app.apk?key='+encodeURIComponent(KEY);
  var w=document.getElementById('updlink_watch'); if(w) w.href='/watch.apk?key='+encodeURIComponent(KEY);
  var nt=document.getElementById('updlink_notify'); if(nt) nt.href='/notifytest?key='+encodeURIComponent(KEY);
})();
document.addEventListener('contextmenu', function(e){ e.preventDefault(); });   // 대시보드 전체 우클릭/롱프레스 메뉴 차단
const MI=1.609344, PSI=14.5037738;
const $=id=>document.getElementById(id);
function q(p){ return p + (p.includes('?')?'&':'?') + 'key=' + encodeURIComponent(KEY); }
let toastT;
function toast(m){ const t=$('toast'); t.textContent=m; t.classList.add('show'); clearTimeout(toastT); toastT=setTimeout(()=>t.classList.remove('show'),2300); }

async function cmd(action,label){
  toast(label+' 전송…');
  try{
    const r=await fetch(q('/api/command/'+action),{method:'POST'});
    const j=await r.json().catch(()=>({}));
    if(r.ok && j.response && j.response.result===false){ toast('❌ '+label+': '+(j.response.reason||'실패')); }
    else if(r.ok){ toast('✅ '+label+' 완료'); setTimeout(refresh,1800); }
    else { toast('❌ '+label+' 실패 ('+r.status+')'); }
  }catch(e){ toast('❌ 연결 실패'); }
}
async function setTemp(){ const c=$('tempslider').value; toast('온도 '+c+'° 설정…');
  try{ const r=await fetch(q('/api/set_temp?celsius='+c),{method:'POST'}); toast(r.ok?'✅ 온도 '+c+'°':'❌ 실패'); }catch(e){ toast('❌ 연결 실패'); } }
function tire(id,wid,bar){ const el=$(id),w=$(wid); if(bar==null){el.textContent='–';return;} const p=Math.round(bar*PSI); el.textContent=p; const warn=p<33||p>44; el.classList.toggle('warn',warn); if(w) w.setAttribute('fill', warn?'#6b4a1f':'#4a4f55'); }

const SEATMAP={0:{el:'sFL',hi:'hFL',f:'seat_heater_left',n:'운전석'},1:{el:'sFR',hi:'hFR',f:'seat_heater_right',n:'조수석'},2:{el:'sRL',hi:'hRL',f:'seat_heater_rear_left',n:'뒤좌'},4:{el:'sRC',hi:'hRC',f:'seat_heater_rear_center',n:'뒤중'},5:{el:'sRR',hi:'hRR',f:'seat_heater_rear_right',n:'뒤우'}};
let seatState={};
function paintSeat(i,on){
  const el=$(SEATMAP[i].el); if(el) el.setAttribute('fill', on?'url(#heat)':'#3a3f45');
  const hi=$(SEATMAP[i].hi); if(hi) hi.style.color = on ? '#ffffff' : '#8a8f96';
}
function updateSeats(cl){
  for(const i in SEATMAP){ const v=cl[SEATMAP[i].f]; if(v!=null){ seatState[i]=v; paintSeat(i, v>0); } }
  if(cl && 'steering_wheel_heater' in cl) paintWheel(!!cl.steering_wheel_heater);
}
let wheelOn=false;
function paintWheel(on){
  wheelOn=on;
  const el=$('sWheel'); if(el) el.setAttribute('fill', on?'url(#heat)':'#3a3f45');
}
async function toggleWheel(){
  const next=!wheelOn;
  paintWheel(next);
  toast('🔥 핸들 열선 ' + (next?'켜는 중…':'끄는 중…'));
  try{
    const r=await fetch(q('/api/steering?on='+next), {method:'POST'});
    const j=await r.json().catch(()=>({}));
    if(j.response && j.response.result===false){ toast('❌ 핸들 열선: '+(j.response.reason||'실패')); paintWheel(!next); }
    else toast('🔥 핸들 열선 ' + (next?'ON':'OFF'));
  }catch(e){ toast('❌ 연결 실패'); paintWheel(!next); }
}
function applyHeatCollapse(){
  const collapsed = localStorage.getItem('heatCollapsed') === '1';
  const body=$('heatBody'), chev=$('heatChevron');
  if(body) body.style.display = collapsed ? 'none' : '';
  if(chev) chev.style.transform = collapsed ? 'rotate(-90deg)' : '';
}
function toggleHeatCollapse(){
  const cur = localStorage.getItem('heatCollapsed') === '1';
  localStorage.setItem('heatCollapsed', cur?'0':'1');
  applyHeatCollapse();
}
applyHeatCollapse();
async function toggleSeat(i){
  const on=(seatState[i]||0)>0, lvl=on?0:3;
  paintSeat(i,!on); seatState[i]=lvl; toast('🔥 '+SEATMAP[i].n+(lvl>0?' 켜는 중…':' 끄는 중…'));
  try{ const r=await fetch(q('/api/seat?seat='+i+'&level='+lvl),{method:'POST'}); const j=await r.json().catch(()=>({}));
    if(j.response && j.response.result===false){ toast('❌ '+SEATMAP[i].n+': '+(j.response.reason||'실패')); paintSeat(i,on); seatState[i]=on?3:0; }
    else toast('🔥 '+SEATMAP[i].n+(lvl>0?' ON':' OFF'));
  }catch(e){ toast('❌ 연결 실패'); paintSeat(i,on); seatState[i]=on?3:0; }
}
async function act(path,label){ toast(label+' 전송…');
  try{ const r=await fetch(q(path),{method:'POST'}); const j=await r.json().catch(()=>({}));
    if(r.ok && j.response && j.response.result===false){ toast('❌ '+label+': '+(j.response.reason||'실패')); }
    else if(r.ok){ toast('✅ '+label+' 완료'); } else { toast('❌ '+label+' 실패 ('+r.status+')'); }
  }catch(e){ toast('❌ 연결 실패'); } }
const IC={
  lock:'M17.744 8.667v-.953a5.744 5.744 0 0 0-11.488 0v.953a1.915 1.915 0 0 0-1.915 1.9V20.1A1.915 1.915 0 0 0 6.256 22h11.488a1.915 1.915 0 0 0 1.915-1.9v-9.529a1.915 1.915 0 0 0-1.915-1.904m-1.914 0H8.17v-.953A3.74 3.74 0 0 1 12 3.905a3.74 3.74 0 0 1 3.83 3.809Z',
  lockClosed:'M12 2a5 5 0 0 0-5 5v3h-.4c-.88 0-1.6.72-1.6 1.6v7C5 19.92 6.08 21 7.4 21h9.2c1.32 0 2.4-1.08 2.4-2.4v-7c0-.88-.72-1.6-1.6-1.6H17V7a5 5 0 0 0-5-5m3 8V7c0-1.658-1.342-3-3-3S9 5.342 9 7v3z',
  lockOpen:'M18 2a5 5 0 0 0-5 5v3H4.6c-.88 0-1.6.72-1.6 1.6v7C3 19.92 4.08 21 5.4 21h9.2c1.32 0 2.4-1.08 2.4-2.4v-7c0-.88-.72-1.6-1.6-1.6H15V7c0-1.658 1.342-3 3-3s3 1.342 3 3v3a1 1 0 1 0 2 0V7a5 5 0 0 0-5-5',
  open:'m17.635 9.991l-4.938-.1l-.064-7.01c.067-.958-.633-1.4-1.366.059l-5.629 9.53c-.648 1.046-.557 1.41.625 1.524h5l.131 6.573c-.017 1.41.574 2.16 1.438.432l5.686-9.735c.482-.728.282-1.251-.883-1.273',
  fan:'M13.658 12.2a1.01 1.01 0 0 0-1.252.978V21a1.01 1.01 0 0 0 1.252.978a5.01 5.01 0 0 0 4.067-4.891a5.16 5.16 0 0 0-4.067-4.887m-3.424-.4a1.008 1.008 0 0 0 1.251-.978V3a1.01 1.01 0 0 0-1.251-.978a5.16 5.16 0 0 0-4.067 4.89a5.01 5.01 0 0 0 4.067 4.888m11.712-1.445a5.16 5.16 0 0 0-4.891-4.067a5.01 5.01 0 0 0-4.891 4.067a1.008 1.008 0 0 0 .978 1.251h7.826a1.01 1.01 0 0 0 .978-1.251m-11.162 2.099H2.959a1.012 1.012 0 0 0-.979 1.252a5.164 5.164 0 0 0 4.892 4.067a5.01 5.01 0 0 0 4.891-4.067a1.01 1.01 0 0 0-.979-1.252',
  flash:'M15.911 5.852h-1.953A1.644 1.644 0 0 0 12.314 7.5v9.438a1.5 1.5 0 0 0 1.5 1.5H15.5a6.5 6.5 0 0 0 6.5-6.5a6.09 6.09 0 0 0-6.089-6.086M2.814 17.931a.692.692 0 1 0 .162 1.374l8.142-.946l-.145-1.372zM2.721 6.069l8.158.944l.145-1.372L2.882 4.7a.692.692 0 1 0-.161 1.374Zm0 8.848l7.956-.316v-1.38l-8.011.319a.689.689 0 1 0 .055 1.377m-.055-4.48l8.011.319v-1.38L2.721 9.06a.689.689 0 1 0-.055 1.377',
  sentry:'M12 2a10 10 0 1 0 10 10A10.01 10.01 0 0 0 12 2m0 18.187A8.187 8.187 0 1 1 20.187 12A8.2 8.2 0 0 1 12 20.187M18.638 12A6.64 6.64 0 0 1 12 18.638A6.64 6.64 0 0 1 5.362 12A6.64 6.64 0 0 1 12 5.362A6.64 6.64 0 0 1 18.638 12',
  hood:'m19.99 16.3l-4.6-.021c0-.044.006-.086.006-.131a5.017 5.017 0 1 0-10.033 0v.085L3.9 16.227v-3.083a1.36 1.36 0 0 1 1.066-1.33l3.021-.965a15 15 0 0 1 4.233-.709l3-.068a4.9 4.9 0 0 0 2.046-.366l3.851-1.917c.7-.371 1.128-.816.757-1.514c-.275-.516-1.468-.224-1.985.051L16.467 7.7c-.168.09-.339.164-.488.227a16 16 0 0 0-5.109-3.761A14.36 14.36 0 0 0 4.3 2.848a1.5 1.5 0 0 0-.878.282a.74.74 0 0 0-.287.656a1.34 1.34 0 0 0 .816.967a2.9 2.9 0 0 0 1.406.318l.218-.007c.171-.006.342-.014.519-.008a11.2 11.2 0 0 1 7.09 3.089a17 17 0 0 0-5.127.744c-.8.194-1.6.428-2.376.7l-1.53.528A3.185 3.185 0 0 0 2 13.125v3.764c0 1.145.511 1.452 1.7 1.453l2.187.022a5.007 5.007 0 0 0 8.946.091l5.23.052A1.174 1.174 0 0 0 21.3 17.45a1.175 1.175 0 0 0-1.31-1.15m-7.09-.031a2.52 2.52 0 0 1-1.426 2.152a2.43 2.43 0 0 1-2.22-.023a2.52 2.52 0 0 1-1.388-2.153c0-.032-.009-.063-.009-.1a2.528 2.528 0 1 1 5.055 0c-.002.045-.012.083-.012.124',
  trunk:'m21.548 13.363l-.248-.473l.013-1.291c.005-.206.27-.022.339-.215l.255-.891c.34-.635-.577-1.621-.912-1.557l-3.267-.687a11.4 11.4 0 0 1-1.768-.525l-.267-.1L17.986 5.3a.816.816 0 0 1 1.322.135l.307.377a1.115 1.115 0 0 0 1.582.148a.87.87 0 0 0 .078-1.254l-1.424-1.343a1.574 1.574 0 0 0-2.41 0l-3.67 3.514l-5.249-2.034a5.9 5.9 0 0 0-2.139-.4H3.046a1.046 1.046 0 0 0 0 2.092H6.1a5.4 5.4 0 0 1 1.932.365l7.121 2.758a13.5 13.5 0 0 0 2.035.608l.652.14c.7.152 1.844.126 1.5.759l-.229.436l.017 1.651l.566.993A1.1 1.1 0 0 1 20 15v.585a1.08 1.08 0 0 1-1.08 1.08h-1.152c.013-.129.028-.256.032-.387c0-.045.007-.088.007-.132a5.062 5.062 0 1 0-10.124 0v.085c0 .147.017.29.032.434H3.046a1.047 1.047 0 0 0 0 2.093h5.383a5.027 5.027 0 0 0 8.633 0h2.3c1.509 0 2.613-.681 2.613-2.189L22 14.325a1.8 1.8 0 0 0-.452-.962M10.2 16.238c0-.032-.01-.063-.01-.1a2.551 2.551 0 1 1 5.1 0c0 .041-.01.079-.012.12a2.5 2.5 0 0 1-.053.4a2.55 2.55 0 0 1-1.386 1.773a2.46 2.46 0 0 1-2.24-.023a2.57 2.57 0 0 1-1.4-2.173z'
};
const IC_EVENODD={lockClosed:1};
function ico(n){ const fr=IC_EVENODD[n]?' fill-rule="evenodd"':''; return '<svg class="ic" viewBox="0 0 24 24"><path'+fr+' d="'+IC[n]+'"/></svg>'; }
let ST={locked:false,clim:false,chg:false,sentry:false,frunkOpen:false,trunkOpen:false};
function paintToggles(){
  const b=(id,on,ic,onTxt,offTxt)=>{ const e=$(id); if(!e)return; e.innerHTML=ico(ic)+(on?onTxt:offTxt); e.classList.toggle('active',on); };
  b('btnLock',!ST.locked, ST.locked?'lockClosed':'lockOpen', '잠금 해제','잠금');
  b('btnClim',ST.clim,'fan','공조 ON','공조 OFF');
  b('btnChg',ST.chg,'open','충전구 열림','충전구');
  b('btnSentry',ST.sentry,'sentry','감시 ON','감시 OFF');
  b('btnFrunk',ST.frunkOpen,'hood','프렁크 열림','프렁크');
  b('btnTrunk',ST.trunkOpen,'trunk','트렁크 열림','트렁크');
}
function tgl(kind){
  const m={
    lock:()=>{ST.locked=!ST.locked; cmd(ST.locked?'lock':'unlock', ST.locked?'잠금':'해제');},
    clim:()=>{ST.clim=!ST.clim; cmd(ST.clim?'climate_on':'climate_off','공조 '+(ST.clim?'ON':'OFF'));},
    chg:()=>{ST.chg=!ST.chg; cmd(ST.chg?'charge_port_open':'charge_port_close','충전구 '+(ST.chg?'열기':'닫기'));},
    sentry:()=>{ST.sentry=!ST.sentry; act('/api/sentry?on='+ST.sentry,'감시 '+(ST.sentry?'ON':'OFF'));},
    frunk:async ()=>{ if(!await confirmModal('프렁크를 여시겠습니까?')) return; ST.frunkOpen=!ST.frunkOpen; act('/api/trunk?which=front','프렁크');},
    trunk:async ()=>{ if(!await confirmModal('트렁크를 여시겠습니까?')) return; ST.trunkOpen=!ST.trunkOpen; act('/api/trunk?which=rear','트렁크');},
  };
  if(m[kind]){ m[kind](); paintToggles(); setTimeout(refresh,1800); }
}
async function wake(){ toast('☀️ 깨우는 중…'); try{ await fetch(q('/api/command/wake'),{method:'POST'}); toast('☀️ 깨우기 전송'); setTimeout(refresh,4000); setTimeout(refresh,10000); }catch(e){ toast('❌ 연결 실패'); } }

// eta 라벨을 마커(pct%) 중앙에 두되, 게이지바 좌우 끝을 넘지 않게 clamp
function placeEta(pct){
  const eta=$('eta'); if(!eta) return;
  const W=eta.parentNode.clientWidth, w=eta.offsetWidth;
  let x = W*pct/100 - w/2;
  x = Math.max(0, Math.min(W - w, x));
  eta.style.left = x+'px';
  eta.style.transform = 'none';
}

// 완충까지 남은 분(m) → "오늘 10:30" / "내일 23:50" / "7/26 08:00"
function etaLabel(mins){
  const t=new Date(Date.now()+mins*60000);
  const hh=String(t.getHours()).padStart(2,'0'), mm=String(t.getMinutes()).padStart(2,'0');
  const now=new Date();
  const d0=new Date(now.getFullYear(),now.getMonth(),now.getDate());
  const d1=new Date(t.getFullYear(),t.getMonth(),t.getDate());
  const days=Math.round((d1-d0)/86400000);
  let day = days<=0?'오늘' : days===1?'내일' : days===2?'모레' : (t.getMonth()+1)+'/'+t.getDate();
  return day+' '+hh+':'+mm;
}

// 충전 중이면 충전 카드를 목표온도 위로, 아니면 타이어 앞(원위치)으로
function reorderCharging(charging){
  const chg=$('chgcard'), temp=$('tempCard'), tire=$('tireCard');
  if(!chg||!temp||!tire) return;
  const p=chg.parentNode;
  if(charging){
    if(temp.previousElementSibling!==chg) p.insertBefore(chg, temp);
  } else {
    if(tire.previousElementSibling!==chg) p.insertBefore(chg, tire);
  }
}

// ── 충전한도 드래그 노브 (충전기 연결 시에만) ──
let limDragging=false, curLimit=80;
(function(){
  const knob=$('limknob'); if(!knob) return;
  const bar=$('socbar').parentNode;   // .bar
  const pctFromX=(clientX)=>{ const r=bar.getBoundingClientRect();
    return Math.max(50, Math.min(100, Math.round((clientX-r.left)/r.width*100))); };
  const place=(p)=>{ curLimit=p; knob.style.left=p+'%'; const t=$('limtxt'); if(t)t.textContent=p; };
  window.placeLimKnob=place;
  knob.addEventListener('pointerdown',e=>{ limDragging=true; knob.setPointerCapture(e.pointerId); e.preventDefault(); });
  knob.addEventListener('pointermove',e=>{ if(limDragging) place(pctFromX(e.clientX)); });
  const end=e=>{ if(!limDragging)return; limDragging=false; commitLimit(curLimit); };
  knob.addEventListener('pointerup',end);
  knob.addEventListener('pointercancel',end);
})();
async function commitLimit(p){ toast('충전한도 '+p+'% 설정…');
  try{ const r=await fetch(q('/api/charge_limit?percent='+p),{method:'POST'}); toast(r.ok?'✅ 한도 '+p+'%':'❌ 실패'); }catch(e){ toast('❌ 연결 실패'); } }

async function refresh(){
  $('updated').textContent='불러오는 중…';
  try{
    const r=await fetch(q('/api/state'));
    const j=await r.json().catch(()=>({}));
    if(!r.ok || !j.response){ $('conn').textContent='💤 자는 중 · 깨우기'; $('conn').className='pill sleeping'; $('updated').textContent='차량 잠듦 — 배지 탭하여 깨우기'; return; }
    const d=j.response;
    const cs=d.charge_state||{}, cl=d.climate_state||{}, vs=d.vehicle_state||{}, ds=d.drive_state||{};
    if(d.vin){ const ve=$('vin'); ve.textContent='VIN  '+d.vin; ve.setAttribute('data-vin',d.vin); }
    // 배터리/주행
    if(cs.battery_level!=null){ $('soc').textContent=cs.battery_level; $('socbar').style.width=cs.battery_level+'%'; const sp=$('chgspark'); if(sp) sp.style.left=cs.battery_level+'%'; }
    if(cs.battery_range!=null) $('range').textContent=Math.round(cs.battery_range*MI);
    if(cs.charge_limit_soc!=null && !limDragging && window.placeLimKnob) window.placeLimKnob(cs.charge_limit_soc);
    if(vs.odometer!=null) $('odo').textContent=Math.round(vs.odometer*MI).toLocaleString();
    if(cl.inside_temp!=null) $('itemp').textContent=cl.inside_temp.toFixed(1);
    if(cl.outside_temp!=null) $('otemp').textContent=cl.outside_temp.toFixed(1);
    if(cl.driver_temp_setting!=null) $('ttemp').textContent=cl.driver_temp_setting.toFixed(0);
    // 충전
    const charging = cs.charging_state==='Charging';
    const connected = cs.charging_state && cs.charging_state!=='Disconnected';
    // 충전한도 노브·라벨: 충전기 연결 시에만
    $('limknob').classList.toggle('on', !!connected);
    $('limlabel').style.display = connected ? '' : 'none';
    $('chgbadge').textContent = ({Charging:'충전중',Complete:'완료',Stopped:'중지',Disconnected:'미연결',NoPower:'대기'}[cs.charging_state]||cs.charging_state||'–');
    $('chgbadge').className = 'pill'+(charging?' on':'');
    $('chgstate').textContent = charging?('충전중 '+(cs.charger_power||0)+'kW'):(cs.charging_state==='Complete'?'충전 완료':'미충전');
    $('ckw').textContent = cs.charger_power!=null?cs.charger_power:'–';
    // 암페어 (충전 중일 때만)
    const amp = cs.charger_actual_current;
    $('camp').textContent = (charging && amp!=null) ? ('· '+amp+'A') : '';
    $('addedk').textContent = charging?'이번 충전':'마지막 충전';
    const m=cs.minutes_to_full_charge!=null?cs.minutes_to_full_charge:(cs.time_to_full_charge?cs.time_to_full_charge*60:0);
    $('cfull').textContent = (charging&&m>0)?(Math.floor(m/60)+'시간 '+Math.round(m%60)+'분'):'–';
    // 완충 예상 시각 — 노란 한도 마커 아래 중앙 (카드 밖으로 안 나가게 clamp)
    const eta=$('eta');
    if(eta){
      const lim=cs.charge_limit_soc;
      if(charging && m>0 && lim!=null){
        eta.textContent = '('+etaLabel(m)+' 완료)';
        placeEta(lim);
      } else { eta.textContent=''; }
    }
    // 충전 요금 (서버 계산)
    const cc = j.charge_cost;
    const kwh = cs.charge_energy_added!=null?cs.charge_energy_added.toFixed(1):'–';
    if(cc){
      $('chgloc').textContent = cc.location || '–';
      $('cthis').textContent = kwh + 'kWh / ' + (cc.session_won||0).toLocaleString() + '원';
      $('cmonwon').textContent = (cc.month_kwh||0) + 'kWh / ' + (cc.month_won||0).toLocaleString() + '원';
    } else {
      $('cthis').textContent = kwh + 'kWh';
    }
    // 배터리 게이지 충전 애니메이션 + 카드 재배치
    const bar=$('socbar').parentNode;
    if(bar) bar.classList.toggle('charging', charging);
    reorderCharging(charging);
    // 타이어
    tire('tfl','wtfl',vs.tpms_pressure_fl); tire('tfr','wtfr',vs.tpms_pressure_fr); tire('trl','wtrl',vs.tpms_pressure_rl); tire('trr','wtrr',vs.tpms_pressure_rr);
    updateSeats(cl);
    // on/off 현재 상태 반영
    ST.locked=!!vs.locked; ST.clim=!!cl.is_climate_on; ST.chg=!!cs.charge_port_door_open; ST.sentry=!!vs.sentry_mode;
    ST.frunkOpen=(vs.ft!=null && vs.ft!==0); ST.trunkOpen=(vs.rt!=null && vs.rt!==0);
    paintToggles();
    // 위치
    if(ds.latitude!=null && ds.longitude!=null){
      const la=ds.latitude, lo=ds.longitude;
      const bb=(lo-0.006)+','+(la-0.0035)+','+(lo+0.006)+','+(la+0.0035);
      $('map').src='https://www.openstreetmap.org/export/embed.html?bbox='+bb+'&layer=mapnik&marker='+la+','+lo;
      $('maplink').href='https://www.google.com/maps/search/?api=1&query='+la+','+lo;
      $('loctxt').textContent=la.toFixed(5)+', '+lo.toFixed(5);
      $('locbadge').textContent='실시간'; $('locbadge').className='pill on';
    } else {
      $('loctxt').innerHTML='위치 권한(vehicle_location)이 필요합니다 — 재로그인 후 표시됩니다';
      $('locbadge').textContent='권한 필요'; $('locbadge').className='pill';
    }
    if(j.cached){
      const mm = j.cached_at!=null ? Math.round((Date.now()/1000 - j.cached_at)/60) : null;
      $('conn').textContent = '💤 '+(mm!=null ? (mm<1?'방금':mm+'분 전') : '캐시')+' · 깨우기';
      $('conn').className='pill sleeping';
      $('updated').textContent = (mm!=null ? (mm<1?'방금':mm+'분 전')+' 정보 (자는 중·배지 탭하여 깨우기)' : '마지막 정보');
    } else {
      $('conn').textContent='● 온라인'; $('conn').className='pill on';
      $('updated').textContent=new Date().toLocaleTimeString('ko-KR');
    }
  }catch(e){ $('conn').textContent='오프라인'; $('conn').className='pill'; $('updated').textContent='연결 실패'; }
}
// 모던 확인 모달 (구식 confirm 대체)
function confirmModal(msg){
  return new Promise((resolve)=>{
    const m=$('modal'); $('modalmsg').textContent=msg; m.classList.add('on');
    const done=(v)=>{ m.classList.remove('on'); $('mok').onclick=null; $('mcancel').onclick=null; m.onclick=null; resolve(v); };
    $('mok').onclick=()=>done(true);
    $('mcancel').onclick=()=>done(false);
    m.onclick=(e)=>{ if(e.target===m) done(false); };
  });
}

// 프렁크/트렁크: 롱프레스(600ms) → tgl()이 확인창까지. 짧게 누르면 안내.
function attachLongPress(el, fn){
  if(!el) return;
  const HOLD=600;
  let timer=null, fired=false;
  const setFill=(pct,ms)=>{ el.style.transition='--fill '+ms+'ms linear'; el.style.setProperty('--fill',pct); };
  const start=()=>{ fired=false; setFill('100%',HOLD); timer=setTimeout(()=>{ fired=true; setFill('0%',150); fn(); }, HOLD); };
  const cancel=()=>{ if(timer){ clearTimeout(timer); timer=null; } setFill('0%',150); };
  el.addEventListener('touchstart', start, {passive:true});
  el.addEventListener('touchend', cancel);
  el.addEventListener('touchmove', cancel);
  el.addEventListener('touchcancel', cancel);
  el.addEventListener('mousedown', start);
  el.addEventListener('mouseup', cancel);
  el.addEventListener('mouseleave', cancel);
  el.addEventListener('click', (e)=>{ e.preventDefault(); if(!fired) toast('길게 눌러 여세요'); });
  el.addEventListener('contextmenu', (e)=>e.preventDefault());   // 롱프레스 텍스트선택/복사 팝업 차단
}
attachLongPress($('btnFrunk'), ()=>tgl('frunk'));
attachLongPress($('btnTrunk'), ()=>tgl('trunk'));

// 차대번호 롱프레스 복사
function copyVin(){
  const v=($('vin').getAttribute('data-vin'))||''; if(!v) return;
  const ok=()=>toast('차대번호 복사됨');
  if(navigator.clipboard && navigator.clipboard.writeText){ navigator.clipboard.writeText(v).then(ok).catch(()=>fallbackCopy(v,ok)); }
  else fallbackCopy(v,ok);
}
function fallbackCopy(t,cb){ const ta=document.createElement('textarea'); ta.value=t; ta.style.position='fixed'; ta.style.opacity='0'; document.body.appendChild(ta); ta.focus(); ta.select(); try{ document.execCommand('copy'); cb(); }catch(e){ toast('복사 실패'); } document.body.removeChild(ta); }
(function(){ const e=$('vin'); if(!e) return; let t=null;
  const s=()=>{ t=setTimeout(copyVin,600); }; const c=()=>{ if(t){clearTimeout(t);t=null;} };
  e.addEventListener('touchstart',s,{passive:true}); e.addEventListener('touchend',c); e.addEventListener('touchmove',c); e.addEventListener('touchcancel',c);
  e.addEventListener('mousedown',s); e.addEventListener('mouseup',c); e.addEventListener('mouseleave',c);
  e.addEventListener('contextmenu',(ev)=>ev.preventDefault());
})();

if(!KEY) toast('⚠️ URL에 ?key=토큰 이 필요합니다');
paintToggles();
refresh();
setInterval(refresh,60000);

// ── 분석 그래프 (Canvas) ──
// 캔버스 폭을 항목수×단위폭으로 잡아 가로 스크롤(모든 라벨 표시). 폰트 추가 20%↑
function visW(canvas){ return (canvas.parentNode && canvas.parentNode.clientWidth) || 340; }
function drawLineChart(canvas, points, opts){
  const ctx = canvas.getContext('2d');
  const H = canvas.height;
  const perUnit = opts.perUnit || 46;     // 포인트당 최소 폭
  const vis = visW(canvas);
  const W = Math.max(vis, points.length*perUnit);
  canvas.width = W; canvas.style.width = W+'px';
  ctx.clearRect(0,0,W,H);
  if(!points.length){ ctx.fillStyle='#8a8f96'; ctx.font='18px sans-serif'; ctx.fillText('데이터 없음', 20, 34); return; }
  const pad={l:44,r:16,t:opts.showValues?22:16,b:34};
  const vals = points.map(p=>p.v);
  let vmin=Math.min(...vals), vmax=Math.max(...vals);
  if(opts && opts.ymin!=null) vmin=opts.ymin;
  if(opts && opts.ymax!=null) vmax=opts.ymax;
  if(vmax===vmin) vmax=vmin+1;
  const xW = W-pad.l-pad.r, yH = H-pad.t-pad.b;
  const x = i => pad.l + xW * (i/(points.length-1||1));
  const y = v => pad.t + yH - yH * (v-vmin)/(vmax-vmin);
  ctx.strokeStyle='#2c2f33'; ctx.lineWidth=1;
  ctx.fillStyle='#8a8f96'; ctx.font='13px sans-serif';
  for(let g=0;g<=3;g++){
    const yv = vmin + (vmax-vmin)*g/3, py = y(yv);
    ctx.beginPath(); ctx.moveTo(pad.l, py); ctx.lineTo(W-pad.r, py); ctx.stroke();
    ctx.fillText(opts.yFmt?opts.yFmt(yv):yv.toFixed(0), 4, py+5);
  }
  ctx.strokeStyle = opts.color||'#2ecc71'; ctx.lineWidth=2; ctx.beginPath();
  points.forEach((p,i)=>{ const px=x(i), py=y(p.v); if(i===0) ctx.moveTo(px,py); else ctx.lineTo(px,py); });
  ctx.stroke();
  ctx.lineTo(x(points.length-1), pad.t+yH); ctx.lineTo(x(0), pad.t+yH); ctx.closePath();
  ctx.fillStyle = (opts.fill||'rgba(46,204,113,.15)'); ctx.fill();
  if(opts.showValues){
    ctx.font='12px sans-serif'; ctx.textAlign='center';
    points.forEach((p,i)=>{
      const px=x(i), py=y(p.v);
      ctx.beginPath(); ctx.arc(px,py,3,0,Math.PI*2); ctx.fillStyle=opts.color||'#2ecc71'; ctx.fill();
      ctx.fillStyle='#f2f2f2';
      ctx.fillText(opts.valFmt?opts.valFmt(p.v):Math.round(p.v), px, py-8);
    });
  }
  // x축 라벨 — 폭이 넉넉하므로 전부 표시 (xFmt에 index·배열 전달 → 날짜 경계 표기 가능)
  ctx.fillStyle='#8a8f96'; ctx.font='13px sans-serif'; ctx.textAlign='center';
  points.forEach((p,i)=>{
    ctx.fillText(opts.xFmt?opts.xFmt(p.t,i,points):String(p.t), x(i), H-9);
  });
  ctx.textAlign='left';
  scrollRight(canvas);
}
function scrollRight(canvas){ const w=canvas.parentNode; if(w) w.scrollLeft = w.scrollWidth; }

// 한 화면에 약 6개 막대, 나머지는 가로 스크롤
function drawBarChart(canvas, items, opts){
  const ctx = canvas.getContext('2d');
  const H = canvas.height;
  const vis = visW(canvas);
  const pad={l:52,r:16,t:18,b:38};
  const VISN = 6;                                  // 화면에 보일 막대 수
  const slot = (vis - pad.l - pad.r) / VISN;       // 막대 1개 슬롯 폭
  const W = Math.max(vis, pad.l + pad.r + slot*items.length);
  canvas.width = W; canvas.style.width = W+'px';
  ctx.clearRect(0,0,W,H);
  if(!items.length){ ctx.fillStyle='#8a8f96'; ctx.font='18px sans-serif'; ctx.fillText('데이터 없음', 20, 34); return; }
  const yH = H-pad.t-pad.b;
  const vals = items.map(it=>it.v);
  const vmax = Math.max(...vals)*1.15 || 1;
  const bw = slot*0.6, gap = slot*0.4;
  ctx.strokeStyle='#2c2f33'; ctx.lineWidth=1; ctx.fillStyle='#8a8f96'; ctx.font='13px sans-serif';
  for(let g=0;g<=3;g++){
    const yv=vmax*g/3, py=pad.t+yH-yH*g/3;
    ctx.beginPath(); ctx.moveTo(pad.l,py); ctx.lineTo(W-pad.r,py); ctx.stroke();
    ctx.fillText(opts.yFmt?opts.yFmt(yv):yv.toFixed(0), 4, py+5);
  }
  items.forEach((it,i)=>{
    const bx = pad.l + slot*i + gap/2;
    const bh = yH*(it.v/vmax);
    const by = pad.t + yH - bh;
    ctx.fillStyle = opts.color||'#e82127';
    ctx.fillRect(bx, by, bw, bh);
    ctx.fillStyle='#c8cacb'; ctx.font='13px sans-serif'; ctx.textAlign='center';
    ctx.fillText(it.lbl, bx+bw/2, H-16);
    if(opts.topFmt){ ctx.fillStyle='#f2f2f2'; ctx.font='12px sans-serif';
      ctx.fillText(opts.topFmt(it.v), bx+bw/2, by-5); }
  });
  ctx.textAlign='left';
  scrollRight(canvas);
}

function setTabActive(prefix, mode){
  ['hour','day','week','month'].forEach(m=>{
    const el=document.getElementById(prefix+m.charAt(0).toUpperCase()+m.slice(1));
    if(el) el.classList.toggle('active', m===mode);
  });
}

async function loadBatt(mode){
  setTabActive('tabBatt', mode);
  $('battRangeLbl').textContent = (mode==='day'?'일별 (30일)':'시간별 (24시간)');
  try{
    const r=await fetch(q('/api/analytics/battery?mode='+mode));
    const j=await r.json();
    const pts=j.series.map(p=>({t:p.t, v:p.v}));
    drawLineChart($('cBatt'), pts, {
      ymin:0, ymax:100, color:'#2ecc71', fill:'rgba(46,204,113,.2)',
      yFmt:v=>Math.round(v)+'%',
      xFmt:(t,i,pts)=>{ const d=new Date(t.replace(' ','T'));
        if(mode==='day') return (d.getMonth()+1)+'/'+d.getDate();
        const hh=String(d.getHours()).padStart(2,'0')+':00';
        // 첫 포인트이거나 날짜가 바뀌면 날짜도 표기
        if(i===0 || new Date(pts[i-1].t.replace(' ','T')).getDate()!==d.getDate())
          return (d.getMonth()+1)+'/'+d.getDate()+' '+hh;
        return hh;
      },
      perUnit: mode==='day'?52:56,
      showValues: true,
      valFmt: v=>Math.round(v)+'%'
    });
  }catch(e){}
}

async function loadHome(){
  try{
    const r=await fetch(q('/api/analytics/home_charge'));
    const j=await r.json();
    const items=j.series.map(s=>({lbl:s.month.slice(5)+'월', v:s.cost, kwh:s.kwh}));
    drawBarChart($('cHome'), items, {color:'#e82127',
      yFmt:v=>Math.round(v/1000)+'k',
      topFmt:v=>v.toLocaleString()});
  }catch(e){}
}

async function loadDist(mode){
  setTabActive('tabDist', mode);
  try{
    const r=await fetch(q('/api/analytics/distance?mode='+mode));
    const j=await r.json();
    const items=j.series.map(s=>{
      const d=new Date(s.t.replace(' ','T'));
      let lbl;
      if(mode==='month') lbl=(d.getMonth()+1)+'월';
      else if(mode==='week') lbl=(d.getMonth()+1)+'/'+d.getDate();
      else lbl=(d.getMonth()+1)+'/'+d.getDate();
      return {lbl, v:s.v};
    });
    drawBarChart($('cDist'), items, {color:'#38bdf8',
      yFmt:v=>Math.round(v)+'km',
      topFmt:v=>Math.round(v)});
  }catch(e){}
}

loadBatt('hour'); loadHome(); loadDist('day');
setInterval(()=>{loadBatt('hour'); loadHome(); loadDist('day');}, 300000);
</script>
</body>
</html>"""


# ════════════════════════════════════════════════════════════════
#  갤럭시워치(Wear OS) 대시보드 — 480×480 원형 기준
#  기초 스캐폴딩: 스테이지 스케일 맞춤, 화면전환 프레임워크, 연결확인
# ════════════════════════════════════════════════════════════════
WATCH_HTML = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no, viewport-fit=cover">
<title>Tesla Watch</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; -webkit-user-select:none; user-select:none; }
  html, body { width:100%; height:100%; background:#151515; overflow:hidden;
    font-family:-apple-system,'Roboto','Noto Sans KR',sans-serif; color:#e8e8e8;
    display:flex; align-items:center; justify-content:center; }
  /* 480 기준 디자인을 실제 워치 해상도에 맞춰 축소(잘림 방지) */
  #stage { position:relative; width:480px; height:480px; border-radius:50%;
    overflow:hidden; background:#151515; transform-origin:center center; }
  .screen { position:absolute; inset:0; display:none; }
  .screen.on { display:block; }
  .bg { position:absolute; inset:0; width:480px; height:480px; object-fit:cover; pointer-events:none; }
  /* 부팅/연결 오버레이 */
  #boot { position:absolute; inset:0; z-index:60; background:#151515;
    display:flex; flex-direction:column; align-items:center; justify-content:center; gap:20px; }
  .spin { width:46px; height:46px; border:4px solid rgba(255,255,255,.12);
    border-top-color:#e82127; border-radius:50%; animation:sp .9s linear infinite; }
  @keyframes sp { to { transform:rotate(360deg); } }
  #bootmsg { color:#8a8c8b; font-size:15px; letter-spacing:1px; }
  /* 색상 토큰 (꺼짐 회색 / 켜짐 색상) */
  :root { --off:#8a8c8b; }

  /* ── 화면1: 메인 ── */
  .batt { position:absolute; left:50%; top:14px; transform:translateX(-50%);
    display:flex; flex-direction:column; align-items:center; gap:3px; z-index:5; }
  .battpct { font-size:23px; font-weight:700; color:#f0f0f0; letter-spacing:.5px; }
  .navlbl { position:absolute; transform:translateX(-50%); color:#dcdcdc;
    font-size:25px; font-weight:600; padding:10px 14px; cursor:pointer; z-index:5; }
  .navlbl:active { color:#fff; }
  .dline { position:absolute; width:0; border-left:2px dashed #7d7f7e;
    transform:translateX(-1px); pointer-events:none; z-index:4; }
</style>
</head>
<body>
  <div id="stage">
    <div id="scr1" class="screen on">
      <img class="bg" src="/w/s1.png">
      <!-- 배터리 (상단 중앙) -->
      <div class="batt">
        <svg viewBox="0 0 48 24" width="46" height="23">
          <rect x="1" y="4" width="40" height="16" rx="3" ry="3" fill="none" stroke="#e8e8e8" stroke-width="2"/>
          <rect x="43" y="9" width="3.5" height="6" rx="1" fill="#e8e8e8"/>
          <rect id="battfill" x="4" y="7" width="0" height="10" rx="1.5" fill="#b8bab9"/>
        </svg>
        <div class="battpct"><span id="s1soc">–</span>%</div>
      </div>
      <!-- 제어 (좌) : 차량 본네트 → 위로 라벨 -->
      <div class="dline" style="left:80px; top:134px; height:120px;"></div>
      <div class="navlbl" id="navCtl" style="left:80px; top:76px;">제어</div>
      <!-- 상태 (우) -->
      <div class="dline" style="left:376px; top:108px; height:78px;"></div>
      <div class="navlbl" id="navStat" style="left:376px; top:52px;">상태</div>
      <!-- 공조 (하) : 차량 → 아래로 라벨 -->
      <div class="dline" style="left:282px; top:300px; height:98px;"></div>
      <div class="navlbl" id="navHvac" style="left:282px; top:404px;">공조</div>
    </div>
    <div id="scr2" class="screen"><img class="bg" src="/w/s2.png"></div>
    <div id="scr3" class="screen"><img class="bg" src="/w/s3.png"></div>
    <div id="boot"><div class="spin"></div><div id="bootmsg">연결 중…</div></div>
  </div>
<script>
const KEY = new URLSearchParams(location.search).get('key') || '';
function q(p){ return p + (p.includes('?')?'&':'?') + 'key=' + encodeURIComponent(KEY); }
async function api(p){ const r = await fetch(q(p)); if(!r.ok) throw new Error(r.status); return r.json(); }
async function post(p){ const r = await fetch(q(p), {method:'POST'}); return r.ok; }

// 480 디자인을 실제 화면에 꽉 맞게(원형 잘림 없이) 스케일
function fit(){
  const w = window.innerWidth || 0, h = window.innerHeight || 0;
  const s = Math.min(w, h) / 480;
  if (s > 0.05) document.getElementById('stage').style.transform = 'scale(' + s + ')';
}
fit();
window.addEventListener('resize', fit);
window.addEventListener('load', fit);
setTimeout(fit, 120); setTimeout(fit, 400);

// 화면 전환 프레임워크
let CUR = 1;
function show(n){
  CUR = n;
  for(const i of [1,2,3]) document.getElementById('scr'+i).classList.toggle('on', i===n);
}

// 워치 물리 뒤로가기 → 메인 아니면 메인으로, 메인이면 앱 종료 (네이티브에서 호출)
window.handleBack = function(){
  if (CUR !== 1) { show(1); }
  else { try { AndroidApp.exit(); } catch(e) {} }
};

// 차량 상태 캐시
let STATE = null;

// 화면1: 배터리 렌더
function renderMain(){
  const soc = (STATE && STATE.charge_state) ? STATE.charge_state.battery_level : null;
  document.getElementById('s1soc').textContent = (soc!=null ? soc : '–');
  const f = document.getElementById('battfill');
  if (soc!=null) {
    f.setAttribute('width', (34 * Math.max(0,Math.min(100,soc)) / 100).toFixed(1));
    f.setAttribute('fill', soc<=15 ? '#e82127' : '#b8bab9');
  } else {
    f.setAttribute('width', 0);
  }
}

// 상태 조회
async function refreshState(){
  try {
    const j = await api('/api/state');
    STATE = j.response || {};
    renderMain();
    return true;
  } catch(e) { return false; }
}

// 부팅
async function boot(){
  const ok = await refreshState();
  if (ok) {
    document.getElementById('bootmsg').textContent = '연결됨';
    setTimeout(function(){ document.getElementById('boot').style.display='none'; }, 500);
  } else {
    document.getElementById('bootmsg').textContent = '연결 실패 — 재시도 중';
    setTimeout(boot, 2500);
  }
}

// 네비게이션 (제어→화면2, 공조→화면3, 상태→동작없음)
document.getElementById('navCtl').addEventListener('click', function(){ show(2); });
document.getElementById('navHvac').addEventListener('click', function(){ show(3); });
document.getElementById('navStat').addEventListener('click', function(){ /* 상태: 미구현 */ });

boot();
setInterval(refreshState, 60000);
</script>
</body>
</html>"""
