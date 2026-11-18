// Layerforge UI (plain JavaScript, no libraries). The server token arrives in the page URL
// (?token=...) and is sent on every API call.
"use strict";

const TOKEN = new URLSearchParams(location.search).get("token") || "";
const MATCH_RMS = Math.pow(10, -20 / 20);   // every played sound is matched to this RMS (first 200 ms)
const MATCH_MAX_GAIN = Math.pow(10, 12 / 20);

let PRESETS = null;
let slots = [];          // {el, upload, buffer}
let groups = [];         // per group (Low, Mid, High): the chosen slot object, or null = Free
let cards = [];          // result cards: {el, play()}
let audioCtx = null;
let current = null;      // {source, onstop}
let lastPlayed = null;   // plays the last thing again (space bar)
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

async function postJson(path, body) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

function fileUrl(id, download) {
  return `/api/file/${id}?token=${encodeURIComponent(TOKEN)}${download ? "&download=1" : ""}`;
}

function setStatus(text, bad) {
  const s = $("#status");
  s.textContent = text || "";
  s.classList.toggle("bad", !!bad);
}

function randomSeed() { return Math.floor(Math.random() * 2147483647); }

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
  const me = { source, onstop };
  source.onended = () => { if (current === me) { current = null; if (onstop) onstop(); } };
  current = me;
  if (onstart) onstart();
  source.start();
}

async function decode(arrayBuffer) { return await ctx().decodeAudioData(arrayBuffer); }

function drawWave(canvas, buffer) {
  const w = canvas.width = Math.max(1, canvas.clientWidth * (window.devicePixelRatio || 1));
  const h = canvas.height = Math.max(1, canvas.clientHeight * (window.devicePixelRatio || 1));
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
    for (const d of chans) for (let i = a; i < b && i < n; i++) { lo = Math.min(lo, d[i]); hi = Math.max(hi, d[i]); }
    const y1 = h / 2 - (hi / peak) * (h / 2 - 2), y2 = h / 2 - (lo / peak) * (h / 2 - 2);
    g.fillRect(x, y1, 1, Math.max(1, y2 - y1));
  }
}

// ---------------------------------------------------------------- samples
function loadedSlots() { return slots.filter((s) => s.upload); }

function addSlot() {
  const el = $("#slotTemplate").content.firstElementChild.cloneNode(true);
  const slot = { el, upload: null, buffer: null };
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
  render();
}

function removeSlot(slot) {
  stop();
  slots = slots.filter((s) => s !== slot);
  groups = groups.map((g) => (g === slot ? null : g));
  while (slots.length < PRESETS.min_inputs) addSlot();
  render();
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
  render();
}

function renderSlots() {
  const box = $("#slots");
  box.innerHTML = "";
  const loaded = loadedSlots();
  for (const slot of slots) {
    box.appendChild(slot.el);
    const isLoaded = !!slot.upload;
    $(".drop", slot.el).hidden = isLoaded;
    $(".loaded", slot.el).hidden = !isLoaded;
    if (isLoaded) {
      $(".badge", slot.el).textContent = String(loaded.indexOf(slot) + 1);
      $(".slot-name", slot.el).textContent = slot.upload.name;
      $(".slot-name", slot.el).title = slot.upload.name;
      $(".slot-meta", slot.el).textContent =
        `${(slot.upload.seconds * 1000).toFixed(0)} ms · ${(slot.upload.sample_rate / 1000).toFixed(1)} kHz · ` +
        (slot.upload.channels === 1 ? "mono" : "stereo");
    }
  }
  if (slots.length < PRESETS.max_inputs) {
    const add = document.createElement("button");
    add.className = "slot add-slot";
    add.textContent = "+ Add sample slot";
    add.addEventListener("click", addSlot);
    box.appendChild(add);
  }
  for (const slot of slots) if (slot.upload && slot.buffer) drawWave($(".wave", slot.el), slot.buffer);
}

