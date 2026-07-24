# Tesla Bridge

Tesla Fleet API와 SmartThings, 그리고 Android/Wear OS 앱을 하나로 연결하는 자체 호스팅 백엔드입니다.

폰 앱과 워치 앱 모두 이 서버 하나를 바라봅니다. Tesla 공식 앱을 대체하는 것이 목적이 아니라, SmartThings 자동화에 차량 상태를 끌어오고 커스텀 앱에서 명령을 보내기 위해 만들었습니다.

---

## 구성

```
인터넷
  └─ Caddy (HTTPS, Let's Encrypt 자동 발급)
       └─ FastAPI Bridge  ←── Android 폰 앱 (WebView)
       └─ FastAPI Bridge  ←── Galaxy Watch 앱 (Native)
            ├─ Tesla Fleet API  (차량 상태 읽기)
            ├─ tesla-http-proxy (명령 서명 → Fleet API 전송)
            └─ SmartThings Edge Driver (가상 디바이스 동기화)
```

- **Caddy**: 외부 HTTPS 처리. Let's Encrypt 인증서를 자동으로 발급·갱신하고 브릿지로 프록시
- **FastAPI Bridge**: 핵심 서버. OAuth 토큰 관리, 차량 상태 캐싱, 명령 라우팅, 웹 대시보드 서빙
- **tesla-http-proxy**: 2021년 이후 차량에 필수인 서명 명령을 처리하는 Tesla 공식 프록시 (Docker)
- **SmartThings Edge Driver**: 브릿지 REST API를 폴링해서 SmartThings 가상 디바이스 상태를 업데이트

모두 Docker Compose로 묶여서 뜹니다.

---

## 브릿지 API 엔드포인트

모든 API는 `?key=BRIDGE_TOKEN` 인증 필요.

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/` | 웹 대시보드 (폰 앱이 WebView로 로드) |
| GET | `/api/state` | 차량 전체 상태 (배터리, 위치, 온도 등) |
| POST | `/api/command/{action}` | 명령 전송 (lock, unlock, climate_on/off, wake 등) |
| POST | `/api/set_temp` | 목표 온도 설정 |
| POST | `/api/seat` | 좌석 히터 단계 설정 (0~3) |
| POST | `/api/steering` | 스티어링 휠 히터 ON/OFF |
| POST | `/api/trunk` | 프렁크/트렁크 열기 |
| POST | `/api/charge_limit` | 충전 한도 설정 |
| POST | `/api/sentry` | 센트리 모드 ON/OFF |
| GET | `/app.apk` | 폰 앱 APK 다운로드 (key 인증) |
| GET | `/watch.apk` | 워치 앱 APK 다운로드 (key 인증) |
| GET | `/login` | Tesla OAuth 로그인 시작 |

---

## 차량 슬립 처리

차량이 슬립 상태일 때 `/api/state`는 마지막으로 캐싱된 데이터를 `"cached": true`와 함께 반환합니다. 폰 앱과 워치 앱은 이 값을 보고 슬립 여부를 판단하고, 명령 전송 전에 `wake`를 먼저 호출한 뒤 실제 응답이 올 때까지 대기합니다.

---

## SmartThings 연동

`smartthings/` 폴더에 Edge 드라이버 4개 버전과 가상 디바이스 생성·설정 스크립트가 있습니다. 최종 버전은 `driver4`입니다.

드라이버는 브릿지의 `/api/state`를 주기적으로 폴링해서 SmartThings 앱에 배터리, 충전 상태, 잠금 상태, 온도 등을 반영합니다. SmartThings 자동화에서 차량 상태를 조건으로 쓰거나 명령을 트리거하는 데 활용됩니다.

---

## 셋업

**1. 환경변수 파일 작성**
```bash
cp bridge/.env.example bridge/.env
# bridge/.env 편집 — CLIENT_ID, CLIENT_SECRET, VIN 등 입력
```

**2. Tesla 개발자 앱 등록**  
[developer.tesla.com](https://developer.tesla.com) 에서 앱 생성 → CLIENT_ID/SECRET 발급  
redirect URI: `https://your-domain/callback`

**3. Fleet API 공개키 등록**
```bash
# keys/private-key.pem, certs/ 생성 후
# docker compose up caddy 로 공개키 서빙 → Tesla에 등록
```

**4. 실행**
```bash
docker compose --profile full up -d
```

**5. OAuth 로그인**  
브라우저에서 `https://your-domain/login` 접속 → Tesla 계정으로 로그인  
토큰은 `bridge/data/tokens.json`에 저장되고 만료 전 자동 갱신됩니다.

---

## 보안

`.gitignore`로 제외되는 파일들:

- `bridge/.env` — 모든 시크릿 값
- `bridge/data/tokens.json` — Tesla OAuth 토큰
- `keys/private-key.pem` — Fleet API 명령 서명 개인키
- `certs/` — TLS 인증서
- `smartthings/_*.json` — SmartThings 기기 ID 및 런타임 상태
