// Layerforge UI (plain JavaScript, no libraries). The server token arrives in the page URL
// (?token=...) and is sent on every API call.
"use strict";

const TOKEN = new URLSearchParams(location.search).get("token") || "";
const STATES = ["open", "keep", "avoid"];
const MATCH_RMS = Math.pow(10, -20 / 20);   // every played sound is matched to this RMS (first 200 ms)
const MATCH_MAX_GAIN = Math.pow(10, 12 / 20);

let PRESETS = null;
let slots = [];          // {el, upload, buffer, keep: [band][zone] -> [state, strength]}
let audioCtx = null;
let current = null;      // {source, gainNode, onstop}
let lastPlayed = null;   // function that plays the last thing again (space bar)
let busy = false;

const $ = (sel, root = document) => root.querySelector(sel);

async function api(path, options = {}) {
  const headers = Object.assign({}, options.headers, { "X-Layerforge-Token": TOKEN });
  const res = await fetch(path, Object.assign({}, options, { headers }));
  let data = null;
  try { data = await res.json(); } catch (e) { /* not JSON */ }
  if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
  return data;
}

function fileUrl(id, download) {
  return `/api/file/${id}?token=${encodeURIComponent(TOKEN)}${download ? "&download=1" : ""}`;
}

function setStatus(text, bad) {
  const s = $("#status");
  s.textContent = text || "";
  s.classList.toggle("bad", !!bad);
}

// ---------------------------------------------------------------- audio
function ctx() {
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  if (audioCtx.state === "suspended") audioCtx.resume();
  return audioCtx;
}

function matchGain(buffer) {
  const n = Math.min(buffer.length, Math.round(0.2 * buffer.sampleRate));
  let sum = 0;
  for (let c = 0; c < buffer.numberOfChannels; c++) {
    const d = buffer.getChannelData(c);
    for (let i = 0; i < n; i++) sum += d[i] * d[i];
  }
  const rms = Math.sqrt(sum / Math.max(1, n * buffer.numberOfChannels));
  return rms > 0 ? Math.min(MATCH_RMS / rms, MATCH_MAX_GAIN) : 1;
}

function stop() {
  if (current) {
    const c = current;
    current = null;
    try { c.source.stop(); } catch (e) { /* already stopped */ }
    if (c.onstop) c.onstop();
  }
}

function play(buffer, onstart, onstop) {
  stop();
  const ac = ctx();
  const source = ac.createBufferSource();
  source.buffer = buffer;
  const gainNode = ac.createGain();
  gainNode.gain.value = matchGain(buffer);
  source.connect(gainNode).connect(ac.destination);
  const me = { source, gainNode, onstop };
  source.onended = () => { if (current === me) { current = null; if (onstop) onstop(); } };
  current = me;
  if (onstart) onstart();
  source.start();
}

async function decode(arrayBuffer) {
  return await ctx().decodeAudioData(arrayBuffer);
}

function drawWave(canvas, buffer) {
  const w = canvas.width = canvas.clientWidth * (window.devicePixelRatio || 1);
  const h = canvas.height = canvas.clientHeight * (window.devicePixelRatio || 1);
  const g = canvas.getContext("2d");
  g.clearRect(0, 0, w, h);
  g.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--wave").trim() || "#9fb4ff";
  const chans = [];
  for (let c = 0; c < buffer.numberOfChannels; c++) chans.push(buffer.getChannelData(c));
  const n = buffer.length;
  let peak = 0;
  for (const d of chans) for (let i = 0; i < n; i++) peak = Math.max(peak, Math.abs(d[i]));
  peak = peak || 1;
  for (let x = 0; x < w; x++) {
    const a = Math.floor((x / w) * n), b = Math.max(a + 1, Math.floor(((x + 1) / w) * n));
    let lo = 0, hi = 0;
    for (const d of chans) for (let i = a; i < b; i++) { lo = Math.min(lo, d[i]); hi = Math.max(hi, d[i]); }
    const y1 = h / 2 - (hi / peak) * (h / 2 - 2), y2 = h / 2 - (lo / peak) * (h / 2 - 2);
    g.fillRect(x, y1, 1, Math.max(1, y2 - y1));
  }
}

// ---------------------------------------------------------------- slots
function bandsOf(type) { return PRESETS.types[type].bands; }

function emptyKeep() {
  return bandsOf($("#soundType").value).map(() => [0, 1, 2].map(() => ["open", 1.0]));
}