// ---------------------------------------------------------------- direction (Low / Mid / High)
function renderDirection() {
  const loaded = loadedSlots();
  $("#directionSection").hidden = loaded.length === 0;
  const table = $("#direction");
  table.innerHTML = "";
  const head = table.insertRow();
  head.appendChild(document.createElement("th"));
  loaded.forEach((s, i) => {
    const th = document.createElement("th");
    th.className = "src";
    th.textContent = `${i + 1}: ${s.upload.name.replace(/\.[^.]+$/, "")}`;
    th.title = s.upload.name;
    head.appendChild(th);
  });
  const freeHead = document.createElement("th");
  freeHead.className = "src";
  freeHead.textContent = "Free";
  head.appendChild(freeHead);
  const bandsPerGroup = PRESETS.types[$("#soundType").value].groups;
  for (let g = PRESETS.groups.length - 1; g >= 0; g--) {   // High on top
    const tr = table.insertRow();
    const th = document.createElement("th");
    th.className = "group";
    th.innerHTML = `${PRESETS.groups[g]}<small></small>`;
    $("small", th).textContent = bandsPerGroup[g].join(" + ");
    tr.appendChild(th);
    const choices = loaded.concat([null]);
    for (const choice of choices) {
      const td = tr.insertCell();
      const btn = document.createElement("button");
      const on = groups[g] === choice;
      btn.className = (choice === null ? "free " : "") + (on ? "on" : "");
      btn.textContent = choice === null ? "Free" : String(loaded.indexOf(choice) + 1);
      btn.title = choice === null ? "Layerforge decides (different per variation)" : choice.upload.name;
      btn.addEventListener("click", () => { groups[g] = choice; renderDirection(); });
      td.appendChild(btn);
    }
  }
}

function render() {
  renderSlots();
  renderDirection();
  $("#generate").disabled = busy || loadedSlots().length < PRESETS.min_inputs;
}

// ---------------------------------------------------------------- generate
async function generate() {
  const loaded = loadedSlots();
  if (loaded.length < PRESETS.min_inputs) return;
  stop();
  if (!$("#lock").checked || $("#seed").value === "") $("#seed").value = String(randomSeed());
  busy = true;
  render();
  $("#progressWrap").hidden = false;
  $("#progressBar").style.width = "0%";
  setStatus("Generating...");
  try {
    const rate = $("#rate").value;
    const body = {
      sound_type: $("#soundType").value,
      sample_rate: rate === "auto" ? "auto" : Number(rate),
      variations: Number($("#count").value),
      amount: Number($("#amount").value),
      seed: Number($("#seed").value),
      inputs: loaded.map((s) => ({ id: s.upload.id, trim_db: 0 })),
      groups: groups.map((g) => (g && loaded.includes(g) ? loaded.indexOf(g) : null)),
    };
    const { job } = await postJson("/api/generate", body);
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
    render();
  }
}

async function fetchBuffer(id) {
  const res = await fetch(fileUrl(id));
  if (!res.ok) throw new Error(`${res.status}`);
  return await decode(await res.arrayBuffer());
}

async function showRecipe(title, fileId) {
  const text = await (await fetch(`/api/recipe/${fileId}?token=${encodeURIComponent(TOKEN)}`)).text();
  $("#recipeTitle").textContent = title;
  $("#recipeText").textContent = text;
  $("#recipeDialog").showModal();
}

function selectCard(i, andPlay) {
  if (!cards.length) return;
  i = (i + cards.length) % cards.length;
  cards.forEach((c, k) => c.el.classList.toggle("selected", k === i));
  cards[i].el.focus();
  if (andPlay) cards[i].play(false);
}

