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
let fbTarget = null;     // {fileId, button} of the card the feedback dialog is for
let edit = null;         // editable settings: {edges, zones, order, guard, envelope, crossover, drive}
const previewCache = new Map();   // "source|selection|edges|order" -> AudioBuffer

// Dutch labels for the feedback tags (ids come from the server)
const TAG_LABELS = {
  good: "Goed zo", two_layers: "Klinkt als 2 lagen", tail_wrong: "Staart klopt niet",
  length_wrong: "Lengte klopt niet", low_end_thin: "Laag te dun", low_end_boomy: "Laag te dik/boemerig",
  attack_weak: "Aanslag te zwak", attack_doubled: "Dubbele aanslag", click_artifact: "Klik of artefact",
  phasey: "Fase-achtig/hol", too_dull: "Te dof", too_harsh: "Te scherp", pitch_clash: "Toonhoogtes botsen",
  not_my_direction: "Niet mijn richting",
};
const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
function noteName(f0) {
  if (!f0) return "";
  const midi = Math.round(69 + 12 * Math.log2(f0 / 440));
  return `${NOTE_NAMES[((midi % 12) + 12) % 12]}${Math.floor(midi / 12) - 1}`;
}

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
  source.loop = $("#loop").checked;
  const gainNode = ac.createGain();
  gainNode.gain.value = matchGain(buffer);
  let node = source.connect(gainNode);
  if ($("#mono").checked) {
    // Mono: the browser sums L and R ((L+R)/2) when a stereo signal enters a 1-channel node
    const mono = ac.createGain();
    mono.channelCount = 1;
    mono.channelCountMode = "explicit";
    mono.channelInterpretation = "speakers";
    node = node.connect(mono);
  }
  node.connect(ac.destination);
  const me = { source, onstop };
  source.onended = () => { if (current === me) { current = null; if (onstop) onstop(); } };
  current = me;
  if (onstart) onstart();
  source.start();
}

function soloSelection() {
  const v = $("#solo").value;
  if (!v || v === "all") return null;
  return { kind: v[0] === "g" ? "group" : "band", index: Number(v.slice(1)) };
}

async function previewBuffer(sourceId, select) {
  const key = `${sourceId}|${select.kind}${select.index}|${edit.edges.join(",")}|${edit.order}`;
  if (previewCache.has(key)) return previewCache.get(key);
  const res = await fetch("/api/preview", {
    method: "POST",
    headers: { "X-Layerforge-Token": TOKEN, "Content-Type": "application/json" },
    body: JSON.stringify({ source: sourceId, sound_type: $("#soundType").value, band_edges_hz: edit.edges,
                           filter_order: edit.order, select }),
  });
  if (!res.ok) throw new Error(`${res.status}`);
  const buf = await decode(await res.arrayBuffer());
  if (previewCache.size > 60) previewCache.clear();
  previewCache.set(key, buf);
  return buf;
}

