"use strict";

// ---------- auth ----------
const params = new URLSearchParams(location.search);
if (params.get("token")) {
  localStorage.setItem("ticklight-token", params.get("token"));
  params.delete("token");
  history.replaceState(null, "", location.pathname + (params.toString() ? "?" + params : ""));
}
const token = localStorage.getItem("ticklight-token") || "";
const NATIVE = window.TicklightApp || null;  // running inside the Android app
// The service worker can't read localStorage; give it the token for notification buttons
if ("caches" in window) caches.open("ticklight-auth").then((c) =>
  token ? c.put("/__token", new Response(token)) : c.delete("/__token"));

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (token) headers.Authorization = "Bearer " + token;
  if (opts.body !== undefined && typeof opts.body !== "string" && !(opts.body instanceof Blob)) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.body);
  }
  const r = await fetch(path, { ...opts, headers });
  if (r.status === 401) {
    if (token) localStorage.removeItem("ticklight-token");
    location.href = "/auth/login?next=" + encodeURIComponent(location.pathname + location.search);
    throw new Error("login required");
  }
  const data = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
  if (!r.ok) throw new Error(data?.detail || r.statusText);
  return data;
}
const post = (path, body) => api(path, { method: "POST", body: body ?? {} });

// ---------- tiny DOM helper ----------
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid);
  return el;
}
const $ = (s) => document.querySelector(s);

// ---------- state ----------
let board = null;
let version = -1;
const ENERGY = { 1: "🪫 low", 2: "🔋 medium", 3: "⚡ high" };

// ---------- clock ----------
function tickClock() {
  const now = new Date();
  $("#time").textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  $("#date").textContent = now.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
}
setInterval(tickClock, 1000);
tickClock();

// ---------- rendering ----------
function fmtDay(iso) {
  const d = new Date(iso + "T00:00");
  const today = new Date(board.today_date + "T00:00");
  const diff = Math.round((d - today) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  if (diff < 0) return "from " + d.toLocaleDateString([], { weekday: "short" });
  return d.toLocaleDateString([], { weekday: "long", day: "numeric", month: "short" });
}

function metaBits(t) {
  const bits = [];
  if (t.area) bits.push(h("span", { class: `chip area area-${t.area}` }, t.area));
  if (t.due_time) bits.push(h("span", { class: "chip" }, t.due_time));
  if (t.minutes) bits.push(h("span", { class: "chip" }, `~${t.minutes} min`));
  if (t.energy) bits.push(h("span", { class: "chip" }, ENERGY[t.energy].split(" ")[0]));
  if (t.recurrence_text) bits.push(h("span", { class: "chip" }, "🔁 " + t.recurrence_text));
  if (t.current_streak >= 2) bits.push(h("span", { class: "chip" }, "🔥 " + t.current_streak));
  if (t.subtasks.length) bits.push(h("span", { class: "chip" }, `☑ ${t.subtasks_done}/${t.subtasks.length}`));
  if (t.shared) bits.push(h("span", { class: "chip shared" }, t.owner_name ? `🏠 ${t.owner_name}'s` : "🏠 shared"));
  if (t.from_name) bits.push(h("span", { class: "chip from" }, `💌 from ${t.from_name}`));
  return bits;
}

function focusCard(t, i) {
  const step = t.next_step && h("div", { class: "next-step", title: "Tick this step", onclick: () => tickStep(t.next_step.id) },
    h("div", { class: "box" }),
    h("div", {}, h("div", { class: "label" }, "next step"), t.next_step.title));
  return h("div", { class: `card ${i === 0 ? "first" : ""} ${t.area ? "area-b-" + t.area : ""}` },
    h("div", { class: "title", onclick: () => openEdit(t.id) }, t.title),
    h("div", { class: "meta" },
      t.focus_why.map((w) => h("span", { class: "chip why" }, w)), metaBits(t)),
    step,
    h("div", { class: "actions" },
      h("button", { class: "done", onclick: () => complete(t.id) }, "✓ Done"),
      h("button", { onclick: () => openStartSession(t.id), title: "Focus session on this, with your companion" }, "▶"),
      h("button", { class: t.pinned ? "on" : "", onclick: () => togglePin(t), title: t.pinned ? "Unpick" : "Make it one of today's three" }, "📌"),
      h("button", { onclick: () => notNow(t.id), title: "Hide from Focus for 3 hours" }, "💤", h("span", { class: "lbl" }, " Not now")),
      h("button", { onclick: (e) => breakdown(t.id, e.currentTarget), title: "Split into tiny steps" }, "🪜", h("span", { class: "lbl" }, " Break it down"))));
}

function listItem(t) {
  const sub = [];
  if (t.carried_over) sub.push(fmtDay(t.due_date));
  else if (t.due_time) sub.push(t.due_time);
  if (t.recurrence_text) sub.push("🔁 " + t.recurrence_text);
  if (t.next_step) sub.push("↳ " + t.next_step.title);
  if (t.shared) sub.push(t.owner_name ? `🏠 ${t.owner_name}` : "🏠");
  if (t.from_name) sub.push(`💌 ${t.from_name}`);
  return h("li", { class: `${t.carried_over ? "carried" : ""} p${t.priority}` },
    h("button", { class: "check", title: "Done", onclick: () => complete(t.id) }),
    t.area ? h("span", { class: `dot area-${t.area}`, title: t.area }) : null,
    h("div", { class: "t", onclick: () => openEdit(t.id) },
      t.title, sub.length ? h("div", { class: "sub" }, sub.join(" · ")) : null));
}

function render() {
  const b = board;
  // focus
  const focus = $("#focus");
  focus.replaceChildren(...(b.focus.length
    ? b.focus.map(focusCard)
    : [h("div", { class: "empty" }, "Nothing needs you right now. ✨", h("br"), "Dump anything on your mind above.")]));
  // energy
  $("#energy").classList.toggle("auto", !b.energy.manual);
  for (const btn of document.querySelectorAll("#energy button"))
    btn.classList.toggle("on", +btn.dataset.e === b.energy.level);
  $("#energy-label").textContent = `· picked for ${b.energy.name} energy${b.energy.manual ? "" : " (auto)"}`;
  // lists
  $("#today").replaceChildren(...(b.today.length ? b.today.map((t) => listItem(t)) : [h("li", { class: "muted" }, "Nothing scheduled.")]));
  const up = [];
  let lastDay = null;
  for (const t of b.upcoming) {
    if (t.due_date !== lastDay) { up.push(h("li", { class: "day" }, fmtDay(t.due_date))); lastDay = t.due_date; }
    up.push(listItem(t));
  }
  if (b.later.length) up.push(h("li", { class: "day" }, `+ ${b.later.length} later`));
  $("#upcoming").replaceChildren(...(up.length ? up : [h("li", { class: "muted" }, "Clear week ahead.")]));
  $("#anytime").replaceChildren(...b.anytime.map((t) => listItem(t)));
  $("#anytime-count").textContent = b.anytime.length || "";
  $("#someday-count").textContent = b.someday_count;
  // stats
  const s = b.stats;
  $("#level-num").textContent = "Lv " + s.level;
  $("#xp-bar").style.width = Math.round((100 * s.into) / s.needed) + "%";
  $("#level").title = `${s.xp} XP total · ${s.needed - s.into} to level ${s.level + 1}`;
  $("#xp-today").textContent = s.xp_today ? `+${s.xp_today} XP today` : "";
  // done strip
  const done = b.done_today.filter((t) => !t.skipped);
  $("#done-strip").replaceChildren(...(done.length
    ? [h("span", {}, `✅ ${done.length} done today`),
       ...done.slice(0, 12).map((t) => h("span", { class: "d", title: "Undo", onclick: () => undo(t.id) }, t.title))]
    : []));
  renderBanners();
  renderPeople();
  renderGame();
  renderBuddy();
  renderSession();
  // capture status
  $("#capture-status").textContent = b.pending_captures ? "✨ Sorting your brain dump…" : "";
}

async function load() {
  try {
    board = await api("/api/board");
    version = board.version;
    render();
  } catch (e) {
    console.error(e);
  }
}

async function poll() {
  try {
    const v = await api("/api/version");
    if (v.version !== version || (board && v.today !== board.today_date)) await load();
  } catch (e) { /* offline; try again */ }
}
setInterval(poll, 3000);
setInterval(load, 60000); // focus depends on the time of day

// ---------- toasts & rewards ----------
function toast(content, { undo, bonus, ms = 5000 } = {}) {
  const el = h("div", { class: "toast" + (bonus ? " bonus" : "") }, content,
    undo ? h("button", { onclick: () => { undo(); el.remove(); } }, "Undo") : null);
  $("#toasts").append(el);
  setTimeout(() => el.remove(), ms);
}

function reward(r, title, undoFn) {
  if (!r) return toast(`✓ ${title}`, { undo: undoFn });
  toast(h("span", {}, h("span", { class: "xp" }, `+${r.xp} XP `), r.coins ? h("span", { class: "coin" }, `+${r.coins} 🪙 `) : null, title,
    r.lines.length ? h("span", { class: "muted small" }, "  " + r.lines.join(" · ")) : null),
    { undo: undoFn, bonus: r.bonus, ms: r.bonus ? 7000 : 5000 });
  $("#level-num").classList.remove("pop");
  void $("#level-num").offsetWidth;
  $("#level-num").classList.add("pop");
  if (r.level_up) {
    $("#levelup-text").textContent = `Level ${r.level}!`;
    $("#levelup").hidden = false;
    setTimeout(() => ($("#levelup").hidden = true), 2600);
  }
}
$("#levelup").addEventListener("click", () => ($("#levelup").hidden = true));

// ---------- actions ----------
async function complete(id) {
  const t = await post(`/api/tasks/${id}/complete`);
  reward(t.reward, t.title, () => undo(id));
  if (t.coach) buddySay(t.coach);
  load();
}
async function undo(id) {
  await post(`/api/tasks/${id}/undo`);
  load();
}
async function notNow(id) {
  const t = await post(`/api/tasks/${id}/not-now`, { hours: 3 });
  toast(t.suggest_breakdown ? "💤 Moved aside. It keeps getting pushed. Try 🪜 Break it down?" : "💤 Out of the way for 3 hours");
  load();
}
async function tickStep(subId) {
  const t = await api(`/api/subtasks/${subId}`, { method: "PATCH", body: { done: true } });
  toast(h("span", {}, h("span", { class: "xp" }, "+3 XP "), "step done",
    t.next_step ? h("span", { class: "muted" }, " · next: " + t.next_step.title) : null),
    { undo: async () => { await api(`/api/subtasks/${subId}`, { method: "PATCH", body: { done: false } }); load(); } });
  load();
}
async function breakdown(id, btn) {
  if (btn) btn.disabled = true;
  try {
    const t = await post(`/api/tasks/${id}/breakdown`);
    toast("🪜 First step: " + t.next_step.title);
    load();
    return t;
  } catch (e) {
    toast("Couldn't break it down: " + e.message);
    if (btn) btn.disabled = false;
  }
}

// ---------- capture ----------
const input = $("#capture-input");
function autosize() { input.style.height = "auto"; input.style.height = input.scrollHeight + "px"; }
input.addEventListener("input", autosize);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#capture").requestSubmit(); }
  if (e.key === "Escape") { input.value = ""; autosize(); input.blur(); }
});
$("#capture").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  autosize();
  $("#capture-status").textContent = "✨ Sorting your brain dump…";
  try {
    const r = await post("/api/capture?wait=1", { text });
    const n = r.tasks.length;
    const names = r.tasks.slice(0, 3).map((t) => t.title).join(", ") + (n > 3 ? "…" : "");
    toast(`➕ ${n} task${n === 1 ? "" : "s"}: ${names}`, {
      ms: 8000,
      undo: async () => { await post(`/api/captures/${r.capture_id}/undo`); load(); },
    });
  } catch (err) {
    input.value = text;
    toast("Couldn't save that: " + err.message);
  }
  load();
});