async function showResults(result) {
  const box = $("#resultCards");
  box.innerHTML = "";
  cards = [];
  $("#results").hidden = false;
  const notes = $("#notes");
  notes.hidden = !result.notes.length;
  notes.textContent = result.notes.join(" ");
  const stackBuf = result.stack ? await fetchBuffer(result.stack) : null;
  for (const v of result.variations) {
    const buf = await fetchBuffer(v.file);
    const el = document.createElement("div");
    el.className = "card";
    el.tabIndex = 0;
    el.draggable = true;
    el.innerHTML = `
      <div class="row"><span class="title"></span><span class="muted ms"></span></div>
      <canvas class="wave" height="70"></canvas>
      <div class="row">
        <button class="star round" title="Star: copy to Favourites">☆</button>
        <button class="ab" title="Switch between this variation and the simple stack">A/B stack</button>
        <a class="button dl" title="Download WAV">Download</a>
        <button class="recipe" title="Show the recipe (all numbers of this blend)">Recipe</button>
      </div>`;
    const fname = `layerforge_${result.project}_${v.name}.wav`;
    $(".title", el).textContent = v.name;
    $(".ms", el).textContent = `${(v.seconds * 1000).toFixed(0)} ms`;
    const dl = $(".dl", el);
    dl.href = fileUrl(v.file, true);
    dl.setAttribute("download", fname);
    box.appendChild(el);
    drawWave($(".wave", el), buf);
    const card = { el, buf, abStack: false };
    const index = cards.length;
    card.play = (stack) => {
      card.abStack = !!stack && !!stackBuf;
      $(".ab", el).classList.toggle("on", card.abStack);
      lastPlayed = () => card.play(card.abStack);
      play(card.abStack ? stackBuf : buf, () => el.classList.add("playing"), () => el.classList.remove("playing"));
    };
    cards.push(card);
    el.addEventListener("click", (e) => {
      if (e.target.closest("button, a")) return;
      selectCard(index, true);
    });
    $(".ab", el).addEventListener("click", () => { selectCard(index, false); card.play(!card.abStack); });
    $(".recipe", el).addEventListener("click", () => showRecipe(`${result.project} · ${v.name}`, v.file));
    const star = $(".star", el);
    star.addEventListener("click", async () => {
      const on = !star.classList.contains("on");
      try {
        await postJson("/api/star", { file: v.file, on });
        star.classList.toggle("on", on);
        star.textContent = on ? "★" : "☆";
      } catch (err) { setStatus(`Star mislukt: ${err.message}`, true); }
    });
    el.addEventListener("dragstart", (e) => {
      const url = new URL(fileUrl(v.file, true), location.href).href;
      e.dataTransfer.setData("DownloadURL", `audio/wav:${fname}:${url}`);
      e.dataTransfer.setData("text/uri-list", url);
    });
  }
  if (cards.length) selectCard(0, false);
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
  groups = PRESETS.groups.map(() => null);
  const sel = $("#soundType");
  for (const t of Object.keys(PRESETS.types)) {
    const o = document.createElement("option");
    o.value = t;
    o.textContent = t;
    sel.appendChild(o);
  }
  sel.addEventListener("change", render);
  const v = PRESETS.variations;
  for (const id of ["#count", "#countRange"]) {
    $(id).min = v.min; $(id).max = v.max; $(id).value = v.default;
  }
  $("#countRange").addEventListener("input", () => { $("#count").value = $("#countRange").value; });
  $("#count").addEventListener("change", () => {
    const n = Math.min(v.max, Math.max(v.min, Math.round(Number($("#count").value) || v.default)));
    $("#count").value = n;
    $("#countRange").value = n;
  });
  $("#amount").value = PRESETS.amount_default;
  const showAmount = () => { $("#amountLabel").textContent = `${$("#amount").value}%`; };
  $("#amount").addEventListener("input", showAmount);
  showAmount();
  $("#seed").value = String(randomSeed());
  $("#newSeed").addEventListener("click", () => { $("#seed").value = String(randomSeed()); });
  $("#seed").addEventListener("change", () => { $("#lock").checked = true; });
  $("#openFolder").addEventListener("click", async () => {
    try {
      const r = await postJson("/api/open-folder", {});
      setStatus(`Folder: ${r.path}`);
    } catch (err) { setStatus(`Kan de map niet openen: ${err.message}`, true); }
  });
  $("#recipeClose").addEventListener("click", () => $("#recipeDialog").close());
  for (let i = 0; i < PRESETS.min_inputs; i++) addSlot();
  $("#generate").addEventListener("click", generate);
  document.addEventListener("keydown", (e) => {
    const tag = document.activeElement ? document.activeElement.tagName : "";
    if (["INPUT", "SELECT", "TEXTAREA"].includes(tag) || $("#recipeDialog").open) return;
    if (e.code === "Space") {
      e.preventDefault();
      if (current) stop(); else if (lastPlayed) lastPlayed();
    } else if (cards.length && ["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(e.code)) {
      e.preventDefault();
      const sel = cards.findIndex((c) => c.el.classList.contains("selected"));
      selectCard(sel + (e.code === "ArrowRight" || e.code === "ArrowDown" ? 1 : -1), true);
    }
  });
  window.addEventListener("resize", () => {
    renderSlots();
    for (const c of cards) drawWave($(".wave", c.el), c.buf);
  });
}

init();