// Play a sample or result, honouring Solo (and Mono / Loop through play()).
async function playSource(sourceId, buffer, onstart, onstop, forceSelect) {
  const select = forceSelect || soloSelection();
  if (!select) { play(buffer, onstart, onstop); return; }
  try {
    play(await previewBuffer(sourceId, select), onstart, onstop);
  } catch (err) {
    setStatus(`Solo mislukt: ${err.message}`, true);
  }
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
    playSource(slot.upload.id, slot.buffer, () => btn.classList.add("active"), () => btn.classList.remove("active"));
  };
  slot.play = playSlot;
  $(".play", el).addEventListener("click", playSlot);
  slot.trim = 0;
  $(".trimRange", el).addEventListener("input", () => {
    slot.trim = Number($(".trimRange", el).value);
    $(".trimLabel", el).textContent = `${slot.trim > 0 ? "+" : ""}${slot.trim.toFixed(1)} dB`;
  });
  setupSlotEditing(slot);
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
    slot.specImage = null;
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
      const meta = $(".slot-meta", slot.el);
      meta.textContent =
        `${(slot.upload.seconds * 1000).toFixed(0)} ms · ${(slot.upload.sample_rate / 1000).toFixed(1)} kHz · ` +
        (slot.upload.channels === 1 ? "mono" : "stereo");
      if (PRESETS.types[$("#soundType").value].pitched) {
        const p = document.createElement("span");
        p.className = "pitch";
        p.textContent = slot.upload.f0_hz ? ` · ${noteName(slot.upload.f0_hz)} (${slot.upload.f0_hz.toFixed(1)} Hz)` : " · no clear pitch";
        meta.appendChild(p);
      }
    }
  }
  if (slots.length < PRESETS.max_inputs) {
    const add = document.createElement("button");
    add.className = "slot add-slot";
    add.textContent = "+ Add sample slot";
    add.addEventListener("click", addSlot);
    box.appendChild(add);
  }
  for (const slot of slots) if (slot.upload && slot.buffer) drawSlot(slot);
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
      btn.title = choice === null ? "Layerforge decides (different per variation)"
        : `${choice.upload.name} (Alt/Option-click: listen to this part)`;
      btn.addEventListener("click", (e) => {
        if (e.altKey && choice) {   // Alt/Option-click: listen to exactly this part of this sample
          lastPlayed = () => playSource(choice.upload.id, choice.buffer, null, null, { kind: "group", index: g });
          lastPlayed();
          return;
        }
        groups[g] = choice;
        renderDirection();
      });
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
      inputs: loaded.map((s) => ({ id: s.upload.id, trim_db: s.trim || 0 })),
      groups: groups.map((g) => (g && loaded.includes(g) ? loaded.indexOf(g) : null)),
      advanced: {
        band_edges_hz: edit.edges, zone_edges_ms: edit.zones, filter_order: edit.order, guard_ms: edit.guard,
        relaxed_alignment: edit.envelope, crossover_check: edit.crossover, drive_db: edit.drive,
      },
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
        <button class="fb" title="Tell Claude what you think of this variation">Feedback</button>
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
      playSource(card.abStack ? result.stack : v.file, card.abStack ? stackBuf : buf,
        () => el.classList.add("playing"), () => el.classList.remove("playing"));
    };
    cards.push(card);
    el.addEventListener("click", (e) => {
      if (e.target.closest("button, a")) return;
      selectCard(index, true);
    });
    $(".ab", el).addEventListener("click", () => { selectCard(index, false); card.play(!card.abStack); });
    $(".recipe", el).addEventListener("click", () => showRecipe(`${result.project} · ${v.name}`, v.file));
    $(".fb", el).addEventListener("click", () => openFeedback(`${result.project} · ${v.name}`, v.file, $(".fb", el)));
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

// ---------------------------------------------------------------- editing: band edges, zones, Advanced
function typeInfo() { return PRESETS.types[$("#soundType").value]; }

function resetEdit() {
  const t = typeInfo(), a = PRESETS.advanced;
  edit = {
    edges: t.band_edges_hz.slice(), zones: t.zone_edges_ms.slice(), order: a.filter_order,
    guard: a.guard_ms, envelope: true, crossover: true, drive: t.drive_db,
  };
  const solo = $("#solo");
  solo.innerHTML = "";
  const opts = [["all", "All"]].concat(PRESETS.groups.map((g, i) => [`g${i}`, `${g} (group)`]),
    t.bands.map((b, i) => [`b${i}`, `${b} (band)`]));
  for (const [v, label] of opts) {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = label;
    solo.appendChild(o);
  }
  previewCache.clear();
  for (const slot of slots) slot.specImage = null;
  syncAdvanced();
}

function syncAdvanced() {
  $("#advOrder").value = String(edit.order);
  $("#advGuard").value = String(edit.guard);
  $("#advEnvelope").checked = edit.envelope;
  $("#advCrossover").checked = edit.crossover;
  $("#advDrive").value = String(edit.drive);
  $("#advDriveLabel").textContent = `${edit.drive.toFixed(1)} dB`;
  const ed = $("#advEdges");
  ed.innerHTML = "";
  edit.edges.forEach((v, i) => {
    const inp = document.createElement("input");
    inp.type = "number";
    inp.className = "num";
    inp.value = String(Math.round(v));
    inp.title = `${typeInfo().bands[i]} | ${typeInfo().bands[i + 1]}`;
    inp.addEventListener("change", () => { setEdge(i, Number(inp.value)); });
    ed.appendChild(inp);
  });
  const zd = $("#advZones");
  zd.innerHTML = "";
  edit.zones.forEach((v, i) => {
    const inp = document.createElement("input");
    inp.type = "number";
    inp.className = "num";
    inp.value = String(Math.round(v));
    inp.title = i === 0 ? "Attack | Sustain" : "Sustain | Tail";
    inp.addEventListener("change", () => { setZone(i, Number(inp.value)); });
    zd.appendChild(inp);
  });
}

