"use strict";

function renderOnboarding(section) {
  section.innerHTML = `
    <h1>Welcome to Macro Tracker 🥗</h1>
    <p class="subtitle">Tell us about yourself so we can compute your daily calorie and macro targets.</p>
    <div class="card">
      <form id="ob-form" class="form-grid">
        <div class="field"><label>Age</label><input name="age" type="number" min="10" max="100" required></div>
        <div class="field"><label>Gender</label>
          <select name="gender"><option value="male">Male</option><option value="female">Female</option></select>
        </div>
        <div class="field"><label>Height (cm)</label><input name="height_cm" type="number" step="0.1" min="100" max="250" required></div>
        <div class="field"><label>Weight (kg)</label><input name="weight_kg" type="number" step="0.1" min="30" max="300" required></div>
        <div class="field"><label>Activity level</label>
          <select name="activity_level">
            <option value="sedentary">Sedentary (desk job, little exercise)</option>
            <option value="light">Light (1-3 workouts/week)</option>
            <option value="moderate" selected>Moderate (3-5 workouts/week)</option>
            <option value="active">Active (6-7 workouts/week)</option>
            <option value="very_active">Very active (physical job + training)</option>
          </select>
        </div>
        <div class="field"><label>Goal</label>
          <select name="goal">
            <option value="lose_fat">Lose fat</option>
            <option value="gain_muscle">Gain muscle</option>
            <option value="maintain">Maintain</option>
            <option value="recomp">Recomp (lose fat + gain muscle)</option>
          </select>
        </div>
        <div class="field"><label>Timeline (weeks, optional)</label><input name="timeline_weeks" type="number" min="1" max="520"></div>
        <div class="field"><label>Target weight (kg, optional)</label><input name="target_weight_kg" type="number" step="0.1" min="30" max="300"></div>
        <div class="full"><button type="submit">Compute my targets</button></div>
      </form>
      <div id="ob-error" class="error-text hidden"></div>
    </div>
    <div id="ob-result" class="hidden"></div>
  `;

  section.querySelector("#ob-form").addEventListener("submit", async (e) => {
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
    const errEl = section.querySelector("#ob-error");
    errEl.classList.add("hidden");
    try {
      const profile = await api.createProfile(data);
      const resultEl = section.querySelector("#ob-result");
      resultEl.classList.remove("hidden");
      resultEl.innerHTML = `
        <div class="card">
          <h2 style="margin-top:0">Your daily targets</h2>
          <div class="totals-grid">
            <div><label>Calories</label><div class="rating-score" style="font-size:24px">${profile.calorie_target}</div></div>
            <div><label>Protein</label><div class="rating-score" style="font-size:24px">${profile.protein_target_g}g</div></div>
            <div><label>Carbs</label><div class="rating-score" style="font-size:24px">${profile.carbs_target_g}g</div></div>
            <div><label>Fat</label><div class="rating-score" style="font-size:24px">${profile.fat_target_g}g</div></div>
          </div>
          <p class="muted">Computed with the Mifflin-St Jeor formula, adjusted for your activity level and goal. You can tweak your profile any time in Settings.</p>
          <br>
          <button id="ob-go">Looks good — let's go!</button>
        </div>
      `;
      resultEl.querySelector("#ob-go").addEventListener("click", async () => {
        App.profile = await api.getProfile();
        location.hash = "#/";
      });
      resultEl.scrollIntoView({ behavior: "smooth" });
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove("hidden");
    }
  });
}
