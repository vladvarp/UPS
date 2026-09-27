const $ = (id) => document.getElementById(id);

let state = null;
let points = [];
let minutes = 60;
let series = "voltage";
let hiddenLines = {};
let chartHoverX = null;
let sparkHoverX = null;
let audioCtx = null;
let wasOnBattery = false;
let lastVoltRank = 0;
let toastTimer = 0;
let sessionIn = { min: null, max: null };
let reportKind = "summary";
let reportDays = [];
let reportDate = "";

const SERIES = {
  voltage: {
    unit: "В",
    lines: [
      { key: "in", color: "#e0b06a", name: "вход" },
      { key: "out", color: "#8fd0ff", name: "выход" },
    ],
  },
  load: { unit: "%", lines: [{ key: "load", color: "#e0b06a", name: "нагрузка" }] },
  battery: { unit: "В", lines: [{ key: "batt", color: "#3dbe7a", name: "батарея" }] },
  freq: { unit: "Гц", lines: [{ key: "hz", color: "#f0d7ad", name: "частота" }] },
  temp: { unit: "°C", lines: [{ key: "temp", color: "#e15d4c", name: "температура" }] },
};

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => showTab(button.dataset.tab));
});
$("openSettings").addEventListener("click", openSettings);
$("closeSettings").addEventListener("click", () => $("settings").close());
$("settingsForm").addEventListener("submit", saveSettings);
$("telegramForm").addEventListener("submit", saveTelegram);
$("tgPill").addEventListener("click", () => showTab("telegram"));
$("gateRescan").addEventListener("click", () => command("rescan"));
$("gateDemo").addEventListener("click", () => command("demo"));
$("leaveDemo").addEventListener("click", () => command("live"));
$("btnSimulate").addEventListener("click", () => command("simulate"));
$("btnCancelShutdown").addEventListener("click", () => command("cancel_shutdown"));
$("tgNewPair").addEventListener("click", () => telegramAction("telegram_new_pair"));
$("tgUnlink").addEventListener("click", () => telegramAction("telegram_unlink"));
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
$("tgClear").addEventListener("click", () => telegramAction("telegram_clear"));
$("tgCopyPair").addEventListener("click", copyPairCommand);
$("quitTray").addEventListener("click", () => sendQuit("tray"));
$("quitClose").addEventListener("click", () => sendQuit("close"));
$("quitStay").addEventListener("click", () => sendQuit("stay"));
$("quitDialog").addEventListener("cancel", (event) => {
  event.preventDefault();
  sendQuit("stay");
});
window.showQuitDialog = showQuitDialog;
window.prepareForScreenshot = prepareForScreenshot;
window.restoreAfterScreenshot = restoreAfterScreenshot;
["tgNotifyPower", "tgNotifyAlerts", "tgDigest"].forEach((id) => {
  $(id).addEventListener("change", saveTelegramFlags);
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
});
$("series").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  series = button.dataset.series;
  document.querySelectorAll("#series button").forEach((item) => item.classList.toggle("on", item === button));
  drawChart();
});
$("chart").addEventListener("mousemove", hoverChart);
$("chart").addEventListener("mouseleave", () => {
  chartHoverX = null;
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
  if (state && state.reading) {
    $("sparkNote").textContent = `вход ${fixed(state.reading.input_v, 1)} · выход ${fixed(state.reading.output_v, 1)} В`;
  }
  drawSpark();
});
syncPackCells(2, 1);
window.addEventListener("resize", () => {
  drawChart();
  drawSpark();
});
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
  document.querySelector("main").classList.toggle("fill", name === "reports");
  if (name === "chart") drawChart();
  if (name === "log") loadEvents();
  if (name === "telegram") fillTelegramFields();
  if (name === "reports") loadReport();
}