function addSlot() {
  const el = $("#slotTemplate").content.firstElementChild.cloneNode(true);
  const slot = { el, upload: null, buffer: null, keep: emptyKeep() };
  slots.push(slot);
  const drop = $(".drop", el), input = $("input", el);
  drop.addEventListener("click", () => input.click());
  input.addEventListener("change", () => { if (input.files[0]) loadFile(slot, input.files[0]); input.value = ""; });
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    if (e.dataTransfer.files[0]) loadFile(slot, e.dataTransfer.files[0]);
  });
  $(".remove", el).addEventListener("click", () => removeSlot(slot));
  const playSlot = () => {
    if (!slot.buffer) return;
    const btn = $(".play", el);
    lastPlayed = playSlot;
    play(slot.buffer, () => btn.classList.add("active"), () => btn.classList.remove("active"));
  };
  $(".play", el).addEventListener("click", playSlot);
  $(".wave", el).addEventListener("click", playSlot);
  renderSlots();
}

function removeSlot(slot) {
  stop();
  slots = slots.filter((s) => s !== slot);
  while (slots.length < PRESETS.min_inputs) addSlot();
  renderSlots();
}

async function loadFile(slot, file) {
  const ext = "." + file.name.split(".").pop().toLowerCase();
  if (!PRESETS.extensions.includes(ext)) { setStatus("Alleen WAV, AIFF of FLAC.", true); return; }
  if (file.size > PRESETS.max_upload_bytes) { setStatus("Dit bestand is groter dan 50 MB.", true); return; }
  setStatus(`Loading ${file.name}...`);
  try {
    const bytes = await file.arrayBuffer();
    const info = await api("/api/upload", {
      method: "POST",
      headers: { "X-Filename": encodeURIComponent(file.name), "Content-Type": "application/octet-stream" },
      body: bytes,
    });
    slot.upload = info;
    try { slot.buffer = await decode(bytes.slice(0)); } catch (e) { slot.buffer = null; }
    setStatus("");
  } catch (err) {
    setStatus(`Kan ${file.name} niet laden: ${err.message}`, true);
  }
  renderSlots();
}

function renderKeepmap(slot) {
  const table = $(".keepmap", slot.el);
  const bands = bandsOf($("#soundType").value);
  table.innerHTML = "";
  const head = table.insertRow();
  const corner = document.createElement("th");
  corner.className = "rowhead";
  head.appendChild(corner);
  PRESETS.zones.forEach((z, zi) => {
    const th = document.createElement("th");
    th.textContent = z;
    th.title = "Click: set the whole column";
    th.addEventListener("click", () => setMany(slot, bands.map((_, b) => [b, zi])));
    head.appendChild(th);
  });
  for (let b = bands.length - 1; b >= 0; b--) {   // highest band on top
    const tr = table.insertRow();
    const th = document.createElement("th");
    th.className = "rowhead";
    th.textContent = bands[b];
    th.title = "Click: set the whole row";
    th.addEventListener("click", () => setMany(slot, [0, 1, 2].map((z) => [b, z])));
    tr.appendChild(th);
    for (let z = 0; z < 3; z++) {
      const td = tr.insertCell();
      const st = slot.keep[b][z][0];
      td.className = st;
      td.textContent = st === "keep" ? "KEEP" : st === "avoid" ? "✕" : "";
      td.title = `${bands[b]} / ${PRESETS.zones[z]}: ${st}`;
      td.addEventListener("click", () => {
        const next = STATES[(STATES.indexOf(slot.keep[b][z][0]) + 1) % 3];
        slot.keep[b][z] = [next, 1.0];
        renderKeepmap(slot);
      });
    }
  }
}

function setMany(slot, cells) {
  // cycle all cells together, starting from the state of the first one
  const first = slot.keep[cells[0][0]][cells[0][1]][0];
  const next = STATES[(STATES.indexOf(first) + 1) % 3];
  for (const [b, z] of cells) slot.keep[b][z] = [next, 1.0];
  renderKeepmap(slot);
}

function renderSlots() {
  const box = $("#slots");
  box.innerHTML = "";
  for (const slot of slots) {
    box.appendChild(slot.el);
    const loaded = !!slot.upload;
    $(".drop", slot.el).hidden = loaded;
    $(".loaded", slot.el).hidden = !loaded;
    if (loaded) {
      $(".slot-name", slot.el).textContent = slot.upload.name;
      $(".slot-name", slot.el).title = slot.upload.name;
      $(".slot-meta", slot.el).textContent =
        `${(slot.upload.seconds * 1000).toFixed(0)} ms · ${(slot.upload.sample_rate / 1000).toFixed(1)} kHz · ` +
        (slot.upload.channels === 1 ? "mono" : "stereo");
      renderKeepmap(slot);
    }
  }
  if (slots.length < PRESETS.max_inputs) {
    const add = document.createElement("button");
    add.className = "slot add-slot";
    add.textContent = "+ Add sample slot";
    add.addEventListener("click", addSlot);
    box.appendChild(add);
  }
  // canvases need to be in the DOM to have a size
  for (const slot of slots) if (slot.upload && slot.buffer) drawWave($(".wave", slot.el), slot.buffer);
  const ready = slots.filter((s) => s.upload).length;
  $("#generate").disabled = busy || ready < PRESETS.min_inputs;
}

