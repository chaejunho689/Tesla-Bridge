local log = require "log"
log.warn("### mini: load start")
local Driver = require "st.driver"
local caps = require "st.capabilities"
local cosock = require "cosock"
local http = cosock.asyncify "socket.http"
local ltn12 = require "ltn12"

local function ping(tag)
  cosock.spawn(function()
    local r = {}
    pcall(function()
      http.request{ url = "http://192.168.50.175:8787/hubping-" .. tag, sink = ltn12.sink.table(r) }
    end)
  end, "ping")
end

local function discovery(driver, opts, cont)
  log.warn("### mini: discovery called")
  ping("mini-discovery")
  driver:try_create_device({
    type = "LAN",
    device_network_id = "tesla-mini-1",
    label = "홍차 미니",
    profile = "mini",
  })
end

local mini = Driver("tesla-mini", {
  discovery = discovery,
  capability_handlers = {
    [caps.switch.ID] = {
      [caps.switch.commands.on.NAME] = function(d, dev, c) dev:emit_event(caps.switch.switch.on()) end,
      [caps.switch.commands.off.NAME] = function(d, dev, c) dev:emit_event(caps.switch.switch.off()) end,
    },
  },
})

cosock.spawn(function()
  cosock.socket.sleep(2)
  if #mini:get_devices() == 0 then
    log.warn("### mini: auto-create")
    mini:try_create_device({ type="LAN", device_network_id="tesla-mini-1", label="홍차 미니", profile="mini" })
  end
end, "autocreate")

ping("mini-load")
mini:run()
