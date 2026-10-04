"use strict";

async function apiFetch(path, options = {}) {
  const res = await fetch("/api" + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* ignore */ }
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    throw err;
  }
  return res.json();
}

const api = {
  health: () => apiFetch("/health"),
  getProfile: () => apiFetch("/profile"),
  createProfile: (data) => apiFetch("/profile", { method: "POST", body: JSON.stringify(data) }),
  updateProfile: (data) => apiFetch("/profile", { method: "PUT", body: JSON.stringify(data) }),

  analyzeMeal: (mealType, messages) =>
    apiFetch("/meals/analyze", { method: "POST", body: JSON.stringify({ meal_type: mealType, messages }) }),
  saveMeal: (data) => apiFetch("/meals", { method: "POST", body: JSON.stringify(data) }),
  updateMeal: (id, data) => apiFetch(`/meals/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteMeal: (id) => apiFetch(`/meals/${id}`, { method: "DELETE" }),

  analyzeActivity: (messages) =>
    apiFetch("/activities/analyze", { method: "POST", body: JSON.stringify({ messages }) }),
  saveActivity: (data) => apiFetch("/activities", { method: "POST", body: JSON.stringify(data) }),
  deleteActivity: (id) => apiFetch(`/activities/${id}`, { method: "DELETE" }),

  daySummary: (date) => apiFetch(`/days/${date}/summary`),
  rateDay: (date) => apiFetch(`/days/${date}/rate`, { method: "POST" }),
  listDays: (start, end) => apiFetch(`/days?start=${start}&end=${end}`),

  logWeight: (date, weightKg) =>
    apiFetch("/weight", { method: "POST", body: JSON.stringify({ date, weight_kg: weightKg }) }),
  listWeights: () => apiFetch("/weight"),
};

function todayStr(offsetDays = 0) {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return localDateStr(d);
}

function localDateStr(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function shiftDate(dateStr, days) {
  const [y, m, d] = dateStr.split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  dt.setDate(dt.getDate() + days);
  return localDateStr(dt);
}

function prettyDate(dateStr) {
  const [y, m, d] = dateStr.split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  const today = todayStr();
  if (dateStr === today) return "Today";
  if (dateStr === shiftDate(today, -1)) return "Yesterday";
  return dt.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function esc(s) {
  const div = document.createElement("div");
  div.textContent = s == null ? "" : String(s);
  return div.innerHTML;
}

function scoreClass(score) {
  if (score >= 7) return "s-good";
  if (score >= 4) return "s-mid";
  return "s-bad";
}