function setupAdvanced() {
  const a = PRESETS.advanced;
  for (const o of a.filter_orders) {
    const opt = document.createElement("option");
    opt.value = String(o);
    opt.textContent = String(o);
    $("#advOrder").appendChild(opt);
  }
  $("#advDrive").max = String(a.drive_max_db);
  $("#advGuard").max = String(a.guard_max_ms);
  $("#advOrder").addEventListener("change", () => { edit.order = Number($("#advOrder").value); previewCache.clear(); });
  $("#advGuard").addEventListener("change", () => {
    edit.guard = Math.min(a.guard_max_ms, Math.max(0, Number($("#advGuard").value) || 0));
    syncAdvanced();
  });
  $("#advEnvelope").addEventListener("change", () => { edit.envelope = $("#advEnvelope").checked; });
  $("#advCrossover").addEventListener("change", () => { edit.crossover = $("#advCrossover").checked; });
  $("#advDrive").addEventListener("input", () => {
    edit.drive = Number($("#advDrive").value);
    $("#advDriveLabel").textContent = `${edit.drive.toFixed(1)} dB`;
  });
  $("#advReset").addEventListener("click", () => { resetEdit(); redrawAll(); });
}

function maxEdgeHz() {
  const rates = loadedSlots().map((s) => s.upload.sample_rate);
  return 0.45 * Math.min(44100, ...rates);
}

function setEdge(i, hz) {
  if (!Number.isFinite(hz)) { syncAdvanced(); return; }
  const r = PRESETS.advanced.edge_min_ratio;
  const lo = i > 0 ? edit.edges[i - 1] * r : PRESETS.advanced.edge_min_hz;
  const hi = i < edit.edges.length - 1 ? edit.edges[i + 1] / r : maxEdgeHz();
  edit.edges[i] = Math.min(hi, Math.max(lo, hz));
  syncAdvanced();
  redrawAll();
}

function setZone(i, ms) {
  if (!Number.isFinite(ms)) { syncAdvanced(); return; }
  const a = PRESETS.advanced;
  if (i === 0) edit.zones[0] = Math.min(edit.zones[1] - 1, Math.max(a.zone_min_ms, ms));
  else edit.zones[1] = Math.min(a.zone_max_ms, Math.max(edit.zones[0] + 1, ms));
  syncAdvanced();
  redrawAll();
}

function redrawAll() {
  for (const slot of slots) if (slot.upload && slot.buffer) drawSlot(slot);
}

// --- spectrogram (log frequency 20 Hz - 20 kHz) and zone waveform
const F_LO = 20, F_HI = 20000;
function freqToY(f, h) { return h * (1 - Math.log(f / F_LO) / Math.log(F_HI / F_LO)); }
function yToFreq(y, h) { return F_LO * Math.pow(F_HI / F_LO, 1 - y / h); }

function fft(re, im) {   // in place, radix 2
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) { [re[i], re[j]] = [re[j], re[i]]; [im[i], im[j]] = [im[j], im[i]]; }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = -2 * Math.PI / len, wr = Math.cos(ang), wi = Math.sin(ang);
    for (let i = 0; i < n; i += len) {
      let cr = 1, ci = 0;
      for (let k = 0; k < len / 2; k++) {
        const a = i + k, b = a + len / 2;
        const tr = re[b] * cr - im[b] * ci, ti = re[b] * ci + im[b] * cr;
        re[b] = re[a] - tr; im[b] = im[a] - ti;
        re[a] += tr; im[a] += ti;
        const nr = cr * wr - ci * wi;
        ci = cr * wi + ci * wr;
        cr = nr;
      }
    }
  }
}

function viewSeconds(slot) {
  const z2 = typeInfo().zone_edges_ms[1] / 1000;
  return Math.min(slot.buffer.duration, Math.max(4 * z2, 0.5));
}

function colour(v) {   // 0..1 -> dark blue -> accent -> pale
  const stops = [[14, 15, 19], [40, 60, 150], [255, 138, 61], [255, 240, 200]];
  const x = Math.min(0.999, Math.max(0, v)) * (stops.length - 1);
  const k = Math.floor(x), f = x - k;
  return stops[k].map((c, i) => Math.round(c + (stops[k + 1][i] - c) * f));
}

