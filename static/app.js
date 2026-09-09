const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// -- Tabs (Dropdown) ---------------------------------------------------
$("#tab-select").addEventListener("change", (ev) => {
  $$(".tab-panel").forEach((p) => p.classList.remove("active"));
  $(`#tab-${ev.target.value}`).classList.add("active");
});

// -- Helpers ------------------------------------------------------------
function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleString();
}

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${res.status}`);
  }
  return res.json();
}

function setMqttStatus(connected) {
  const dot = $("#mqtt-dot");
  const text = $("#mqtt-text");
  dot.className = "dot " + (connected ? "dot-ok" : "dot-bad");
  text.textContent = "MQTT: " + (connected ? "verbunden" : "getrennt");
}

function setRingAccountStatus(authenticated) {
  const dot = $("#ring-account-dot");
  const text = $("#ring-account-text");
  dot.className = "dot " + (authenticated ? "dot-ok" : "dot-bad");
  text.textContent = "Ring: " + (authenticated ? "angemeldet" : "nicht angemeldet");
}

// -- Status / SSE ---------------------------------------------------
async function loadStatus() {
  const status = await api("/api/status");
  setMqttStatus(status.mqtt_connected);
  fillSettingsForm(status.config);
  renderRingAccount(status.config.ring);
}

function addFeedItem(text) {
  const feed = $("#live-feed");
  const li = document.createElement("li");
  li.textContent = text;
  feed.prepend(li);
  while (feed.children.length > 50) feed.removeChild(feed.lastChild);
}

function connectEvents() {
  const es = new EventSource("/api/events");
  es.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "mqtt_status") {
      setMqttStatus(msg.data.connected);
    } else if (msg.type === "tap") {
      const d = msg.data;
      addFeedItem(`${fmtTime(Date.now() / 1000)} - Tap: ${d.kind} ${d.identifier}`);
    } else if (msg.type === "access") {
      const d = msg.data;
      addFeedItem(
        `${fmtTime(Date.now() / 1000)} - ${d.granted ? "OK" : "ABGELEHNT"}: ${d.kind} ${d.identifier} (${d.reason})`
      );
      loadLog();
    } else if (msg.type === "learn_captured") {
      onLearnCaptured(msg.data);
    }
  };
  es.onerror = () => {
    // EventSource retries automatically.
  };
}

// -- Dashboard: manual open / log ---------------------------------------
$("#btn-open").addEventListener("click", async () => {
  $("#open-result").textContent = "...";
  try {
    const res = await api("/api/open", { method: "POST" });
    $("#open-result").textContent = res.action ? `Ausgelöst: ${res.action}` : "Keine Aktion konfiguriert!";
  } catch (e) {
    $("#open-result").textContent = "Fehler: " + e.message;
  }
});

async function loadLog() {
  const rows = await api("/api/log?limit=50");
  const tbody = $("#log-table tbody");
  tbody.innerHTML = "";
  for (const r of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${fmtTime(r.ts)}</td>
      <td>${r.kind}</td>
      <td class="mono">${r.identifier}</td>
      <td>${r.name || ""}</td>
      <td><span class="badge ${r.granted ? "badge-ok" : "badge-bad"}">${r.granted ? "erlaubt" : "abgelehnt"}</span></td>
      <td>${r.reason}</td>
      <td>${r.action || ""}</td>
    `;
    tbody.appendChild(tr);
  }
}

