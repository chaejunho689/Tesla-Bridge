local log = require "log"
log.warn("### tesla-bridge: module load start")
local Driver = require "st.driver"
local caps = require "st.capabilities"
local cosock = require "cosock"
local ltn12 = require "ltn12"
local ok_json, json = pcall(require, "st.json")
if not ok_json then
  log.warn("### st.json 없음, dkjson 사용")
  json = require "dkjson"
end
local http = cosock.asyncify "socket.http"
log.warn("### tesla-bridge: requires OK")

http.TIMEOUT = 30

-- ── 브릿지 HTTP 호출 ─────────────────────────────────────────────
local function bridge_request(device, method, path, query)
  local addr = device.preferences.bridgeAddress or "192.168.50.175:8787"
  local token = device.preferences.bridgeToken or ""
  query = query or {}
  query.key = token
  if device.preferences.vin and device.preferences.vin ~= "" and query.vin == nil then
    query.vin = device.preferences.vin
  end
  local qs = {}
  for k, v in pairs(query) do
    qs[#qs + 1] = tostring(k) .. "=" .. tostring(v)
  end
  local url = "http://" .. addr .. path .. "?" .. table.concat(qs, "&")

  local resp = {}
  local _, code = http.request {
    url = url,
    method = method,
    headers = { ["Content-Type"] = "application/json", ["Content-Length"] = "0" },
    source = ltn12.source.string(""),
    sink = ltn12.sink.table(resp),
  }
  return code, table.concat(resp)
end

local function api_command(device, action)
  local code, body = bridge_request(device, "POST", "/api/command/" .. action)
  log.info(string.format("cmd %s -> %s %s", action, tostring(code), tostring(body)))
  return code == 200
end

-- ── 상태 갱신 ────────────────────────────────────────────────────
local function refresh_state(device)
  local code, body = bridge_request(device, "GET", "/api/state")
  if code ~= 200 then
    log.warn("state fetch failed: " .. tostring(code) .. " " .. tostring(body))
    return
  end
  local ok, data = pcall(json.decode, body)
  if not ok or not data.response then
    log.warn("state parse failed")
    return
  end
  local r = data.response
  local comps = device.profile.components
  local cs = r.charge_state or {}
  local clim = r.climate_state or {}
  local vs = r.vehicle_state or {}

  if cs.battery_level ~= nil then
    device:emit_component_event(comps.main, caps.battery.battery(math.floor(cs.battery_level)))
  end
  if clim.inside_temp ~= nil then
    device:emit_component_event(comps.main,
      caps.temperatureMeasurement.temperature({ value = clim.inside_temp, unit = "C" }))
  end
  if vs.locked ~= nil then
    device:emit_component_event(comps.main,
      vs.locked and caps.lock.lock.locked() or caps.lock.lock.unlocked())
  end
  device:emit_component_event(comps.climate,
    clim.is_climate_on and caps.switch.switch.on() or caps.switch.switch.off())
  device:emit_component_event(comps.charging,
    (cs.charging_state == "Charging") and caps.switch.switch.on() or caps.switch.switch.off())
end

-- ── 폴링 스케줄 ──────────────────────────────────────────────────
local function reschedule_poll(driver, device)
  for _, t in pairs(device.thread.timers or {}) do device.thread:cancel_timer(t) end
  local iv = tonumber(device.preferences.pollInterval) or 0
  if iv and iv > 0 then
    device.thread:call_on_schedule(iv, function() refresh_state(device) end, "tesla-poll")
    log.info("polling every " .. iv .. "s")
  end
end

-- ── capability 핸들러 ────────────────────────────────────────────
local function handle_lock(driver, device, command)
  if api_command(device, "lock") then
    device:emit_component_event(device.profile.components.main, caps.lock.lock.locked())
  end
end

local function handle_unlock(driver, device, command)
  if api_command(device, "unlock") then
    device:emit_component_event(device.profile.components.main, caps.lock.lock.unlocked())
  end
end

local function handle_switch_on(driver, device, command)
  local comp = command.component
  local action = (comp == "charging") and "charge_start" or "climate_on"
  if api_command(device, action) then
    device:emit_component_event(device.profile.components[comp], caps.switch.switch.on())
  end
end

local function handle_switch_off(driver, device, command)
  local comp = command.component
  local action = (comp == "charging") and "charge_stop" or "climate_off"
  if api_command(device, action) then
    device:emit_component_event(device.profile.components[comp], caps.switch.switch.off())
  end
end

local function handle_refresh(driver, device, command)
  refresh_state(device)
end

-- ── 라이프사이클 ─────────────────────────────────────────────────
local function device_init(driver, device)
  log.info("init: " .. device.label)
  reschedule_poll(driver, device)
end

local function device_added(driver, device)
  -- 초기 기본 상태
  device:emit_component_event(device.profile.components.main, caps.lock.lock.unlocked())
  device:emit_component_event(device.profile.components.climate, caps.switch.switch.off())
  device:emit_component_event(device.profile.components.charging, caps.switch.switch.off())
end

local function info_changed(driver, device, event, args)
  reschedule_poll(driver, device)
end

-- ── 디스커버리 (디바이스 생성) ──────────────────────────────────
local function discovery(driver, opts, should_continue)
  if driver:get_devices() and #driver:get_devices() > 0 then
    log.info("device already exists, skip discovery")
    return
  end
  driver:try_create_device({
    type = "LAN",
    device_network_id = "tesla-bridge-vehicle",
    label = "홍차 (Tesla)",
    profile = "tesla-vehicle",
    manufacturer = "Tesla",
    model = "Model 3",
    vendor_provided_label = "Tesla Bridge",
  })
end

-- ── 자동 디바이스 생성 메타데이터 ───────────────────────────────
local DEVICE_META = {
  type = "LAN",
  device_network_id = "tesla-bridge-vehicle",
  label = "홍차 (Tesla)",
  profile = "tesla-vehicle",
  manufacturer = "Tesla",
  model = "Model 3",
  vendor_provided_label = "Tesla Bridge",
}

-- ── 드라이버 정의 ────────────────────────────────────────────────
local tesla_driver = Driver("tesla-bridge", {
  discovery = discovery,
  lifecycle_handlers = {
    init = device_init,
    added = device_added,
    infoChanged = info_changed,
  },
  capability_handlers = {
    [caps.lock.ID] = {
      [caps.lock.commands.lock.NAME] = handle_lock,
      [caps.lock.commands.unlock.NAME] = handle_unlock,
    },
    [caps.switch.ID] = {
      [caps.switch.commands.on.NAME] = handle_switch_on,
      [caps.switch.commands.off.NAME] = handle_switch_off,
    },
    [caps.refresh.ID] = {
      [caps.refresh.commands.refresh.NAME] = handle_refresh,
    },
  },
})

-- ── 로드 확인용 핑 + 스캔없이 디바이스 자동생성 ────────────────
cosock.spawn(function()
  -- 브릿지에 로드 신호 (브릿지 로그로 구동 확인)
  local resp = {}
  pcall(function()
    http.request {
      url = "http://192.168.50.175:8787/hubping-driverload",
      sink = ltn12.sink.table(resp),
    }
  end)
  log.warn("### tesla-bridge: hubping sent")
  -- 3초 후 디바이스 없으면 자동 생성 (스캔 불필요)
  cosock.socket.sleep(3)
  local ok_dev, devs = pcall(function() return tesla_driver:get_devices() end)
  if ok_dev and #devs == 0 then
    log.warn("### tesla-bridge: auto-creating device")
    tesla_driver:try_create_device(DEVICE_META)
  else
    log.warn("### tesla-bridge: device exists (" .. tostring(ok_dev and #devs) .. ")")
  end
end, "tesla-loadcheck")

tesla_driver:run()