// ---------- energy ----------
document.querySelectorAll("#energy button").forEach((b) =>
  b.addEventListener("click", async () => { await post("/api/energy", { level: +b.dataset.e }); load(); }));

// ---------- task editor ----------
const edit = $("#edit");
const form = $("#edit-form");
let editing = null;

function renderSteps(t) {
  $("#edit-steps").replaceChildren(...t.subtasks.map((s) => h("li", { class: s.done ? "done" : "" },
    h("input", { type: "checkbox", checked: s.done, onchange: async (e) => {
      editing = await api(`/api/subtasks/${s.id}`, { method: "PATCH", body: { done: e.target.checked } });
      renderSteps(editing); } }),
    h("span", {}, s.title),
    h("button", { type: "button", title: "Remove", onclick: async () => {
      editing = await api(`/api/subtasks/${s.id}`, { method: "DELETE" }); renderSteps(editing); } }, "×"))));
}

async function openEdit(id) {
  editing = id ? await api(`/api/tasks/${id}`) : null;
  const t = editing || { title: "", notes: "", priority: 1, subtasks: [] };
  form.title.value = t.title;
  form.notes.value = t.notes || "";
  form.due_date.value = t.due_date || "";
  form.due_time.value = t.due_time || "";
  form.area.value = t.area || "";
  form.energy.value = t.energy || "";
  form.minutes.value = t.minutes || "";
  form.priority.value = t.priority;
  form.shared.checked = !!t.shared;
  $("#edit-repeat").textContent = t.recurrence_text ? `🔁 ${t.recurrence_text} · streak ${t.current_streak} (best ${t.best_streak})` : "";
  renderSteps(t);
  for (const id of ["#edit-delete", "#edit-someday", "#edit-breakdown", "#edit-steps", "#edit-step-input"])
    $(id).hidden = !editing;
  $("#edit-someday").textContent = editing?.status === "someday" ? "↩️ Bring back" : "📦 Someday";
  edit.showModal();
  form.title.focus();
}

function formValues() {
  return {
    title: form.title.value, notes: form.notes.value,
    due_date: form.due_date.value || null, due_time: form.due_time.value || null,
    area: form.area.value || null, energy: form.energy.value ? +form.energy.value : null,
    minutes: form.minutes.value ? +form.minutes.value : null, priority: +form.priority.value,
    shared: form.shared.checked,
  };
}

edit.addEventListener("close", async () => {
  if (edit.returnValue !== "save") return;
  try {
    if (editing) await api(`/api/tasks/${editing.id}`, { method: "PATCH", body: formValues() });
    else await post("/api/tasks", formValues());
  } catch (e) { toast("Couldn't save: " + e.message); }
  load();
});
$("#edit-delete").addEventListener("click", async () => {
  const id = editing.id;
  await api(`/api/tasks/${id}`, { method: "DELETE" });
  edit.close();
  toast("🗑 Deleted", { undo: async () => { await post(`/api/tasks/${id}/restore`); load(); } });
  load();
});
$("#edit-someday").addEventListener("click", async () => {
  const back = editing.status === "someday";
  await post(`/api/tasks/${editing.id}/${back ? "bring-back" : "someday"}`);
  edit.close();
  toast(back ? "↩️ Back on the list" : "📦 Parked in Someday");
  load();
});
$("#edit-breakdown").addEventListener("click", async (e) => {
  const t = await breakdown(editing.id, e.target);
  e.target.disabled = false;
  if (t) { editing = t; renderSteps(t); }
});
$("#edit-step-input").addEventListener("keydown", async (e) => {
  if (e.key !== "Enter") return;
  e.preventDefault();
  const v = e.target.value.trim();
  if (!v) return;
  editing = await post(`/api/tasks/${editing.id}/subtasks`, { title: v });
  e.target.value = "";
  renderSteps(editing);
});
$("#edit-step-input").addEventListener("paste", async (e) => {
  const lines = e.clipboardData.getData("text").split("\n").map((s) => s.trim()).filter(Boolean);
  if (lines.length < 2) return;
  e.preventDefault();
  editing = await post(`/api/tasks/${editing.id}/subtasks`, { titles: lines });
  renderSteps(editing);
});

// ---------- someday ----------
async function openSomeday() {
  const items = await api("/api/tasks?status=someday");
  $("#someday-list").replaceChildren(...(items.length ? items.map((t) => h("li", {},
    h("div", { class: "t", onclick: () => { $("#someday").close(); openEdit(t.id); } }, t.title,
      t.someday_reason ? h("div", { class: "sub" }, t.someday_reason) : null),
    h("button", { type: "button", onclick: async (e) => {
      await post(`/api/tasks/${t.id}/bring-back`); e.target.closest("li").remove(); load(); } }, "↩️ Bring back"))) : [h("li", { class: "muted" }, "Empty.")]));
  $("#someday").showModal();
}

// ---------- settings ----------
let me = null;
let prefs = null;
const getPath = (o, path) => path.split(".").reduce((x, k) => x?.[k], o);
function setPath(o, path, v) { const ks = path.split("."); ks.slice(0, -1).reduce((x, k) => x[k], o)[ks.at(-1)] = v; }

async function savePrefs(patch) {
  prefs = await api("/api/prefs", { method: "PATCH", body: patch });
  load();
}

function bindPrefInputs() {
  for (const el of document.querySelectorAll("[data-pref]")) {
    const v = getPath(prefs, el.dataset.pref);
    if (el.type === "checkbox") el.checked = !!v; else el.value = v ?? "";
    el.onchange = () => {
      const val = el.type === "checkbox" ? el.checked : el.type === "number" || "num" in el.dataset ? +el.value : el.value;
      const top = el.dataset.pref.split(".")[0];
      const patch = { [top]: structuredClone(prefs[top]) };
      if (el.dataset.pref.includes(".")) setPath(patch, el.dataset.pref, val); else patch[top] = val;
      savePrefs(patch);
    };
  }
}