async function loadHistory() {
  const response = await fetch(`/api/history?minutes=${minutes}`);
  const data = await response.json();
  points = data.points || [];
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
        <li class="${escapeAttr(item.level)}">
          <time>${clockTime(item.t)}</time>
          <p>${escapeHtml(item.message)}</p>
        </li>`).join("")
    : `<li class="empty">Пока событий нет.</li>`;
}

async function command(action) {
  const response = await fetch("/api/command", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  const data = await response.json();
  toast(data.message || "Готово");
  if (action === "demo" || action === "live" || action === "rescan") {
    setTimeout(loadHistory, 600);
  }
  return data;
}

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

async function saveTelegramFlags() {
  syncDigestTime();
  await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      telegram_notify_power: $("tgNotifyPower").checked,
      telegram_notify_alerts: $("tgNotifyAlerts").checked,
      telegram_digest: $("tgDigest").checked,
      telegram_digest_time: digestClockValue(),
    }),
  });
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
  const text = $("tgPairCmd").textContent || "";
  try {
    await navigator.clipboard.writeText(text);
    toast("Команда скопирована");
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
  if (data.ok) toast("Настройки сохранены");
}

function apply(next) {
  const previousMode = state && state.mode;
  state = next;
  const reading = next.reading;
  const age = reading ? Date.now() / 1000 - reading.updated_at : 1e9;
  const fresh = reading && reading.source === "live" && age < 180;
  const live = next.mode === "live" || next.mode === "demo" || fresh;
  document.body.dataset.power = reading && reading.on_battery ? "battery" : live ? "mains" : "idle";
  $("connPill").textContent = pillText(next);
  $("connPill").classList.toggle("live", next.mode === "live" || fresh);
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
    text = "Напишите боту команду с кодом привязки.";
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
  $("tgPairCmd").textContent = code ? `/start ${code}` : "/start";
  const list = (tg.chats && tg.chats.length) ? tg.chats : settings.telegram_chats || [];
  $("tgChatList").innerHTML = list.length
    ? list.map((item) => `<li>${escapeHtml(item.name || item.id)}</li>`).join("")
    : `<li class="empty">Пока никто не привязан.</li>`;
}

function fillTelegramFields() {
  const settings = (state && state.settings) || {};
  $("tgNotifyPower").checked = settings.telegram_notify_power !== false;
  $("tgNotifyAlerts").checked = settings.telegram_notify_alerts !== false;
  $("tgDigest").checked = Boolean(settings.telegram_digest);
  setDigestClock(settings.telegram_digest_time || "21:00");
  syncDigestTime();
  $("tgToken").placeholder = settings.telegram_token_set
    ? "токен уже сохранён — вставьте новый, чтобы заменить"
    : "123456789:AAH…";
}

function paint(reading, snapshot) {
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
  $("inHz").textContent = fixed(reading.frequency, 1);
  $("deltaV").textContent = reading.input_v == null || reading.output_v == null
    ? "—"
    : `${reading.output_v - reading.input_v >= 0 ? "+" : ""}${(reading.output_v - reading.input_v).toFixed(1)} В`;
  $("outV").textContent = fixed(reading.output_v, 1);
  $("loadVal").textContent = load == null ? "—" : Math.round(load);
  $("loadVa").textContent = reading.load_w != null ? `≈ ${reading.load_w} Вт` : va == null ? "—" : `≈ ${va} ВА`;
  $("loadRing").style.setProperty("--p", String(Math.max(0, Math.min(100, load || 0))));
  $("cellCap").textContent = reading.battery_pct == null ? "—" : `${Math.round(reading.battery_pct)}%`;
  $("mBattCell").textContent = reading.battery_v == null ? "—" : `${(reading.battery_v / seriesCount).toFixed(1)} В на банку`;
  $("mHealth").textContent = settings.battery_health == null ? "—" : `здоровье ${settings.battery_health}%`;
  $("mMode").textContent = reading.mode_label || "—";
  $("mKind").textContent = reading.ups_kind || "";
  chip($("factMains"), reading.mains_ok, reading.mains_ok ? "Сеть есть" : "Сети нет", reading.mains_ok ? "on" : "bad");
  chip($("factPath"), true, reading.on_battery ? "Питание от батареи" : "Питание от сети", reading.on_battery ? "warn" : "on");
  chip($("factAvr"), reading.avr, reading.avr ? "AVR держит напряжение" : "AVR не активен", reading.avr ? "warn" : "");
  chip($("factBeep"), reading.beeper, reading.beeper ? "Зуммер включён" : "Зуммер выключен", reading.beeper ? "warn" : "");
  const swap = reading.battery_swap;
  const swapped = Boolean(swap && swap.from != null && swap.to != null);
  chip(
    $("factLoose"),
    reading.battery_loose || swapped,
    reading.battery_loose
      ? "Батареи отключены"
      : swapped
        ? `Пакет сменён ${Number(swap.from).toFixed(1)}→${Number(swap.to).toFixed(1)} В`
        : "Пакет на месте",
    reading.battery_loose ? "bad" : swapped ? "warn" : "on",
  );
  const volt = reading.voltage_state || "ok";
  const voltTone = volt === "crit_low" || volt === "crit_high" ? "bad" : volt === "low" || volt === "high" ? "warn" : "on";
  const voltLabel = {
    low: "Вход ниже 200 В",
    high: "Вход выше 240 В",
    crit_low: "Вход ниже 180 В",
    crit_high: "Вход выше 250 В",
  }[volt];
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
  $("hopBypass").classList.toggle("hot", !onBattery);
  $("hopBypass").classList.toggle("dim", onBattery);
  $("hopInverter").classList.toggle("hot", onBattery);
  $("hopInverter").classList.toggle("dim", !onBattery);
  const soc = reading.battery_pct == null ? 0 : reading.battery_pct;
  const fill = soc < 20
    ? "linear-gradient(#ffd0c8, #e15d4c)"
    : soc < 50
      ? "linear-gradient(#ffe3a8, #e6b15c)"
      : "linear-gradient(#d7ffb0, #3dbe7a)";
  document.querySelectorAll(".bat-fill").forEach((node) => {
    node.style.height = `${soc}%`;
    node.style.background = fill;
  });
  $("mBatt").textContent = fixed(reading.battery_v, 1);
  $("mTemp").textContent = reading.temperature == null ? "—" : fixed(reading.temperature, 1);
  $("mSoc").textContent = reading.battery_pct == null ? "—" : Math.round(reading.battery_pct);
  $("mDis").textContent = clock(reading.discharge_seconds || 0);
  $("mRun").textContent = runtime(reading.runtime_min);
  const shutting = reading.shutdown_seconds != null;
  $("btnCancelShutdown").hidden = !shutting;
  const alarm = $("alarmBanner");
  if (reading.battery_loose) {
    alarm.hidden = false;
    alarm.textContent = "Напряжение батареи скачет — похоже, батареи физически отключены от ИБП.";
  } else if (onBattery) {
    alarm.hidden = false;
    alarm.textContent = `Питание от батареи · разряд ${clock(reading.discharge_seconds || 0)} · осталось ${runtime(reading.runtime_min)}`;
  } else if (reading.fault) {
    alarm.hidden = false;
    alarm.textContent = "ИБП сообщил об аварии.";
  } else if (voltLabel) {
    alarm.hidden = false;
    alarm.textContent = `${voltLabel} · сейчас ${fixed(reading.input_v, 1)} В`;
  } else if (swapped) {
    alarm.hidden = false;
    alarm.textContent = `Пакет батарей сменился: ${Number(swap.from).toFixed(1)} → ${Number(swap.to).toFixed(1)} В. Напряжение держится — это не обрыв.`;
  } else {
    alarm.hidden = true;
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
    reading.battery_loose || swapped,
    reading.battery_loose ? "bad" : swapped ? "warn" : "",
    reading.battery_loose ? "Пакет снят" : swapped ? "Пакет сменён" : "Пакет",
  );
  lamp($("lampOff"), Boolean(reading.shutdown || shutting), "bad", "Выключение");
  document.title = `${fixed(reading.input_v, 0)} В · ${load == null ? "—" : Math.round(load)}% · UPS`;
  if (onBattery && !wasOnBattery && snapshot.settings && snapshot.settings.sound) beep();
  wasOnBattery = onBattery;
  const voltRank = { idle: 0, ok: 0, low: 1, high: 1, crit_low: 2, crit_high: 2 }[volt] || 0;
  if (voltRank > lastVoltRank && snapshot.settings && snapshot.settings.sound) beep();
  lastVoltRank = voltRank;
  const sparkPoints = points.filter((point) => point.t >= Date.now() / 1000 - 15 * 60);
  const last = sparkPoints[sparkPoints.length - 1];
  const first = sparkPoints[0];
  if (sparkHoverX == null) {
    $("sparkNote").textContent = last && first
      ? `вход ${fixed(last.in, 1)} · выход ${fixed(last.out, 1)} В`
      : "";
  }
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
  const point = {
    t: reading.updated_at,
    in: reading.input_v,
    out: reading.output_v,
    load: reading.load_pct,
    batt: reading.battery_v,
    soc: reading.battery_pct,
    hz: reading.frequency,
    temp: reading.temperature,
  };
  if (!points.length || points[points.length - 1].t !== point.t) {
    points.push(point);
    const cutoff = Date.now() / 1000 - minutes * 60;
    if (points.length > 20 && points[0].t < cutoff) {
      points = points.filter((item) => item.t >= cutoff);
    }
  }
  if ($("view-chart").classList.contains("on")) drawChart();
  drawSpark();
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

function drawSpark() {
  const recent = points.filter((point) => point.t >= Date.now() / 1000 - 15 * 60);
  drawLines($("spark"), recent, [
    { key: "in", color: "#e0b06a", name: "вход" },
    { key: "out", color: "#8fd0ff", name: "выход" },
  ], { pad: 10, axis: false, unit: "В" });
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
    digits: series === "load" ? 0 : 1,
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
  const bottom = options.axis ? 28 : 10;
  const plotW = width - left - 16;
  const plotH = height - pad - bottom;
  const t0 = data[0].t;
  const t1 = data[data.length - 1].t;
  const tSpan = Math.max(1, t1 - t0);
  const xOf = (t) => left + ((t - t0) / tSpan) * plotW;
  const yOf = (value) => pad + ((max - value) / (max - min)) * plotH;
  ctx.strokeStyle = "rgba(232,220,196,0.10)";
  ctx.lineWidth = 1;
  ctx.fillStyle = "#93a79a";
  ctx.font = "12px Cascadia Mono, Consolas, monospace";
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
  if (options.axis) {
    for (let i = 0; i <= 4; i += 1) {
      const t = t0 + (tSpan * i) / 4;
      const x = xOf(t);
      ctx.fillText(clockTime(t).slice(0, 5), Math.max(left, x - 16), height - 8);
    }
  }
  lines.forEach((line) => {
    const samples = data.filter((point) => point[line.key] != null);
    if (samples.length < 2) return;
    const first = samples[0];
    const last = samples[samples.length - 1];
    const fill = ctx.createLinearGradient(0, pad, 0, pad + plotH);
    fill.addColorStop(0, hexAlpha(line.color, 0.28));
    fill.addColorStop(1, hexAlpha(line.color, 0.01));
    ctx.beginPath();
    ctx.moveTo(xOf(first.t), yOf(first[line.key]));
    samples.forEach((point) => ctx.lineTo(xOf(point.t), yOf(point[line.key])));
    ctx.lineTo(xOf(last.t), pad + plotH);
    ctx.lineTo(xOf(first.t), pad + plotH);
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();
    ctx.beginPath();
    ctx.lineWidth = 2.4;
    ctx.strokeStyle = line.color;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.shadowColor = hexAlpha(line.color, 0.35);
    ctx.shadowBlur = 8;
    samples.forEach((point, index) => {
      const x = xOf(point.t);
      const y = yOf(point[line.key]);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.shadowBlur = 0;
  });
  const plot = { data, lines, min, max, left, pad, plotW, plotH, unit: options.unit || "", xOf, yOf };
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
    const y = plot.yOf(value);
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

function hoverChart(event) {
  const canvas = $("chart");
  const plot = canvas._plot;
  if (!plot) return;
  const rect = canvas.getBoundingClientRect();
  chartHoverX = event.clientX - rect.left;
  const point = nearestPoint(plot, chartHoverX);
  if (!point) return;
  const bits = plot.lines.map((line) => `${line.name} ${fixed(point[line.key], 1)} ${plot.unit}`);
  $("chartTip").textContent = `${clockTime(point.t)}  ·  ${bits.join("   ")}`;
  drawChart(false);
}

function hoverSpark(event) {
  const canvas = $("spark");
  const plot = canvas._plot;
  if (!plot) return;
  const rect = canvas.getBoundingClientRect();
  sparkHoverX = event.clientX - rect.left;
  const point = nearestPoint(plot, sparkHoverX);
  if (!point) return;
  $("sparkNote").textContent = `${clockTime(point.t)} · вход ${fixed(point.in, 1)} · выход ${fixed(point.out, 1)} В`;
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
  if (snapshot.mode === "live" || (reading && reading.source === "live" && age < 180)) {
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
  return "Ищу ИБП по USB. Программа UPSmart для этого не нужна.";
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
  if (settings && settings.open) settings.close();
  if (quit && quit.open) quit.close();
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
