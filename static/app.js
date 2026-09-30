const $ = (id) => document.getElementById(id);

let state = null;
let points = [];
let minutes = 60;
let historyMinutes = 60;
let series = "voltage";
let hiddenLines = {};
let chartHoverX = null;
let sparkHoverX = null;
let sparkOn = { in: true, out: true };
let sparkView = { window: 900, follow: true, end: 0 };
let sparkEase = { units: {}, live: {}, shown: {}, lastT: 0, at: 0 };
let sparkDrag = null;
let sparkRaf = 0;
let chartPrefsReady = false;
let chartSaveTimer = 0;
let audioCtx = null;
let wasOnBattery = false;
let lastVoltRank = 0;
let toastTimer = 0;
let sessionIn = { min: null, max: null };
let reportKind = "summary";
let reportDays = [];
let reportDate = "";
let energyPreset = "today";
let energyData = null;
let energyRange = null;
let energyView = { t0: 0, t1: 0 };
let energyHoverX = null;
let energyDrag = null;
let energyCal = { month: new Date(), focus: "from" };
let energyDay = "";
let alertReady = false;
let lastEventT = 0;
let lastAlarmKey = "";
let notices = [];
let noticeUnread = 0;
let noticeTimer = 0;
let simCode = "";
let simUntil = 0;
let lastAlertCode = "";

const ALERT_TITLES = {
  on_battery: "Сеть пропала",
  on_battery_clear: "Сеть вернулась",
  battery_low: "Батарея садится",
  battery_low_clear: "Батарея в норме",
  fault: "Неисправность",
  fault_clear: "Авария снята",
  link_lost: "Нет связи с ИБП",
  link: "ИБП на связи",
  battery_loose: "Батареи отключены",
  battery_loose_clear: "Пакет в норме",
  battery_swap: "Пакет сменился",
  voltage_low: "Сеть понижена",
  voltage_high: "Сеть повышена",
  voltage_crit_low: "Сеть слишком низкая",
  voltage_crit_high: "Сеть слишком высокая",
  voltage_low_clear: "Сеть в норме",
  voltage_high_clear: "Сеть в норме",
  voltage_crit_low_clear: "Спад снят",
  voltage_crit_high_clear: "Скачок снят",
  battery_boost: "Подпитка с батареи",
  battery_boost_clear: "Подпитка снята",
  startup: "UPS запущен",
  shutdown: "UPS закрыт",
  pc_shutdown: "Выключение ПК",
  pc_off: "Выключить ПК",
  pc_reboot: "Перезагрузка",
  pc_sleep: "Сон",
  pc_hibernate: "Гибернация",
  app_restart: "Перезапуск UPS",
};

const LINE_COLORS = {
  in: "#e0b06a",
  out: "#4de2ff",
  load: "#ff7a3d",
  batt: "#3dbe7a",
  soc: "#b388ff",
  hz: "#dce7ff",
  temp: "#e15d4c",
};

const SERIES = {
  voltage: {
    unit: "В",
    lines: [
      { key: "in", color: LINE_COLORS.in, name: "вход" },
      { key: "out", color: LINE_COLORS.out, name: "выход" },
    ],
  },
  load: { unit: "Вт", lines: [{ key: "load", color: LINE_COLORS.load, name: "нагрузка" }] },
  battery: { unit: "В", lines: [{ key: "batt", color: LINE_COLORS.batt, name: "батарея" }] },
  charge: { unit: "%", lines: [{ key: "soc", color: LINE_COLORS.soc, name: "заряд" }] },
  freq: { unit: "Гц", lines: [{ key: "hz", color: LINE_COLORS.hz, name: "частота" }] },
  temp: { unit: "°C", lines: [{ key: "temp", color: LINE_COLORS.temp, name: "температура" }] },
};

const TG_EVENTS = [
  { group: "Сеть", items: [
    { code: "on_battery", title: "Сеть пропала" },
    { code: "on_battery_clear", title: "Сеть вернулась" },
    { code: "voltage_low", title: "Сеть понижена" },
    { code: "voltage_low_clear", title: "Понижение снято" },
    { code: "voltage_high", title: "Сеть повышена" },
    { code: "voltage_high_clear", title: "Повышение снято" },
    { code: "voltage_crit_low", title: "Сеть слишком низкая" },
    { code: "voltage_crit_low_clear", title: "Спад снят" },
    { code: "voltage_crit_high", title: "Сеть слишком высокая" },
    { code: "voltage_crit_high_clear", title: "Скачок снят" },
    { code: "battery_boost", title: "Подпитка с батареи" },
    { code: "battery_boost_clear", title: "Подпитка снята" },
  ]},
  { group: "Батарея", items: [
    { code: "battery_low", title: "Батарея садится" },
    { code: "battery_low_clear", title: "Батарея в норме" },
    { code: "battery_loose", title: "Батареи отключены" },
    { code: "battery_loose_clear", title: "Пакет в норме" },
    { code: "battery_swap", title: "Пакет сменился" },
  ]},
  { group: "ИБП", items: [
    { code: "fault", title: "Неисправность" },
    { code: "fault_clear", title: "Авария снята" },
    { code: "link_lost", title: "Нет связи с ИБП" },
    { code: "link", title: "ИБП на связи" },
  ]},
  { group: "ПК", items: [
    { code: "pc_shutdown", title: "Отсчёт выключения" },
    { code: "pc_off", title: "Выключить ПК" },
    { code: "pc_reboot", title: "Перезагрузка" },
    { code: "pc_sleep", title: "Сон" },
    { code: "pc_hibernate", title: "Гибернация" },
  ]},
  { group: "Программа", items: [
    { code: "startup", title: "UPS запущен" },
    { code: "shutdown", title: "UPS закрыт" },
    { code: "app_restart", title: "Перезапуск UPS" },
  ]},
];

const TG_ACTIONS = [
  { group: "ПК", items: [
    { code: "pc_off", title: "Выключить ПК" },
    { code: "pc_reboot", title: "Перезагрузка" },
    { code: "pc_sleep", title: "Сон" },
    { code: "pc_hibernate", title: "Гибернация" },
    { code: "cancel_shutdown", title: "Отмена выключения" },
  ]},
  { group: "ПО", items: [
    { code: "screenshot", title: "Скриншот" },
    { code: "app_restart", title: "Перезапуск программы" },
  ]},
  { group: "Отчёты", items: [
    { code: "status", title: "Состояние" },
    { code: "events", title: "Журнал" },
    { code: "day_report", title: "Отчёт дня" },
    { code: "summary", title: "Сводка" },
  ]},
];

const SPARK_LINES = [
  { key: "in", color: LINE_COLORS.in, name: "вход", unit: "В" },
  { key: "out", color: LINE_COLORS.out, name: "выход", unit: "В" },
  { key: "load", color: LINE_COLORS.load, name: "нагрузка", unit: "Вт" },
  { key: "batt", color: LINE_COLORS.batt, name: "батарея", unit: "В" },
  { key: "soc", color: LINE_COLORS.soc, name: "заряд", unit: "%" },
  { key: "hz", color: LINE_COLORS.hz, name: "частота", unit: "Гц" },
  { key: "temp", color: LINE_COLORS.temp, name: "температура", unit: "°C" },
];

const BOOST_DELTA = 20;

function pointBoost(point) {
  if (!point) return false;
  if (point.boost || point.battery_boost) return true;
  const inn = point.in != null ? point.in : point.input_v;
  const out = point.out != null ? point.out : point.output_v;
  if (inn == null || out == null) return false;
  return Number(out) - Number(inn) > BOOST_DELTA;
}

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => showTab(button.dataset.tab));
});
$("openSettings").addEventListener("click", openSettings);
$("closeSettings").addEventListener("click", () => $("settings").close());
$("openLab").addEventListener("click", openLab);
$("closeLab").addEventListener("click", () => $("labDialog").close());
$("stopSim").addEventListener("click", stopSim);
$("schStopSim").addEventListener("click", stopSim);
$("schCancelOff").addEventListener("click", cancelShutdown);
$("settingsForm").addEventListener("submit", saveSettings);
$("telegramForm").addEventListener("submit", saveTelegram);
$("tgPill").addEventListener("click", () => showTab("telegram"));
$("gateRescan").addEventListener("click", () => command("rescan"));
$("gateScan").addEventListener("click", () => openScan(true));
if ($("devScan")) $("devScan").addEventListener("click", () => openScan(true));
$("closeScan").addEventListener("click", () => $("scanDialog").close());
$("scanAgain").addEventListener("click", () => openScan(true));
$("leaveDemo").addEventListener("click", () => command("live"));
$("btnSimulate").addEventListener("click", () => command("simulate"));
$("btnCancelShutdown").addEventListener("click", cancelShutdown);
$("tgNewPair").addEventListener("click", () => telegramAction("telegram_new_pair"));
$("tgUnlink").addEventListener("click", () => telegramAction("telegram_unlink"));
$("tgChatList").addEventListener("click", onChatListClick);
$("closeChat").addEventListener("click", () => $("chatDialog").close());
$("chatGuest").addEventListener("change", onChatRoleChange);
$("chatAdmin").addEventListener("change", onChatRoleChange);
$("chatEventList").addEventListener("change", saveChatRules);
$("chatActionList").addEventListener("change", saveChatRules);
$("windowsNotify").addEventListener("change", saveTelegramFlags);
$("repSummary").addEventListener("click", () => setReportKind("summary"));
$("repDay").addEventListener("click", () => setReportKind("day"));
$("repPrev").addEventListener("click", () => shiftReportDay(-1));
$("repNext").addEventListener("click", () => shiftReportDay(1));
$("repRefresh").addEventListener("click", () => loadReport(true));
$("repFolder").addEventListener("click", openReportFolder);
$("repDate").addEventListener("change", () => {
  reportDate = $("repDate").value.trim();
  setReportKind("day");
});
document.querySelectorAll("[data-en]").forEach((button) => {
  button.addEventListener("click", () => setEnergyPreset(button.dataset.en));
});
$("enPrev").addEventListener("click", () => shiftEnergy(-1));
$("enNext").addEventListener("click", () => shiftEnergy(1));
$("enRangeBtn").addEventListener("click", toggleEnergyPop);
$("calPrev").addEventListener("click", () => shiftCalMonth(-1));
$("calNext").addEventListener("click", () => shiftCalMonth(1));
$("calDone").addEventListener("click", () => closeEnergyPop(true));
$("calGrid").addEventListener("click", onCalDayClick);
document.querySelectorAll(".time-spin button").forEach((button) => {
  button.addEventListener("click", () => spinEnergyTime(button.closest("[data-which]").dataset.which, button.dataset.part, Number(button.dataset.delta)));
});
document.querySelectorAll(".cal-quick button").forEach((button) => {
  button.addEventListener("click", () => applyEnergyQuick(button.dataset.quick));
});
$("enChart").addEventListener("mousemove", hoverEnergy);
$("enChart").addEventListener("mouseleave", () => {
  energyHoverX = null;
  hidePlotTip();
  drawEnergyChart();
});
$("enChart").addEventListener("wheel", onEnergyWheel, { passive: false });
$("enChart").addEventListener("pointerdown", onEnergyDown);
$("enChart").addEventListener("dblclick", resetEnergyView);
$("enZoomIn").addEventListener("click", () => zoomEnergy(1 / 1.35));
$("enZoomOut").addEventListener("click", () => zoomEnergy(1.35));
$("enZoomReset").addEventListener("click", resetEnergyView);
$("enDays").addEventListener("click", onEnergyDayClick);
document.addEventListener("pointerdown", (event) => {
  const pop = $("enPop");
  if (!pop || pop.hidden) return;
  if (pop.contains(event.target) || $("enRangeBtn").contains(event.target) || $("enPrev").contains(event.target) || $("enNext").contains(event.target)) return;
  closeEnergyPop(true);
});
window.addEventListener("pointermove", onEnergyMove);
window.addEventListener("pointerup", onEnergyUp);
$("tgClear").addEventListener("click", () => telegramAction("telegram_clear"));
$("tgCopyPair").addEventListener("click", copyPairCommand);
$("quitTray").addEventListener("click", () => sendQuit("tray"));
$("quitClose").addEventListener("click", () => sendQuit("close"));
$("quitStay").addEventListener("click", () => sendQuit("stay"));
$("quitDialog").addEventListener("cancel", (event) => {
  event.preventDefault();
  sendQuit("stay");
});
$("noticeBtn").addEventListener("click", toggleInbox);
$("noticeClear").addEventListener("click", clearInbox);
$("noticeList").addEventListener("click", (event) => {
  const row = event.target.closest("li[data-index]");
  if (!row) return;
  const item = notices[Number(row.dataset.index)];
  if (item) openAlert(item, { keep: true });
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if ($("alertModal") && !$("alertModal").hidden) closeAlert();
    if ($("noticeInbox") && !$("noticeInbox").hidden) $("noticeInbox").hidden = true;
  }
});
$("eventList").addEventListener("click", (event) => {
  const row = event.target.closest("li[data-code]");
  if (!row) return;
  openAlert({
    code: row.dataset.code,
    level: row.dataset.level,
    message: row.dataset.message,
    t: Number(row.dataset.t) || Date.now() / 1000,
  });
});
window.showQuitDialog = showQuitDialog;
window.prepareForScreenshot = prepareForScreenshot;
window.restoreAfterScreenshot = restoreAfterScreenshot;
window.reloadLiveHistory = reloadLiveHistory;
window.openNoticeInbox = openNoticeInbox;
window.closeNoticeInbox = closeNoticeInbox;
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") reloadLiveHistory();
});
$("tgDigest").addEventListener("change", saveTelegramFlags);
$("tgEventList").addEventListener("change", (event) => {
  const input = event.target;
  if (!(input instanceof HTMLInputElement)) return;
  const row = input.closest(".tg-event");
  if (row) {
    const botOn = row.querySelector('[data-kind="on"]');
    const trayOn = row.querySelector('[data-kind="tray"]');
    row.classList.toggle("is-off", !(botOn && botOn.checked) && !(trayOn && trayOn.checked));
  }
  input.blur();
  saveTelegramFlags();
});
$("tgHourUp").addEventListener("click", () => nudgeClock("hour", 1));
$("tgHourDown").addEventListener("click", () => nudgeClock("hour", -1));
$("tgMinUp").addEventListener("click", () => nudgeClock("minute", 5));
$("tgMinDown").addEventListener("click", () => nudgeClock("minute", -5));
["tgDigestHour", "tgDigestMinute"].forEach((id) => {
  $(id).addEventListener("change", () => {
    clampClockInputs();
    saveTelegramFlags();
  });
  $(id).addEventListener("blur", () => {
    clampClockInputs();
    saveTelegramFlags();
  });
});
$("ranges").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  minutes = Number(button.dataset.minutes);
  document.querySelectorAll("#ranges button").forEach((item) => item.classList.toggle("on", item === button));
  loadHistory();
  persistChartPrefs();
});
$("series").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  series = button.dataset.series;
  document.querySelectorAll("#series button").forEach((item) => item.classList.toggle("on", item === button));
  drawChart();
  persistChartPrefs();
});
$("chart").addEventListener("mousemove", hoverChart);
$("chart").addEventListener("mouseleave", () => {
  chartHoverX = null;
  hidePlotTip();
  $("chartTip").textContent = "Наведите курсор — увидите точные значения";
  drawChart(false);
});
$("chart").addEventListener("wheel", (event) => {
  event.preventDefault();
  const order = [15, 60, 360, 1440];
  const index = order.indexOf(minutes);
  const next = event.deltaY > 0 ? Math.min(order.length - 1, index + 1) : Math.max(0, index - 1);
  if (order[next] === minutes) return;
  minutes = order[next];
  document.querySelectorAll("#ranges button").forEach((item) => item.classList.toggle("on", Number(item.dataset.minutes) === minutes));
  loadHistory();
  persistChartPrefs();
}, { passive: false });
$("chartLegend").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  hiddenLines[button.dataset.key] = !hiddenLines[button.dataset.key];
  drawChart();
});
$("spark").addEventListener("mousemove", hoverSpark);
$("spark").addEventListener("mouseleave", () => {
  sparkHoverX = null;
  hidePlotTip();
  refreshSparkNote();
  drawSpark();
});
$("spark").addEventListener("wheel", onSparkWheel, { passive: false });
$("spark").addEventListener("dblclick", resetSpark);
$("spark").addEventListener("pointerdown", onSparkDown);
window.addEventListener("pointermove", onSparkMove);
window.addEventListener("pointerup", onSparkUp);
$("sparkZoomIn").addEventListener("click", () => zoomSpark(1 / 1.35));
$("sparkZoomOut").addEventListener("click", () => zoomSpark(1.35));
$("sparkPanLeft").addEventListener("click", () => panSpark(-0.35));
$("sparkPanRight").addEventListener("click", () => panSpark(0.35));
$("sparkReset").addEventListener("click", resetSpark);
$("sparkLines").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-key]");
  if (!button) return;
  const key = button.dataset.key;
  const on = SPARK_LINES.filter((line) => sparkOn[line.key]);
  if (sparkOn[key] && on.length < 2) return;
  sparkOn[key] = !sparkOn[key];
  refreshSparkUi();
  persistChartPrefs();
});
syncPackCells(2, 1);
refreshSparkUi();
if (!sparkRaf) sparkRaf = requestAnimationFrame(tickSpark);
window.addEventListener("resize", () => {
  drawChart();
  drawSpark();
  layoutSchemeTraces();
  if (document.querySelector("#view-energy.on")) drawEnergyChart();
});
requestAnimationFrame(layoutSchemeTraces);
document.addEventListener("pointerdown", () => {
  if (!audioCtx) audioCtx = new AudioContext();
  if (audioCtx.state === "suspended") audioCtx.resume();
}, { once: true });