const NUDGE_ICON = { meds: "💊", water: "💧", meal: "🍽", move: "🚶", custom: "🔔" };
function renderNudges() {
  const save = () => savePrefs({ nudges: prefs.nudges });
  $("#nudge-list").replaceChildren(...prefs.nudges.map((n, i) => {
    const interval = "every_minutes" in n;
    return h("li", {},
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: n.enabled,
        onchange: (e) => { n.enabled = e.target.checked; save(); } })),
      h("select", { onchange: (e) => { n.kind = e.target.value; save(); } },
        Object.keys(NUDGE_ICON).map((k) => h("option", { value: k, selected: k === n.kind }, `${NUDGE_ICON[k]} ${k}`))),
      h("input", { value: n.label, class: "grow", onchange: (e) => { n.label = e.target.value; save(); } }),
      interval
        ? h("span", { class: "small" }, "every ",
            h("input", { type: "number", min: 15, value: n.every_minutes, class: "num", onchange: (e) => { n.every_minutes = +e.target.value; save(); } }),
            " min ", h("input", { type: "time", value: n.from, onchange: (e) => { n.from = e.target.value; save(); } }),
            "–", h("input", { type: "time", value: n.to, onchange: (e) => { n.to = e.target.value; save(); } }))
        : h("input", { value: n.times.join(", "), placeholder: "08:00, 20:00", class: "times",
            onchange: (e) => { n.times = e.target.value.split(/[ ,]+/).filter(Boolean); save(); } }),
      h("button", { type: "button", title: "Remove", onclick: () => { prefs.nudges.splice(i, 1); save().then(renderNudges); } }, "×"));
  }));
}
$("#nudge-add").addEventListener("click", async () => {
  const label = prompt("What should I nudge you about? (e.g. Stretch)");
  if (!label) return;
  const every = confirm("Repeat every couple of hours (OK), or at set times (Cancel)?");
  const n = { id: label.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 16) + Date.now() % 1000, kind: "custom",
              label, enabled: true };
  if (every) Object.assign(n, { every_minutes: 120, from: "09:00", to: "21:00" });
  else Object.assign(n, { times: ["12:00"], follow_up_minutes: 15, max_follow_ups: 1 });
  prefs.nudges.push(n);
  await savePrefs({ nudges: prefs.nudges });
  renderNudges();
});

