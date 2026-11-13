const $ = (s) => document.querySelector(s);
const state = { mode: "video", source: "all", jobs: new Map(), polling: null };
const SOURCE_NAMES = { ytmusic: "YT Music", soundcloud: "SoundCloud", youtube: "YouTube" };

const URL_RE = /https?:\/\/[^\s<>"']+/g;

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
  return many;
}

function fmtSize(bytes) {
  if (!bytes) return "";
  const mb = bytes / 1048576;
  return mb >= 1 ? `${mb.toFixed(1)} МБ` : `${Math.round(bytes / 1024)} КБ`;
}

function fmtDuration(sec) {
  if (!sec) return "";
  sec = Math.round(sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const pad = (x) => String(x).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function hostOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; }
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function parseLinks() {
  return [...new Set(($("#links").value.match(URL_RE) || []).map((u) => u.replace(/[.,;)]+$/, "")))];
}

/* ---------- tabs ---------- */
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    state.mode = tab.dataset.tab;
    const isSearch = state.mode === "search";
    $("#panel-links").hidden = isSearch;
    $("#panel-search").hidden = !isSearch;
    $("#qualityWrap").hidden = state.mode !== "video";
    $("#audioHint").hidden = state.mode !== "audio";
    if (isSearch) $("#q").focus(); else $("#links").focus();
    updateGoLabel();
  });
});

function updateGoLabel() {
  const n = parseLinks().length;
  $("#go").textContent = n > 1 ? `Скачать ${n} ${plural(n, "ссылку", "ссылки", "ссылок")}` : "Скачать";
}
$("#links").addEventListener("input", () => { $("#linksError").hidden = true; updateGoLabel(); });

/* ---------- jobs ---------- */
async function createJobs(urls, mode, quality = "best") {
  const res = await fetch("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ urls, mode, quality }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Сервер не ответил. Попробуй ещё раз.");
  [...data.jobs].reverse().forEach(addJob);
  startPolling();
  return data.jobs;
}

function addJob(job) {
  state.jobs.set(job.id, job);
  const li = document.createElement("li");
  li.id = `job-${job.id}`;
  $("#jobs").prepend(li);
  $("#jobsWrap").hidden = false;
  renderJob(job);
}

function renderJob(job) {
  const li = document.getElementById(`job-${job.id}`);
  if (!li) return;
  const kind = job.mode === "audio" ? "mp3" : "видео";
  const title = job.title || hostOf(job.url);
  let status = "", action = "", bar = "";
  if (job.status === "queued") {
    status = `<div class="small">В очереди · ${kind}</div>`;
    bar = `<div class="bar indeterminate"><i></i></div>`;
  } else if (job.status === "downloading") {
    const p = Math.round(job.progress || 0);
    status = `<div class="small">${p ? p + "%" : "Готовим"} · ${kind}</div>`;
    bar = p ? `<div class="bar"><i style="width:${p}%"></i></div>` : `<div class="bar indeterminate"><i></i></div>`;
  } else if (job.status === "done") {
    status = `<div class="small ok">Готово · ${kind} · ${fmtSize(job.size)}</div>`;
    action = `<a class="btn sm primary" href="/api/files/${job.id}" download>Сохранить</a>`;
  } else if (job.status === "error") {
    status = `<div class="small err">${esc(job.error || "Не получилось скачать")}</div>`;
    action = `<button class="btn sm ghost" data-retry="${job.id}">Ещё раз</button>`;
  }
  li.innerHTML = `
    <div class="meta">
      <div class="title" title="${esc(job.url)}">${esc(title)}</div>
      ${status}${bar}
    </div>${action}`;
  const slot = document.querySelector(`[data-slot-job="${job.id}"]`);
  if (slot) {
    if (job.status === "done") slot.innerHTML = `<a class="btn sm primary" href="/api/files/${job.id}" download>Сохранить</a>`;
    else if (job.status === "error") slot.innerHTML = `<span class="small err">Ошибка</span>`;
    else slot.innerHTML = `<span class="small">${Math.round(job.progress || 0)}%</span>`;
  }
  const doneCount = [...state.jobs.values()].filter((j) => j.status === "done").length;
  $("#saveAll").hidden = doneCount < 2;
}