// -- Credentials ----------------------------------------------------
async function loadCredentials() {
  const rows = await api("/api/credentials");
  const tbody = $("#cred-table tbody");
  tbody.innerHTML = "";
  for (const c of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${c.kind === "homekey" ? "HomeKey" : "NFC-Tag"}</td>
      <td class="mono">${c.identifier}</td>
      <td><input type="text" data-id="${c.id}" class="cred-name" value="${c.name}" /></td>
      <td><input type="checkbox" data-id="${c.id}" class="cred-enabled" ${c.enabled ? "checked" : ""} /></td>
      <td>${c.use_count}${c.last_used_at ? " (" + fmtTime(c.last_used_at) + ")" : ""}</td>
      <td><button class="btn btn-danger btn-sm cred-delete" data-id="${c.id}">Löschen</button></td>
    `;
    tbody.appendChild(tr);
  }

  $$(".cred-name").forEach((input) => {
    input.addEventListener("change", () =>
      api(`/api/credentials/${input.dataset.id}`, {
        method: "PUT",
        body: JSON.stringify({ name: input.value }),
      })
    );
  });
  $$(".cred-enabled").forEach((input) => {
    input.addEventListener("change", () =>
      api(`/api/credentials/${input.dataset.id}`, {
        method: "PUT",
        body: JSON.stringify({ enabled: input.checked }),
      })
    );
  });
  $$(".cred-delete").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("Diese Zugangskarte wirklich löschen?")) return;
      await api(`/api/credentials/${btn.dataset.id}`, { method: "DELETE" });
      loadCredentials();
    });
  });
}

// -- Learn mode -------------------------------------------------------
let learnPollTimer = null;

$("#btn-learn-start").addEventListener("click", async () => {
  await api("/api/learn/start", { method: "POST", body: JSON.stringify({ kind: "nfc", timeout: 30 }) });
  $("#learn-status").textContent = "Warte auf Tag... bitte jetzt an den Leser halten (30s Zeit).";
  $("#btn-learn-start").disabled = true;
  $("#btn-learn-cancel").disabled = false;
  $("#learn-form").hidden = true;
  clearInterval(learnPollTimer);
  learnPollTimer = setInterval(pollLearnStatus, 1000);
});

$("#btn-learn-cancel").addEventListener("click", async () => {
  await api("/api/learn/cancel", { method: "POST" });
  stopLearnPolling("Abgebrochen.");
});

function stopLearnPolling(message) {
  clearInterval(learnPollTimer);
  learnPollTimer = null;
  $("#btn-learn-start").disabled = false;
  $("#btn-learn-cancel").disabled = true;
  if (message) $("#learn-status").textContent = message;
}

async function pollLearnStatus() {
  const status = await api("/api/learn/status");
  if (status.result) {
    onLearnCaptured(status.result);
  } else if (!status.armed) {
    stopLearnPolling("Zeit abgelaufen, kein Tag erkannt.");
  }
}

function onLearnCaptured(result) {
  stopLearnPolling("Tag erkannt!");
  $("#learn-kind").value = result.kind;
  $("#learn-identifier").value = result.identifier;
  $("#learn-identifier-display").textContent = `${result.kind === "homekey" ? "HomeKey" : "NFC"}: ${result.identifier}`;
  $("#learn-form").hidden = false;
  $("#learn-name").focus();
}

$("#learn-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  await api("/api/credentials", {
    method: "POST",
    body: JSON.stringify({
      kind: $("#learn-kind").value,
      identifier: $("#learn-identifier").value,
      name: $("#learn-name").value,
    }),
  });
  $("#learn-form").hidden = true;
  $("#learn-name").value = "";
  loadCredentials();
});

$("#add-nfc-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const identifier = $("#nfc-uid").value.trim();
  const name = $("#nfc-name").value.trim();
  if (!identifier) return;
  await api("/api/credentials", {
    method: "POST",
    body: JSON.stringify({ kind: "nfc", identifier, name }),
  });
  $("#nfc-uid").value = "";
  $("#nfc-name").value = "";
  loadCredentials();
});

// -- Settings ---------------------------------------------------------
function fillSettingsForm(cfg) {
  const form = $("#settings-form");
  for (const el of form.elements) {
    if (!el.name) continue;
    const [section, key] = el.name.split(".");
    const value = cfg[section] ? cfg[section][key] : undefined;
    if (value === undefined) continue;
    if (el.type === "checkbox") el.checked = Boolean(value);
    else el.value = value;
  }
}

function readSettingsForm() {
  const form = $("#settings-form");
  const patch = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    const [section, key] = el.name.split(".");
    patch[section] = patch[section] || {};
    if (el.type === "checkbox") patch[section][key] = el.checked;
    else if (el.type === "number") patch[section][key] = el.value === "" ? null : Number(el.value);
    else patch[section][key] = el.value;
  }
  return patch;
}

$("#settings-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("#settings-result").textContent = "Speichere...";
  try {
    const cfg = await api("/api/config", { method: "POST", body: JSON.stringify(readSettingsForm()) });
    fillSettingsForm(cfg);
    $("#settings-result").textContent = "Gespeichert.";
  } catch (e) {
    $("#settings-result").textContent = "Fehler: " + e.message;
  }
});

// -- Ring-Konto (direkte Cloud-API statt ring-mqtt) ----------------------
let ringOtpPending = false;

function renderRingAccount(ringCfg) {
  const authenticated = Boolean(ringCfg && ringCfg.token);
  setRingAccountStatus(authenticated);
  $("#ring-login-form").hidden = authenticated;
  $("#ring-account-info").hidden = !authenticated;
  $("#ring-device-picker").hidden = !authenticated;

  if (authenticated) {
    $("#ring-account-email").textContent = ringCfg.email || "?";
    $("#ring-status").textContent = ringCfg.device_name
      ? `Intercom: ${ringCfg.device_name}`
      : "Angemeldet - bitte Intercom-Gerät auswählen.";
  } else {
    $("#ring-login-form").hidden = false;
    $("#ring-status").textContent = "Nicht angemeldet.";
  }
}

$("#ring-login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const email = $("#ring-email").value.trim();
  const password = $("#ring-password").value;
  const otp_code = $("#ring-otp").value.trim();
  $("#ring-login-result").textContent = "Melde an...";
  try {
    const res = await api("/api/ring/login", {
      method: "POST",
      body: JSON.stringify({ email, password, otp_code: ringOtpPending ? otp_code : undefined }),
    });
    if (res.needs_2fa) {
      ringOtpPending = true;
      $("#ring-otp-wrap").hidden = false;
      $("#ring-otp").focus();
      $("#ring-login-result").textContent = "2FA-Code erforderlich - wurde per E-Mail/SMS von Ring verschickt.";
      return;
    }
    ringOtpPending = false;
    $("#ring-otp-wrap").hidden = true;
    $("#ring-password").value = "";
    $("#ring-otp").value = "";
    $("#ring-login-result").textContent = "Angemeldet.";
    renderRingAccount(res.config.ring);
    refreshRingDevices();
  } catch (e) {
    $("#ring-login-result").textContent = "Fehler: " + e.message;
  }
});

$("#btn-ring-logout").addEventListener("click", async () => {
  if (!confirm("Ring-Konto wirklich abmelden?")) return;
  const res = await api("/api/ring/logout", { method: "POST" });
  ringOtpPending = false;
  renderRingAccount(res.config.ring);
});

async function refreshRingDevices() {
  const select = $("#ring-device-select");
  $("#ring-device-result").textContent = "Lade Geräte...";
  try {
    const devices = await api("/api/ring/devices");
    select.innerHTML = "";
    for (const d of devices) {
      const opt = document.createElement("option");
      opt.value = d.id;
      opt.textContent = d.name;
      opt.dataset.name = d.name;
      select.appendChild(opt);
    }
    $("#ring-device-result").textContent = devices.length
      ? `${devices.length} Gerät(e) gefunden.`
      : "Keine Intercom gefunden.";
  } catch (e) {
    $("#ring-device-result").textContent = "Fehler: " + e.message;
  }
}

$("#btn-ring-refresh-devices").addEventListener("click", refreshRingDevices);

$("#btn-ring-select-device").addEventListener("click", async () => {
  const select = $("#ring-device-select");
  const opt = select.selectedOptions[0];
  if (!opt) {
    $("#ring-device-result").textContent = "Bitte zuerst Geräte laden.";
    return;
  }
  const res = await api("/api/ring/select-device", {
    method: "POST",
    body: JSON.stringify({ device_id: Number(opt.value), device_name: opt.dataset.name }),
  });
  $("#ring-device-result").textContent = "Gespeichert.";
  renderRingAccount(res.config.ring);
});

// -- Init ---------------------------------------------------------------
loadStatus();
loadCredentials();
loadLog();
connectEvents();