setInterval(() => {
  const node = $("freshness");
  if (!node || !state || !state.reading) return;
  const age = Math.max(0, Math.round(Date.now() / 1000 - state.reading.updated_at));
  const stamp = clockTime(state.reading.updated_at);
  node.textContent = age < 3 ? `живой ответ ${stamp}` : `последний ответ ${stamp}, ${age} с назад`;
}, 500);
showTab("overview");
loadHistory();
loadEvents();
let pulling = false;
pull();
setInterval(pull, 1000);
setInterval(() => {
  if (!state || !state.reading) return;
  if (!simUntil && (state.reading.shutdown_seconds == null)) return;
  paint(state.reading, state);
}, 250);

async function pull() {
  if (pulling) return;
  pulling = true;
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) return;
    apply(await response.json());
  } catch (error) {
    if (!state) $("connPill").textContent = "Нет связи со страницей";
  } finally {
    pulling = false;
  }
}

function showTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("on", tab.dataset.tab === name));
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("on", view.dataset.view === name));
  document.querySelector("main").classList.toggle("fill", name === "reports" || name === "telegram" || name === "energy");
  if (name !== "overview" && name !== "chart") hidePlotTip();
  if (name === "chart") drawChart();
  if (name === "log") loadEvents();
  if (name === "telegram") fillTelegramFields();
  if (name === "reports") loadReport();
  if (name === "energy") loadEnergy();
}

let historyReloadTimer = 0;

function wattsFromPct(pct) {
  if (pct == null || Number.isNaN(Number(pct))) return null;
  const kva = Number(state && state.settings && state.settings.ups_kva);
  const rating = Number.isFinite(kva) && kva > 0 ? kva : 0.7;
  return Math.max(0, Math.round(rating * 10 * Number(pct)));
}

function readingWatts(reading) {
  if (!reading) return null;
  if (reading.load_w != null) return reading.load_w;
  return wattsFromPct(reading.load_pct);
}

function asChartPoint(point, reading) {
  const pct = reading && reading.load_pct != null
    ? reading.load_pct
    : (point.load_pct != null ? point.load_pct : point.load);
  return {
    ...point,
    load_pct: pct,
    load: reading ? readingWatts(reading) : wattsFromPct(pct),
  };
}

function typicalStep(samples) {
  if (!samples || samples.length < 3) return 2;
  const dts = [];
  for (let i = 1; i < samples.length; i += 1) {
    const dt = samples[i].t - samples[i - 1].t;
    if (dt > 0 && dt < 3600) dts.push(dt);
  }
  if (!dts.length) return 2;
  dts.sort((a, b) => a - b);
  return dts[Math.floor(dts.length / 2)];
}

function gapLimit(samples) {
  const poll = Number(state && state.settings && state.settings.poll_seconds) || 2;
  return Math.max(12, poll * 5, typicalStep(samples) * 5);
}

function reloadLiveHistory() {
  clearTimeout(historyReloadTimer);
  historyReloadTimer = setTimeout(() => loadHistory(), 80);
}

async function loadHistory(spanMin) {
  const need = Math.min(1440, Math.max(minutes, Number(spanMin) || 0, historyMinutes));
  historyMinutes = need;
  const response = await fetch(`/api/history?minutes=${need}`);
  const data = await response.json();
  points = (data.points || []).map((point) => asChartPoint(point));
  drawChart();
  drawSpark();
}

async function loadEvents() {
  const response = await fetch("/api/events");
  const data = await response.json();
  const list = $("eventList");
  const events = data.events || [];
  list.innerHTML = events.length
    ? events.map((item) => `
        <li class="${escapeAttr(item.level)}" data-code="${escapeAttr(item.code || "")}" data-level="${escapeAttr(item.level || "")}" data-message="${escapeAttr(item.message || "")}" data-t="${escapeAttr(item.t || "")}">
          <time>${clockTime(item.t)}</time>
          <p>${escapeHtml(item.message)}</p>
        </li>`).join("")
    : `<li class="empty">Пока событий нет.</li>`;
}

async function command(action, extra) {
  const response = await fetch("/api/command", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...(extra || {}) }),
  });
  const data = await response.json();
  toast(data.message || "Готово");
  if (action === "demo" || action === "live" || action === "rescan") {
    setTimeout(loadHistory, 600);
  }
  return data;
}

async function openScan(force) {
  const dialog = $("scanDialog");
  const box = $("scanGroups");
  const note = $("scanMsg");
  if (!dialog || !box) return;
  if (dialog.open && !force) {
    dialog.close();
    return;
  }
  if (!dialog.open) dialog.show();
  box.innerHTML = `<p class="lead">Сканирую HID и COM…</p>`;
  if (note) note.textContent = "";
  const data = await fetch("/api/ports").then((item) => item.json()).catch(() => ({}));
  if (note) note.textContent = data.message || "";
  const hid = data.hid || [];
  const com = data.com || [];
  const hidHtml = hid.length
    ? hid.map((item) => `
        <button type="button" class="${item.status === "отвечает" || item.status === "подключено сейчас" ? "ok" : ""}" data-kind="hid" data-path="${escapeAttr(item.path || "")}">
          ${escapeHtml(item.label || "HID")} · ${escapeHtml(item.status || "")}
        </button>`).join("")
    : `<p class="lead">USB HID ИБП не найден.</p>`;
  const comHtml = com.length
    ? com.map((item) => `
        <button type="button" class="${item.likely ? "warn" : ""}" data-kind="com" data-port="${escapeAttr(item.device || "")}">
          ${escapeHtml(item.device || "COM")} · ${escapeHtml(item.description || "")} · ${escapeHtml(item.status || "")}
        </button>`).join("")
    : `<p class="lead">COM-портов нет. Для Mega USB они не нужны.</p>`;
  box.innerHTML = `
    <section class="lab-group">
      <h3>USB HID</h3>
      <div>${hidHtml}</div>
    </section>
    <section class="lab-group">
      <h3>COM-порты</h3>
      <div>${comHtml}</div>
    </section>`;
}

$("scanGroups") && $("scanGroups").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-kind]");
  if (!button) return;
  button.disabled = true;
  const kind = button.dataset.kind;
  const port = kind === "com" ? (button.dataset.port || "") : "";
  const settings = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ serial_port: port }),
  }).then((item) => item.json()).catch(() => ({}));
  if (settings && settings.ok === false) {
    toast(settings.message || "Порт не выбран");
    button.disabled = false;
    return;
  }
  await command("rescan");
  button.disabled = false;
  openScan(true);
});

async function openLab() {
  const dialog = $("labDialog");
  const box = $("labGroups");
  if (!dialog || !box) return;
  if (!box.dataset.ready) {
    const data = await fetch("/api/simulations").then((item) => item.json()).catch(() => ({}));
    const items = data.items || [];
    const groups = [];
    items.forEach((item) => {
      let group = groups.find((entry) => entry.name === item.group);
      if (!group) {
        group = { name: item.group, items: [] };
        groups.push(group);
      }
      group.items.push(item);
    });
    box.innerHTML = groups.map((group) => `
      <section class="lab-group">
        <h3>${escapeHtml(group.name)}</h3>
        <div>
          ${group.items.map((item) => `
            <button type="button" class="${escapeAttr(item.level || "")}" data-code="${escapeAttr(item.code)}">
              ${escapeHtml(item.title)}
            </button>`).join("")}
        </div>
      </section>`).join("");
    box.dataset.ready = "1";
  }
  if (dialog.open) {
    dialog.close();
    return;
  }
  dialog.show();
}

$("labGroups").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-code]");
  if (!button) return;
  button.disabled = true;
  const data = await command("simulate_event", { code: button.dataset.code });
  button.disabled = false;
  if (data && data.ok) {
    loadEvents();
    startSim(button.dataset.code);
  }
});

async function openSettings() {
  const response = await fetch("/api/settings");
  const settings = await response.json();
  const form = $("settingsForm");
  for (const [key, value] of Object.entries(settings)) {
    const field = form.elements[key];
    if (!field) continue;
    if (field.type === "checkbox") field.checked = Boolean(value);
    else field.value = value;
  }
  $("settingsMsg").textContent = "";
  $("settings").showModal();
}

async function saveTelegram(event) {
  event.preventDefault();
  const token = $("tgToken").value.trim();
  const response = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ telegram_token: token }),
  });
  const data = await response.json();
  $("tgFormMsg").textContent = data.message || "";
  if (data.ok) {
    $("tgToken").value = "";
    $("tgToken").placeholder = token ? "токен сохранён, поле можно оставить пустым" : "123456789:AAH…";
    toast("Токен сохранён");
  }
}

function defaultEventSound(code) {
  const name = String(code);
  if (name.startsWith("voltage_") && name.endsWith("_clear")) return true;
  return !name.endsWith("_clear") && name !== "startup";
}

function defaultEventTray(code) {
  return !String(code).endsWith("_clear") && code !== "startup" && code !== "shutdown";
}

function telegramEventsPayload() {
  const events = {};
  TG_EVENTS.forEach((group) => {
    group.items.forEach((item) => {
      const send = document.querySelector(`[data-tg-ev="${item.code}"][data-kind="on"]`);
      const sound = document.querySelector(`[data-tg-ev="${item.code}"][data-kind="sound"]`);
      const tray = document.querySelector(`[data-tg-ev="${item.code}"][data-kind="tray"]`);
      events[item.code] = {
        on: send ? send.checked : true,
        sound: sound ? sound.checked : defaultEventSound(item.code),
        tray: tray ? tray.checked : defaultEventTray(item.code),
      };
    });
  });
  return events;
}

function renderTelegramEvents(settings) {
  const box = $("tgEventList");
  if (!box) return;
  const stored = (settings && settings.telegram_events) || {};
  if (!box.dataset.ready) {
    box.innerHTML = TG_EVENTS.map((group) => `
      <section class="tg-event-group">
        <h4>${escapeHtml(group.group)}</h4>
        ${group.items.map((item) => `
          <div class="tg-event" data-code="${escapeAttr(item.code)}">
            <em>${escapeHtml(item.title)}</em>
            <label class="switch" title="Отправлять">
              <input type="checkbox" data-tg-ev="${escapeAttr(item.code)}" data-kind="on">
              <span></span>
            </label>
            <label class="switch sound" title="Со звуком">
              <input type="checkbox" data-tg-ev="${escapeAttr(item.code)}" data-kind="sound">
              <span></span>
            </label>
            <label class="switch tray" title="Уведомление Windows в трее">
              <input type="checkbox" data-tg-ev="${escapeAttr(item.code)}" data-kind="tray">
              <span></span>
            </label>
          </div>`).join("")}
      </section>`).join("");
    box.dataset.ready = "1";
  }
  TG_EVENTS.forEach((group) => {
    group.items.forEach((item) => {
      const pref = stored[item.code];
      const on = pref ? pref.on !== false : true;
      let sound = pref ? Boolean(pref.sound) : defaultEventSound(item.code);
      if (on && String(item.code).startsWith("voltage_") && String(item.code).endsWith("_clear")) {
        sound = true;
      }
      const tray = pref && "tray" in pref ? Boolean(pref.tray) : defaultEventTray(item.code);
      const sendBox = document.querySelector(`[data-tg-ev="${item.code}"][data-kind="on"]`);
      const soundBox = document.querySelector(`[data-tg-ev="${item.code}"][data-kind="sound"]`);
      const trayBox = document.querySelector(`[data-tg-ev="${item.code}"][data-kind="tray"]`);
      if (sendBox) sendBox.checked = on;
      if (soundBox) soundBox.checked = sound;
      if (trayBox) trayBox.checked = tray;
      const row = sendBox && sendBox.closest(".tg-event");
      if (row) row.classList.toggle("is-off", !on && !tray);
    });
  });
}

async function saveTelegramFlags() {
  syncDigestTime();
  await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      telegram_digest: $("tgDigest").checked,
      telegram_digest_time: digestClockValue(),
      telegram_events: telegramEventsPayload(),
      windows_notify: $("windowsNotify").checked,
    }),
  });
}

function persistChartPrefs() {
  if (!chartPrefsReady) return;
  clearTimeout(chartSaveTimer);
  chartSaveTimer = setTimeout(() => {
    fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        spark_lines: SPARK_LINES.filter((line) => sparkOn[line.key]).map((line) => line.key),
        spark_window: Math.round(sparkView.window),
        chart_minutes: minutes,
        chart_series: series,
      }),
    }).catch(() => {});
  }, 400);
}

function applyChartPrefs(settings) {
  if (chartPrefsReady || !settings) return;
  const lines = settings.spark_lines;
  if (Array.isArray(lines) && lines.length) {
    const allowed = new Set(SPARK_LINES.map((line) => line.key));
    sparkOn = Object.fromEntries(SPARK_LINES.map((line) => [line.key, false]));
    lines.forEach((key) => {
      if (allowed.has(key)) sparkOn[key] = true;
    });
    if (!SPARK_LINES.some((line) => sparkOn[line.key])) {
      sparkOn.in = true;
      sparkOn.out = true;
    }
  }
  const win = Number(settings.spark_window);
  if (win >= 30 && win <= 86400) sparkView.window = win;
  const mins = Number(settings.chart_minutes);
  if ([15, 60, 360, 1440].includes(mins)) {
    minutes = mins;
    document.querySelectorAll("#ranges button").forEach((item) => {
      item.classList.toggle("on", Number(item.dataset.minutes) === minutes);
    });
  }
  if (settings.chart_series && SERIES[settings.chart_series]) {
    series = settings.chart_series;
    document.querySelectorAll("#series button").forEach((item) => {
      item.classList.toggle("on", item.dataset.series === series);
    });
  }
  chartPrefsReady = true;
  refreshSparkUi();
  if (minutes > historyMinutes) loadHistory(minutes);
}

