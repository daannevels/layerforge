// Layerforge UI. The server token arrives in the page URL (?token=...) and is sent on every API call.
"use strict";

const TOKEN = new URLSearchParams(location.search).get("token") || "";

async function api(path, options = {}) {
  const headers = Object.assign({}, options.headers, { "X-Layerforge-Token": TOKEN });
  const res = await fetch(path, Object.assign({}, options, { headers }));
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function init() {
  const status = document.getElementById("status");
  try {
    const info = await api("/api/health");
    document.getElementById("version").textContent = info.version;
    status.textContent = "Engine connected.";
    status.classList.add("ok");
  } catch (err) {
    // Dutch help text for the user
    status.textContent = "Geen verbinding met de engine. Start Layerforge opnieuw met het startbestand.";
    status.classList.add("bad");
  }
}

init();
