"use strict";

const MealChat = { messages: [], mealType: "lunch", busy: false };

function renderLogMeal(section) {
  MealChat.messages = [];
  MealChat.busy = false;

  section.innerHTML = `
    <h1>Log a meal</h1>
    <p class="subtitle">Describe what you ate in plain words — the AI works out the macros${App.aiAvailable ? "" : " (AI unavailable — use manual entry below)"}.</p>

    <div class="card">
      <div class="field" style="max-width:220px">
        <label>Meal type</label>
        <select id="meal-type">
          <option value="breakfast">Breakfast</option>
          <option value="lunch" selected>Lunch</option>
          <option value="dinner">Dinner</option>
          <option value="snack">Snack</option>
        </select>
      </div>
      <br>
      <div class="chat-box" id="chat-box"></div>
      <div class="chat-input-row">
        <textarea id="chat-input" placeholder="e.g. chicken wrap with mayo and a can of coke" ${App.aiAvailable ? "" : "disabled"}></textarea>
        <button id="chat-send" ${App.aiAvailable ? "" : "disabled"}>Send</button>
      </div>
      <div id="chat-error" class="error-text hidden"></div>
    </div>

    <div id="analysis-slot"></div>

    <div class="card">
      <h2 style="margin-top:0">Manual entry</h2>
      <form id="manual-form" class="form-grid">
        <div class="field full"><label>Description</label><input name="description" required placeholder="What did you eat?"></div>
        <div class="field"><label>Calories</label><input name="calories" type="number" min="0" required></div>
        <div class="field"><label>Protein (g)</label><input name="protein_g" type="number" step="0.1" min="0" required></div>
        <div class="field"><label>Carbs (g)</label><input name="carbs_g" type="number" step="0.1" min="0" required></div>
        <div class="field"><label>Fat (g)</label><input name="fat_g" type="number" step="0.1" min="0" required></div>
        <div class="full"><button type="submit" class="secondary">Save manually</button></div>
      </form>
    </div>
  `;

  const chatBox = section.querySelector("#chat-box");
  const input = section.querySelector("#chat-input");
  const sendBtn = section.querySelector("#chat-send");
  const errEl = section.querySelector("#chat-error");

  function addBubble(role, text, thinking = false) {
    const div = document.createElement("div");
    div.className = `bubble ${role}${thinking ? " thinking" : ""}`;
    div.textContent = text;
    chatBox.appendChild(div);
    div.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return div;
  }

  async function send() {
    const text = input.value.trim();
    if (!text || MealChat.busy) return;
    input.value = "";
    errEl.classList.add("hidden");
    MealChat.mealType = section.querySelector("#meal-type").value;
    MealChat.messages.push({ role: "user", content: text });
    addBubble("user", text);
    const spinner = addBubble("ai", "Analyzing...", true);
    MealChat.busy = true;
    sendBtn.disabled = true;
    try {
      const res = await api.analyzeMeal(MealChat.mealType, MealChat.messages);
      spinner.remove();
      if (res.status === "question") {
        MealChat.messages.push({ role: "assistant", content: res.question });
        addBubble("ai", res.question);
      } else {
        addBubble("ai", "Here's my analysis — review and edit before saving 👇");
        showAnalysis(section, res.analysis);
      }
    } catch (err) {
      spinner.remove();
      errEl.textContent = err.message;
      errEl.classList.remove("hidden");
      MealChat.messages.pop();
    } finally {
      MealChat.busy = false;
      sendBtn.disabled = !App.aiAvailable;
    }
  }

  sendBtn.onclick = send;
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });

  section.querySelector("#manual-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    await api.saveMeal({
      date: todayStr(),
      meal_type: section.querySelector("#meal-type").value,
      description: fd.get("description"),
      calories: Number(fd.get("calories")),
      protein_g: Number(fd.get("protein_g")),
      carbs_g: Number(fd.get("carbs_g")),
      fat_g: Number(fd.get("fat_g")),
    });
    location.hash = "#/";
  });
}

function showAnalysis(section, a) {
  const slot = section.querySelector("#analysis-slot");
  const itemRows = (a.items || []).map((i) => `
    <tr>
      <td>${esc(i.name)}${i.portion ? ` <span class="muted">(${esc(i.portion)})</span>` : ""}</td>
      <td class="num">${i.calories}</td>
      <td class="num">${i.protein_g}</td>
      <td class="num">${i.carbs_g}</td>
      <td class="num">${i.fat_g}</td>
    </tr>`).join("");

  slot.innerHTML = `
    <div class="card">
      <h2 style="margin-top:0">Analysis
        <span class="score-badge ${scoreClass(a.score)}" style="float:right">${a.score}/10</span>
      </h2>
      <table class="analysis-table">
        <thead><tr><th>Item</th><th class="num">kcal</th><th class="num">P (g)</th><th class="num">C (g)</th><th class="num">F (g)</th></tr></thead>
        <tbody>${itemRows}</tbody>
      </table>
      <div class="totals-grid">
        <div><label>Calories</label><input id="a-cal" type="number" value="${a.total_calories}"></div>
        <div><label>Protein (g)</label><input id="a-pro" type="number" step="0.1" value="${a.total_protein_g}"></div>
        <div><label>Carbs (g)</label><input id="a-carb" type="number" step="0.1" value="${a.total_carbs_g}"></div>
        <div><label>Fat (g)</label><input id="a-fat" type="number" step="0.1" value="${a.total_fat_g}"></div>
      </div>
      <div class="feedback-box">${esc(a.feedback)}</div>
      <div style="display:flex;gap:8px">
        <button id="a-save">Save meal</button>
        <button id="a-discard" class="danger-btn">Discard</button>
      </div>
      <div id="a-error" class="error-text hidden"></div>
    </div>
  `;
  slot.scrollIntoView({ behavior: "smooth", block: "nearest" });

  slot.querySelector("#a-discard").onclick = () => { slot.innerHTML = ""; };
  slot.querySelector("#a-save").onclick = async () => {
    const description = MealChat.messages.filter((m) => m.role === "user").map((m) => m.content).join("; ");
    try {
      await api.saveMeal({
        date: todayStr(),
        meal_type: MealChat.mealType,
        description,
        calories: Number(slot.querySelector("#a-cal").value),
        protein_g: Number(slot.querySelector("#a-pro").value),
        carbs_g: Number(slot.querySelector("#a-carb").value),
        fat_g: Number(slot.querySelector("#a-fat").value),
        items: a.items || null,
        score: a.score,
        feedback: a.feedback,
      });
      location.hash = "#/";
    } catch (err) {
      const el = slot.querySelector("#a-error");
      el.textContent = err.message;
      el.classList.remove("hidden");
    }
  };
}