function clockPart(id, max) {
  const raw = Number(($(id) && $(id).value) || 0);
  if (!Number.isFinite(raw)) return 0;
  return Math.min(max, Math.max(0, Math.round(raw)));
}

function digestClockValue() {
  return `${String(clockPart("tgDigestHour", 23)).padStart(2, "0")}:${String(clockPart("tgDigestMinute", 59)).padStart(2, "0")}`;
}

function setDigestClock(value) {
  const [hour = "21", minute = "00"] = String(value || "21:00").split(":");
  $("tgDigestHour").value = String(Math.min(23, Math.max(0, Number(hour) || 0))).padStart(2, "0");
  $("tgDigestMinute").value = String(Math.min(59, Math.max(0, Number(minute) || 0))).padStart(2, "0");
}

function clampClockInputs() {
  setDigestClock(digestClockValue());
}

function nudgeClock(part, delta) {
  if (part === "hour") {
    $("tgDigestHour").value = String((clockPart("tgDigestHour", 23) + delta + 24) % 24).padStart(2, "0");
  } else {
    $("tgDigestMinute").value = String((clockPart("tgDigestMinute", 59) + delta + 60) % 60).padStart(2, "0");
  }
  saveTelegramFlags();
}

function syncDigestTime() {
  $("digestClock").classList.toggle("is-off", !$("tgDigest").checked);
}

async function telegramAction(action) {
  const data = await command(action);
  if (action === "telegram_clear") {
    $("tgToken").value = "";
    $("tgToken").placeholder = "123456789:AAH…";
    $("tgFormMsg").textContent = data.message || "";
  }
  const stateResponse = await fetch("/api/state", { cache: "no-store" });
  if (stateResponse.ok) {
    const next = await stateResponse.json();
    state = next;
    paintTelegram(next);
    fillTelegramFields();
  }
}

async function copyPairCommand() {
  const text = ($("tgPairCode").textContent || "").replace(/[^\d]/g, "");
  try {
    await navigator.clipboard.writeText(text);
    toast("Код скопирован");
  } catch (error) {
    toast(text);
  }
}

async function saveSettings(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = {};
  for (const field of form.elements) {
    if (!field.name) continue;
    payload[field.name] = field.type === "checkbox" ? field.checked : field.value;
  }
  const response = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  $("settingsMsg").textContent = data.message || "";
  if (data.ok) {
    if (data.settings && form.elements.ups_kva && data.settings.ups_kva != null) {
      form.elements.ups_kva.value = data.settings.ups_kva;
    }
    toast("Настройки сохранены");
  }
}

function apply(next) {
  const previousMode = state && state.mode;
  state = next;
  applyChartPrefs(next.settings || {});
  const reading = next.reading;
  const live = next.mode === "live" || next.mode === "demo";
  document.body.dataset.power = reading && reading.on_battery ? "battery" : live ? "mains" : "idle";
  $("connPill").textContent = pillText(next);
  $("connPill").classList.toggle("live", live);
  $("dashboard").hidden = !live;
  $("gate").hidden = live;
  $("demoBanner").hidden = next.mode !== "demo";
  if (next.mode === "demo") {
    $("demoText").textContent = "Демонстрация — цифры примерные, не с вашего ИБП.";
  }
  $("btnSimulate").hidden = next.mode !== "demo";
  if (!live) {
    $("gateTitle").textContent = gateTitle(next);
    $("gateText").textContent = gateText(next);
  }
  $("footPort").textContent = `Порт: ${portText(next)}`;
  $("footState").textContent = `Состояние: ${next.message || "—"}`;
  $("footTime").textContent = `Время работы: ${clock(next.session_seconds || 0)}`;
  paintTelegram(next);
  if (reading) paint(reading, next);
  syncAlerts(reading, next);
  if (reading && next.mode !== previousMode) loadHistory();
  else if (reading) pushPoint(reading);
  if (previousMode && previousMode !== next.mode) loadEvents();
  renderDevice(next);
}

function paintTelegram(snapshot) {
  const tg = (snapshot && snapshot.telegram) || {};
  const settings = (snapshot && snapshot.settings) || {};
  const pill = $("tgPill");
  const hero = $("tgHero");
  pill.classList.remove("live", "bad", "wait");
  let mode = "off";
  let title = "Бот выключен";
  let text = "Вставьте токен на этой странице.";
  let pillTextValue = "Телеграм";
  if (!tg.configured) {
    pillTextValue = "Телеграм выкл.";
  } else if (tg.ok && tg.chats && tg.chats.length) {
    mode = "live";
    title = tg.username ? `@${tg.username}` : "Бот на связи";
    text = `Привязано чатов: ${tg.chats.length}`;
    pill.classList.add("live");
    pillTextValue = tg.username ? `Телеграм @${tg.username}` : "Телеграм на связи";
  } else if (tg.ok) {
    mode = "wait";
    title = tg.username ? `@${tg.username}` : "Бот готов";
    text = "Напишите боту код привязки, только цифры.";
    pill.classList.add("wait");
    pillTextValue = "Телеграм · нет чата";
  } else {
    mode = "bad";
    title = "Нет связи с ботом";
    text = tg.error || "Проверьте токен и интернет.";
    pill.classList.add("bad");
    pillTextValue = "Телеграм недоступен";
  }
  pill.textContent = pillTextValue;
  hero.dataset.state = mode;
  $("tgHeroTitle").textContent = title;
  $("tgHeroText").textContent = text;
  const code = tg.pair_code || settings.telegram_pair || "";
  $("tgPairCode").textContent = code || "————";
  const list = (tg.chats && tg.chats.length) ? tg.chats : settings.telegram_chats || [];
  $("tgChatList").innerHTML = list.length
    ? list.map((item) => `
        <li class="tg-chat" data-id="${escapeAttr(item.id)}">
          <div>
            <strong>${escapeHtml(item.name || item.id)}</strong>
            <span class="tg-role">${item.guest ? "гость" : item.admin ? "админ" : "полный доступ"}</span>
          </div>
          <div class="tg-chat-actions">
            <button type="button" class="ghost" data-chat-act="rules">Правила</button>
            <button type="button" class="ghost" data-chat-act="unlink">Отвязать</button>
          </div>
        </li>`).join("")
    : `<li class="empty">Пока никто не привязан.</li>`;
}

function syncWindowsMaster() {
  const box = $("tgEventList") && $("tgEventList").closest(".tg-events");
  if (box) box.classList.toggle("is-tray-off", !$("windowsNotify").checked);
}

function currentChatList() {
  const tg = (state && state.telegram) || {};
  const settings = (state && state.settings) || {};
  return (tg.chats && tg.chats.length) ? tg.chats : settings.telegram_chats || [];
}

function findChat(id) {
  return currentChatList().find((item) => String(item.id) === String(id));
}

function defaultGuestActions() {
  return {
    pc_off: false, pc_reboot: false, pc_sleep: false, pc_hibernate: false,
    cancel_shutdown: true, screenshot: true, app_restart: false,
    status: true, events: true, day_report: true, summary: true,
  };
}

function defaultGuestEvents() {
  const events = {};
  TG_EVENTS.forEach((group) => {
    group.items.forEach((item) => {
      const on = item.code === "on_battery" || item.code === "on_battery_clear";
      events[item.code] = { on, sound: item.code === "on_battery" };
    });
  });
  return events;
}

function guestEventOn(events, code) {
  const item = events && events[code];
  if (item && typeof item === "object") return Boolean(item.on);
  return Boolean(item);
}

function guestEventSound(events, code) {
  const item = events && events[code];
  if (item && typeof item === "object") return Boolean(item.on && item.sound);
  return Boolean(item) && defaultEventSound(code);
}

function onChatListClick(event) {
  const button = event.target.closest("[data-chat-act]");
  const row = event.target.closest(".tg-chat");
  if (!button || !row) return;
  const id = row.dataset.id;
  if (button.dataset.chatAct === "unlink") {
    updateTelegramChat({ id, action: "unlink" });
    return;
  }
  openChatDialog(id);
}

function openChatDialog(id) {
  const chat = findChat(id);
  if (!chat) return;
  const dialog = $("chatDialog");
  dialog.dataset.id = String(chat.id);
  $("chatDialogTitle").textContent = chat.name || `Чат ${chat.id}`;
  $("chatGuest").checked = Boolean(chat.guest);
  $("chatAdmin").checked = Boolean(chat.admin) && !chat.guest;
  renderChatRules(chat);
  $("chatRules").hidden = !chat.guest;
  dialog.showModal();
}

function defaultAdminEvents() {
  const events = {};
  TG_EVENTS.forEach((group) => {
    group.items.forEach((item) => {
      events[item.code] = { on: true, sound: defaultEventSound(item.code) };
    });
  });
  return events;
}

function defaultAdminActions() {
  const actions = {};
  TG_ACTIONS.forEach((group) => {
    group.items.forEach((item) => {
      actions[item.code] = true;
    });
  });
  return actions;
}

function rulesForChat(chat) {
  if (chat && chat.guest) {
    return { events: chat.events || defaultGuestEvents(), actions: chat.actions || defaultGuestActions() };
  }
  return { events: (chat && chat.events) || defaultAdminEvents(), actions: (chat && chat.actions) || defaultAdminActions() };
}

function onChatRoleChange(event) {
  const guest = $("chatGuest");
  const admin = $("chatAdmin");
  if (event && event.target === guest && guest.checked) admin.checked = false;
  if (event && event.target === admin && admin.checked) guest.checked = false;
  if (guest.checked) renderChatRules({ guest: true, events: defaultGuestEvents(), actions: defaultGuestActions() });
  $("chatRules").hidden = !guest.checked;
  saveChatRules();
}

function renderChatRules(chat) {
  const { events, actions } = rulesForChat(chat);
  $("chatEventList").innerHTML = TG_EVENTS.map((group) => `
    <section class="chat-group" data-ev-group="${escapeAttr(group.group)}">
      <div class="chat-group-head">
        <h4>${escapeHtml(group.group)}</h4>
        <label class="switch" title="Вся группа">
          <input type="checkbox" data-group-on="${escapeAttr(group.group)}" ${group.items.every((item) => guestEventOn(events, item.code)) ? "checked" : ""}>
          <span></span>
        </label>
        <label class="switch sound" title="Звук группы">
          <input type="checkbox" data-group-sound="${escapeAttr(group.group)}" ${group.items.every((item) => guestEventSound(events, item.code)) ? "checked" : ""}>
          <span></span>
        </label>
      </div>
      ${group.items.map((item) => `
        <div class="tg-event">
          <em>${escapeHtml(item.title)}</em>
          <label class="switch" title="Присылать">
            <input type="checkbox" data-chat-ev="${escapeAttr(item.code)}" data-kind="on" ${guestEventOn(events, item.code) ? "checked" : ""}>
            <span></span>
          </label>
          <label class="switch sound" title="Со звуком">
            <input type="checkbox" data-chat-ev="${escapeAttr(item.code)}" data-kind="sound" ${guestEventSound(events, item.code) ? "checked" : ""}>
            <span></span>
          </label>
        </div>`).join("")}
    </section>`).join("");
  $("chatActionList").innerHTML = TG_ACTIONS.map((group) => `
    <section class="chat-group" data-act-group="${escapeAttr(group.group)}">
      <div class="chat-group-head one">
        <h4>${escapeHtml(group.group)}</h4>
        <label class="switch tray" title="Вся группа">
          <input type="checkbox" data-act-group-on="${escapeAttr(group.group)}" ${group.items.every((item) => actions[item.code]) ? "checked" : ""}>
          <span></span>
        </label>
      </div>
      ${group.items.map((item) => `
        <div class="tg-event">
          <em>${escapeHtml(item.title)}</em>
          <label class="switch tray" title="Кнопка в боте">
            <input type="checkbox" data-chat-btn="${escapeAttr(item.code)}" ${actions[item.code] ? "checked" : ""}>
            <span></span>
          </label>
        </div>`).join("")}
    </section>`).join("");
}

function chatRulesPayload() {
  const events = {};
  const actions = {};
  TG_EVENTS.forEach((group) => {
    group.items.forEach((item) => {
      const send = document.querySelector(`[data-chat-ev="${item.code}"][data-kind="on"]`);
      const sound = document.querySelector(`[data-chat-ev="${item.code}"][data-kind="sound"]`);
      const on = send ? send.checked : false;
      events[item.code] = { on, sound: on && sound ? sound.checked : false };
    });
  });
  TG_ACTIONS.forEach((group) => {
    group.items.forEach((item) => {
      const box = document.querySelector(`[data-chat-btn="${item.code}"]`);
      actions[item.code] = box ? box.checked : false;
    });
  });
  return { events, actions };
}

function applyGroupToggle(input) {
  const group = input.dataset.groupOn || input.dataset.groupSound || input.dataset.actGroupOn;
  if (input.dataset.groupOn) {
    document.querySelectorAll(`[data-ev-group="${group}"] [data-chat-ev][data-kind="on"]`).forEach((box) => {
      box.checked = input.checked;
    });
    if (!input.checked) {
      document.querySelectorAll(`[data-ev-group="${group}"] [data-chat-ev][data-kind="sound"]`).forEach((box) => {
        box.checked = false;
      });
    }
  } else if (input.dataset.groupSound) {
    document.querySelectorAll(`[data-ev-group="${group}"] [data-chat-ev][data-kind="sound"]`).forEach((box) => {
      box.checked = input.checked;
    });
    if (input.checked) {
      document.querySelectorAll(`[data-ev-group="${group}"] [data-chat-ev][data-kind="on"]`).forEach((box) => {
        box.checked = true;
      });
    }
  } else if (input.dataset.actGroupOn) {
    document.querySelectorAll(`[data-act-group="${group}"] [data-chat-btn]`).forEach((box) => {
      box.checked = input.checked;
    });
  }
}

async function saveChatRules(event) {
  const dialog = $("chatDialog");
  const id = dialog && dialog.dataset.id;
  if (!id) return;
  if (event && event.target && (event.target.dataset.groupOn || event.target.dataset.groupSound || event.target.dataset.actGroupOn)) {
    applyGroupToggle(event.target);
  }
  const guest = $("chatGuest").checked;
  const admin = $("chatAdmin").checked && !guest;
  $("chatRules").hidden = !guest;
  const payload = {
    id,
    action: "update",
    guest,
    admin,
  };
  if (guest) Object.assign(payload, chatRulesPayload());
  await updateTelegramChat(payload, true);
}

