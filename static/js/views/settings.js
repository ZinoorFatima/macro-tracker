"use strict";

function renderSettings(section) {
  const p = App.profile;
  section.innerHTML = `
    <h1>Settings</h1>
    <p class="subtitle">Update your profile — targets recompute automatically.</p>
    <div class="card">
      <form id="set-form" class="form-grid">
        <div class="field"><label>Age</label><input name="age" type="number" min="10" max="100" value="${p.age}" required></div>
        <div class="field"><label>Gender</label>
          <select name="gender">
            <option value="male" ${p.gender === "male" ? "selected" : ""}>Male</option>
            <option value="female" ${p.gender === "female" ? "selected" : ""}>Female</option>
          </select>
        </div>
        <div class="field"><label>Height (cm)</label><input name="height_cm" type="number" step="0.1" value="${p.height_cm}" required></div>
        <div class="field"><label>Weight (kg)</label><input name="weight_kg" type="number" step="0.1" value="${p.weight_kg}" required></div>
        <div class="field"><label>Activity level</label>
          <select name="activity_level">
            ${["sedentary", "light", "moderate", "active", "very_active"].map((a) =>
              `<option value="${a}" ${p.activity_level === a ? "selected" : ""}>${a.replace("_", " ")}</option>`).join("")}
          </select>
        </div>
        <div class="field"><label>Goal</label>
          <select name="goal">
            ${[["lose_fat", "Lose fat"], ["gain_muscle", "Gain muscle"], ["maintain", "Maintain"], ["recomp", "Recomp"]].map(([v, l]) =>
              `<option value="${v}" ${p.goal === v ? "selected" : ""}>${l}</option>`).join("")}
          </select>
        </div>
        <div class="field"><label>Timeline (weeks)</label><input name="timeline_weeks" type="number" min="1" value="${p.timeline_weeks ?? ""}"></div>
        <div class="field"><label>Target weight (kg)</label><input name="target_weight_kg" type="number" step="0.1" value="${p.target_weight_kg ?? ""}"></div>
        <div class="full"><button type="submit">Save changes</button> <span id="set-msg" class="success-text"></span></div>
      </form>
      <div id="set-error" class="error-text hidden"></div>
    </div>
    <div class="card" id="targets-card">
      <h2 style="margin-top:0">Current daily targets</h2>
      <div class="totals-grid">
        <div><label>Calories</label><div class="rating-score" style="font-size:22px">${p.calorie_target}</div></div>
        <div><label>Protein</label><div class="rating-score" style="font-size:22px">${p.protein_target_g}g</div></div>
        <div><label>Carbs</label><div class="rating-score" style="font-size:22px">${p.carbs_target_g}g</div></div>
        <div><label>Fat</label><div class="rating-score" style="font-size:22px">${p.fat_target_g}g</div></div>
      </div>
    </div>
  `;

  section.querySelector("#set-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const data = {
      age: Number(fd.get("age")),
      gender: fd.get("gender"),
      height_cm: Number(fd.get("height_cm")),
      weight_kg: Number(fd.get("weight_kg")),
      activity_level: fd.get("activity_level"),
      goal: fd.get("goal"),
      timeline_weeks: fd.get("timeline_weeks") ? Number(fd.get("timeline_weeks")) : null,
      target_weight_kg: fd.get("target_weight_kg") ? Number(fd.get("target_weight_kg")) : null,
    };
    const errEl = section.querySelector("#set-error");
    errEl.classList.add("hidden");
    try {
      await api.updateProfile(data);
      App.profile = await api.getProfile();
      renderSettings(section);
      section.querySelector("#set-msg").textContent = "Saved ✓";
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove("hidden");
    }
  });
}
