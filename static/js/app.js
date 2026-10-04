"use strict";

const App = {
  profile: null,
  aiAvailable: false,
  dashboardDate: todayStr(),
};

const ROUTES = {
  "": "dashboard",
  "#/": "dashboard",
  "#/onboarding": "onboarding",
  "#/meal": "logmeal",
  "#/activity": "logactivity",
  "#/history": "history",
  "#/settings": "settings",
};

const VIEW_RENDERERS = {
  onboarding: renderOnboarding,
  dashboard: renderDashboard,
  logmeal: renderLogMeal,
  logactivity: renderLogActivity,
  history: renderHistory,
  settings: renderSettings,
};

function navigate() {
  let hash = location.hash || "#/";
  // Day drill-down: #/day/YYYY-MM-DD renders the dashboard for that date
  const dayMatch = hash.match(/^#\/day\/(\d{4}-\d{2}-\d{2})$/);
  let view;
  if (dayMatch) {
    App.dashboardDate = dayMatch[1];
    view = "dashboard";
  } else {
    view = ROUTES[hash];
    if (view === "dashboard") App.dashboardDate = todayStr();
  }
  if (!view) { location.hash = "#/"; return; }
  if (!App.profile && view !== "onboarding") { location.hash = "#/onboarding"; return; }
  if (App.profile && view === "onboarding") { location.hash = "#/"; return; }

  document.querySelectorAll(".view").forEach((s) => s.classList.add("hidden"));
  const section = document.getElementById("view-" + view);
  section.classList.remove("hidden");

  document.querySelectorAll(".nav-links a").forEach((a) => {
    a.classList.toggle("active", a.dataset.view === view);
  });
  document.getElementById("nav").classList.toggle("hidden", view === "onboarding");

  VIEW_RENDERERS[view](section);
}

async function refreshHealth() {
  try {
    const h = await api.health();
    App.aiAvailable = h.api_key_present;
  } catch (e) {
    App.aiAvailable = false;
  }
  const banner = document.getElementById("banner");
  if (App.aiAvailable || sessionStorage.getItem("banner-dismissed")) {
    banner.classList.add("hidden");
    return;
  }
  banner.innerHTML = `
    <span class="banner-text">AI features are off — add an <code>ANTHROPIC_API_KEY</code>
      to <code>.env</code> and restart to enable meal analysis and day ratings.
      Everything else, including manual logging, works as normal.</span>
    <button class="banner-close" type="button" aria-label="Dismiss">×</button>`;
  banner.classList.remove("hidden");
  banner.querySelector(".banner-close").onclick = () => {
    banner.classList.add("hidden");
    try { sessionStorage.setItem("banner-dismissed", "1"); } catch (e) { /* ignore */ }
  };
}

async function init() {
  initTheme();
  await refreshHealth();
  try {
    App.profile = await api.getProfile();
  } catch (e) {
    App.profile = null;
  }
  window.addEventListener("hashchange", navigate);
  navigate();
}

init();