function showTab(name) {
  for (const b of document.querySelectorAll("#settings .tabs button")) b.classList.toggle("on", b.dataset.tab === name);
  for (const p of document.querySelectorAll("#settings [data-pane]")) p.hidden = p.dataset.pane !== name;
}
document.querySelectorAll("#settings .tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

async function renderTokens() {
  const tokens = await api("/api/tokens");
  $("#token-list").replaceChildren(...tokens.map((t) => h("li", {},
    h("div", { class: "t" }, t.name, h("div", { class: "sub" }, "last used " + (t.last_used_at || "never"))),
    h("button", { type: "button", class: "danger", onclick: async (e) => {
      await api(`/api/tokens/${t.id}`, { method: "DELETE" }); e.target.closest("li").remove(); } }, "Revoke"))));
}

async function openSettings(tab = "routines") {
  [me, prefs] = await Promise.all([api("/api/me"), api("/api/prefs")]);
  $("#who").textContent = me.auth_mode === "oidc" ? `Logged in as ${me.user.name}` : "No login required (AUTH_MODE=none)";
  $("#logout").hidden = me.auth_mode !== "oidc";
  bindPrefInputs();
  renderNudges();
  renderPersonas();
  renderPeopleSettings();
  renderTokens();
  updatePushStatus();
  $("#token-new").hidden = true;
  showTab(tab);
  $("#settings").showModal();
}
$("#token-create").addEventListener("click", async () => {
  const t = await post("/api/tokens", { name: $("#token-name").value });
  $("#token-value").textContent = t.token;
  $("#token-kiosk").textContent = `${location.origin}/?display&token=${t.token}`;
  $("#token-curl").textContent = `curl -X POST '${location.origin}/api/capture?source=script' -H 'Authorization: Bearer ${t.token}' -H 'Content-Type: text/plain' -d 'call mum tomorrow'`;
  $("#token-new").hidden = false;
  $("#token-name").value = "";
  renderTokens();
});
$("#pair-go").addEventListener("click", async () => {
  try {
    await post("/api/pair/confirm", { code: $("#pair-code").value, name: "Watch" });
    $("#pair-code").value = "";
    toast("⌚ Watch paired");
    renderTokens();
  } catch (e) { toast(e.message); }
});

// ---------- push notifications ----------
function b64ToBytes(s) {
  const raw = atob((s + "=".repeat((4 - (s.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}
const PUSH_TEXT = {
  on: "On for this phone", off: "Off for this phone", pending: "Connecting…",
  "no-distributor": "Needs a push app. Install ntfy (Play Store), open it once, then tap Enable.",
  failed: "Didn't work",
};
async function updatePushStatus() {
  const st = $("#push-status");
  if (NATIVE) {
    const i = JSON.parse(NATIVE.info());
    st.textContent = (PUSH_TEXT[i.push] || i.push) + (i.push === "failed" && i.pushError ? `: ${i.pushError}` : "")
      + (i.push === "on" && i.distributor ? ` (via ${i.distributor.split(".").pop()})` : "");
    $("#push-enable").hidden = false;
    $("#push-enable").textContent = i.push === "on" ? "Turn off" : "Enable notifications";
    $("#push-test").hidden = i.push !== "on";
    return;
  }
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    st.textContent = location.protocol === "https:" ? "This browser doesn't support push." : "Push needs HTTPS.";
    $("#push-enable").hidden = true;
    return;
  }
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  st.textContent = sub ? "On for this device" : Notification.permission === "denied" ? "Blocked in browser settings" : "Off for this device";
  $("#push-enable").textContent = sub ? "Turn off" : "Enable notifications";
  $("#push-test").hidden = !sub;
}
$("#push-enable").addEventListener("click", async () => {
  if (NATIVE) {
    me = me || await api("/api/me");
    JSON.parse(NATIVE.info()).push === "on" ? NATIVE.disablePush() : NATIVE.enablePush(me.push_key);
    return updatePushStatus();
  }
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  if (sub) {
    await post("/api/push/unsubscribe", { endpoint: sub.endpoint });
    await sub.unsubscribe();
  } else {
    if ((await Notification.requestPermission()) !== "granted") return updatePushStatus();
    me = me || await api("/api/me");
    const s = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(me.push_key) });
    await post("/api/push/subscribe", { subscription: s.toJSON(), label: navigator.userAgent.match(/\(([^)]+)\)/)?.[1] || "" });
  }
  updatePushStatus();
});
$("#push-test").addEventListener("click", async () => {
  const r = await post("/api/push/test");
  toast(r.sent ? "Sent. Check your notifications." : "Nothing sent; try turning notifications off and on.");
});

// ---------- banners: routines, nudges, hyperfocus ----------
const ROUTINE_LABEL = { morning: "☀️ Plan my day", evening: "🌙 Wind down", weekly: "🧹 Weekly reset" };
function renderBanners() {
  const b = board;
  const items = [];
  if (b.hyperfocus_until) {
    const mins = Math.max(0, Math.round((new Date(b.hyperfocus_until) - new Date()) / 60000));
    items.push(h("div", { class: "banner hf" }, `🎧 Hyperfocus · ${mins} min left · only meetings, meds, meals and deadlines get through`,
      h("button", { onclick: () => setHyperfocus(0) }, "End")));
  }
  for (const r of b.routines || [])
    items.push(h("button", { class: "banner routine", onclick: () => openFlow(r) }, ROUTINE_LABEL[r]));
  for (const n of b.nudges || [])
    items.push(h("button", { class: "banner nudge", title: "Tap when done",
      onclick: async () => { const r = await post("/api/action", { action: `took:${n.id}` }); toast(r.message); load(); } },
      `${NUDGE_ICON[n.kind] || "🔔"} ${n.label}?`));
  $("#banners").replaceChildren(...items);
  $("#btn-hf").classList.toggle("on", !!b.hyperfocus_until);
  $("#hf-left").textContent = b.hyperfocus_until
    ? " " + Math.max(0, Math.round((new Date(b.hyperfocus_until) - new Date()) / 60000)) + "m" : "";
}

async function setHyperfocus(minutes) {
  const r = await post("/api/hyperfocus", minutes === undefined ? {} : { minutes });
  toast(r.until ? "🎧 Hyperfocus on. I'll only interrupt for meetings, meds, meals and deadlines." : "🎧 Hyperfocus off");
  load();
}
$("#btn-hf").addEventListener("click", () => setHyperfocus(board?.hyperfocus_until ? 0 : undefined));

async function togglePin(t) {
  await post(`/api/tasks/${t.id}/${t.pinned ? "unpin" : "pin"}`);
  load();
}

// ---------- voice ----------
let recorder = null;
async function toggleVoice() {
  const btn = $("#btn-mic");
  if (recorder) { recorder.stop(); return; }
  me = me || await api("/api/me");
  if (!me.voice) {
    // No speech server: use the browser's own recognition if it has one
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return toast("Voice needs WHISPER_URL on the server (or a browser with speech recognition).");
    const rec = new SR();
    rec.interimResults = false;
    btn.classList.add("rec");
    rec.onresult = (e) => { input.value = (input.value + " " + e.results[0][0].transcript).trim(); autosize(); input.focus(); };
    rec.onend = () => btn.classList.remove("rec");
    rec.start();
    return;
  }
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch { return toast("Microphone blocked. Allow it in the browser settings."); }
  const chunks = [];
  recorder = new MediaRecorder(stream);
  recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  recorder.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    btn.classList.remove("rec");
    const type = recorder.mimeType || "audio/webm";
    recorder = null;
    $("#capture-status").textContent = "🎙 Listening back…";
    try {
      const r = await api("/api/capture/voice", { method: "POST", body: new Blob(chunks, { type }), headers: { "Content-Type": type } });
      toast(h("span", {}, h("i", {}, `“${r.heard}”`), ` → ${r.tasks.length} task${r.tasks.length === 1 ? "" : "s"}`), {
        ms: 8000, undo: async () => { await post(`/api/captures/${r.capture_id}/undo`); load(); } });
    } catch (e) { toast("Voice failed: " + e.message); }
    load();
  };
  recorder.start();
  btn.classList.add("rec");
  toast("🎙 Recording… tap the mic again to stop", { ms: 3000 });
  setTimeout(() => recorder?.state === "recording" && recorder.stop(), 3 * 60 * 1000);
}
$("#btn-mic").addEventListener("click", toggleVoice);

// ---------- guided routines ----------
const flow = $("#flow");
let flowSteps = [];
let flowIndex = 0;
let flowName = "";

function runStep() {
  const step = flowSteps[flowIndex];
  $("#flow-body").replaceChildren(...step.render().filter(Boolean));
  $("#flow-dots").textContent = `${flowIndex + 1} / ${flowSteps.length}`;
  $("#flow-next").textContent = flowIndex === flowSteps.length - 1 ? step.finish || "Done" : step.next || "Next";
}
$("#flow-next").addEventListener("click", async () => {
  const step = flowSteps[flowIndex];
  if (step.leave) await step.leave();
  if (flowIndex < flowSteps.length - 1) { flowIndex++; runStep(); return; }
  const r = await post(`/api/routine/${flowName}/done`);
  flow.close();
  if (r.coach) buddySay(r.coach);
  toast(h("span", {}, h("span", { class: "xp" }, `+${r.xp} XP `), `${ROUTINE_LABEL[flowName].slice(2)} done`));
  load();
});
$("#flow-close").addEventListener("click", () => { flow.close(); load(); });

function pickList(tasks, { max = 3, day = null, pickedIds = [] } = {}) {
  const picked = new Set(pickedIds);
  const ul = h("ul", { class: "list pick" });
  const draw = () => ul.replaceChildren(...tasks.map((t) => h("li", { class: picked.has(t.id) ? "picked" : "",
    onclick: async () => {
      if (picked.has(t.id)) { picked.delete(t.id); await post(`/api/tasks/${t.id}/unpin`); }
      else {
        if (picked.size >= max) return toast(max === 1 ? "Just one. Untick the other first." : `${max} is plenty. Untick one first.`);
        picked.add(t.id); await post(`/api/tasks/${t.id}/pin`, day ? { when: day } : {});
      }
      draw();
    } },
    h("span", { class: "pin" }, picked.has(t.id) ? "📌" : "○"),
    h("div", { class: "t" }, t.title, h("div", { class: "sub" }, [t.area, t.minutes && `~${t.minutes} min`, ...(t.focus_why || [])].filter(Boolean).join(" · "))))));
  draw();
  return ul;
}

function dumpBox(placeholder) {
  const ta = h("textarea", { rows: 4, placeholder, class: "dump" });
  return { el: ta, async save() {
    if (!ta.value.trim()) return;
    const r = await post("/api/capture?wait=1", { text: ta.value });
    toast(`➕ ${r.tasks.length} task${r.tasks.length === 1 ? "" : "s"} captured`);
  } };
}

async function openFlow(name) {
  flowName = name;
  flowIndex = 0;
  const addDays = (isoDay, n) => {  // calendar maths on the server's "today", not UTC
    const [y, m, d] = isoDay.split("-").map(Number);
    const dt = new Date(Date.UTC(y, m - 1, d + n));
    return dt.toISOString().slice(0, 10);
  };
  if (name === "morning") {
    const d = await api("/api/routine/morning");
    const meds = d.prefs.nudges.filter((n) => n.enabled && n.kind === "meds" && !d.checkins.some((c) => c.kind === n.id));
    const dump = dumpBox("Anything rattling around? Dump it here and get it out of your head.");
    flowSteps = [
      { render: () => [h("h2", {}, "☀️ Good morning"),
          ...meds.map((n) => h("button", { class: "big-choice", onclick: async (e) => {
            await post("/api/action", { action: `took:${n.id}` }); e.target.textContent = `✅ ${n.label} taken`; e.target.disabled = true; } }, `💊 I took my ${n.label.toLowerCase()}`)),
          h("p", {}, "How's your energy right now?"),
          h("div", { class: "choices" }, [1, 2, 3].map((lv) => h("button", { class: "big-choice" + (d.energy.level === lv && d.energy.manual ? " on" : ""),
            onclick: async (e) => { await post("/api/energy", { level: lv }); e.target.parentNode.querySelectorAll("button").forEach((b) => b.classList.remove("on")); e.target.classList.add("on"); } },
            ENERGY[lv])))] },
      { render: () => [h("h2", {}, "Pick today's three"), h("p", { class: "muted" }, "Only three. Everything else can wait. Tap to pick."),
          pickList([...d.pinned, ...d.candidates.filter((c) => !d.pinned.some((p) => p.id === c.id))], { pickedIds: d.pinned.map((t) => t.id) })] },
      { render: () => [h("h2", {}, "Brain dump"), dump.el], leave: () => dump.save(), finish: "Let's go" },
    ];
  } else if (name === "evening") {
    const d = await api("/api/routine/evening");
    const dump = dumpBox("What's still in your head? Get it out so you can switch off.");
    flowSteps = [
      { render: () => [h("h2", {}, d.done.length ? `🌙 You did ${d.done.length} thing${d.done.length === 1 ? "" : "s"} today` : "🌙 Closing the day"),
          d.done.length ? h("ul", { class: "list" }, d.done.map((t) => h("li", {}, "✅ ", t.title))) : h("p", { class: "muted" }, "Some days are like that. Rest counts too."),
          h("p", { class: "muted" }, `+${d.stats.xp_today} XP today · level ${d.stats.level}`)] },
      ...(d.leftovers.length ? [{ render: () => [h("h2", {}, "Park what's left"), h("p", { class: "muted" }, "Nothing here is late. Just decide where it goes."),
          h("ul", { class: "list triage" }, d.leftovers.map((t) => h("li", {}, h("div", { class: "t" }, t.title),
            h("button", { onclick: async (e) => { await post(`/api/tasks/${t.id}/move`, { when: "tomorrow" }); e.target.closest("li").replaceChildren(h("span", { class: "muted" }, `➡️ ${t.title}: tomorrow`)); } }, "Tomorrow"),
            h("button", { onclick: async (e) => { await post(`/api/tasks/${t.id}/someday`); e.target.closest("li").replaceChildren(h("span", { class: "muted" }, `📦 ${t.title}: someday`)); } }, "Someday"),
            h("button", { onclick: (e) => e.target.closest("li").replaceChildren(h("span", { class: "muted" }, `👍 ${t.title}: stays`)) }, "Keep")))) ] }] : []),
      { render: () => [h("h2", {}, "Brain dump"), dump.el], leave: () => dump.save() },
      { render: () => [h("h2", {}, "Tomorrow's first thing"), h("p", { class: "muted" }, "Pick one. Future you will know exactly where to start."),
          pickList(d.candidates, { max: 1, day: addDays(board.today_date, 1) })], finish: "Good night" },
    ];
  } else if (name === "weekly") {
    const d = await api("/api/routine/weekly");
    let i = 0;
    const card = h("div", { class: "triage-card" });
    const drawCard = () => {
      if (i >= d.items.length) return card.replaceChildren(h("p", {}, "✨ All decided. Clean slate."));
      const t = d.items[i];
      const next = (msg) => { toast(msg, { ms: 2000 }); i++; drawCard(); };
      const dateIn = h("input", { type: "date", onchange: async (e) => { await post(`/api/tasks/${t.id}/move`, { when: e.target.value }); next("📅 " + e.target.value); } });
      card.replaceChildren(...[
        h("div", { class: "muted small" }, `${i + 1} of ${d.items.length} · ${t.review_why}`),
        h("div", { class: "title" }, t.title),
        t.someday_reason ? h("div", { class: "muted small" }, t.someday_reason) : null,
        h("div", { class: "choices" },
          h("button", { onclick: async () => { await post(`/api/tasks/${t.id}/move`, { when: "today" }); next("Today"); } }, "Today"),
          h("button", { onclick: async () => { await post(`/api/tasks/${t.id}/move`, { when: "tomorrow" }); next("Tomorrow"); } }, "Tomorrow"),
          h("label", { class: "date-btn" }, "📅 Pick day", dateIn),
          t.status === "someday" ? null : h("button", { onclick: async () => { await post(`/api/tasks/${t.id}/someday`); next("📦 Someday"); } }, "Someday"),
          h("button", { onclick: async () => { await post(`/api/tasks/${t.id}/breakdown`).catch((e) => toast(e.message)); next("🪜 Broken down"); } }, "🪜 Break down"),
          h("button", { class: "danger", onclick: async () => { await api(`/api/tasks/${t.id}`, { method: "DELETE" }); next("🗑 Dropped"); } }, "Drop"),
          h("button", { class: "link", onclick: () => next("Skipped") }, "skip"))].filter(Boolean));
    };
    drawCard();
    flowSteps = [
      { render: () => [h("h2", {}, "🧹 Weekly reset"), h("p", {}, `This week: ✅ ${d.done_this_week} done · +${d.xp_this_week} XP`),
          h("p", { class: "muted" }, d.items.length ? `${d.items.length} thing${d.items.length === 1 ? "" : "s"} need a quick decision. One at a time.` : "Nothing needs deciding. Nice.")] },
      ...(d.items.length ? [{ render: () => [h("h2", {}, "Decide"), card] }] : []),
      { render: () => [h("h2", {}, "Coming up this week"),
          d.upcoming.length ? h("ul", { class: "list" }, d.upcoming.map((t) => h("li", {}, h("div", { class: "t" }, t.title, h("div", { class: "sub" }, fmtDay(t.due_date) + (t.due_time ? " · " + t.due_time : "")))))) : h("p", { class: "muted" }, "A quiet week.")],
        finish: "Done" },
    ];
  }
  runStep();
  flow.showModal();
}

// ---------- companion ----------
let serverOffset = 0;  // server clock minus browser clock, ms
// Accessories are drawn over the blob (viewBox 0 0 100 100; eyes at y=46)
const ACCESSORY_SVG = {
  party_hat: `<path d="M36 16 50 -16 64 16Z" fill="#5b9dff"/><path d="M41 6 59 6M45 -4 55 -4" stroke="#fff" stroke-width="3"/><circle cx="50" cy="-17" r="5" fill="#f0b429"/>`,
  beanie: `<path d="M16 30Q50 -14 84 30Z" fill="#e0565b"/><rect x="14" y="24" width="72" height="10" rx="5" fill="#b8434a"/><circle cx="50" cy="-1" r="7" fill="#fff"/>`,
  headphones: `<path d="M16 46Q16 2 50 2Q84 2 84 46" fill="none" stroke="#2a2f3a" stroke-width="6"/><rect x="6" y="36" width="14" height="24" rx="6" fill="#2a2f3a"/><rect x="80" y="36" width="14" height="24" rx="6" fill="#2a2f3a"/>`,
  wizard_hat: `<ellipse cx="50" cy="18" rx="36" ry="7" fill="#4b2a8a"/><path d="M30 18 54 -30 70 18Z" fill="#5f37b0"/><text x="46" y="8" font-size="12" fill="#f0b429">★</text>`,
  crown: `<path d="M27 18 31 -6 41 8 50 -10 59 8 69 -6 73 18Z" fill="#f0c43a" stroke="#c99a12" stroke-width="2"/><circle cx="50" cy="8" r="3" fill="#e0565b"/>`,
  halo: `<ellipse cx="50" cy="-4" rx="24" ry="6" fill="none" stroke="#f7d774" stroke-width="5"/>`,
  witch_hat: `<ellipse cx="50" cy="18" rx="40" ry="7" fill="#1b1424"/><path d="M30 18 46 -20 76 -28 58 -12 68 18Z" fill="#2a1d38"/><rect x="31" y="11" width="38" height="6" fill="#ff8c1a"/>`,
  santa_hat: `<path d="M20 22Q44 -16 80 14L88 30" fill="#d93636"/><rect x="16" y="16" width="68" height="11" rx="5" fill="#fff"/><circle cx="88" cy="32" r="7" fill="#fff"/>`,
  flower_crown: `<g>${[18, 30, 42, 58, 70, 82].map((x, i) => `<circle cx="${x}" cy="${20 - Math.sin((x - 10) / 80 * Math.PI) * 12}" r="7" fill="${["#ff8fb8", "#ffd166", "#b48cff", "#7ee0a1", "#ff8fb8", "#ffd166"][i]}"/>`).join("")}</g>`,
  sun_hat: `<ellipse cx="50" cy="18" rx="46" ry="9" fill="#e9c98c"/><path d="M26 18Q50 -18 74 18Z" fill="#f2d9a6"/><rect x="27" y="10" width="46" height="5" fill="#19a7b8"/>`,
  blush: `<ellipse cx="28" cy="60" rx="7" ry="4" fill="#ff7a9a" opacity=".6"/><ellipse cx="72" cy="60" rx="7" ry="4" fill="#ff7a9a" opacity=".6"/>`,
  glasses: `<g fill="none" stroke="#1b1f27" stroke-width="3"><circle cx="37" cy="46" r="11"/><circle cx="63" cy="46" r="11"/><path d="M48 46h4"/></g>`,
  shades: `<g fill="#111"><rect x="24" y="38" width="24" height="15" rx="6"/><rect x="52" y="38" width="24" height="15" rx="6"/><rect x="46" y="42" width="8" height="3"/></g>`,
  monocle: `<circle cx="63" cy="46" r="12" fill="none" stroke="#c99a12" stroke-width="3"/><path d="M73 54Q80 72 70 84" fill="none" stroke="#c99a12" stroke-width="2"/>`,
  bowtie: `<path d="M50 84 36 76 36 92Z M50 84 64 76 64 92Z" fill="#e0565b"/><circle cx="50" cy="84" r="4" fill="#b8434a"/>`,
  scarf: `<path d="M18 74Q50 90 82 74L84 84Q50 100 16 84Z" fill="#3fbf7f"/><rect x="62" y="82" width="11" height="20" rx="3" fill="#2f9d68"/>`,
  medal: `<path d="M42 70 50 84 58 70" fill="none" stroke="#5b9dff" stroke-width="5"/><circle cx="50" cy="88" r="8" fill="#f0c43a" stroke="#c99a12" stroke-width="2"/>`,
  sparkles: `<g fill="#f7d774">${[[8, 20], [90, 30], [4, 70], [94, 74], [50, -6]].map(([x, y]) => `<path d="M${x} ${y - 6}L${x + 2} ${y - 2}L${x + 6} ${y}L${x + 2} ${y + 2}L${x} ${y + 6}L${x - 2} ${y + 2}L${x - 6} ${y}L${x - 2} ${y - 2}Z"/>`).join("")}</g>`,
  rainbow: `<g fill="none" stroke-width="5" opacity=".75"><path d="M0 70A50 56 0 0 1 100 70" stroke="#ff6b6b"/><path d="M6 72A44 50 0 0 1 94 72" stroke="#ffd166"/><path d="M12 74A38 44 0 0 1 88 74" stroke="#7ee0a1"/><path d="M18 76A32 38 0 0 1 82 76" stroke="#5b9dff"/></g>`,
  fire: `<path d="M8 90Q2 56 22 40Q20 60 34 58Q26 30 50 6Q48 34 66 36Q62 18 78 32Q98 54 92 90Z" fill="#ff7a2f" opacity=".8"/>`,
};
const BEHIND = new Set(["rainbow", "fire"]);

function avatar(p, size = 64, equipped = board?.game?.equipped || {}) {
  // A little blob that breathes and blinks, tinted with the persona's colour
  const wrap = h("div", { class: "avatar", style: `--c:${p.color};width:${size}px;height:${size}px`, title: p.name });
  const items = Object.values(equipped || {});
  const behind = items.filter((i) => BEHIND.has(i)).map((i) => ACCESSORY_SVG[i] || "").join("");
  const front = items.filter((i) => !BEHIND.has(i)).map((i) => ACCESSORY_SVG[i] || "").join("");
  wrap.innerHTML = `<svg viewBox="0 0 100 100">${behind}<g class="bodywrap"><path class="body" d="M50 8c22 0 40 16 40 40 0 26-17 44-40 44S10 74 10 48C10 24 28 8 50 8z"/>
    <g class="eyes"><ellipse cx="37" cy="46" rx="6" ry="8"/><ellipse cx="63" cy="46" rx="6" ry="8"/></g>
    <path class="mouth" d="M40 64q10 8 20 0" fill="none" stroke-width="4" stroke-linecap="round"/>${front}</g></svg>
    <span class="badge">${p.emoji}</span>`;
  return wrap;
}

let buddyTimer = null;
let lastBuddyText = "";
function buddySay(line, ms = 12000) {
  if (!line) return;
  lastBuddyText = line.text;
  const b = $("#buddy-bubble");
  b.textContent = line.text;
  b.hidden = false;
  b.classList.remove("pop"); void b.offsetWidth; b.classList.add("pop");
  $("#buddy-avatar").querySelector(".avatar")?.classList.add("talk");
  clearTimeout(buddyTimer);
  buddyTimer = setTimeout(() => { b.hidden = true; $("#buddy-avatar").querySelector(".avatar")?.classList.remove("talk"); }, ms);
  if (board?.session) $("#s-bubble").textContent = line.text;
}

function renderBuddy() {
  const c = board.coach;
  if (!c) return;
  const holder = $("#buddy-avatar");
  const look = c.persona.id + JSON.stringify(board.game?.equipped || {});
  if (holder.dataset.look !== look) {
    holder.replaceChildren(avatar(c.persona, 64));
    holder.dataset.look = look;
  }
  const latest = c.latest[0];
  if (latest && latest.text !== lastBuddyText && Date.now() - new Date(latest.at) < 5 * 60000) buddySay(latest);
  else if (!lastBuddyText && latest) lastBuddyText = latest.text;
}
$("#buddy-avatar").addEventListener("click", () => {
  if ($("#buddy-bubble").hidden && lastBuddyText) buddySay({ text: lastBuddyText }, 6000);
  else openSettings("coach");
});

async function renderPersonas() {
  const info = await api("/api/coach");
  $("#persona-list").replaceChildren(...info.personas.map((p) => h("button", {
    type: "button", class: "persona" + (prefs.coach.persona === p.id ? " on" : ""),
    onclick: async () => { await savePrefs({ coach: { ...prefs.coach, persona: p.id } }); renderPersonas(); } },
    avatar(p, 44), h("div", {}, h("b", {}, p.name), h("div", { class: "small muted" }, p.style.split(";")[0]),
      h("div", { class: "small" }, `“${p.sample}”`)))));
}

// ---------- ambient sound (generated, no files) ----------
const Sound = {
  ctx: null, out: null, nodes: [], kind: "off", timers: [],
  init() {
    if (this.ctx) return;
    this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    this.out = this.ctx.createGain();
    this.out.gain.value = +$("#s-volume").value;
    this.out.connect(this.ctx.destination);
  },
  noise(type) {
    const len = this.ctx.sampleRate * 4, buf = this.ctx.createBuffer(1, len, this.ctx.sampleRate), d = buf.getChannelData(0);
    let last = 0, b0 = 0, b1 = 0, b2 = 0;
    for (let i = 0; i < len; i++) {
      const w = Math.random() * 2 - 1;
      if (type === "brown") { last = (last + 0.02 * w) / 1.02; d[i] = last * 3.5; }
      else { b0 = 0.99765 * b0 + w * 0.099; b1 = 0.963 * b1 + w * 0.2965; b2 = 0.57 * b2 + w * 1.0527; d[i] = (b0 + b1 + b2 + w * 0.1848) * 0.11; }
    }
    const src = this.ctx.createBufferSource();
    src.buffer = buf; src.loop = true; src.start();
    return src;
  },
  chain(...nodes) { for (let i = 0; i < nodes.length - 1; i++) nodes[i].connect(nodes[i + 1]); nodes.at(-1).connect(this.out); this.nodes.push(...nodes); },
  filter(type, freq, q = 0.7) { const f = this.ctx.createBiquadFilter(); f.type = type; f.frequency.value = freq; f.Q.value = q; return f; },
  gain(v) { const g = this.ctx.createGain(); g.gain.value = v; return g; },
  blip(freq, dur, vol) {
    const o = this.ctx.createOscillator(), g = this.ctx.createGain(), t = this.ctx.currentTime;
    o.frequency.value = freq; o.type = "sine";
    g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(vol, t + 0.005); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g); g.connect(this.out); o.start(t); o.stop(t + dur + 0.05);
  },
  play(kind) {
    this.stop();
    this.kind = kind;
    if (kind === "off") return;
    this.init();
    this.ctx.resume();
    if (kind === "brown") this.chain(this.noise("brown"), this.filter("lowpass", 900), this.gain(0.9));
    if (kind === "rain") {
      this.chain(this.noise("pink"), this.filter("highpass", 400), this.filter("lowpass", 6000), this.gain(0.5));
      const drip = () => { this.blip(1800 + Math.random() * 2500, 0.03, 0.03 + Math.random() * 0.04); this.timers.push(setTimeout(drip, 40 + Math.random() * 260)); };
      drip();
    }
    if (kind === "cafe") {
      const murmur = this.gain(0.6);
      this.chain(this.noise("brown"), this.filter("bandpass", 450, 0.6), murmur);
      const lfo = this.ctx.createOscillator(), depth = this.gain(0.25);
      lfo.frequency.value = 0.3; lfo.connect(depth); depth.connect(murmur.gain); lfo.start(); this.nodes.push(lfo, depth);
      const clink = () => { if (Math.random() < 0.5) this.blip(2600 + Math.random() * 1500, 0.25, 0.02); this.timers.push(setTimeout(clink, 3000 + Math.random() * 9000)); };
      clink();
    }
    if (kind === "hum") {
      for (const [f, v] of [[55, 0.25], [110, 0.12], [165, 0.05]]) {
        const o = this.ctx.createOscillator(); o.frequency.value = f; o.start(); this.chain(o, this.gain(v));
      }
      this.chain(this.noise("brown"), this.filter("lowpass", 300), this.gain(0.5));
    }
  },
  stop() {
    this.timers.forEach(clearTimeout); this.timers = [];
    for (const n of this.nodes) { try { n.stop?.(); } catch {} try { n.disconnect(); } catch {} }
    this.nodes = [];
    this.kind = "off";
  },
  chime() {
    this.init(); this.ctx.resume();
    [660, 880, 1320].forEach((f, i) => setTimeout(() => this.blip(f, 0.9, 0.12), i * 140));
  },
};
$("#s-sound").addEventListener("change", (e) => { localStorage.setItem("ticklight-sound", e.target.value); Sound.play(e.target.value); });
$("#s-volume").addEventListener("input", (e) => { localStorage.setItem("ticklight-volume", e.target.value); if (Sound.out) Sound.out.gain.value = +e.target.value; });
$("#s-volume").value = localStorage.getItem("ticklight-volume") || "0.4";