// ---------------------------------------------------------------- generate
async function generate() {
  const loaded = slots.filter((s) => s.upload);
  if (loaded.length < PRESETS.min_inputs) return;
  stop();
  busy = true;
  renderSlots();
  $("#progressWrap").hidden = false;
  $("#progressBar").style.width = "0%";
  setStatus("Generating...");
  try {
    const rate = $("#rate").value;
    const body = {
      sound_type: $("#soundType").value,
      sample_rate: rate === "auto" ? "auto" : Number(rate),
      inputs: loaded.map((s) => ({ id: s.upload.id, trim_db: 0 })),
      keepmap: loaded.map((s) => s.keep),
    };
    const { job } = await api("/api/generate", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    let snap;
    for (;;) {
      await new Promise((r) => setTimeout(r, 200));
      snap = await api(`/api/jobs/${job}`);
      $("#progressBar").style.width = `${Math.round(snap.progress * 100)}%`;
      setStatus(snap.message);
      if (snap.status !== "running") break;
    }
    if (snap.status === "error") throw new Error(snap.error);
    await showResults(snap.result);
    setStatus(snap.message);
  } catch (err) {
    setStatus(`Generate is mislukt: ${err.message}`, true);
  } finally {
    busy = false;
    $("#progressWrap").hidden = true;
    renderSlots();
  }
}

async function fetchBuffer(id) {
  const res = await fetch(fileUrl(id));
  if (!res.ok) throw new Error(`${res.status}`);
  return await decode(await res.arrayBuffer());
}

async function showResults(result) {
  const box = $("#resultCards");
  box.innerHTML = "";
  const stackBuf = await fetchBuffer(result.stack);
  for (const v of result.variations) {
    const buf = await fetchBuffer(v.file);
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <div class="row"><span class="title"></span><span class="muted ms"></span></div>
      <canvas class="wave" height="70"></canvas>
      <div class="row">
        <button class="play round" title="Play">▶</button>
        <button class="ab">Compare with simple stack</button>
        <a class="button dl" title="Download WAV">Download</a>
      </div>
      <div class="row"><span class="ab-label muted"></span></div>`;
    $(".title", card).textContent = `${result.project} · ${v.name}`;
    $(".ms", card).textContent = `${(v.seconds * 1000).toFixed(0)} ms`;
    $(".dl", card).href = fileUrl(v.file, true);
    box.appendChild(card);
    $("#results").hidden = false;
    drawWave($(".wave", card), buf);
    const label = $(".ab-label", card);
    let showingStack = false;
    const playIt = (which) => {
      const isStack = which === "stack";
      lastPlayed = () => playIt(which);
      play(isStack ? stackBuf : buf,
        () => { card.classList.add("playing"); label.textContent = isStack ? "Now playing: simple stack" : "Now playing: your blend"; },
        () => card.classList.remove("playing"));
    };
    $(".play", card).addEventListener("click", () => { showingStack = false; playIt("blend"); });
    $(".wave", card).addEventListener("click", () => { showingStack = false; playIt("blend"); });
    $(".ab", card).addEventListener("click", () => { showingStack = !showingStack; playIt(showingStack ? "stack" : "blend"); });
  }
  $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------------------------------------------------------------- init
async function init() {
  try {
    PRESETS = await api("/api/presets");
  } catch (err) {
    setStatus("Geen verbinding met de engine. Start Layerforge opnieuw met het startbestand.", true);
    return;
  }
  $("#version").textContent = `v${PRESETS.version}`;
  const sel = $("#soundType");
  for (const t of Object.keys(PRESETS.types)) {
    const o = document.createElement("option");
    o.value = t;
    o.textContent = t;
    sel.appendChild(o);
  }
  sel.addEventListener("change", () => {
    for (const s of slots) s.keep = emptyKeep();   // band names differ per type
    renderSlots();
  });
  for (let i = 0; i < PRESETS.min_inputs; i++) addSlot();
  $("#generate").addEventListener("click", generate);
  document.addEventListener("keydown", (e) => {
    if (e.code === "Space" && !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) {
      e.preventDefault();
      if (current) stop(); else if (lastPlayed) lastPlayed();
    }
  });
  window.addEventListener("resize", () => renderSlots());
}

init();