function computeSpecImage(slot, w, h) {
  const buf = slot.buffer, sr = buf.sampleRate, N = 2048;
  const chans = [];
  for (let c = 0; c < buf.numberOfChannels; c++) chans.push(buf.getChannelData(c));
  const view = viewSeconds(slot) * sr;
  const cols = [];
  let maxDb = -200;
  const win = new Float32Array(N).map((_, i) => 0.5 - 0.5 * Math.cos(2 * Math.PI * i / N));
  for (let x = 0; x < w; x++) {
    const centre = Math.floor((x + 0.5) / w * view);
    const re = new Float64Array(N), im = new Float64Array(N);
    for (let i = 0; i < N; i++) {
      const k = centre - N / 2 + i;
      if (k < 0 || k >= buf.length) continue;
      let v = 0;
      for (const d of chans) v += d[k];
      re[i] = (v / chans.length) * win[i];
    }
    fft(re, im);
    const col = new Float32Array(h);
    for (let y = 0; y < h; y++) {
      const f0 = yToFreq(y + 1, h), f1 = yToFreq(y, h);
      let b0 = Math.floor(f0 / sr * N), b1 = Math.max(b0, Math.ceil(f1 / sr * N));
      b1 = Math.min(b1, N / 2);
      let m = 0;
      for (let b = b0; b <= b1; b++) m = Math.max(m, re[b] * re[b] + im[b] * im[b]);
      col[y] = 10 * Math.log10(m + 1e-20);
      maxDb = Math.max(maxDb, col[y]);
    }
    cols.push(col);
  }
  const img = new ImageData(w, h);
  for (let x = 0; x < w; x++) {
    for (let y = 0; y < h; y++) {
      const [r, g, b] = colour((cols[x][y] - maxDb + 80) / 80);
      const o = (y * w + x) * 4;
      img.data[o] = r; img.data[o + 1] = g; img.data[o + 2] = b; img.data[o + 3] = 255;
    }
  }
  return img;
}

function drawSlot(slot) {
  const dpr = window.devicePixelRatio || 1;
  // spectrogram + band edges
  const spec = $(".spec", slot.el);
  const w = Math.max(1, Math.round(spec.clientWidth * dpr)), h = Math.max(1, Math.round(spec.clientHeight * dpr));
  if (spec.width !== w || spec.height !== h) { spec.width = w; spec.height = h; slot.specImage = null; }
  if (!slot.specImage) slot.specImage = computeSpecImage(slot, w, h);
  const g = spec.getContext("2d");
  g.putImageData(slot.specImage, 0, 0);
  const t = typeInfo();
  g.font = `${11 * dpr}px system-ui, sans-serif`;
  const bounds = [F_LO].concat(edit.edges, [F_HI]);
  for (let b = 0; b < t.bands.length; b++) {
    const ya = freqToY(bounds[b + 1], h), yb = freqToY(bounds[b], h);
    const label = `${t.bands[b]} · ${PRESETS.groups[t.band_groups[b]]}`;
    const ty = Math.max(12 * dpr, (ya + yb) / 2 + 4 * dpr);
    g.fillStyle = "rgba(14,15,19,0.7)";
    g.fillRect(2 * dpr, ty - 11 * dpr, g.measureText(label).width + 6 * dpr, 14 * dpr);
    g.fillStyle = "#e8e9ec";
    g.fillText(label, 5 * dpr, ty);
  }
  edit.edges.forEach((f, i) => {
    const y = freqToY(f, h);
    g.strokeStyle = slot.dragEdge === i ? "#ffffff" : "rgba(255,138,61,0.95)";
    g.lineWidth = 2 * dpr;
    g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke();
    const label = f >= 1000 ? `${(f / 1000).toFixed(f >= 10000 ? 0 : 1)}k` : `${Math.round(f)}`;
    const lw = g.measureText(label).width;
    g.fillStyle = "rgba(14,15,19,0.7)";
    g.fillRect(w - lw - 8 * dpr, y - 14 * dpr, lw + 6 * dpr, 12 * dpr);
    g.fillStyle = "#ffffff";
    g.fillText(label, w - lw - 5 * dpr, y - 4 * dpr);
  });
  // waveform (same time window) + onset + zone edges
  const wave = $(".wave", slot.el);
  const ww = Math.max(1, Math.round(wave.clientWidth * dpr)), wh = Math.max(1, Math.round(wave.clientHeight * dpr));
  wave.width = ww; wave.height = wh;
  const gw = wave.getContext("2d");
  const buf = slot.buffer, sr = buf.sampleRate, view = viewSeconds(slot) * sr;
  const chans = [];
  for (let c = 0; c < buf.numberOfChannels; c++) chans.push(buf.getChannelData(c));
  let peak = 0;
  for (const d of chans) for (let i = 0; i < Math.min(view, buf.length); i++) peak = Math.max(peak, Math.abs(d[i]));
  peak = peak || 1;
  gw.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--wave").trim() || "#9fb4ff";
  for (let x = 0; x < ww; x++) {
    const a = Math.floor(x / ww * view), b = Math.max(a + 1, Math.floor((x + 1) / ww * view));
    let lo = 0, hi = 0;
    for (const d of chans) for (let i = a; i < b && i < buf.length; i++) { lo = Math.min(lo, d[i]); hi = Math.max(hi, d[i]); }
    const y1 = wh / 2 - (hi / peak) * (wh / 2 - 2), y2 = wh / 2 - (lo / peak) * (wh / 2 - 2);
    gw.fillRect(x, y1, 1, Math.max(1, y2 - y1));
  }
  const onset = (slot.upload.onset_ms || 0) / 1000;
  const xs = (sec) => sec * sr / view * ww;
  gw.strokeStyle = "rgba(232,233,236,0.5)";
  gw.lineWidth = 1 * dpr;
  gw.beginPath(); gw.moveTo(xs(onset), 0); gw.lineTo(xs(onset), wh); gw.stroke();
  const names = ["A", "S", "T"];
  gw.font = `${11 * dpr}px system-ui, sans-serif`;
  const edgesX = edit.zones.map((ms) => xs(onset + ms / 1000));
  edgesX.forEach((x, i) => {
    gw.strokeStyle = slot.dragZone === i ? "#ffffff" : "rgba(255,138,61,0.95)";
    gw.lineWidth = 2 * dpr;
    gw.beginPath(); gw.moveTo(x, 0); gw.lineTo(x, wh); gw.stroke();
  });
  const mids = [(xs(onset) + edgesX[0]) / 2, (edgesX[0] + edgesX[1]) / 2, (edgesX[1] + ww) / 2];
  gw.fillStyle = "#ffffff";
  mids.forEach((x, i) => { if (x < ww - 6 * dpr) gw.fillText(names[i], Math.max(2 * dpr, x - 3 * dpr), 12 * dpr); });
  $(".zone-label", slot.el).textContent =
    `Attack ${edit.zones[0].toFixed(0)} ms · Sustain tot ${edit.zones[1].toFixed(0)} ms · getoond: eerste ${(viewSeconds(slot) * 1000).toFixed(0)} ms`;
}