// ---------- focus sessions ----------
let sessionHidden = false;
let askedCheckin = null;
let sessionTick = null;
let lastSessionState = null;

async function openStartSession(taskId) {
  const [p, list] = await Promise.all([prefs ? prefs : api("/api/prefs"), api("/api/tasks")]);
  prefs = p;
  const sel = $("#ss-task");
  const top = board?.focus || [];
  const ordered = [...top, ...list.filter((t) => !top.some((f) => f.id === t.id))];
  sel.replaceChildren(h("option", { value: "" }, "— nothing specific —"),
    ...ordered.map((t) => h("option", { value: t.id, selected: t.id === (taskId ?? top[0]?.id) }, t.title)));
  $("#ss-intention").value = "";
  const mins = [10, 15, 25, 45, 60];
  if (!mins.includes(p.sessions.minutes)) mins.push(p.sessions.minutes);
  let chosen = p.sessions.minutes;
  const draw = () => $("#ss-minutes").replaceChildren(...mins.sort((a, b) => a - b).map((m) =>
    h("button", { type: "button", class: m === chosen ? "on" : "", onclick: () => { chosen = m; draw(); } }, `${m} min`)));
  draw();
  const dlg = $("#start-session");
  dlg.onclose = async () => {
    if (dlg.returnValue !== "go") return;
    const sound = localStorage.getItem("ticklight-sound") || p.sessions.sound;
    $("#s-sound").value = sound;
    Sound.play(sound);  // started from a click, so the browser allows audio
    const s = await post("/api/sessions", { minutes: chosen, task_id: sel.value ? +sel.value : null, intention: $("#ss-intention").value });
    sessionHidden = false;
    buddySay(s.coach);
    await load();
  };
  dlg.showModal();
}
$("#btn-session").addEventListener("click", () => (board?.session ? showSession(true) : openStartSession()));

