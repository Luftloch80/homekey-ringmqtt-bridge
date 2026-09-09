const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// -- Tabs -------------------------------------------------------------
$$(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".tab-btn").forEach((b) => b.classList.remove("active"));
    $$(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(`#tab-${btn.dataset.tab}`).classList.add("active");
  });
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

// -- Status / SSE ---------------------------------------------------
async function loadStatus() {
  const status = await api("/api/status");
  setMqttStatus(status.mqtt_connected);
  fillSettingsForm(status.config);
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
    $("#open-result").textContent = res.action ? `Ausgeloest: ${res.action}` : "Keine Aktion konfiguriert!";
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
      <td><button class="btn btn-danger btn-sm cred-delete" data-id="${c.id}">Loeschen</button></td>
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
      if (!confirm("Diese Zugangskarte wirklich loeschen?")) return;
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

$("#add-homekey-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const identifier = $("#hk-endpoint-id").value.trim();
  const name = $("#hk-name").value.trim();
  if (!identifier) return;
  await api("/api/credentials", {
    method: "POST",
    body: JSON.stringify({ kind: "homekey", identifier, name }),
  });
  $("#hk-endpoint-id").value = "";
  $("#hk-name").value = "";
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

$("#btn-discover-ring").addEventListener("click", async () => {
  await api("/api/discover/ring/start", { method: "POST", body: JSON.stringify({ duration: 15 }) });
  $("#ring-discovery-status").textContent = "Suche laeuft (15s) - loese jetzt ggf. einen Test-Klingel/Unlock in Ring aus...";
  const timer = setInterval(async () => {
    const status = await api("/api/discover/ring/status");
    const list = $("#ring-candidates");
    list.innerHTML = "";
    for (const t of status.candidates.length ? status.candidates : status.topics) {
      const li = document.createElement("li");
      li.textContent = t;
      li.style.cursor = "pointer";
      li.title = "Klicken zum Uebernehmen als Befehls-Topic";
      li.addEventListener("click", () => {
        $('input[name="ring.command_topic"]').value = t;
      });
      list.appendChild(li);
    }
    if (!status.active) {
      clearInterval(timer);
      $("#ring-discovery-status").textContent = `Fertig, ${status.topics.length} Topics gefunden.`;
    }
  }, 1500);
});

// -- Init ---------------------------------------------------------------
loadStatus();
loadCredentials();
loadLog();
connectEvents();