function setupSlotEditing(slot) {
  const spec = $(".spec", slot.el), wave = $(".wave", slot.el);
  const near = 7;
  const edgeAt = (e) => {
    const r = spec.getBoundingClientRect();
    const y = e.clientY - r.top;
    let best = -1, d = near;
    edit.edges.forEach((f, i) => { const dd = Math.abs(freqToY(f, r.height) - y); if (dd < d) { d = dd; best = i; } });
    return best;
  };
  spec.addEventListener("pointermove", (e) => {
    if (slot.dragEdge != null) {
      const r = spec.getBoundingClientRect();
      setEdge(slot.dragEdge, yToFreq(Math.min(r.height, Math.max(0, e.clientY - r.top)), r.height));
    } else {
      spec.style.cursor = edgeAt(e) >= 0 ? "ns-resize" : "default";
    }
  });
  spec.addEventListener("pointerdown", (e) => {
    const i = edgeAt(e);
    if (i < 0) return;
    slot.dragEdge = i;
    spec.setPointerCapture(e.pointerId);
    redrawAll();
  });
  const endEdge = () => { if (slot.dragEdge != null) { slot.dragEdge = null; previewCache.clear(); redrawAll(); } };
  spec.addEventListener("pointerup", endEdge);
  spec.addEventListener("pointercancel", endEdge);

  const zoneSec = (e) => {
    const r = wave.getBoundingClientRect();
    return (e.clientX - r.left) / r.width * viewSeconds(slot);
  };
  const zoneAt = (e) => {
    const r = wave.getBoundingClientRect();
    const onset = (slot.upload.onset_ms || 0) / 1000;
    let best = -1, d = near;
    edit.zones.forEach((ms, i) => {
      const x = (onset + ms / 1000) / viewSeconds(slot) * r.width;
      const dd = Math.abs(x - (e.clientX - r.left));
      if (dd < d) { d = dd; best = i; }
    });
    return best;
  };
  wave.addEventListener("pointermove", (e) => {
    if (slot.dragZone != null) {
      slot.moved = true;
      setZone(slot.dragZone, (zoneSec(e) - (slot.upload.onset_ms || 0) / 1000) * 1000);
    } else {
      wave.style.cursor = zoneAt(e) >= 0 ? "ew-resize" : "pointer";
    }
  });
  wave.addEventListener("pointerdown", (e) => {
    const i = zoneAt(e);
    slot.moved = false;
    if (i < 0) return;
    slot.dragZone = i;
    wave.setPointerCapture(e.pointerId);
    redrawAll();
  });
  wave.addEventListener("pointerup", () => {
    if (slot.dragZone != null) { slot.dragZone = null; redrawAll(); return; }
    if (!slot.moved) slot.play();
  });
}