function showSession(on) {
  sessionHidden = !on;
  renderSession();
}
$("#s-min").addEventListener("click", () => showSession(false));
$("#session-pill").addEventListener("click", () => showSession(true));

function fmtClock(sec) {
  const m = Math.floor(sec / 60), s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function sessionControls(s) {
  const act = (path, body) => async () => {
    const r = await post(`/api/sessions/${s.id}/${path}`, body);
    if (r.coach) buddySay(r.coach);
    if (path === "end") {
      if (r.xp) toast(h("span", {}, h("span", { class: "xp" }, `+${r.xp} XP `), `${r.actual_minutes} focused minute${r.actual_minutes === 1 ? "" : "s"}`));
      if (r.reward) reward(r.reward, s.task?.title || "Task");
      Sound.stop();
    }
    if (path === "break") Sound.stop();
    load();
  };
  if (s.state === "break") return [
    h("button", { class: "primary big", onclick: async () => {
      Sound.play($("#s-sound").value);
      const n = await post("/api/sessions", { minutes: s.minutes, task_id: s.task_id, intention: s.intention });
      buddySay(n.coach); load(); } }, "▶ Another round"),
    h("button", { class: "big", onclick: act("end-break") }, "✋ Done for now")];
  const out = [];
  if (s.state === "overtime") {
    out.push(h("button", { class: "primary big", onclick: act("break", {}) }, "☕ Take a break"));
    out.push(h("button", { class: "big", onclick: act("extend", { minutes: 5 }) }, "➕ 5 min"));
  } else {
    out.push(h("button", { onclick: act("extend", { minutes: 5 }) }, "➕ 5 min"));
  }
  if (s.task_id) out.push(h("button", { class: "done big", onclick: act("end", { complete_task: true }) }, "✅ Task done"));
  out.push(h("button", { onclick: act("end", {}) }, "⏹ End session"));
  return out;
}

function renderSession() {
  const s = board?.session;
  const overlay = $("#session");
  const display = document.body.classList.contains("display");
  serverOffset = new Date(board.now) - Date.now();
  if (!s) {
    overlay.hidden = true;
    $("#session-pill").hidden = true;
    clearInterval(sessionTick); sessionTick = null;
    if (lastSessionState && Sound.kind !== "off") Sound.stop();
    lastSessionState = null;
    return;
  }
  const visible = display || !sessionHidden;
  overlay.hidden = !visible;
  $("#session-pill").hidden = visible;
  overlay.dataset.state = s.state;
  overlay.style.setProperty("--c", board.coach.persona.color);
  const holder = $("#s-avatar");
  const look = board.coach.persona.id + JSON.stringify(board.game?.equipped || {});
  if (holder.dataset.look !== look) {
    holder.replaceChildren(avatar(board.coach.persona, 96));
    holder.dataset.look = look;
  }
  $("#s-bubble").textContent = board.coach.latest[0]?.text || "I'm here.";
  $("#s-task").textContent = s.task?.title || s.intention || "Focus time";
  const step = s.task?.next_step;
  $("#s-step").replaceChildren(...(step ? [h("div", { class: "next-step", onclick: () => tickStep(step.id) },
    h("div", { class: "box" }), h("div", {}, h("div", { class: "label" }, "now"), step.title))] : []));
  $("#s-controls").replaceChildren(...sessionControls(s));
  if (s.state !== lastSessionState) {
    if (lastSessionState === "running" && s.state === "overtime") Sound.chime();
    lastSessionState = s.state;
  }
  if (!sessionTick) sessionTick = setInterval(tickSession, 1000);
  tickSession();
}

function tickSession() {
  const s = board?.session;
  if (!s) return;
  const now = Date.now() + serverOffset;
  let label, remaining, total;
  if (s.state === "break") {
    remaining = Math.max(0, Math.round((new Date(s.break_until) - now) / 1000));
    total = Math.max(1, (new Date(s.break_until) - new Date(s.ended_at)) / 1000);
    label = remaining ? "☕ Stretch · water · window" : "Break's over";
  } else {
    remaining = Math.round((new Date(s.ends_at) - now) / 1000);
    total = s.minutes * 60;
    label = remaining > 0 ? `${s.minutes} min session` : "⏱ Time! Nice work";
  }
  const shown = Math.abs(remaining);
  $("#s-clock").textContent = (remaining < 0 ? "+" : "") + fmtClock(shown);
  $("#s-sub").textContent = label;
  $("#s-label").textContent = s.state === "break" ? "Break" : `Focus · ${board.focus_minutes_today} min today`;
  const frac = s.state === "break" ? remaining / total : remaining <= 0 ? 1 : remaining / total;  // full ring when time's up
  const ring = $("#s-ring"), c = 2 * Math.PI * 54;
  ring.style.strokeDasharray = c;
  ring.style.strokeDashoffset = c * (1 - frac);
  $("#session-pill").textContent = `⏱ ${remaining < 0 ? "+" : ""}${fmtClock(shown)}${s.state === "break" ? " ☕" : ""}`;
  // Check-in time? Ask once, show mood buttons
  const due = s.next_checkin_at && now >= new Date(s.next_checkin_at);
  $("#s-checkin").hidden = !due;
  if (due && askedCheckin !== s.next_checkin_at) {
    askedCheckin = s.next_checkin_at;
    Sound.chime();
    post("/api/coach/say", { event: "checkin_ask" }).then(buddySay);
  }
  if (s.state === "running" && remaining <= 0) load();  // flip to overtime promptly
}
document.querySelectorAll("#s-checkin button").forEach((b) => b.addEventListener("click", async () => {
  const s = board.session;
  const r = await post(`/api/sessions/${s.id}/checkin`, { mood: b.dataset.mood });
  buddySay(r.coach);
  if (b.dataset.mood === "stuck" && s.task_id && !s.task?.next_step) {
    const t = await post(`/api/tasks/${s.task_id}/breakdown`).catch(() => null);
    if (t?.next_step) toast("🪜 Try this: " + t.next_step.title);
  }
  load();
}));

// ---------- people ----------
function ago(iso) {
  const m = Math.round((Date.now() + serverOffset - new Date(iso)) / 60000);
  if (m < 60) return `${Math.max(1, m)}m ago`;
  if (m < 24 * 60) return `${Math.round(m / 60)}h ago`;
  return `${Math.round(m / 1440)}d ago`;
}

function renderPeople() {
  const ppl = board.people || [];
  $("#people-card").hidden = !ppl.length;
  $("#people-list").replaceChildren(...ppl.map((p) => {
    const inp = h("input", { placeholder: `Send ${p.name} a task…`, class: "send-input" });
    const send = async () => {
      if (!inp.value.trim()) return;
      const r = await post(`/api/people/${p.id}/send`, { text: inp.value });
      inp.value = "";
      toast(`💌 Sent ${p.name}: ${r.titles.join(", ")}`);
    };
    inp.addEventListener("keydown", (e) => e.key === "Enter" && send());
    return h("div", { class: "person" },
      h("div", { class: "person-head" }, h("b", {}, p.name),
        h("span", { class: "muted small" }, p.done_today === null ? "" : ` · ${p.done_today} done today`),
        h("span", { class: "spacer" }), h("button", { onclick: openFeed }, "🎉 Wins")),
      h("div", { class: "row tight" }, inp, h("button", { onclick: send, title: "Send" }, "💌")));
  }));
}

async function openFeed() {
  me = me || await api("/api/me");
  const { feed, reactions } = await api("/api/people/feed");
  $("#feed-list").replaceChildren(...(feed.length ? feed.map((f) => h("li", { class: f.mine ? "mine" : "" },
    h("div", { class: "t" }, h("b", {}, f.mine ? "You" : f.name), ` finished ${f.hard ? "💪 " : ""}`, h("i", {}, f.title),
      h("div", { class: "sub" }, ago(f.at), f.reactions.length ? " · " + f.reactions.map((r) => `${r.emoji} ${r.name}`).join("  ") : "")),
    f.mine ? null : h("div", { class: "reacts" }, reactions.map((e) => h("button", { type: "button",
      class: f.reactions.some((r) => r.emoji === e && r.from === me?.user?.id) ? "on" : "",
      onclick: async (ev) => { await post("/api/people/react", { completion_id: f.completion_id, emoji: e }); ev.target.classList.add("on", "pop"); } }, e)))))
    : [h("li", { class: "muted" }, "No wins yet this week. They'll show up here.")]));
  $("#feed").open || $("#feed").showModal();
}

$("#my-name").addEventListener("change", async (e) => {
  try { me.user = await api("/api/me", { method: "PATCH", body: { name: e.target.value } }); toast("Saved"); }
  catch (err) { toast(err.message); }
});

async function renderPeopleSettings() {
  me = me || await api("/api/me");
  $("#my-name").value = me.user.name === "Me" ? "" : me.user.name;
  const { people } = await api("/api/people");
  $("#people-manage").replaceChildren(...(people.length ? people.map((p) => h("li", {},
    h("div", { class: "t" }, "💞 ", p.name),
    h("button", { type: "button", class: "danger", onclick: async () => {
      if (!confirm(`Unlink ${p.name}? Shared tasks stay with whoever made them.`)) return;
      await api(`/api/people/${p.id}`, { method: "DELETE" }); renderPeopleSettings(); load(); } }, "Unlink")))
    : [h("li", { class: "muted" }, "No one linked yet.")]));
}
$("#invite-go").addEventListener("click", async () => {
  if (!$("#my-name").value.trim()) { $("#my-name").focus(); return toast("Add your name first so they know who it's from"); }
  const r = await post("/api/people/invite", { name: $("#invite-name").value });
  $("#invite-url").textContent = r.url;
  $("#invite-out").hidden = false;
  navigator.clipboard?.writeText(r.url).then(() => toast("Link copied"), () => {});
});
$("#tg-link").addEventListener("click", async () => {
  const r = await post("/api/telegram/link-code");
  $("#tg-code").textContent = `Send this to the bot: /link ${r.code}  (valid 15 min)`;
});

// ---------- game ----------
const LIGHT_THEMES = new Set(["paper", "candy", "bloom", "beach"]);
let shownBadges = new Set();
function applyTheme(theme) {
  const root = document.documentElement;
  if (root.dataset.theme === (theme || "")) return;
  if (theme) {
    root.dataset.theme = theme;
    root.classList.toggle("light", LIGHT_THEMES.has(theme));
  } else {
    delete root.dataset.theme;
    const t = localStorage.getItem("ticklight-theme");
    root.classList.toggle("light", t ? t === "light" : matchMedia("(prefers-color-scheme: light)").matches);
  }
}

function renderGame() {
  const g = board.game;
  if (!g) return;
  applyTheme(g.theme);
  $("#g-coins").textContent = `🪙 ${g.coins}`;
  $("#g-streak").textContent = `🔥 ${g.streak}`;
  $("#g-freezes").textContent = `❄️ ${g.freezes}`;
  $("#g-claim").hidden = !g.claimable;
  const fresh = (board.new_badges || []).filter((b) => !shownBadges.has(b.key));
  fresh.forEach((b, i) => {
    shownBadges.add(b.key);
    setTimeout(() => toast(h("span", {}, `${b.emoji} Badge unlocked: `, h("b", {}, b.name), h("span", { class: "muted small" }, ` · ${b.desc}`)), { bonus: true, ms: 8000 }), i * 900);
  });
  if (fresh.length) post("/api/game/seen");
  if (g.claimable && !document.querySelector(".banner.claim"))
    $("#banners").append(h("button", { class: "banner claim", onclick: () => openGame("challenges") }, "🎖 Challenge done! Claim your reward"));
  const evKey = g.event && `ticklight-event-${g.event.id}-${board.today_date}`;
  if (g.event && !localStorage.getItem(evKey) && !document.querySelector(".banner.event"))
    $("#banners").append(h("div", { class: "banner event" }, `${g.event.emoji} ${g.event.name}: ${g.event.blurb}`,
      h("button", { onclick: () => openGame("shop") }, "Shop"),
      h("button", { class: "link", onclick: (e) => { localStorage.setItem(evKey, "1"); e.target.closest(".banner").remove(); } }, "✕")));
}

let gameTab = "challenges";
let gameData = null;
async function openGame(tab) {
  if (tab) gameTab = tab;
  gameData = await api("/api/game");
  drawGame();
  if (!$("#game").open) $("#game").showModal();
}
document.querySelectorAll("#game .tabs button").forEach((b) => b.addEventListener("click", () => { gameTab = b.dataset.gtab; drawGame(); }));
$("#btn-game").addEventListener("click", () => openGame());

async function gameAct(path, body, msg) {
  try {
    await post(`/api/game/${path}`, body);
    if (msg) toast(msg);
    await load();
    await openGame();
  } catch (e) { toast(e.message); }
}

function drawGame() {
  const g = gameData;
  for (const b of document.querySelectorAll("#game .tabs button")) b.classList.toggle("on", b.dataset.gtab === gameTab);
  $("#game-avatar").replaceChildren(avatar(board.coach.persona, 72, g.equipped));
  $("#game-wallet").textContent = `Level ${g.level} · 🪙 ${g.coins} coins · 🔥 ${g.streak}-day streak · ❄️ ${g.freezes}/${g.max_freezes} freezes`;
  $("#game-event").replaceChildren(...(g.event ? [h("div", { class: "event-strip" }, `${g.event.emoji} ${g.event.name} · ${g.event.blurb}`)] : []));
  let body = [];
  if (gameTab === "challenges") {
    body = [h("p", { class: "muted small" }, "New challenges every Monday. Finish them any way you like."),
      ...g.challenges.map((c) => h("div", { class: "challenge" + (c.claimed ? " claimed" : "") },
        h("div", { class: "grow" }, h("div", {}, c.text), h("div", { class: "bar" }, h("div", { style: `width:${Math.round(100 * c.progress / c.target)}%` })),
          h("div", { class: "muted small" }, `${c.progress} / ${c.target} · reward 🪙 ${c.coins} + ${c.xp} XP`)),
        c.claimed ? h("span", { class: "muted" }, "✓ claimed")
          : h("button", { type: "button", class: c.complete ? "primary" : "", disabled: !c.complete,
              onclick: async () => { const r = await post("/api/game/claim", { slot: c.slot }); toast(h("span", {}, "🎖 ", h("span", { class: "coin" }, `+${r.coins} 🪙 `), h("span", { class: "xp" }, `+${r.xp} XP`)), { bonus: true }); await load(); openGame(); } },
              c.complete ? "Claim" : "…")))];
  } else if (gameTab === "shop") {
    const card = (it) => h("div", { class: "item" + (it.owned ? " owned" : "") + (it.locked ? " locked" : "") },
      h("div", { class: "item-emoji" }, it.emoji), h("div", { class: "grow" }, h("b", {}, it.name),
        h("div", { class: "muted small" }, it.owned ? "owned" : it.price === null ? it.how : it.locked ? `level ${it.level}` : `🪙 ${it.price}`)),
      it.owned || it.price === null ? null : h("button", { type: "button", disabled: it.locked || !it.affordable,
        onclick: () => gameAct("buy", { item: it.id }, `🛍 Got the ${it.name}!`) }, "Buy"));
    body = [
      h("h3", {}, "❄️ Streak freeze"),
      h("div", { class: "item" }, h("div", { class: "item-emoji" }, "❄️"), h("div", { class: "grow" }, h("b", {}, "Streak freeze"),
        h("div", { class: "muted small" }, `Covers a missed day automatically. You have ${g.freezes}/${g.max_freezes}.`)),
        h("button", { type: "button", disabled: g.freezes >= g.max_freezes || g.coins < g.freeze_price,
          onclick: () => gameAct("buy", { item: "freeze" }, "❄️ Freeze stocked") }, `🪙 ${g.freeze_price}`)),
      h("h3", {}, "Accessories"), h("div", { class: "items" }, g.items.filter((i) => i.kind === "accessory").map(card)),
      h("h3", {}, "Themes"), h("div", { class: "items" }, g.items.filter((i) => i.kind === "theme" && !i.owned || i.kind === "theme" && i.price > 0).map(card)),
    ];
  } else if (gameTab === "wardrobe") {
    const owned = g.items.filter((i) => i.owned);
    const slots = { head: "Head", face: "Face", neck: "Neck", aura: "Aura" };
    body = [
      ...Object.entries(slots).map(([slot, label]) => h("div", {}, h("h3", {}, label), h("div", { class: "chips" },
        h("button", { type: "button", class: !g.equipped[slot] ? "on" : "", onclick: () => gameAct("equip", { slot }) }, "none"),
        ...owned.filter((i) => i.slot === slot).map((i) => h("button", { type: "button", class: g.equipped[slot] === i.id ? "on" : "",
          onclick: () => gameAct("equip", { item: i.id }) }, `${i.emoji} ${i.name}`))))),
      h("h3", {}, "Theme"),
      h("div", { class: "chips" },
        h("button", { type: "button", class: g.theme_setting === "auto" ? "on" : "", onclick: () => gameAct("theme", { item: "auto" }) }, "◐ Light/dark switch"),
        h("button", { type: "button", class: g.theme_setting === "weekly" ? "on" : "", title: "Rotates through themes you own (not the free two)",
          onclick: () => gameAct("theme", { item: "weekly" }) }, "🔄 New each week"),
        ...owned.filter((i) => i.kind === "theme").map((i) => h("button", { type: "button", class: g.theme_setting === i.id ? "on" : "",
          onclick: () => gameAct("theme", { item: i.id }) }, `${i.emoji} ${i.name}`))),
      owned.some((i) => i.kind === "accessory") ? null : h("p", { class: "muted small" }, "Nothing to wear yet. Earn coins and visit the shop!"),
    ];
  } else {
    const got = g.badges.filter((b) => b.at).length;
    body = [h("p", { class: "muted small" }, `${got} of ${g.badges.length} badges`),
      h("div", { class: "badges" }, g.badges.map((b) => h("div", { class: "badge-card" + (b.at ? "" : " locked"), title: b.desc },
        h("div", { class: "item-emoji" }, b.at ? b.emoji : "🔒"), h("b", {}, b.name), h("div", { class: "muted small" }, b.desc))))];
  }
  $("#game-body").replaceChildren(...body.filter(Boolean));
}

// ---------- Android app (window.TicklightApp) ----------
// The app keeps its own device token so notification buttons, the widget and quick add work
// without the page. Make one for whoever is signed in here.
async function nativeSetup() {
  document.body.classList.add("native");
  const info = JSON.parse(NATIVE.info());
  const m = await api("/api/me");
  if (!info.token || info.uid !== m.user.id) {
    const t = await post("/api/tokens", { name: "Android app" });
    NATIVE.setToken(t.token, m.user.id);
  }
}
if (NATIVE) {
  nativeSetup().catch((e) => console.warn("app setup failed", e));
  window.ticklightResume = () => load();
  window.ticklightPush = () => $("#settings").open && updatePushStatus();
  // Back button: close the top dialog first
  window.ticklightBack = () => {
    const open = [...document.querySelectorAll("dialog[open]")];
    if (!open.length) return false;
    open[open.length - 1].close();
    return true;
  };
  $("#logout").addEventListener("click", () => NATIVE.clearToken());
  $("#push-test").after(h("button", { type: "button", onclick: () => confirm("Disconnect this phone and pick a different server?") && NATIVE.resetServer() }, "Change server"));
}

// ---------- installable app ----------
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch((e) => console.warn("SW failed", e));
  navigator.serviceWorker.addEventListener("message", (e) => e.data?.type === "refresh" && load());
}