async function updateTelegramChat(payload, quiet) {
  const response = await fetch("/api/telegram/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!quiet) toast(data.message || "Готово");
  if (data.settings && state) {
    state.settings = data.settings;
    if (state.telegram) state.telegram.chats = data.settings.telegram_chats || [];
  }
  paintTelegram(state || {});
  if ($("chatDialog").open && data.chat) {
    $("chatGuest").checked = Boolean(data.chat.guest);
    $("chatAdmin").checked = Boolean(data.chat.admin) && !data.chat.guest;
    if (data.chat.guest) renderChatRules(data.chat);
    $("chatRules").hidden = !data.chat.guest;
  }
}

function fillTelegramFields() {
  const settings = (state && state.settings) || {};
  $("tgDigest").checked = Boolean(settings.telegram_digest);
  $("windowsNotify").checked = settings.windows_notify !== false;
  setDigestClock(settings.telegram_digest_time || "21:00");
  syncDigestTime();
  renderTelegramEvents(settings);
  syncWindowsMaster();
  $("tgToken").placeholder = settings.telegram_token_set
    ? "токен уже сохранён — вставьте новый, чтобы заменить"
    : "123456789:AAH…";
}

function withSim(reading) {
  if (!simCode || !reading) return reading;
  const next = { ...reading };
  const code = simCode;
  if (code === "on_battery") {
    next.on_battery = true;
    next.mains_ok = false;
    next.mode_label = "Питание от батареи";
  } else if (code === "on_battery_clear") {
    next.on_battery = false;
    next.mains_ok = true;
  } else if (code === "battery_loose") {
    next.battery_loose = true;
  } else if (code === "battery_loose_clear") {
    next.battery_loose = false;
  } else if (code === "battery_low") {
    next.battery_low = true;
    next.battery_pct = 12;
  } else if (code === "battery_low_clear") {
    next.battery_low = false;
  } else if (code === "battery_swap") {
    next.battery_swap = { from: 24.0, to: 27.2 };
  } else if (code === "fault") {
    next.fault = true;
    next.mode_label = "Авария";
  } else if (code === "fault_clear") {
    next.fault = false;
  } else if (code === "voltage_low") {
    next.voltage_state = "low";
    next.voltage_note = "Симуляция · сеть понижена";
    next.input_v = 192;
    next.mains_ok = true;
    next.on_battery = false;
    next.avr = true;
  } else if (code === "voltage_high") {
    next.voltage_state = "high";
    next.voltage_note = "Симуляция · сеть повышена";
    next.input_v = 246;
    next.mains_ok = true;
    next.on_battery = false;
    next.avr = true;
  } else if (code === "voltage_crit_low") {
    next.voltage_state = "crit_low";
    next.voltage_note = "Симуляция · сеть слишком низкая";
    next.input_v = 172;
    next.mains_ok = true;
    next.on_battery = false;
    next.avr = true;
  } else if (code === "voltage_crit_high") {
    next.voltage_state = "crit_high";
    next.voltage_note = "Симуляция · сеть слишком высокая";
    next.input_v = 256;
    next.mains_ok = true;
    next.on_battery = false;
    next.avr = true;
  } else if (code.endsWith("_clear") && code.startsWith("voltage_")) {
    next.voltage_state = "ok";
    next.voltage_note = "";
    next.avr = false;
  } else if (code === "battery_boost") {
    next.input_v = 198;
    next.output_v = 221;
    next.battery_boost = true;
    next.mains_ok = true;
    next.on_battery = false;
    next.avr = true;
  } else if (code === "battery_boost_clear") {
    next.battery_boost = false;
    if (next.input_v != null) next.output_v = next.input_v;
  } else if (code === "pc_shutdown") {
    next.on_battery = true;
    next.mains_ok = false;
    next.mode_label = "Питание от батареи";
    next.shutdown_seconds = simLeft();
  } else if (code === "pc_off" || code === "pc_reboot") {
    next.shutdown_seconds = simLeft();
  } else if (code === "link_lost") {
    next.mode_label = "Нет связи";
  }
  return next;
}

function simLeft() {
  if (!simUntil) return 0;
  return Math.max(0, Math.ceil((simUntil - Date.now()) / 1000));
}

function startSim(code) {
  simCode = code || "";
  const delay = Number(state && state.settings && state.settings.shutdown_delay) || 180;
  if (code === "pc_shutdown") simUntil = Date.now() + delay * 1000;
  else if (code === "pc_off" || code === "pc_reboot") simUntil = Date.now() + 20 * 1000;
  else simUntil = 0;
  refreshSimUi();
}

function stopSim() {
  simCode = "";
  simUntil = 0;
  refreshSimUi();
}

function cancelShutdown() {
  if (simCode === "pc_shutdown" || simCode === "pc_off" || simCode === "pc_reboot") {
    stopSim();
    return;
  }
  command("cancel_shutdown");
}

function refreshSimUi() {
  const active = Boolean(simCode);
  if ($("stopSim")) $("stopSim").hidden = !active;
  if ($("schStopSim")) $("schStopSim").hidden = !active;
  if (state && state.reading) paint(state.reading, state);
}

function paint(reading, snapshot) {
  reading = withSim(reading);
  const settings = snapshot.settings || {};
  const seriesCount = Number(settings.battery_count) || 2;
  const groupCount = Number(settings.battery_groups) || 1;
  syncPackCells(seriesCount, groupCount);
  const kva = Number(settings.ups_kva) || 0.7;
  const load = reading.load_pct;
  const va = load == null ? null : Math.round(kva * 10 * load);
  if (reading.input_v != null) {
    sessionIn.min = sessionIn.min == null ? reading.input_v : Math.min(sessionIn.min, reading.input_v);
    sessionIn.max = sessionIn.max == null ? reading.input_v : Math.max(sessionIn.max, reading.input_v);
  }
  $("inV").textContent = fixed(reading.input_v, 1);
  if ($("schInV")) $("schInV").textContent = reading.input_v == null ? "—" : `${Math.round(reading.input_v)} В`;
  $("inHz").textContent = fixed(reading.frequency, 1);
  $("deltaV").textContent = reading.input_v == null || reading.output_v == null
    ? "—"
    : `${reading.output_v - reading.input_v >= 0 ? "+" : ""}${(reading.output_v - reading.input_v).toFixed(1)} В`;
  const boosting = Boolean(reading.battery_boost) || pointBoost(reading);
  if ($("deltaV")) $("deltaV").classList.toggle("boost", boosting);
  $("outV").textContent = fixed(reading.output_v, 1);
  if ($("schOutV")) $("schOutV").textContent = reading.output_v == null ? "—" : `${Math.round(reading.output_v)} В`;
  $("loadVal").textContent = load == null ? "—" : Math.round(load);
  $("loadVa").textContent = reading.load_w != null ? `≈ ${reading.load_w} Вт` : va == null ? "—" : `≈ ${va} ВА`;
  $("loadRing").style.setProperty("--p", String(Math.max(0, Math.min(100, load || 0))));
  const packLoose = Boolean(reading.battery_loose);
  $("cellCap").textContent = packLoose
    ? "снят"
    : reading.battery_pct == null ? "—" : `${Math.round(reading.battery_pct)}%`;
  $("mBattCell").textContent = reading.battery_v == null ? "—" : `${(reading.battery_v / seriesCount).toFixed(1)} В на банку`;
  $("mHealth").textContent = settings.battery_health == null ? "—" : `здоровье ${settings.battery_health}%`;
  $("mMode").textContent = reading.mode_label || "—";
  $("mKind").textContent = reading.ups_kind || "";
  chip($("factMains"), reading.mains_ok, reading.mains_ok ? "Сеть есть" : "Сети нет", reading.mains_ok ? "on" : "bad");
  chip($("factPath"), true, reading.on_battery ? "Питание от батареи" : "Питание от сети", reading.on_battery ? "warn" : "on");
  chip($("factAvr"), reading.avr, reading.avr ? "AVR держит напряжение" : "AVR не активен", reading.avr ? "warn" : "");
  chip(
    $("factBoost"),
    boosting,
    boosting ? "Подпитка с батареи" : "Без подпитки с батареи",
    boosting ? "warn" : "",
  );
  chip($("factBeep"), reading.beeper, reading.beeper ? "Зуммер включён" : "Зуммер выключен", reading.beeper ? "warn" : "");
  const swap = reading.battery_swap;
  const swapped = Boolean(swap && swap.from != null && swap.to != null);
  chip(
    $("factLoose"),
    packLoose || swapped,
    packLoose
      ? "Батареи отключены"
      : swapped
        ? `Пакет сменён ${Number(swap.from).toFixed(1)}→${Number(swap.to).toFixed(1)} В`
        : "Пакет на месте",
    packLoose ? "bad" : swapped ? "warn" : "on",
  );
  const volt = reading.voltage_state || "ok";
  const voltTone = volt === "crit_low" || volt === "crit_high" ? "bad" : volt === "low" || volt === "high" ? "warn" : "on";
  const voltLabel = reading.voltage_note || null;
  chip(
    $("factRange"),
    sessionIn.min != null,
    voltLabel || (sessionIn.min == null ? "Диапазон входа" : `Вход ${sessionIn.min.toFixed(0)}–${sessionIn.max.toFixed(0)} В`),
    voltLabel ? voltTone : "on",
  );
  const inCard = $("inCard");
  if (inCard) {
    inCard.classList.toggle("warn-volt", volt === "low" || volt === "high");
    inCard.classList.toggle("bad-volt", volt === "crit_low" || volt === "crit_high");
  }
  const onBattery = Boolean(reading.on_battery);
  const shutting = reading.shutdown_seconds != null;
  const offLeft = shutting ? Math.max(0, Number(reading.shutdown_seconds) || 0) : 0;
  const voltMark = {
    low: "LOW V",
    high: "HIGH V",
    crit_low: "CRIT LO",
    crit_high: "CRIT HI",
  }[volt] || "";
  if ($("schematic")) {
    $("schematic").dataset.mode = onBattery ? "battery" : "mains";
    $("schematic").dataset.avr = reading.avr || voltMark ? "1" : "0";
    $("schematic").dataset.pack = packLoose ? "loose" : reading.battery_low ? "low" : "ok";
    $("schematic").dataset.sim = simCode || "";
    $("schematic").dataset.volt = voltMark ? volt : "ok";
    $("schematic").dataset.fault = reading.fault ? "1" : "0";
    $("schematic").dataset.boost = boosting ? "1" : "0";
    $("schematic").dataset.link = simCode === "link_lost" ? "lost" : "ok";
    $("schematic").dataset.off = shutting ? "1" : "0";
    layoutSchemeTraces();
  }
  if ($("schMode")) {
    $("schMode").textContent = simCode === "link_lost"
      ? "NO LINK"
      : packLoose ? "NO PACK"
        : voltMark ? voltMark
          : boosting ? "BOOST"
          : onBattery ? "INVERT" : "LINE";
  }
  if ($("schVolt")) {
    $("schVolt").hidden = !voltMark;
    $("schVolt").textContent = voltLabel || voltMark;
    $("schVolt").className = `scheme-volt ${voltTone}`;
  }
  const soc = packLoose ? 0 : (reading.battery_pct == null ? 0 : reading.battery_pct);
  const fill = packLoose
    ? "linear-gradient(#4a1c18, #e15d4c)"
    : soc < 20
      ? "linear-gradient(#ffd0c8, #e15d4c)"
      : soc < 50
        ? "linear-gradient(#ffe3a8, #e6b15c)"
        : "linear-gradient(#d7ffb0, #3dbe7a)";
  document.querySelectorAll(".bat-fill").forEach((node) => {
    node.style.height = packLoose ? "8%" : `${soc}%`;
    node.style.background = fill;
  });
  $("mBatt").textContent = fixed(reading.battery_v, 1);
  $("mTemp").textContent = reading.temperature == null ? "—" : fixed(reading.temperature, 1);
  $("mSoc").textContent = reading.battery_pct == null ? "—" : Math.round(reading.battery_pct);
  $("mDis").textContent = clock(reading.discharge_seconds || 0);
  $("mRun").textContent = runtime(reading.runtime_min);
  if ($("schCount")) {
    $("schCount").hidden = !shutting;
    if ($("schCountVal")) $("schCountVal").textContent = clock(offLeft);
    if ($("schCountNote")) {
      $("schCountNote").textContent = simCode === "pc_reboot" || reading.shutdown_kind === "reboot"
        ? "перезагрузка"
        : offLeft <= 0 ? "отсчёт завершён" : "выключение ПК";
    }
  }
  $("btnCancelShutdown").hidden = !shutting;
  $("btnCancelShutdown").textContent = shutting
    ? `Отменить выключение · ${clock(offLeft)}`
    : "Отменить выключение ПК";
  if ($("alertCount")) {
    const ticking = !$("alertModal").hidden && (lastAlertCode === "pc_shutdown" || lastAlertCode === "pc_off" || lastAlertCode === "pc_reboot");
    $("alertCount").hidden = !ticking;
    if (ticking) $("alertCount").textContent = clock(offLeft);
  }
  lamp($("lampLink"), true, "on", snapshot.mode === "live" ? "Связь есть" : "Демонстрация");
  lamp($("lampMode"), true, reading.fault ? "bad" : onBattery ? "warn" : "on", reading.mode_label);
  lamp(
    $("lampMains"),
    true,
    reading.mains_ok ? voltTone : "bad",
    reading.mains_ok ? (voltLabel || "Сеть в норме") : "Сеть пропала",
  );
  lamp($("lampBatt"), reading.battery_low, "bad", "Низкий заряд");
  lamp(
    $("lampLoose"),
    packLoose || swapped,
    packLoose ? "bad" : swapped ? "warn" : "",
    packLoose ? "Пакет снят" : swapped ? "Пакет сменён" : "Пакет",
  );
  lamp(
    $("lampOff"),
    Boolean(reading.shutdown || shutting),
    "bad",
    shutting ? `Выключение · ${clock(offLeft)}` : "Выключение ПК",
  );
  document.title = `${fixed(reading.input_v, 0)} В · ${load == null ? "—" : Math.round(load)}% · UPS`;
  if (onBattery && !wasOnBattery && snapshot.settings && snapshot.settings.sound) beep();
  wasOnBattery = onBattery;
  const voltRank = { idle: 0, ok: 0, low: 1, high: 1, crit_low: 2, crit_high: 2 }[volt] || 0;
  if (voltRank > lastVoltRank && snapshot.settings && snapshot.settings.sound) beep();
  lastVoltRank = voltRank;
  if (sparkHoverX == null) refreshSparkNote();
}

function chip(node, active, text, tone) {
  if (!node) return;
  node.textContent = text;
  node.className = tone ? `chip ${tone}` : "chip";
}

function lamp(node, active, tone, text) {
  node.className = `lamp${active ? ` ${tone}` : ""}`;
  node.querySelector("span").textContent = text;
}

function pushPoint(reading) {
  const point = asChartPoint({
    t: reading.updated_at,
    in: reading.input_v,
    out: reading.output_v,
    batt: reading.battery_v,
    soc: reading.battery_pct,
    hz: reading.frequency,
    temp: reading.temperature,
  }, reading);
  if (!points.length || points[points.length - 1].t !== point.t) {
    points.push(point);
    const keep = Math.max(minutes, historyMinutes, Math.ceil(sparkView.window / 60)) * 60;
    const cutoff = Date.now() / 1000 - keep;
    if (points.length > 20 && points[0].t < cutoff) {
      points = points.filter((item) => item.t >= cutoff);
    }
  }
  if ($("view-chart").classList.contains("on")) drawChart();
}

function renderDevice(snapshot) {
  const reading = snapshot.reading;
  $("devPort").textContent = `${portText(snapshot)}. ${snapshot.message || ""}`;
  $("devInfo").textContent = snapshot.info || "ИБП не прислал строку I — это нормально для части моделей.";
  const ratings = snapshot.ratings;
  $("devRatings").textContent = ratings
    ? `${ratings.voltage} В, ${ratings.current} А, около ${ratings.va} ВА, батарея ${ratings.battery_v} В, ${ratings.frequency} Гц`
    : "Номиналы (команда F) появятся после подключения.";
  $("devRaw").textContent = reading ? reading.raw : "—";
  const trace = snapshot.trace || [];
  $("devTrace").innerHTML = trace.length
    ? trace.slice().reverse().map((line) => `
        <li><time>${clockTime(line.t)}</time><p>${escapeHtml(line.dir)} ${escapeHtml(line.text)}</p></li>`).join("")
    : `<li class="empty">Обмен появится после подключения.</li>`;
  const ports = snapshot.ports || [];
  $("devPorts").innerHTML = ports.length
    ? ports.map((port) => `<li>${escapeHtml(port.device)} — ${escapeHtml(port.description || "")}</li>`).join("")
    : `<li class="empty">Последовательных портов нет. Для Mega(USB) они не нужны.</li>`;
}

function sparkLines() {
  return SPARK_LINES.filter((line) => sparkOn[line.key]);
}

function sparkNow() {
  return Date.now() / 1000;
}

function sparkSpan() {
  const now = sparkNow();
  const end = sparkView.follow ? now : Math.min(now, sparkView.end || now);
  const start = end - sparkView.window;
  return { start, end, now };
}

function formatSparkSpan(sec) {
  if (sec < 90) return `${Math.round(sec)} с`;
  if (sec < 3600) return `${Math.round(sec / 60)} мин`;
  if (sec < 86400) {
    const hours = sec / 3600;
    return `${hours < 10 ? hours.toFixed(1) : Math.round(hours)} ч`;
  }
  return `${(sec / 86400).toFixed(1)} сут`;
}

function refreshSparkUi() {
  const box = $("sparkLines");
  if (box && !box.dataset.ready) {
    box.innerHTML = SPARK_LINES.map((line) => `
      <button type="button" data-key="${escapeAttr(line.key)}">
        <i style="background:${line.color}"></i>${escapeHtml(line.name)}
      </button>`).join("");
    box.dataset.ready = "1";
  }
  if (box) {
    box.querySelectorAll("button").forEach((button) => {
      button.classList.toggle("on", Boolean(sparkOn[button.dataset.key]));
    });
  }
  if ($("sparkRange")) $("sparkRange").textContent = formatSparkSpan(sparkView.window);
  if ($("sparkReset")) $("sparkReset").hidden = sparkView.follow;
  if ($("sparkTitle")) $("sparkTitle").textContent = sparkView.follow ? "Живой график" : "Обзор · пауза";
  refreshSparkNote();
  drawSpark();
  ensureSparkHistory();
}

function refreshSparkNote() {
  const node = $("sparkNote");
  if (!node) return;
  const reading = state && state.reading;
  if (!reading) {
    node.textContent = "";
    return;
  }
  const live = {
    in: reading.input_v,
    out: reading.output_v,
    load: readingWatts(reading),
    batt: reading.battery_v,
    soc: reading.battery_pct,
    hz: reading.frequency,
    temp: reading.temperature,
  };
  node.textContent = sparkLines().map((line) => `${line.name} ${fixed(live[line.key], line.unit === "Вт" || line.unit === "%" ? 0 : 1)} ${line.unit}`).join(" · ");
  if (reading.battery_boost || pointBoost(reading)) {
    const inn = reading.input_v;
    const out = reading.output_v;
    if (inn != null && out != null) {
      node.textContent += ` · подпитка +${(out - inn).toFixed(1)} В`;
    }
  }
}

function resetSpark() {
  sparkView.follow = true;
  sparkView.end = 0;
  refreshSparkUi();
}

function zoomSpark(factor, anchor) {
  const span = sparkSpan();
  const next = Math.max(30, Math.min(24 * 3600, sparkView.window * factor));
  if (next === sparkView.window) return;
  const pivot = anchor != null ? anchor : (sparkView.follow ? span.end : (span.start + span.end) / 2);
  sparkView.window = next;
  if (!sparkView.follow) {
    const frac = (pivot - span.start) / Math.max(1, span.end - span.start);
    sparkView.end = pivot + (1 - frac) * next;
  }
  refreshSparkUi();
  persistChartPrefs();
}

function panSpark(part) {
  const span = sparkSpan();
  const now = span.now;
  sparkView.end = Math.min(now, span.end + sparkView.window * part);
  sparkView.follow = sparkView.end >= now - 1.2;
  if (sparkView.follow) sparkView.end = 0;
  refreshSparkUi();
}

function onSparkWheel(event) {
  event.preventDefault();
  const canvas = $("spark");
  const plot = canvas && canvas._plot;
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  let anchor = sparkSpan().end;
  if (plot) {
    const frac = Math.min(1, Math.max(0, (x - plot.left) / Math.max(1, plot.plotW)));
    anchor = plot.t0 + frac * (plot.t1 - plot.t0);
    if (frac < 0.86) {
      sparkView.follow = false;
      sparkView.end = sparkSpan().end;
    }
  }
  zoomSpark(event.deltaY > 0 ? 1.18 : 1 / 1.18, anchor);
}

function onSparkDown(event) {
  const canvas = $("spark");
  const plot = canvas && canvas._plot;
  if (!plot) return;
  canvas.setPointerCapture(event.pointerId);
  const card = canvas.closest(".spark-card");
  if (card) card.classList.add("grabbing");
  hidePlotTip();
  sparkDrag = {
    x: event.clientX,
    end: sparkSpan().end,
    follow: sparkView.follow,
    plotW: plot.plotW,
  };
}

function onSparkMove(event) {
  if (!sparkDrag) {
    if (event.target === $("spark")) hoverSpark(event);
    return;
  }
  const dt = ((sparkDrag.x - event.clientX) / Math.max(1, sparkDrag.plotW)) * sparkView.window;
  const now = sparkNow();
  sparkView.follow = false;
  sparkView.end = Math.min(now, sparkDrag.end + dt);
  if (sparkView.end >= now - 0.8) {
    sparkView.follow = true;
    sparkView.end = 0;
  }
  if ($("sparkReset")) $("sparkReset").hidden = sparkView.follow;
  if ($("sparkTitle")) $("sparkTitle").textContent = sparkView.follow ? "Живой график" : "Обзор · пауза";
  refreshSparkNote();
  drawSpark();
}

function onSparkUp() {
  if (!sparkDrag) return;
  sparkDrag = null;
  const card = $("spark") && $("spark").closest(".spark-card");
  if (card) card.classList.remove("grabbing");
  refreshSparkUi();
}

function ensureSparkHistory() {
  const need = Math.ceil(sparkView.window / 60) + 15;
  if (need > historyMinutes) loadHistory(need);
}

function easeToward(cur, next, k) {
  if (next == null) return cur;
  if (cur == null) return next;
  return cur + (next - cur) * k;
}

function sparkValues(source) {
  const bag = {};
  SPARK_LINES.forEach((line) => {
    bag[line.key] = source ? source[line.key] : null;
  });
  return bag;
}

function easeSparkBag(bag, target, k) {
  SPARK_LINES.forEach((line) => {
    bag[line.key] = easeToward(bag[line.key], target[line.key], k);
  });
  return bag;
}

function tickSpark(ts) {
  sparkRaf = requestAnimationFrame(tickSpark);
  if (!$("view-overview") || !$("view-overview").classList.contains("on")) return;
  if ($("dashboard") && $("dashboard").hidden) return;
  const prev = sparkEase.at || ts;
  const dt = Math.min(0.08, Math.max(0.008, (ts - prev) / 1000));
  sparkEase.at = ts;
  const k = 1 - Math.exp(-dt / 0.42);
  const reading = state && state.reading;
  const liveTarget = reading ? {
    in: reading.input_v,
    out: reading.output_v,
    load: readingWatts(reading),
    batt: reading.battery_v,
    soc: reading.battery_pct,
    hz: reading.frequency,
    temp: reading.temperature,
  } : {};
  easeSparkBag(sparkEase.live, liveTarget, k);
  const last = points[points.length - 1];
  if (last) {
    if (sparkEase.lastT !== last.t) {
      if (!sparkEase.shown || sparkEase.lastT === 0) {
        sparkEase.shown = { ...sparkEase.live };
      }
      sparkEase.lastT = last.t;
    }
    easeSparkBag(sparkEase.shown, sparkValues(last), k);
  }
  drawSpark();
}

function drawSpark() {
  const canvas = $("spark");
  if (!canvas) return;
  const lines = sparkLines();
  const span = sparkSpan();
  const visible = points.filter((point) => point.t >= span.start - 2 && point.t <= span.end + 2);
  const data = visible.map((point, index) => {
    const copy = { ...point };
    if (sparkView.follow && index === visible.length - 1 && sparkEase.shown) {
      SPARK_LINES.forEach((line) => {
        if (sparkEase.shown[line.key] != null) copy[line.key] = sparkEase.shown[line.key];
      });
    }
    return copy;
  });
  if (sparkView.follow && data.length && state && state.reading) {
    const head = { t: span.end, ...sparkEase.live };
    const last = data[data.length - 1];
    if (head.t <= last.t + gapLimit(data)) {
      if (head.t > last.t + 0.04) data.push(head);
      else Object.assign(last, head, { t: last.t });
    }
  }
  drawLines(canvas, data, lines, {
    pad: 10,
    axis: false,
    unit: lines.length === 1 ? lines[0].unit : "",
    t0: span.start,
    t1: span.end,
    smooth: true,
    perUnit: true,
    ticks: true,
    gap: gapLimit(data),
  });
}

function drawChart(refreshLegend = true) {
  const spec = SERIES[series];
  const visible = spec.lines.filter((line) => !hiddenLines[line.key]);
  if (refreshLegend) {
    $("chartLegend").innerHTML = spec.lines.map((line) => `
      <button type="button" data-key="${escapeAttr(line.key)}" class="${hiddenLines[line.key] ? "" : "on"}">
        <i style="background:${line.color}"></i>${escapeHtml(line.name)}
      </button>`).join("");
  }
  drawLines($("chart"), points, visible.length ? visible : spec.lines, {
    pad: 18,
    axis: true,
    unit: spec.unit,
    digits: series === "load" || series === "charge" ? 0 : 1,
    gap: gapLimit(points),
  });
}

function syncPackCells(series, groups) {
  const pack = $("battPack");
  if (!pack) return;
  const across = Math.max(1, Math.min(8, Number(series) || 2));
  const down = Math.max(1, Math.min(6, Number(groups) || 1));
  const key = `${across}x${down}`;
  if (pack.dataset.layout === key) return;
  pack.dataset.layout = key;
  pack.innerHTML = Array.from({ length: down }, (_row, row) => {
    const cells = Array.from({ length: across }, (_cell, index) => `
      <div class="cell">
        <div class="cap"></div>
        <div class="jar"><div class="bat-fill"></div></div>
        <p class="cell-idx">${down > 1 ? `${row + 1}.${index + 1}` : index + 1}</p>
      </div>`).join("");
    return `<div class="string">${cells}</div>`;
  }).join("");
}

function lineScale(data, lines) {
  const groups = {};
  lines.forEach((line) => {
    const unit = line.unit || "";
    if (!groups[unit]) groups[unit] = { min: Infinity, max: -Infinity };
  });
  data.forEach((point) => {
    lines.forEach((line) => {
      const value = point[line.key];
      if (value == null) return;
      const group = groups[line.unit || ""];
      group.min = Math.min(group.min, value);
      group.max = Math.max(group.max, value);
    });
  });
  Object.keys(groups).forEach((unit) => {
    const group = groups[unit];
    if (!Number.isFinite(group.min)) {
      group.min = 0;
      group.max = 1;
    }
    if (group.min === group.max) {
      group.min -= 1;
      group.max += 1;
    }
    const span = group.max - group.min;
    const targetMin = group.min - span * 0.12;
    const targetMax = group.max + span * 0.12;
    const eased = sparkEase.units[unit] || { min: targetMin, max: targetMax };
    eased.min = easeToward(eased.min, targetMin, 0.12);
    eased.max = easeToward(eased.max, targetMax, 0.12);
    sparkEase.units[unit] = eased;
    group.min = eased.min;
    group.max = eased.max;
  });
  return groups;
}

function splitByGap(samples, limit) {
  const groups = [];
  let cur = [];
  samples.forEach((point) => {
    if (cur.length && point.t - cur[cur.length - 1].t > limit) {
      groups.push(cur);
      cur = [];
    }
    cur.push(point);
  });
  if (cur.length) groups.push(cur);
  return groups;
}

function fillBoostBands(ctx, data, lines, xOf, yOfValue, gap) {
  const inLine = lines.find((line) => line.key === "in");
  const outLine = lines.find((line) => line.key === "out");
  if (!inLine || !outLine) return;
  const samples = data.filter((point) => point.in != null && point.out != null);
  splitByGap(samples, gap).forEach((chunk) => {
    let run = [];
    const flush = () => {
      if (run.length < 2) {
        run = [];
        return;
      }
      ctx.beginPath();
      run.forEach((point, index) => {
        const x = xOf(point.t);
        const y = yOfValue(point.out, outLine.unit);
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      for (let i = run.length - 1; i >= 0; i -= 1) {
        const point = run[i];
        ctx.lineTo(xOf(point.t), yOfValue(point.in, inLine.unit));
      }
      ctx.closePath();
      ctx.fillStyle = "rgba(224, 176, 106, 0.32)";
      ctx.fill();
      run = [];
    };
    chunk.forEach((point) => {
      if (pointBoost(point)) run.push(point);
      else flush();
    });
    flush();
  });
}

function strokeSmooth(ctx, pts) {
  if (pts.length < 2) return;
  ctx.beginPath();
  ctx.moveTo(pts[0].x, pts[0].y);
  if (pts.length === 2) {
    ctx.lineTo(pts[1].x, pts[1].y);
    ctx.stroke();
    return;
  }
  for (let i = 0; i < pts.length - 1; i += 1) {
    const p0 = pts[Math.max(0, i - 1)];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[Math.min(pts.length - 1, i + 2)];
    const [c1, c2] = splineControls(p0, p1, p2, p3);
    ctx.bezierCurveTo(c1.x, c1.y, c2.x, c2.y, p2.x, p2.y);
  }
  ctx.stroke();
}

function splineControls(p0, p1, p2, p3) {
  const d1 = Math.max(1e-3, Math.hypot(p1.x - p0.x, p1.y - p0.y));
  const d2 = Math.max(1e-3, Math.hypot(p2.x - p1.x, p2.y - p1.y));
  const d3 = Math.max(1e-3, Math.hypot(p3.x - p2.x, p3.y - p2.y));
  const s1 = (2 * d1) / (d1 + d2);
  const s2 = (2 * d3) / (d2 + d3);
  return [
    {
      x: p1.x + (p2.x - p0.x) * s1 / 9,
      y: p1.y + (p2.y - p0.y) * s1 / 9,
    },
    {
      x: p2.x - (p3.x - p1.x) * s2 / 9,
      y: p2.y - (p3.y - p1.y) * s2 / 9,
    },
  ];
}

function layoutSchemeTraces() {
  const hud = document.querySelector(".scheme-hud");
  if (!hud) return;
  const drop = hud.querySelector('[data-slot="drop"]');
  const rise = hud.querySelector('[data-slot="rise"]');
  const inn = hud.querySelector('[data-slot="in"]');
  const xfmr = hud.querySelector('[data-slot="xfmr"]');
  const out = hud.querySelector('[data-slot="out"]');
  const inv = hud.querySelector('[data-slot="inv"]');
  if (!drop || !rise || !inn || !xfmr || !out || !inv) return;
  const box = hud.getBoundingClientRect();
  const stand = (trace, topEl, botEl) => {
    const top = topEl.getBoundingClientRect();
    const bot = botEl.getBoundingClientRect();
    const x = top.left + top.width / 2 - box.left;
    const y0 = top.bottom - box.top - 2;
    const y1 = bot.top - box.top + 2;
    trace.style.left = `${Math.round(x - 7)}px`;
    trace.style.top = `${Math.round(y0)}px`;
    trace.style.height = `${Math.max(12, Math.round(y1 - y0))}px`;
  };
  stand(drop, inn, xfmr);
  stand(rise, out, inv);
}

function drawLines(canvas, data, lines, options) {
  const width = canvas.clientWidth || 600;
  const fixed = Number(canvas.dataset.h || 0);
  const height = canvas.id === "spark"
    ? Math.max(88, canvas.clientHeight || 120)
    : (fixed || canvas.clientHeight || 120);
  if (canvas.id !== "spark") canvas.style.height = `${height}px`;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const pad = options.pad;
  const digits = options.digits ?? 1;
  if (data.length < 2) {
    ctx.fillStyle = "#7f9aaa";
    ctx.font = "14px Segoe UI";
    ctx.fillText("История копится — график появится через пару отсчётов.", pad, height / 2);
    canvas._plot = null;
    return;
  }
  const values = data.flatMap((point) => lines.map((line) => point[line.key])).filter((value) => value != null);
  if (!values.length) {
    ctx.fillStyle = "#7f9aaa";
    ctx.font = "14px Segoe UI";
    ctx.fillText("Нет точек для выбранных линий.", pad, height / 2);
    canvas._plot = null;
    return;
  }
  const scales = options.perUnit ? lineScale(data, lines) : null;
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const span = max - min;
  min -= span * 0.12;
  max += span * 0.12;
  const left = options.axis ? 58 : pad;
  const bottom = options.axis || options.ticks ? 22 : 10;
  const plotW = width - left - 16;
  const plotH = height - pad - bottom;
  const t0 = options.t0 != null ? options.t0 : data[0].t;
  const t1 = options.t1 != null ? options.t1 : data[data.length - 1].t;
  const tSpan = Math.max(1, t1 - t0);
  const xOf = (t) => left + ((t - t0) / tSpan) * plotW;
  const yOfValue = (value, unit) => {
    if (scales) {
      const group = scales[unit || ""] || { min, max };
      return pad + ((group.max - value) / (group.max - group.min)) * plotH;
    }
    return pad + ((max - value) / (max - min)) * plotH;
  };
  const yOf = (value) => yOfValue(value, lines[0] && lines[0].unit);
  ctx.strokeStyle = "rgba(232,220,196,0.10)";
  ctx.lineWidth = 1;
  ctx.fillStyle = "#93a79a";
  ctx.font = "11px Cascadia Mono, Consolas, monospace";
  for (let i = 0; i <= 4; i += 1) {
    const y = pad + (plotH * i) / 4;
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(width - 16, y);
    ctx.stroke();
    if (options.axis) {
      const value = max - ((max - min) * i) / 4;
      ctx.fillText(value.toFixed(digits), 6, y + 4);
    }
  }
  if (options.axis || options.ticks) {
    const marks = options.ticks ? 3 : 4;
    for (let i = 0; i <= marks; i += 1) {
      const t = t0 + (tSpan * i) / marks;
      const x = xOf(t);
      ctx.fillText(clockTime(t).slice(0, 5), Math.max(left, x - 16), height - 6);
    }
  }
  const gap = Number(options.gap) || 5;
  fillBoostBands(ctx, data, lines, xOf, yOfValue, gap);
  lines.forEach((line) => {
    const samples = data.filter((point) => point[line.key] != null);
    splitByGap(samples, gap).forEach((chunk) => {
      if (chunk.length < 2) return;
      const pts = chunk.map((point) => ({
        x: xOf(point.t),
        y: yOfValue(point[line.key], line.unit),
      }));
      const first = pts[0];
      const last = pts[pts.length - 1];
      const fill = ctx.createLinearGradient(0, pad, 0, pad + plotH);
      fill.addColorStop(0, hexAlpha(line.color, 0.22));
      fill.addColorStop(1, hexAlpha(line.color, 0.01));
      ctx.beginPath();
      ctx.moveTo(first.x, first.y);
      if (options.smooth) {
        for (let i = 0; i < pts.length - 1; i += 1) {
          const p0 = pts[Math.max(0, i - 1)];
          const p1 = pts[i];
          const p2 = pts[i + 1];
          const p3 = pts[Math.min(pts.length - 1, i + 2)];
          ctx.bezierCurveTo(
            p1.x + (p2.x - p0.x) / 6,
            p1.y + (p2.y - p0.y) / 6,
            p2.x - (p3.x - p1.x) / 6,
            p2.y - (p3.y - p1.y) / 6,
            p2.x,
            p2.y,
          );
        }
      } else {
        pts.slice(1).forEach((pt) => ctx.lineTo(pt.x, pt.y));
      }
      ctx.lineTo(last.x, pad + plotH);
      ctx.lineTo(first.x, pad + plotH);
      ctx.closePath();
      ctx.fillStyle = fill;
      ctx.fill();
      ctx.lineWidth = 2.4;
      ctx.strokeStyle = line.color;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.shadowColor = hexAlpha(line.color, 0.35);
      ctx.shadowBlur = 8;
      if (options.smooth) strokeSmooth(ctx, pts);
      else {
        ctx.beginPath();
        pts.forEach((pt, index) => {
          if (index === 0) ctx.moveTo(pt.x, pt.y);
          else ctx.lineTo(pt.x, pt.y);
        });
        ctx.stroke();
      }
      ctx.shadowBlur = 0;
    });
  });
  const plot = {
    data, lines, min, max, left, pad, plotW, plotH,
    t0, t1, unit: options.unit || "", xOf,
    yOf: (value, unit) => yOfValue(value, unit),
  };
  canvas._plot = plot;
  const hover = canvas.id === "chart" ? chartHoverX : sparkHoverX;
  if (hover != null) paintCrosshair(ctx, plot, hover);
}

function nearestPoint(plot, x) {
  if (!plot || plot.data.length < 2) return null;
  let best = plot.data[0];
  let bestDist = Infinity;
  plot.data.forEach((point) => {
    const dist = Math.abs(plot.xOf(point.t) - x);
    if (dist < bestDist) {
      best = point;
      bestDist = dist;
    }
  });
  return best;
}

function paintCrosshair(ctx, plot, x) {
  const point = nearestPoint(plot, x);
  if (!point) return;
  const px = plot.xOf(point.t);
  ctx.save();
  ctx.strokeStyle = "rgba(232,220,196,0.35)";
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(px, plot.pad);
  ctx.lineTo(px, plot.pad + plot.plotH);
  ctx.stroke();
  ctx.setLineDash([]);
  plot.lines.forEach((line) => {
    const value = point[line.key];
    if (value == null) return;
    const y = plot.yOf(value, line.unit);
    ctx.fillStyle = line.color;
    ctx.beginPath();
    ctx.arc(px, y, 4.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#10140f";
    ctx.lineWidth = 1.5;
    ctx.stroke();
  });
  ctx.restore();
}

function lineDigits(line, unit) {
  const suffix = (line && line.unit) || unit || "";
  return suffix === "Вт" || suffix === "%" ? 0 : 1;
}

function plotTipHtml(point, lines, unit) {
  const rows = lines.map((line) => {
    const value = point[line.key];
    if (value == null) return "";
    const suffix = line.unit || unit || "";
    return `<div><i style="background:${escapeAttr(line.color)}"></i>${escapeHtml(line.name)} <b>${escapeHtml(String(fixed(value, lineDigits(line, unit))))}</b> ${escapeHtml(suffix)}</div>`;
  }).join("");
  const extra = pointBoost(point) && point.in != null && point.out != null
    ? `<div class="boost">подпитка +${escapeHtml(String((point.out - point.in).toFixed(1)))} В</div>`
    : "";
  return `<strong>${escapeHtml(clockTime(point.t))}</strong>${rows}${extra}`;
}

function showPlotTip(event, html) {
  const tip = $("plotTip");
  if (!tip) return;
  tip.innerHTML = html;
  tip.hidden = false;
  const pad = 16;
  const tw = tip.offsetWidth;
  const th = tip.offsetHeight;
  let x = event.clientX + pad;
  let y = event.clientY + pad;
  if (x + tw > window.innerWidth - 8) x = event.clientX - tw - 12;
  if (y + th > window.innerHeight - 8) y = event.clientY - th - 12;
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${Math.max(8, y)}px`;
}

function hidePlotTip() {
  const tip = $("plotTip");
  if (tip) tip.hidden = true;
}

function hoverChart(event) {
  const canvas = $("chart");
  const plot = canvas._plot;
  if (!plot) return;
  const rect = canvas.getBoundingClientRect();
  chartHoverX = event.clientX - rect.left;
  const point = nearestPoint(plot, chartHoverX);
  if (!point) return;
  const bits = plot.lines.map((line) => `${line.name} ${fixed(point[line.key], lineDigits(line, plot.unit))} ${line.unit || plot.unit}`);
  $("chartTip").textContent = `${clockTime(point.t)}  ·  ${bits.join("   ")}`;
  showPlotTip(event, plotTipHtml(point, plot.lines, plot.unit));
  drawChart(false);
}

function hoverSpark(event) {
  if (sparkDrag) {
    hidePlotTip();
    return;
  }
  const canvas = $("spark");
  const plot = canvas._plot;
  if (!plot) return;
  const rect = canvas.getBoundingClientRect();
  sparkHoverX = event.clientX - rect.left;
  const point = nearestPoint(plot, sparkHoverX);
  if (point) showPlotTip(event, plotTipHtml(point, plot.lines, ""));
  refreshSparkNote();
  drawSpark();
}

function hexAlpha(hex, alpha) {
  const value = hex.replace("#", "");
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function pillText(snapshot) {
  const reading = snapshot.reading;
  const age = reading ? Date.now() / 1000 - reading.updated_at : 1e9;
  if (snapshot.mode === "live") {
    return age < 5 ? "ИБП на связи" : "Жду ответ ИБП";
  }
  if (snapshot.mode === "demo") return "Демонстрация";
  if (snapshot.mode === "blocked") return snapshot.blocker ? `Порт занят · ${snapshot.blocker}` : "Порт занят";
  if (snapshot.mode === "missing") return "ИБП не найден";
  return "Ищу устройство…";
}

function gateTitle(snapshot) {
  if (snapshot.mode === "blocked") return snapshot.blocker ? `${snapshot.blocker} держит порт` : "Порт занят";
  if (snapshot.mode === "missing") return "Бесперебойник не найден";
  return "Ищу бесперебойник";
}

function gateText(snapshot) {
  if (snapshot.mode === "blocked") {
    return "ИБП подключён по USB, но порт уже открыт другой программой. Закройте её, включая значок у часов, и нажмите кнопку ниже. UPS читает ИБП сам, UPSmart для этого не нужен.";
  }
  if (snapshot.mode === "missing") {
    return snapshot.message || "Проверьте USB-кабель. Отдельная программа и отдельный драйвер не нужны.";
  }
  return snapshot.message || "Ищу ИБП по USB. Программа UPSmart для этого не нужна.";
}

function portText(snapshot) {
  if (snapshot.link) return snapshot.link;
  if (snapshot.mode === "demo") return "демо";
  if (snapshot.mode === "blocked") return "Mega USB, занят";
  return "—";
}

function fixed(value, digits) {
  return value == null || Number.isNaN(Number(value)) ? "—" : Number(value).toFixed(digits);
}

function clock(total) {
  const seconds = Math.max(0, total | 0);
  const hh = String(Math.floor(seconds / 3600)).padStart(2, "0");
  const mm = String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

function clockTime(unix) {
  return new Date(unix * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function runtime(minutesValue) {
  if (minutesValue == null) return "—";
  if (minutesValue < 60) return `≈ ${minutesValue} мин`;
  const hours = Math.floor(minutesValue / 60);
  const rest = minutesValue % 60;
  return rest ? `≈ ${hours} ч ${rest} мин` : `≈ ${hours} ч`;
}

function setReportKind(kind) {
  reportKind = kind === "day" ? "day" : "summary";
  $("repSummary").classList.toggle("on", reportKind === "summary");
  $("repDay").classList.toggle("on", reportKind === "day");
  loadReport(true);
}

function shiftReportDay(delta) {
  if (!reportDays.length) return;
  const current = reportDate || reportDays[reportDays.length - 1];
  let index = reportDays.indexOf(current);
  if (index < 0) index = reportDays.length - 1;
  index = Math.max(0, Math.min(reportDays.length - 1, index + delta));
  reportDate = reportDays[index];
  $("repDate").value = reportDate;
  setReportKind("day");
}

async function loadReport(force) {
  const meta = await fetch("/api/reports/meta").then((item) => item.json()).catch(() => ({}));
  reportDays = meta.days || [];
  if (!reportDate) reportDate = meta.date || reportDays[reportDays.length - 1] || "";
  if ($("repDate") && !$("repDate").value) $("repDate").value = reportDate;
  $("repPath").textContent = meta.dir || "";
  const date = ($("repDate").value || reportDate || "").trim();
  const query = reportKind === "day" ? `kind=day&date=${encodeURIComponent(date)}` : "kind=summary";
  const url = `/api/reports/html?${query}&t=${force ? Date.now() : ""}`;
  $("repFrame").src = url;
}

async function openReportFolder() {
  const data = await fetch("/api/reports/open-folder", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then((item) => item.json()).catch(() => ({}));
  toast(data.message || "Папка журнала");
}

function setEnergyPreset(preset) {
  energyPreset = preset || "today";
  energyDay = "";
  markEnergyPreset();
  energyRange = energyWindow(energyPreset);
  closeEnergyPop(false);
  loadEnergy(true);
}

function markEnergyPreset() {
  document.querySelectorAll("[data-en]").forEach((button) => {
    button.classList.toggle("on", button.dataset.en === energyPreset);
  });
}

function energyWindow(preset) {
  const now = new Date();
  let start = new Date(now);
  let end = new Date(now);
  if (preset === "yesterday") {
    start.setDate(start.getDate() - 1);
    start.setHours(0, 0, 0, 0);
    end = new Date(start);
    end.setDate(end.getDate() + 1);
  } else if (preset === "24h") {
    start = new Date(now.getTime() - 24 * 3600 * 1000);
  } else if (preset === "7d") {
    start = new Date(now.getTime() - 7 * 24 * 3600 * 1000);
  } else if (preset === "30d") {
    start = new Date(now.getTime() - 30 * 24 * 3600 * 1000);
  } else {
    start.setHours(0, 0, 0, 0);
  }
  return { from: start, to: end };
}

function ensureEnergyRange() {
  if (!energyRange) energyRange = energyWindow(energyPreset);
  return energyRange;
}

function shiftEnergy(dir) {
  const range = ensureEnergyRange();
  const span = Math.max(60 * 1000, range.to.getTime() - range.from.getTime());
  const from = new Date(range.from.getTime() + dir * span);
  const to = new Date(range.to.getTime() + dir * span);
  const now = Date.now();
  if (to.getTime() > now + 120000) {
    energyRange = { from: new Date(now - span), to: new Date(now) };
  } else {
    energyRange = { from, to };
  }
  energyPreset = "custom";
  energyDay = "";
  markEnergyPreset();
  loadEnergy(true);
}

function formatEnergyStamp(date, withTime) {
  const months = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
  const pad = (value) => String(value).padStart(2, "0");
  const day = `${date.getDate()} ${months[date.getMonth()]}`;
  if (!withTime) return day;
  return `${day}, ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function paintEnergyRange() {
  const range = ensureEnergyRange();
  const same = range.from.toDateString() === range.to.toDateString();
  $("enRangeLabel").textContent = same
    ? `${formatEnergyStamp(range.from, true)} — ${pad2(range.to.getHours())}:${pad2(range.to.getMinutes())}`
    : `${formatEnergyStamp(range.from, true)} — ${formatEnergyStamp(range.to, true)}`;
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

function stampDay(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

function parseStamp(stamp) {
  const parts = String(stamp || "").split("-").map(Number);
  if (parts.length !== 3 || parts.some((item) => !item)) return null;
  return new Date(parts[0], parts[1] - 1, parts[2]);
}

function toggleEnergyPop() {
  const pop = $("enPop");
  if (!pop.hidden) {
    closeEnergyPop(true);
    return;
  }
  const range = ensureEnergyRange();
  energyCal.month = new Date(range.from.getFullYear(), range.from.getMonth(), 1);
  energyCal.focus = "from";
  pop.hidden = false;
  $("enRangeBtn").classList.add("on");
  $("enRangeBtn").setAttribute("aria-expanded", "true");
  paintCalendar();
}

function closeEnergyPop(apply) {
  const pop = $("enPop");
  if (pop) pop.hidden = true;
  if ($("enRangeBtn")) {
    $("enRangeBtn").classList.remove("on");
    $("enRangeBtn").setAttribute("aria-expanded", "false");
  }
  if (apply) {
    energyPreset = "custom";
    markEnergyPreset();
    loadEnergy(true);
  }
}

function shiftCalMonth(dir) {
  energyCal.month = new Date(energyCal.month.getFullYear(), energyCal.month.getMonth() + dir, 1);
  paintCalendar();
}

function paintCalendar() {
  const range = ensureEnergyRange();
  const year = energyCal.month.getFullYear();
  const month = energyCal.month.getMonth();
  const names = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
  $("calTitle").textContent = `${names[month]} ${year}`;
  $("enHFrom").textContent = pad2(range.from.getHours());
  $("enMFrom").textContent = pad2(range.from.getMinutes());
  $("enHTo").textContent = pad2(range.to.getHours());
  $("enMTo").textContent = pad2(range.to.getMinutes());
  const first = new Date(year, month, 1);
  let weekday = first.getDay() - 1;
  if (weekday < 0) weekday = 6;
  const start = new Date(year, month, 1 - weekday);
  const today = stampDay(new Date());
  const fromStamp = stampDay(range.from);
  const toStamp = stampDay(range.to);
  const nodes = [];
  for (let index = 0; index < 42; index++) {
    const date = new Date(start.getFullYear(), start.getMonth(), start.getDate() + index);
    const stamp = stampDay(date);
    const classes = [];
    if (date.getMonth() !== month) classes.push("mute");
    if (stamp === today) classes.push("today");
    if (stamp === fromStamp || stamp === toStamp) classes.push("edge");
    else if (stamp > fromStamp && stamp < toStamp) classes.push("in");
    const future = date.setHours(0, 0, 0, 0) > Date.now();
    nodes.push(`<button type="button" data-stamp="${stamp}" class="${classes.join(" ")}" ${future ? "disabled" : ""}>${date.getDate()}</button>`);
  }
  $("calGrid").innerHTML = nodes.join("");
  paintEnergyRange();
}

function onCalDayClick(event) {
  const button = event.target.closest("button[data-stamp]");
  if (!button || button.disabled) return;
  const picked = parseStamp(button.dataset.stamp);
  if (!picked) return;
  const range = ensureEnergyRange();
  const fromDay = stampDay(range.from);
  const pickedDay = stampDay(picked);
  if (energyCal.focus === "to" && pickedDay >= fromDay) {
    range.to = joinDateTime(picked, range.to);
    energyCal.focus = "from";
  } else {
    range.from = joinDateTime(picked, range.from);
    if (stampDay(range.to) < pickedDay) range.to = joinDateTime(picked, range.to);
    energyCal.focus = "to";
  }
  if (range.to.getTime() <= range.from.getTime()) {
    range.to = new Date(range.from.getTime() + 60 * 60 * 1000);
  }
  energyRange = range;
  energyPreset = "custom";
  energyDay = "";
  markEnergyPreset();
  paintCalendar();
}

function joinDateTime(date, timeSource) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate(), timeSource.getHours(), timeSource.getMinutes(), 0, 0);
}

function spinEnergyTime(which, part, delta) {
  const range = ensureEnergyRange();
  const date = which === "to" ? range.to : range.from;
  energyCal.focus = which;
  if (part === "h") date.setHours(date.getHours() + delta);
  else {
    const minutes = date.getMinutes() + delta;
    date.setMinutes(Math.round(minutes / 5) * 5);
  }
  if (range.to.getTime() <= range.from.getTime()) {
    if (which === "to") range.to = new Date(range.from.getTime() + 5 * 60 * 1000);
    else range.from = new Date(range.to.getTime() - 5 * 60 * 1000);
  }
  energyRange = range;
  energyPreset = "custom";
  markEnergyPreset();
  paintCalendar();
}

function applyEnergyQuick(kind) {
  const range = ensureEnergyRange();
  const now = new Date();
  const target = energyCal.focus === "to" ? range.to : range.from;
  if (kind === "start") target.setHours(0, 0, 0, 0);
  else if (kind === "noon") target.setHours(12, 0, 0, 0);
  else if (kind === "end") target.setHours(23, 59, 0, 0);
  else {
    target.setHours(now.getHours(), now.getMinutes(), 0, 0);
  }
  if (range.to.getTime() <= range.from.getTime()) {
    range.to = new Date(range.from.getTime() + 60 * 60 * 1000);
  }
  energyRange = range;
  energyPreset = "custom";
  markEnergyPreset();
  paintCalendar();
}

async function loadEnergy(force) {
  const range = ensureEnergyRange();
  paintEnergyRange();
  const from = Math.floor(Math.min(range.from.getTime(), range.to.getTime()) / 1000);
  const to = Math.floor(Math.max(range.from.getTime(), range.to.getTime()) / 1000);
  const data = await fetch(`/api/energy?from=${from}&to=${to}&t=${force ? Date.now() : ""}`).then((item) => item.json()).catch(() => null);
  energyData = data && data.ok !== false ? data : { energyKwh: 0, points: [], days: [] };
  const points = energyData.points || [];
  energyView = {
    t0: points.length ? points[0].t : from,
    t1: points.length ? points[points.length - 1].t : to,
  };
  paintEnergy();
}

function paintEnergy() {
  const data = energyData || {};
  const items = [
    ["Энергия", fmtKwh(data.energyKwh), "кВт·ч"],
    ["С сети", fmtKwh(data.energyKwhMains), "кВт·ч"],
    ["С батареи", fmtKwh(data.energyKwhBattery), "кВт·ч"],
    ["Средняя", data.avgW == null ? "—" : String(data.avgW), "Вт"],
    ["Пик", data.peakW == null ? "—" : String(data.peakW), "Вт"],
    ["На батарее", String(data.onBatteryMinutes || 0), "мин"],
  ];
  $("enKpis").innerHTML = items.map(([label, value, unit]) =>
    `<article><h3>${label}</h3><p>${value}<small>${unit}</small></p></article>`
  ).join("");
  const days = data.days || [];
  const maxKwh = Math.max.apply(null, days.map((day) => Number(day.energyKwh) || 0).concat([0.01]));
  $("enDays").innerHTML = days.length
    ? days.slice().reverse().map((day) => {
      const width = Math.max(4, Math.round((Number(day.energyKwh) || 0) / maxKwh * 100));
      const on = energyDay === day.date ? " on" : "";
      return `
        <button type="button" class="energy-day${on}" data-date="${day.date}">
          <div class="energy-day-top"><strong>${formatEnergyStamp(parseStamp(day.date) || new Date(), false)}</strong><b>${fmtKwh(day.energyKwh)} кВт·ч</b></div>
          <div class="energy-day-line"><i style="width:${width}%"></i></div>
          <p>средняя ${day.avgW == null ? "—" : day.avgW} Вт · пик ${day.peakW == null ? "—" : day.peakW} Вт</p>
        </button>`;
    }).join("")
    : `<p class="empty">За этот период замеров нет.</p>`;
  drawEnergyChart();
}

function onEnergyDayClick(event) {
  const button = event.target.closest(".energy-day");
  if (!button) return;
  const date = parseStamp(button.dataset.date);
  if (!date) return;
  energyDay = button.dataset.date;
  energyPreset = "custom";
  markEnergyPreset();
  energyRange = {
    from: new Date(date.getFullYear(), date.getMonth(), date.getDate(), 0, 0, 0, 0),
    to: new Date(date.getFullYear(), date.getMonth(), date.getDate() + 1, 0, 0, 0, 0),
  };
  loadEnergy(true);
}

function visibleEnergyPoints() {
  const points = (energyData && energyData.points) || [];
  const t0 = energyView.t0;
  const t1 = energyView.t1;
  const visible = points.filter((point) => point.t >= t0 && point.t <= t1);
  if (visible.length) return visible;
  return points;
}

function energySpan() {
  const points = (energyData && energyData.points) || [];
  if (!points.length) {
    const range = ensureEnergyRange();
    return { t0: range.from.getTime() / 1000, t1: range.to.getTime() / 1000 };
  }
  return { t0: points[0].t, t1: points[points.length - 1].t };
}

function zoomEnergy(factor, anchor) {
  const span = energySpan();
  const width = Math.max(60, energyView.t1 - energyView.t0);
  const next = Math.min(span.t1 - span.t0, Math.max(15 * 60, width * factor));
  const pivot = anchor == null ? (energyView.t0 + energyView.t1) / 2 : anchor;
  let t0 = pivot - (pivot - energyView.t0) / width * next;
  let t1 = t0 + next;
  if (t0 < span.t0) {
    t0 = span.t0;
    t1 = t0 + next;
  }
  if (t1 > span.t1) {
    t1 = span.t1;
    t0 = Math.max(span.t0, t1 - next);
  }
  energyView = { t0, t1 };
  drawEnergyChart();
}

function resetEnergyView() {
  const span = energySpan();
  energyView = { t0: span.t0, t1: span.t1 };
  drawEnergyChart();
}

function onEnergyWheel(event) {
  event.preventDefault();
  const canvas = $("enChart");
  const plot = canvas && canvas._plot;
  const rect = canvas.getBoundingClientRect();
  let anchor = (energyView.t0 + energyView.t1) / 2;
  if (plot) {
    const frac = Math.min(1, Math.max(0, (event.clientX - rect.left - plot.left) / Math.max(1, plot.plotW)));
    anchor = plot.t0 + frac * (plot.t1 - plot.t0);
  }
  zoomEnergy(event.deltaY > 0 ? 1.18 : 1 / 1.18, anchor);
}

function onEnergyDown(event) {
  const canvas = $("enChart");
  const plot = canvas && canvas._plot;
  if (!plot) return;
  canvas.setPointerCapture(event.pointerId);
  const card = canvas.closest(".energy-chart");
  if (card) card.classList.add("grabbing");
  hidePlotTip();
  energyDrag = {
    x: event.clientX,
    t0: energyView.t0,
    t1: energyView.t1,
    plotW: plot.plotW,
  };
}

function onEnergyMove(event) {
  if (!energyDrag) return;
  const width = energyDrag.t1 - energyDrag.t0;
  const dt = ((energyDrag.x - event.clientX) / Math.max(1, energyDrag.plotW)) * width;
  const span = energySpan();
  let t0 = energyDrag.t0 + dt;
  let t1 = energyDrag.t1 + dt;
  if (t0 < span.t0) {
    t0 = span.t0;
    t1 = t0 + width;
  }
  if (t1 > span.t1) {
    t1 = span.t1;
    t0 = t1 - width;
  }
  energyView = { t0, t1 };
  drawEnergyChart();
}

function onEnergyUp() {
  if (!energyDrag) return;
  energyDrag = null;
  const card = $("enChart") && $("enChart").closest(".energy-chart");
  if (card) card.classList.remove("grabbing");
}

function hoverEnergy(event) {
  if (energyDrag) return;
  const canvas = $("enChart");
  const plot = canvas && canvas._plot;
  if (!plot) return;
  const rect = canvas.getBoundingClientRect();
  energyHoverX = event.clientX - rect.left;
  const point = nearestPoint(plot, energyHoverX);
  if (point) {
    const kwh = energyAt(point.t);
    showPlotTip(event, `<strong>${escapeHtml(clockTime(point.t))}</strong>
      <div><i style="background:#ff7a3d"></i>Мощность <b>${escapeHtml(String(point.w))}</b> Вт</div>
      <div><i style="background:#e0b06a"></i>С начала <b>${escapeHtml(fmtKwh(kwh))}</b> кВт·ч</div>
      ${point.on ? '<div class="boost">на батарее</div>' : ""}`);
    $("enNote").textContent = `${clockTime(point.t)}  ·  ${point.w} Вт${point.on ? "  ·  батарея" : ""}`;
  }
  drawEnergyChart();
}

function energyAt(ts) {
  const points = visibleEnergyPoints();
  if (!points.length) return 0;
  let kwh = 0;
  for (let index = 1; index < points.length; index++) {
    const prev = points[index - 1];
    const next = points[index];
    if (prev.t > ts) break;
    const t1 = Math.min(next.t, ts);
    const dt = t1 - prev.t;
    if (dt <= 0) continue;
    const w1 = prev.w;
    const w2 = t1 === next.t ? next.w : prev.w;
    kwh += ((w1 + w2) / 2) * dt / 3600 / 1000;
    if (next.t >= ts) break;
  }
  return kwh;
}

function drawEnergyChart() {
  const canvas = $("enChart");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 320;
  const height = canvas.clientHeight || 220;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const all = energySpan();
  const zoomed = Math.abs((energyView.t1 - energyView.t0) - (all.t1 - all.t0)) > 30;
  if ($("enZoomReset")) $("enZoomReset").hidden = !zoomed;
  if ($("enZoomLabel")) $("enZoomLabel").textContent = fmtHours((energyView.t1 - energyView.t0) / 3600);
  const points = visibleEnergyPoints();
  if (!points.length) {
    canvas._plot = null;
    ctx.fillStyle = "#7f9aaa";
    ctx.font = "14px Segoe UI, sans-serif";
    ctx.fillText("Нет точек за этот период", 16, height / 2);
    return;
  }
  const values = points.map((point) => Number(point.w) || 0);
  const max = Math.max.apply(null, values.concat([1]));
  const t0 = energyView.t0;
  const t1 = Math.max(t0 + 1, energyView.t1);
  const left = 48, right = 12, top = 12, bottom = 28;
  const innerW = width - left - right;
  const innerH = height - top - bottom;
  const xOf = (ts) => left + ((ts - t0) / (t1 - t0)) * innerW;
  const yOf = (watts) => top + (1 - (Number(watts) || 0) / max) * innerH;
  ctx.strokeStyle = "rgba(77,226,255,.12)";
  ctx.fillStyle = "#7f9aaa";
  ctx.font = "11px Segoe UI, sans-serif";
  for (let i = 0; i < 4; i++) {
    const y = top + innerH * i / 3;
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(width - right, y); ctx.stroke();
    ctx.fillText(String(Math.round(max * (1 - i / 3))), 4, y + 4);
  }
  let batt = false;
  ctx.fillStyle = "rgba(224,176,106,.16)";
  points.forEach((point, index) => {
    if (!point.on) {
      batt = false;
      return;
    }
    const x = xOf(point.t);
    const next = points[index + 1];
    const x2 = next ? xOf(next.t) : x + 2;
    if (!batt) {
      ctx.beginPath();
      ctx.moveTo(x, top + innerH);
      batt = true;
    }
    ctx.lineTo(x, yOf(point.w));
    if (!next || !next.on) {
      ctx.lineTo(x2, top + innerH);
      ctx.closePath();
      ctx.fill();
      batt = false;
    }
  });
  ctx.beginPath();
  points.forEach((point, index) => {
    const x = xOf(point.t);
    const y = yOf(point.w);
    if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = "#ff7a3d";
  ctx.lineWidth = 2;
  ctx.lineJoin = "round";
  ctx.stroke();
  ctx.fillStyle = "rgba(255,122,61,.14)";
  ctx.lineTo(xOf(points[points.length - 1].t), top + innerH);
  ctx.lineTo(xOf(points[0].t), top + innerH);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = "#7f9aaa";
  const mid = (t0 + t1) / 2;
  const long = (t1 - t0) > 36 * 3600;
  const label = (ts) => {
    const date = new Date(ts * 1000);
    return long ? `${pad2(date.getDate())}.${pad2(date.getMonth() + 1)} ${pad2(date.getHours())}:${pad2(date.getMinutes())}` : clockTime(ts);
  };
  ctx.fillText(label(t0), left, height - 8);
  ctx.fillText(label(mid), left + innerW / 2 - 28, height - 8);
  ctx.fillText(label(t1), width - 78, height - 8);
  if (!energyDrag && energyHoverX == null) {
    $("enNote").textContent = (energyData && energyData.kva)
      ? `нагрузка ИБП · ${Number(energyData.kva).toFixed(1)} кВА · ${fmtHours(energyData.hours)} · колёсико и перетаскивание`
      : "Колёсико — масштаб, перетаскивание — сдвиг, наведение — точное значение.";
  }
  const plot = {
    data: points,
    lines: [{ key: "w", name: "Мощность", color: "#ff7a3d", unit: "Вт" }],
    min: 0,
    max,
    left,
    pad: top,
    plotW: innerW,
    plotH: innerH,
    t0,
    t1,
    unit: "Вт",
    xOf,
    yOf: (value) => yOf(value),
  };
  canvas._plot = plot;
  if (energyHoverX != null) paintCrosshair(ctx, plot, energyHoverX);
}

function fmtKwh(value) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  if (n === 0) return "0";
  if (n < 0.01) return n.toFixed(3);
  if (n < 10) return n.toFixed(2);
  return n.toFixed(1);
}

function fmtHours(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return "—";
  if (n < 1) return `${Math.round(n * 60)} мин`;
  if (n < 48) return `${n.toFixed(1)} ч`;
  return `${(n / 24).toFixed(1)} сут`;
}

function beep() {
  try {
  const ctx = audioCtx || new AudioContext();
  audioCtx = ctx;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.frequency.value = 620;
  osc.connect(gain);
  gain.connect(ctx.destination);
  const now = ctx.currentTime;
  gain.gain.setValueAtTime(0.0001, now);
  gain.gain.exponentialRampToValueAtTime(0.07, now + 0.03);
  gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.45);
  osc.start(now);
  osc.stop(now + 0.5);
  } catch (err) {
    /* браузер ещё не разрешил звук */
  }
}

function showQuitDialog() {
  const dialog = $("quitDialog");
  if (dialog && !dialog.open) dialog.showModal();
}

function prepareForScreenshot() {
  const current = document.querySelector(".tab.on");
  const name = current ? current.dataset.tab : "overview";
  const settings = $("settings");
  const quit = $("quitDialog");
  const lab = $("labDialog");
  const scan = $("scanDialog");
  const chat = $("chatDialog");
  if (settings && settings.open) settings.close();
  if (quit && quit.open) quit.close();
  if (lab && lab.open) lab.close();
  if (scan && scan.open) scan.close();
  if (chat && chat.open) chat.close();
  closeAlert();
  if ($("noticeInbox")) $("noticeInbox").hidden = true;
  if ($("noticeBtn")) $("noticeBtn").classList.remove("on");
  document.documentElement.classList.add("shot");
  showTab("overview");
  return name;
}

function restoreAfterScreenshot(tab) {
  document.documentElement.classList.remove("shot");
  if (tab && tab !== "overview") showTab(tab);
}

function sendQuit(action) {
  const dialog = $("quitDialog");
  if (dialog && dialog.open) dialog.close();
  if (action === "stay") return;
  fetch("/api/quit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  }).catch(() => {});
}

function alarmKey(reading) {
  if (!reading) return "";
  if (reading.battery_loose) return "battery_loose";
  if (reading.fault) return "fault";
  if (reading.on_battery) return "on_battery";
  if (reading.battery_boost) return "battery_boost";
  const volt = reading.voltage_state;
  if (volt && volt !== "ok" && volt !== "idle") return `voltage_${volt}`;
  if (reading.battery_swap && reading.battery_swap.from != null) return "battery_swap";
  return "";
}

function alarmPayload(reading) {
  const key = alarmKey(reading);
  if (key === "battery_loose") {
    return { code: "battery_loose", level: "bad", message: "Напряжение батареи скачет — батареи, похоже, отключены от ИБП." };
  }
  if (key === "fault") {
    return { code: "fault", level: "bad", message: "ИБП сообщил об аварии." };
  }
  if (key === "on_battery") {
    return {
      code: "on_battery",
      level: "warn",
      message: `Питание от батареи · разряд ${clock(reading.discharge_seconds || 0)} · осталось ${runtime(reading.runtime_min)}`,
    };
  }
  if (key === "battery_boost") {
    const inn = reading.input_v;
    const out = reading.output_v;
    const delta = inn == null || out == null ? null : out - inn;
    return {
      code: "battery_boost",
      level: "warn",
      message: delta == null
        ? "Выход заметно выше входа — нагрузка подпитывается с батареи."
        : `Выход выше входа на ${delta.toFixed(1)} В — нагрузка подпитывается с батареи.`,
    };
  }
  if (key.startsWith("voltage_")) {
    return {
      code: key,
      level: key.includes("crit") ? "bad" : "warn",
      message: `${reading.voltage_note || "Напряжение сети вне нормы"} · сейчас ${fixed(reading.input_v, 1)} В`,
    };
  }
  if (key === "battery_swap") {
    const swap = reading.battery_swap || {};
    return {
      code: "battery_swap",
      level: "warn",
      message: `Пакет сменился: ${Number(swap.from).toFixed(1)} → ${Number(swap.to).toFixed(1)} В.`,
    };
  }
  return null;
}

function syncAlerts(reading, snapshot) {
  const events = (snapshot && snapshot.events) || [];
  const newest = events[0];
  if (!alertReady) {
    lastEventT = newest ? Number(newest.t) || 0 : 0;
    lastAlarmKey = alarmKey(reading);
    alertReady = true;
    notices = events.map((item) => ({
      code: item.code || "",
      level: item.level || "",
      message: item.message || "",
      t: item.t || 0,
      title: ALERT_TITLES[item.code] || "Событие UPS",
    }));
    noticeUnread = 0;
    renderInbox();
    const current = alarmPayload(reading);
    if (current) openAlert(current, { keep: true });
    return;
  }
  if (newest && Number(newest.t) > lastEventT) {
    lastEventT = Number(newest.t);
    openAlert(newest);
  }
  const key = alarmKey(reading);
  if (key && key !== lastAlarmKey) {
    const current = alarmPayload(reading);
    if (current) openAlert(current);
  }
  lastAlarmKey = key;
}

function noticeTone(item) {
  const code = item.code || "";
  const level = item.level || "";
  if (level === "bad" || code.includes("crit") || code === "fault" || code === "pc_off") return "bad";
  if (level === "ok" || code.endsWith("_clear")) return "ok";
  return "warn";
}

function pushNotice(item) {
  const code = item.code || "";
  notices.unshift({
    code,
    level: item.level || "",
    message: item.message || "",
    t: item.t || Date.now() / 1000,
    title: ALERT_TITLES[code] || item.title || "Событие UPS",
  });
  notices = notices.slice(0, 40);
  noticeUnread += 1;
  renderInbox();
}

function renderInbox() {
  const badge = $("noticeBadge");
  if (badge) {
    badge.hidden = noticeUnread < 1;
    badge.textContent = noticeUnread > 9 ? "9+" : String(noticeUnread);
  }
  const list = $("noticeList");
  if (!list) return;
  list.innerHTML = notices.length
    ? notices.map((item, index) => `
        <li class="${noticeTone(item)}" data-index="${index}">
          <time>${clockTime(item.t)}</time>
          <strong>${escapeHtml(item.title)}</strong>
        </li>`).join("")
    : `<li class="empty">Пока пусто.</li>`;
}

function toggleInbox() {
  const box = $("noticeInbox");
  if (!box) return;
  if (box.hidden) openNoticeInbox();
  else closeNoticeInbox();
}

function openNoticeInbox() {
  const box = $("noticeInbox");
  const btn = $("noticeBtn");
  if (!box) return;
  box.hidden = false;
  if (btn) btn.classList.add("on");
  noticeUnread = 0;
  renderInbox();
}

function closeNoticeInbox() {
  const box = $("noticeInbox");
  const btn = $("noticeBtn");
  if (box) box.hidden = true;
  if (btn) btn.classList.remove("on");
}

function clearInbox() {
  notices = [];
  noticeUnread = 0;
  renderInbox();
}

function openAlert(item, options) {
  const modal = $("alertModal");
  const card = $("alertCard");
  if (!modal || !card || document.documentElement.classList.contains("shot")) return;
  const code = item.code || "";
  lastAlertCode = code;
  const tone = noticeTone(item);
  if (!options || !options.keep) pushNotice(item);
  card.className = `alert-card ${tone}`;
  $("alertKicker").textContent = code === "link"
    ? "Связь"
    : tone === "bad" ? "Авария" : tone === "ok" ? "Снято" : "Предупреждение";
  $("alertTitle").textContent = ALERT_TITLES[code] || item.title || "Событие UPS";
  $("alertText").textContent = item.message || "";
  $("alertTime").textContent = item.t ? clockTime(item.t) : clockTime(Date.now() / 1000);
  $("alertArt").innerHTML = alertGlyph(code);
  if ($("alertCount")) {
    const ticking = code === "pc_shutdown" || code === "pc_off" || code === "pc_reboot";
    $("alertCount").hidden = !ticking;
    $("alertCount").textContent = ticking ? clock(simLeft()) : "";
  }
  modal.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(closeAlert, 7000);
}

function closeAlert() {
  if ($("alertModal")) $("alertModal").hidden = true;
}

function alertGlyph(code) {
  const stroke = "currentColor";
  if (code === "battery_boost") {
    return `<g fill="none" stroke="${stroke}" stroke-width="5" stroke-linejoin="round">
      <rect x="22" y="48" width="52" height="36" rx="4"/>
      <rect x="74" y="58" width="8" height="14"/>
      <path d="M48 18l12 18H36z" fill="${stroke}" stroke="none"/>
      <path d="M48 36v12"/>
    </g>`;
  }
  if (code === "on_battery" || code === "battery_low") {
    return `<g fill="none" stroke="${stroke}" stroke-width="5" stroke-linejoin="round">
      <rect x="22" y="38" width="62" height="44" rx="4"/>
      <rect x="84" y="52" width="10" height="16"/>
      <path d="M34 60h10M50 60h10M66 60h8"/>
      <path d="M28 22 18 8M96 22l10-14"/>
    </g>`;
  }
  if (code === "on_battery_clear" || code.endsWith("_clear")) {
    return `<g fill="none" stroke="${stroke}" stroke-width="5">
      <circle cx="60" cy="60" r="36"/>
      <path d="M40 62l14 14 28-32"/>
    </g>`;
  }
  if (code && code.startsWith("voltage_")) {
    const up = code.includes("high");
    const crit = code.includes("crit");
    return `<g fill="none" stroke="${stroke}" stroke-width="5" stroke-linejoin="round">
      <rect x="28" y="28" width="48" height="28" rx="4"/>
      <path d="M76 36h12v12H76"/>
      <path d="M42 20v8M58 20v8"/>
      <path d="${up ? "M60 68l-22 28h44z" : "M60 104l-22-28h44z"}" fill="${stroke}" stroke="none"/>
      ${crit ? `<path d="M24 100 96 20"/>` : ""}
    </g>`;
  }
  if (code === "battery_loose") {
    return `<g fill="none" stroke="${stroke}" stroke-width="5">
      <rect x="18" y="36" width="36" height="28" rx="3"/>
      <rect x="66" y="56" width="36" height="28" rx="3"/>
      <path d="M54 50 66 70"/>
    </g>`;
  }
  if (code === "battery_swap") {
    return `<g fill="none" stroke="${stroke}" stroke-width="5">
      <rect x="16" y="28" width="36" height="26" rx="3"/>
      <rect x="68" y="66" width="36" height="26" rx="3"/>
      <path d="M48 86c18 0 24-16 24-16"/>
      <path d="M66 62l6 10-12 2"/>
    </g>`;
  }
  if (code === "pc_shutdown" || code === "pc_off" || code === "pc_reboot") {
    return `<g fill="none" stroke="${stroke}" stroke-width="5">
      <circle cx="60" cy="60" r="36"/>
      <path d="M60 32v28l18 10"/>
    </g>`;
  }
  if (code === "fault") {
    return `<g fill="none" stroke="${stroke}" stroke-width="5">
      <path d="M60 18 104 98H16Z"/>
      <path d="M60 46v24"/>
      <circle cx="60" cy="84" r="3" fill="${stroke}"/>
    </g>`;
  }
  if (code === "link_lost") {
    return `<g fill="none" stroke="${stroke}" stroke-width="5">
      <path d="M60 86V40"/>
      <circle cx="60" cy="94" r="6"/>
      <path d="M40 36c12-16 28-16 40 0"/>
      <path d="M30 24c18-22 42-22 60 0"/>
      <path d="M28 100 92 20"/>
    </g>`;
  }
  if (code === "link") {
    return `<g fill="none" stroke="${stroke}" stroke-width="5">
      <path d="M60 86V40"/>
      <circle cx="60" cy="94" r="6"/>
      <path d="M40 36c12-16 28-16 40 0"/>
      <path d="M30 24c18-22 42-22 60 0"/>
    </g>`;
  }
  return `<g fill="none" stroke="${stroke}" stroke-width="5">
    <circle cx="60" cy="60" r="36"/>
    <path d="M28 62h16l10-28 16 48 10-20h12"/>
  </g>`;
}

function toast(text) {
  const node = $("toast");
  node.hidden = false;
  node.textContent = text;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    node.hidden = true;
  }, 2400);
}

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function escapeAttr(text) {
  return escapeHtml(text).replace(/"/g, "");
}
