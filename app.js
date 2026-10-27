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
    if (l.startsWith("TRACK ")) out.push((t = { title: "", performer: "", start: 0 }));
    else if (t && (m = l.match(/^(TITLE|PERFORMER) "(.*)"$/))) t[m[1].toLowerCase()] = m[2];
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

function renderSessions() {
  $("sessions").innerHTML = "";
  for (const s of sessions) {
    const li = document.createElement("li");
    li.innerHTML = `<a href="#${s.id}"><span class="s-title"></span><span class="s-meta">${fmtDate(s.date)} · ${fmt(s.duration)}</span></a>`;
    li.querySelector(".s-title").textContent = s.title;
    $("sessions").append(li);
  }
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

  const res = await fetch(`sessions/${s.id}.cue`);
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
    li.innerHTML = `<span class="n">${i + 1}</span><span><span class="title"></span><br><span class="artist"></span></span><span class="t">${fmt(t.start)}</span>`;
    li.querySelector(".title").textContent = t.title;
    li.querySelector(".artist").textContent = t.performer;
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
    navigator.mediaSession.metadata = new MediaMetadata({ title: t.title, artist: t.performer, album: current.title });
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

fetch("sessions.json")
  .then((r) => r.json())
  .then((data) => {
    sessions = data;
    renderSessions();
    load(location.hash.slice(1));
  });