// ---------------------------------------------------------------- feedback
function openFeedback(title, fileId, button) {
  fbTarget = { fileId, button };
  $("#feedbackTitle").textContent = `Feedback · ${title}`;
  for (const b of document.querySelectorAll("#feedbackDialog .rate")) b.classList.remove("on");
  for (const b of document.querySelectorAll("#fbTags button")) b.classList.remove("on");
  $("#fbText").value = "";
  $("#feedbackDialog").showModal();
}

async function sendFeedback() {
  if (!fbTarget) return;
  const rateBtn = document.querySelector("#feedbackDialog .rate.on");
  const tags = [...document.querySelectorAll("#fbTags button.on")].map((b) => b.dataset.tag);
  const text = $("#fbText").value;
  if (!rateBtn && !tags.length && !text.trim()) { setStatus("Kies eerst iets, of typ een opmerking.", true); return; }
  try {
    const r = await postJson("/api/feedback", {
      file: fbTarget.fileId, rating: rateBtn ? Number(rateBtn.dataset.rating) : 0, tags, text,
    });
    fbTarget.button.classList.add("done");
    fbTarget.button.textContent = "Feedback ✓";
    $("#fbCount").textContent = String(r.count);
    $("#feedbackDialog").close();
    setStatus("Bedankt! Je feedback is bewaard.");
  } catch (err) {
    setStatus(`Feedback mislukt: ${err.message}`, true);
  }
}

function setupFeedback() {
  const box = $("#fbTags");
  for (const tag of PRESETS.feedback_tags) {
    const b = document.createElement("button");
    b.dataset.tag = tag;
    b.textContent = TAG_LABELS[tag] || tag;
    b.addEventListener("click", () => b.classList.toggle("on"));
    box.appendChild(b);
  }
  for (const b of document.querySelectorAll("#feedbackDialog .rate")) {
    b.addEventListener("click", () => {
      const was = b.classList.contains("on");
      for (const o of document.querySelectorAll("#feedbackDialog .rate")) o.classList.remove("on");
      if (!was) b.classList.add("on");
    });
  }
  $("#fbSend").addEventListener("click", sendFeedback);
  $("#feedbackClose").addEventListener("click", () => $("#feedbackDialog").close());
  $("#exportFeedback").href = `/api/feedback/export?token=${encodeURIComponent(TOKEN)}`;
  api("/api/feedback/count").then((r) => { $("#fbCount").textContent = String(r.count); }).catch(() => {});
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
  sel.addEventListener("change", () => { resetEdit(); render(); });
  setupAdvanced();
  resetEdit();
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
  setupFeedback();
  for (let i = 0; i < PRESETS.min_inputs; i++) addSlot();
  $("#generate").addEventListener("click", generate);
  document.addEventListener("keydown", (e) => {
    const tag = document.activeElement ? document.activeElement.tagName : "";
    if (["INPUT", "SELECT", "TEXTAREA"].includes(tag) || $("#recipeDialog").open || $("#feedbackDialog").open) return;
    if (e.code === "Space") {
      e.preventDefault();
      if (current) stop(); else if (lastPlayed) lastPlayed();
    } else if (cards.length && ["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(e.code)) {
      e.preventDefault();
      const sel = cards.findIndex((c) => c.el.classList.contains("selected"));
      selectCard(sel + (e.code === "ArrowRight" || e.code === "ArrowDown" ? 1 : -1), true);
    }
  });
  for (const id of ["#solo", "#mono", "#loop"]) $(id).addEventListener("change", () => $(id).blur());
  window.addEventListener("resize", () => {
    for (const slot of slots) slot.specImage = null;
    renderSlots();
    for (const c of cards) drawWave($(".wave", c.el), c.buf);
  });
}

init();
