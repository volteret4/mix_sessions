const $ = (id) => document.getElementById(id);
const audio = $("audio");
let sessions = [];
let tracks = [];
let current = null;

// Tiempos del cue: mm:ss:ff con 75 frames por segundo
function parseCue(text) {
  const out = [];
  let t = null;
  for (const line of text.split(/\r?\n/)) {
    const l = line.trim();
    let m;
    if (l.startsWith("TRACK ")) out.push((t = { title: "", performer: "", start: 0, genre: "", label: "", date: "", cover: "" }));
    else if (t && (m = l.match(/^(TITLE|PERFORMER) "(.*)"$/))) t[m[1].toLowerCase()] = m[2];
    // Metadatos añadidos por enrich.py
    else if (t && (m = l.match(/^REM (GENRE|LABEL|DATE|COVER) "?(.*?)"?$/))) t[m[1].toLowerCase()] = m[2];
    else if (t && (m = l.match(/^INDEX 01 (\d+):(\d+):(\d+)/))) t.start = +m[1] * 60 + +m[2] + +m[3] / 75;
  }
  return out;
}

function fmt(s) {
  s = Math.floor(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

function fmtDate(iso) {
  return new Date(iso).toLocaleDateString("es-ES", { day: "numeric", month: "long", year: "numeric" });
}

function trackEnd(i) {
  return i + 1 < tracks.length ? tracks[i + 1].start : audio.duration || current.duration;
}

// Grupos y etiquetas (opcionales en sessions.json: "group" y "tags").
// Con window.MIX_EDITOR (lo pone editor.py) cada sesión se puede editar y guardar.
let activeTag = null;

function sessionItem(s) {
  const li = document.createElement("li");
  li.innerHTML = `<a href="#${s.id}"><span class="s-title"></span><span class="s-meta">${fmtDate(s.date)} · ${fmt(s.duration)}</span><span class="s-tags"></span></a>`;
  li.querySelector(".s-title").textContent = s.title;
  for (const t of s.tags || []) {
    const b = document.createElement("span");
    b.className = "tag";
    b.textContent = t;
    li.querySelector(".s-tags").append(b);
  }
  if (window.MIX_EDITOR) li.append(editorRow(s));
  return li;
}

function renderTags() {
  const box = $("tags");
  if (!box) return;
  const all = [...new Set(sessions.flatMap((s) => s.tags || []))].sort((a, b) => a.localeCompare(b, "es"));
  box.innerHTML = "";
  box.hidden = !all.length;
  for (const t of all) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip" + (t === activeTag ? " active" : "");
    b.setAttribute("aria-pressed", String(t === activeTag));
    b.textContent = t;
    b.onclick = () => { activeTag = activeTag === t ? null : t; renderSessions(); };
    box.append(b);
  }
}

function renderSessions() {
  renderTags();
  const list = $("sessions");
  list.innerHTML = "";
  const shown = sessions.filter((s) => !activeTag || (s.tags || []).includes(activeTag));
  const groups = [...new Set(shown.map((s) => s.group || ""))];
  // Sin grupos, lista simple; con grupos, un encabezado por grupo y «Otras» al final.
  groups.sort((a, b) => (a === "") - (b === "") || a.localeCompare(b, "es"));
  for (const g of groups) {
    if (groups.length > 1 || g) {
      const h = document.createElement("li");
      h.className = "group";
      h.textContent = g || "Otras";
      list.append(h);
    }
    for (const s of shown.filter((x) => (x.group || "") === g)) list.append(sessionItem(s));
  }
  if (current) document.querySelectorAll(".sessions a").forEach((a) => a.classList.toggle("active", a.hash === "#" + current.id));
}

// ── Modo edición (editor.py) ─────────────────────────────────────────────────
function editorRow(s) {
  const f = document.createElement("div");
  f.className = "edit";
  f.innerHTML = `<input class="e-title" aria-label="Título"><input class="e-group" list="group-list" aria-label="Grupo" placeholder="Grupo"><input class="e-tags" aria-label="Etiquetas" placeholder="etiquetas, separadas, por comas">`;
  f.querySelector(".e-title").value = s.title;
  f.querySelector(".e-group").value = s.group || "";
  f.querySelector(".e-tags").value = (s.tags || []).join(", ");
  f.oninput = () => {
    s.title = f.querySelector(".e-title").value.trim() || s.title;
    const g = f.querySelector(".e-group").value.trim();
    if (g) s.group = g; else delete s.group;
    const tags = f.querySelector(".e-tags").value.split(",").map((t) => t.trim()).filter(Boolean);
    if (tags.length) s.tags = [...new Set(tags)]; else delete s.tags;
    $("save-state").textContent = "Cambios sin guardar";
  };
  f.onchange = () => renderTags();
  return f;
}

function setupEditor() {
  const bar = document.createElement("div");
  bar.className = "editor-bar";
  bar.innerHTML = `<b>Modo edición</b><span id="save-state"></span><button type="button" id="save">Guardar y publicar</button><datalist id="group-list"></datalist>`;
  $("sessions").before(bar);
  const refreshGroups = () => {
    $("group-list").innerHTML = "";
    for (const g of new Set(sessions.map((s) => s.group).filter(Boolean))) $("group-list").append(new Option(g));
  };
  refreshGroups();
  $("save").onclick = async () => {
    $("save").disabled = true;
    $("save-state").textContent = "Guardando y publicando…";
    try {
      const r = await fetch("api/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(sessions) });
      const d = await r.json();
      $("save-state").textContent = d.ok ? d.message : "Error: " + d.error;
      if (d.ok) { refreshGroups(); renderSessions(); }
    } catch (e) {
      $("save-state").textContent = "Error: " + e.message;
    }
    $("save").disabled = false;
  };
}

async function load(id) {
  const s = sessions.find((x) => x.id === id) || sessions[0];
  if (!s || s === current) return;
  current = s;
  document.querySelectorAll(".sessions a").forEach((a) => a.classList.toggle("active", a.hash === "#" + s.id));

  $("player").hidden = false;
  $("session-title").textContent = s.title;
  $("session-date").textContent = fmtDate(s.date);
  audio.src = s.audio;

  const res = await fetch(`sessions/${s.id}.cue`, { cache: "no-cache" });
  tracks = res.ok ? parseCue(await res.text()) : [];
  renderTracks();
  update();
}

function renderTracks() {
  const total = current.duration;
  $("tracks").innerHTML = "";
  $("timeline").innerHTML = "";
  tracks.forEach((t, i) => {
    const li = document.createElement("li");
    li.innerHTML = `<span class="n">${i + 1}</span><span class="cover"></span><span class="info"><span class="title"></span><span class="artist"></span><span class="meta"></span></span><span class="t">${fmt(t.start)}</span>`;
    li.querySelector(".title").textContent = t.title;
    li.querySelector(".artist").textContent = t.performer;
    li.querySelector(".meta").textContent = [t.date, t.genre, t.label].filter(Boolean).join(" · ");
    if (t.cover) {
      const img = document.createElement("img");
      img.src = t.cover;
      img.alt = "";
      img.loading = "lazy";
      li.querySelector(".cover").append(img);
    }
    li.onclick = () => seek(t.start);
    $("tracks").append(li);

    const seg = document.createElement("span");
    seg.style.flex = String(trackEnd(i) - t.start);
    seg.title = `${t.performer} — ${t.title}`;
    seg.append(document.createElement("i"));
    $("timeline").append(seg);
  });
}

function seek(t) {
  audio.currentTime = t;
  audio.play();
}

function currentIndex() {
  let idx = -1;
  tracks.forEach((t, i) => { if (audio.currentTime >= t.start) idx = i; });
  return idx;
}

let lastIdx = null;
function update() {
  const idx = currentIndex();
  const segs = $("timeline").children;
  tracks.forEach((t, i) => {
    const len = trackEnd(i) - t.start;
    const p = Math.min(1, Math.max(0, (audio.currentTime - t.start) / len));
    segs[i].firstChild.style.width = p * 100 + "%";
  });
  if (idx === lastIdx) return;
  lastIdx = idx;
  [...$("tracks").children].forEach((li, i) => li.classList.toggle("current", i === idx));
  const t = tracks[idx];
  $("now-playing").textContent = t ? `▶ ${t.performer} — ${t.title}` : " ";
  if ("mediaSession" in navigator && t) {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: t.title,
      artist: t.performer,
      album: current.title,
      artwork: t.cover ? [{ src: new URL(t.cover, location.href).href, sizes: "300x300", type: "image/jpeg" }] : [],
    });
  }
}

$("timeline").onclick = (e) => {
  const r = e.currentTarget.getBoundingClientRect();
  seek(((e.clientX - r.left) / r.width) * (audio.duration || current.duration));
};

audio.addEventListener("timeupdate", update);
audio.addEventListener("seeked", update);
audio.addEventListener("loadedmetadata", () => { if (tracks.length) { renderTracks(); lastIdx = null; update(); } });

if ("mediaSession" in navigator) {
  navigator.mediaSession.setActionHandler("previoustrack", () => {
    const i = currentIndex();
    const t = tracks[i];
    seek(t && audio.currentTime - t.start > 3 ? t.start : tracks[Math.max(0, i - 1)]?.start ?? 0);
  });
  navigator.mediaSession.setActionHandler("nexttrack", () => {
    const t = tracks[currentIndex() + 1];
    if (t) seek(t.start);
  });
}

window.addEventListener("hashchange", () => load(location.hash.slice(1)));

fetch("sessions.json", { cache: "no-cache" })
  .then((r) => r.json())
  .then((data) => {
    sessions = data;
    if (window.MIX_EDITOR) setupEditor();
    renderSessions();
    load(location.hash.slice(1));
  });