$("#jobs").addEventListener("click", async (e) => {
  const id = e.target.dataset.retry;
  if (!id) return;
  const job = state.jobs.get(id);
  document.getElementById(`job-${id}`)?.remove();
  state.jobs.delete(id);
  try { await createJobs([job.url], job.mode, job.quality); } catch (err) { alert(err.message); }
});

$("#saveAll").addEventListener("click", () => {
  const done = [...state.jobs.values()].filter((j) => j.status === "done");
  done.forEach((j, i) => setTimeout(() => {
    const a = document.createElement("a");
    a.href = `/api/files/${j.id}`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }, i * 700));
});

function startPolling() {
  if (state.polling) return;
  state.polling = setInterval(async () => {
    const active = [...state.jobs.values()].filter((j) => j.status === "queued" || j.status === "downloading");
    if (!active.length) { clearInterval(state.polling); state.polling = null; return; }
    try {
      const res = await fetch(`/api/jobs?ids=${active.map((j) => j.id).join(",")}`);
      const data = await res.json();
      data.jobs.forEach((j) => { state.jobs.set(j.id, j); renderJob(j); });
    } catch { /* сеть моргнула — попробуем на следующем тике */ }
  }, 1000);
}

$("#go").addEventListener("click", async () => {
  const urls = parseLinks();
  const err = $("#linksError");
  if (!urls.length) {
    err.textContent = "Вставь хотя бы одну ссылку";
    err.hidden = false;
    $("#links").focus();
    return;
  }
  const btn = $("#go");
  btn.setAttribute("aria-busy", "true");
  try {
    await createJobs(urls, state.mode, $("#quality").value);
    $("#links").value = "";
    updateGoLabel();
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  } finally {
    btn.removeAttribute("aria-busy");
  }
});

/* ---------- song search ---------- */
$("#searchForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#q").value.trim();
  const err = $("#searchError");
  err.hidden = true;
  if (!q) {
    err.textContent = "Напиши название песни или исполнителя";
    err.hidden = false;
    return;
  }
  const btn = e.submitter || $("#searchForm button");
  btn.setAttribute("aria-busy", "true");
  $("#results").innerHTML = `<li><div class="meta"><div class="small">Ищем…</div><div class="bar indeterminate"><i></i></div></div></li>`;
  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&source=${state.source}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Поиск не удался");
    renderResults(data.results);
  } catch (e2) {
    $("#results").innerHTML = "";
    err.textContent = e2.message;
    err.hidden = false;
  } finally {
    btn.removeAttribute("aria-busy");
  }
});
$("#q").addEventListener("input", () => { $("#searchError").hidden = true; });

$("#sources").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  state.source = chip.dataset.source;
  document.querySelectorAll(".chip").forEach((c) => c.classList.toggle("active", c === chip));
  if ($("#q").value.trim()) $("#searchForm").requestSubmit();
});

function renderResults(results) {
  if (!results.length) {
    $("#results").innerHTML = `<li><div class="meta"><div class="small">Ничего не нашли. Попробуй написать по-другому.</div></div></li>`;
    return;
  }
  $("#results").innerHTML = results.map((r) => `
    <li>
      ${r.thumbnail ? `<img class="thumb" src="${esc(r.thumbnail)}" alt="" loading="lazy">` : `<div class="thumb"></div>`}
      <div class="meta">
        <div class="title">${esc(r.title)}</div>
        <div class="small"><span class="src">${SOURCE_NAMES[r.source] || ""}</span>${esc([r.uploader, r.album, fmtDuration(r.duration)].filter(Boolean).join(" · "))}</div>
      </div>
      <span class="slot"><button class="btn sm ghost" data-mp3="${esc(r.url)}">mp3</button></span>
    </li>`).join("");
}

$("#results").addEventListener("click", async (e) => {
  const url = e.target.dataset.mp3;
  if (!url) return;
  const slot = e.target.closest(".slot");
  e.target.setAttribute("aria-busy", "true");
  try {
    const [job] = await createJobs([url], "audio");
    slot.dataset.slotJob = job.id;
    renderJob(state.jobs.get(job.id));
  } catch (err) {
    e.target.removeAttribute("aria-busy");
    alert(err.message);
  }
});

/* ---------- config ---------- */
fetch("/api/config").then((r) => r.json()).then((cfg) => {
  if (cfg.bot_username) $("#tgLink").href = `https://t.me/${cfg.bot_username}`;
  else $("#tgLink").hidden = true;
}).catch(() => { $("#tgLink").hidden = true; });

updateGoLabel();