// ---------- display / theme ----------
function setDisplay(on) {
  document.body.classList.toggle("display", on);
  localStorage.setItem("ticklight-display", on ? "1" : "");
}
function toggleTheme() {
  if (board?.game?.theme) return toast(`Theme: ${board.game.theme}. Change it in 🎒 → Wardrobe.`);
  const light = document.documentElement.classList.toggle("light");
  localStorage.setItem("ticklight-theme", light ? "light" : "dark");
}
setDisplay(!!localStorage.getItem("ticklight-display") || params.has("display"));

$("#btn-display").addEventListener("click", () => setDisplay(!document.body.classList.contains("display")));
$("#btn-theme").addEventListener("click", toggleTheme);
$("#btn-new").addEventListener("click", () => openEdit(null));
$("#btn-someday").addEventListener("click", openSomeday);
$("#btn-settings").addEventListener("click", openSettings);
$("#anytime-toggle").addEventListener("click", (e) => {
  const open = $("#anytime").hidden;
  $("#anytime").hidden = !open;
  e.currentTarget.classList.toggle("open", open);
});

document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea, select, dialog")) return;
  if (e.key === "/") { e.preventDefault(); input.focus(); }
  else if (e.key === "n") { e.preventDefault(); openEdit(null); }
  else if (e.key === "d") setDisplay(!document.body.classList.contains("display"));
  else if (e.key === "t") toggleTheme();
  else if (e.key === "e" && board) post("/api/energy", { level: (board.energy.level % 3) + 1 }).then(load);
  else if (e.key === "v") toggleVoice();
  else if (e.key === "g") openGame();
  else if (e.key === "s") board?.session ? showSession(true) : openStartSession();
  else if (e.key === "h") setHyperfocus(board?.hyperfocus_until ? 0 : undefined);
});

// Shortcuts from the installed app and notifications: ?capture, ?voice, ?routine=morning
if (params.has("capture")) input.focus();
if (params.has("voice")) toggleVoice();
if (params.get("routine")) openFlow(params.get("routine"));
if (params.has("people")) setTimeout(openFeed, 600);
if (params.has("session")) setTimeout(() => (board?.session ? showSession(true) : openStartSession()), 600);
for (const k of ["capture", "voice", "routine", "session", "people"]) params.delete(k);
history.replaceState(null, "", location.pathname + (params.toString() ? "?" + params : ""));

load();
