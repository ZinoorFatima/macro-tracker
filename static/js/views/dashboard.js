"use strict";

const MACRO_META = [
  { key: "calories", name: "Calories", unit: " kcal", color: "var(--c-cal)" },
  { key: "protein_g", name: "Protein", unit: "g", color: "var(--c-protein)" },
  { key: "carbs_g", name: "Carbs", unit: "g", color: "var(--c-carbs)" },
  { key: "fat_g", name: "Fat", unit: "g", color: "var(--c-fat)" },
];

async function renderDashboard(section) {
  const date = App.dashboardDate;
  section.innerHTML = `<p class="muted">Loading...</p>`;
  let s;
  try {
    s = await api.daySummary(date);
  } catch (err) {
    section.innerHTML = `<p class="error-text">${esc(err.message)}</p>`;
    return;
  }

  const bars = MACRO_META.map((m) => {
    const eaten = s.totals[m.key];
    const target = s.targets[m.key];
    const pct = target > 0 ? Math.min((eaten / target) * 100, 100) : 0;
    const over = eaten > target;
    const burnChip = m.key === "calories" && s.totals.calories_burned > 0
      ? `<span class="burn-chip">🔥 ${s.totals.calories_burned} kcal burned</span>` : "";
    return `
      <div class="macro-row">
        <div class="macro-head">
          <span class="name">${m.name}${burnChip}</span>
          <span class="nums">${Math.round(eaten)}${m.unit} <span class="target">/ ${target}${m.unit}${over ? " (over)" : ""}</span></span>
        </div>
        <div class="bar"><div class="bar-fill ${over ? "over" : ""}" style="width:${pct}%;background:${m.color}"></div></div>
      </div>`;
  }).join("");

  const mealRows = s.meals.length
    ? s.meals.map((m) => `
      <div class="item-row" data-meal-id="${m.id}">
        <div class="item-main">
          <div class="item-title"><span class="item-tag">${esc(m.meal_type)}</span>${esc(m.description)}</div>
          <div class="item-sub">${m.calories} kcal · ${m.protein_g}g P · ${m.carbs_g}g C · ${m.fat_g}g F</div>
          <div class="edit-slot"></div>
        </div>
        <div class="item-actions">
          ${m.score ? `<span class="score-badge ${scoreClass(m.score)}">${m.score}/10</span>` : ""}
          <button class="small secondary edit-meal">Edit</button>
          <button class="small danger-btn del-meal">✕</button>
        </div>
      </div>`).join("")
    : `<p class="muted">No meals logged yet.</p>`;

  const actRows = s.activities.length
    ? s.activities.map((a) => {
      let title, sub;
      if (a.activity_type === "steps") {
        title = `🚶 ${a.steps.toLocaleString()} steps`;
        sub = `~${a.calories_burned} kcal`;
      } else if (a.activity_type === "rest") {
        title = "😴 Rest day";
        sub = "No training";
      } else {
        title = `🏋️ ${esc(a.description)}`;
        const ex = (a.exercises || []).map((e) => {
          if (e.kind === "cardio") return `${e.name} ${e.duration_min ? e.duration_min + " min" : ""}`;
          return `${e.name} ${e.sets || "?"}×${e.reps || "?"}${e.weight_kg ? " @ " + e.weight_kg + "kg" : ""}`;
        }).join(" · ");
        sub = `${ex ? ex + " · " : ""}~${a.calories_burned} kcal`;
      }
      return `
        <div class="item-row" data-act-id="${a.id}">
          <div class="item-main"><div class="item-title">${title}</div><div class="item-sub">${sub}</div></div>
          <div class="item-actions"><button class="small danger-btn del-act">✕</button></div>
        </div>`;
    }).join("")
    : `<p class="muted">No activity logged yet.</p>`;

  const rating = s.rating ? `
      <div class="rating-score">${s.rating.score}/10 <span>day score</span></div>
      <p style="margin:8px 0">${esc(s.rating.summary)}</p>
      ${s.rating.progress_note ? `<p class="muted">${esc(s.rating.progress_note)}</p>` : ""}
      <br><button id="rate-day" class="secondary small">Re-rate this day</button>`
    : `<p class="muted">Get an AI rating of your day — adherence, food quality, activity, and progress toward your goal.</p>
       <br><button id="rate-day" ${App.aiAvailable ? "" : "disabled"}>Rate my day</button>`;

  const isToday = date === todayStr();
  section.innerHTML = `
    <div class="day-nav">
      <button class="secondary" id="day-prev">←</button>
      <h1>${prettyDate(date)} <span class="muted" style="font-weight:400">${date}</span></h1>
      <button class="secondary" id="day-next" ${isToday ? "disabled" : ""}>→</button>
    </div>

    <div class="card">${bars}</div>

    <div class="card">
      <h2 style="margin-top:0">Meals</h2>
      ${mealRows}
      <br><a href="#/meal"><button class="secondary small">+ Log a meal</button></a>
    </div>

    <div class="card">
      <h2 style="margin-top:0">Activity</h2>
      ${actRows}
      <br><a href="#/activity"><button class="secondary small">+ Log activity</button></a>
    </div>

    <div class="card">
      <h2 style="margin-top:0">Weight</h2>
      <div style="display:flex;gap:8px;align-items:center">
        <input id="weight-input" type="number" step="0.1" min="30" max="300" style="max-width:140px"
               placeholder="kg" value="${s.weight_kg ?? ""}">
        <button id="weight-save" class="small">Log weight</button>
        <span id="weight-msg" class="muted"></span>
      </div>
    </div>

    <div class="card" id="rating-card">${rating}</div>
    <div id="dash-error" class="error-text hidden"></div>
  `;

  section.querySelector("#day-prev").onclick = () => { location.hash = "#/day/" + shiftDate(date, -1); };
  section.querySelector("#day-next").onclick = () => {
    const next = shiftDate(date, 1);
    location.hash = next === todayStr() ? "#/" : "#/day/" + next;
  };

  const showError = (msg) => {
    const el = section.querySelector("#dash-error");
    el.textContent = msg;
    el.classList.remove("hidden");
  };

  section.querySelectorAll(".del-meal").forEach((btn) => {
    btn.onclick = async () => {
      const id = btn.closest("[data-meal-id]").dataset.mealId;
      await api.deleteMeal(id);
      renderDashboard(section);
    };
  });
  section.querySelectorAll(".del-act").forEach((btn) => {
    btn.onclick = async () => {
      const id = btn.closest("[data-act-id]").dataset.actId;
      await api.deleteActivity(id);
      renderDashboard(section);
    };
  });

  section.querySelectorAll(".edit-meal").forEach((btn) => {
    btn.onclick = () => {
      const row = btn.closest("[data-meal-id]");
      const id = row.dataset.mealId;
      const meal = s.meals.find((m) => String(m.id) === id);
      const slot = row.querySelector(".edit-slot");
      if (slot.innerHTML) { slot.innerHTML = ""; return; }
      slot.innerHTML = `
        <div class="inline-edit">
          <div><label>kcal</label><input type="number" class="e-cal" value="${meal.calories}"></div>
          <div><label>P (g)</label><input type="number" step="0.1" class="e-pro" value="${meal.protein_g}"></div>
          <div><label>C (g)</label><input type="number" step="0.1" class="e-carb" value="${meal.carbs_g}"></div>
          <div><label>F (g)</label><input type="number" step="0.1" class="e-fat" value="${meal.fat_g}"></div>
          <button class="small save-edit">Save</button>
        </div>`;
      slot.querySelector(".save-edit").onclick = async () => {
        try {
          await api.updateMeal(id, {
            calories: Number(slot.querySelector(".e-cal").value),
            protein_g: Number(slot.querySelector(".e-pro").value),
            carbs_g: Number(slot.querySelector(".e-carb").value),
            fat_g: Number(slot.querySelector(".e-fat").value),
          });
          renderDashboard(section);
        } catch (err) { showError(err.message); }
      };
    };
  });

  section.querySelector("#weight-save").onclick = async () => {
    const val = Number(section.querySelector("#weight-input").value);
    if (!val) return;
    try {
      await api.logWeight(date, val);
      section.querySelector("#weight-msg").textContent = "Saved ✓ (targets updated)";
      App.profile = await api.getProfile();
    } catch (err) { showError(err.message); }
  };

  const rateBtn = section.querySelector("#rate-day");
  if (rateBtn) {
    rateBtn.onclick = async () => {
      rateBtn.disabled = true;
      rateBtn.textContent = "Rating your day...";
      try {
        await api.rateDay(date);
        renderDashboard(section);
      } catch (err) {
        rateBtn.disabled = false;
        rateBtn.textContent = "Rate my day";
        showError(err.message);
      }
    };
  }
}
