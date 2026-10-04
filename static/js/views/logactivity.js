"use strict";

const ActChat = { messages: [], busy: false };

function renderLogActivity(section) {
  ActChat.messages = [];
  ActChat.busy = false;

  section.innerHTML = `
    <h1>Log activity</h1>
    <p class="subtitle">Tell the AI what you did at the gym, log your steps, or mark a rest day.</p>
    <div class="tabs">
      <button data-tab="workout" class="active">🏋️ Workout</button>
      <button data-tab="steps">🚶 Steps</button>
      <button data-tab="rest">😴 Rest day</button>
    </div>

    <div id="tab-workout" class="tab-pane">
      <div class="card">
        <div class="chat-box" id="act-chat-box"></div>
        <div class="chat-input-row">
          <textarea id="act-input" placeholder="e.g. 3 sets of 10 bicep curls at 12kg, 20 min treadmill" ${App.aiAvailable ? "" : "disabled"}></textarea>
          <button id="act-send" ${App.aiAvailable ? "" : "disabled"}>Send</button>
        </div>
        ${App.aiAvailable ? "" : '<p class="muted">AI unavailable — use the manual form below.</p>'}
        <div id="act-error" class="error-text hidden"></div>
      </div>
      <div id="act-analysis-slot"></div>
      <div class="card">
        <h2 style="margin-top:0">Manual entry</h2>
        <form id="act-manual" class="form-grid">
          <div class="field full"><label>Workout description</label><input name="description" required></div>
          <div class="field"><label>Calories burned (estimate)</label><input name="calories_burned" type="number" min="0" value="0"></div>
          <div class="full"><button type="submit" class="secondary">Save manually</button></div>
        </form>
      </div>
    </div>

    <div id="tab-steps" class="tab-pane hidden">
      <div class="card">
        <div class="field" style="max-width:240px"><label>Steps today</label><input id="steps-input" type="number" min="0" placeholder="e.g. 8000"></div>
        <br><button id="steps-save">Save steps</button>
        <div id="steps-error" class="error-text hidden"></div>
      </div>
    </div>

    <div id="tab-rest" class="tab-pane hidden">
      <div class="card">
        <p>No training today — that's okay, recovery matters too.</p>
        <br><button id="rest-save">Log rest day</button>
      </div>
    </div>
  `;

  section.querySelectorAll(".tabs button").forEach((btn) => {
    btn.onclick = () => {
      section.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b === btn));
      section.querySelectorAll(".tab-pane").forEach((p) => p.classList.add("hidden"));
      section.querySelector("#tab-" + btn.dataset.tab).classList.remove("hidden");
    };
  });

  const chatBox = section.querySelector("#act-chat-box");
  const input = section.querySelector("#act-input");
  const sendBtn = section.querySelector("#act-send");
  const errEl = section.querySelector("#act-error");

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
    if (!text || ActChat.busy) return;
    input.value = "";
    errEl.classList.add("hidden");
    ActChat.messages.push({ role: "user", content: text });
    addBubble("user", text);
    const spinner = addBubble("ai", "Analyzing your workout...", true);
    ActChat.busy = true;
    sendBtn.disabled = true;
    try {
      const res = await api.analyzeActivity(ActChat.messages);
      spinner.remove();
      if (res.status === "question") {
        ActChat.messages.push({ role: "assistant", content: res.question });
        addBubble("ai", res.question);
      } else {
        addBubble("ai", "Parsed your workout 👇");
        showActAnalysis(section, res.analysis);
      }
    } catch (err) {
      spinner.remove();
      errEl.textContent = err.message;
      errEl.classList.remove("hidden");
      ActChat.messages.pop();
    } finally {
      ActChat.busy = false;
      sendBtn.disabled = !App.aiAvailable;
    }
  }
  sendBtn.onclick = send;
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });

  section.querySelector("#act-manual").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    await api.saveActivity({
      date: todayStr(),
      activity_type: "workout",
      description: fd.get("description"),
      calories_burned: Number(fd.get("calories_burned")) || 0,
    });
    location.hash = "#/";
  });

  section.querySelector("#steps-save").onclick = async () => {
    const steps = Number(section.querySelector("#steps-input").value);
    if (!steps) return;
    try {
      await api.saveActivity({ date: todayStr(), activity_type: "steps", steps });
      location.hash = "#/";
    } catch (err) {
      const el = section.querySelector("#steps-error");
      el.textContent = err.message;
      el.classList.remove("hidden");
    }
  };

  section.querySelector("#rest-save").onclick = async () => {
    await api.saveActivity({ date: todayStr(), activity_type: "rest" });
    location.hash = "#/";
  };
}

function showActAnalysis(section, a) {
  const slot = section.querySelector("#act-analysis-slot");
  const rows = (a.exercises || []).map((e) => `
    <tr>
      <td>${esc(e.name)} <span class="muted">${e.kind}</span></td>
      <td class="num">${e.kind === "cardio" ? (e.duration_min ? e.duration_min + " min" : "—")
        : `${e.sets || "?"}×${e.reps || "?"}${e.weight_kg ? " @ " + e.weight_kg + "kg" : ""}`}</td>
    </tr>`).join("");

  slot.innerHTML = `
    <div class="card">
      <h2 style="margin-top:0">Workout summary</h2>
      <table class="analysis-table">
        <thead><tr><th>Exercise</th><th class="num">Details</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="totals-grid" style="grid-template-columns:1fr">
        <div style="max-width:240px"><label>Calories burned</label><input id="act-cal" type="number" value="${a.total_calories_burned}"></div>
      </div>
      ${a.notes ? `<div class="feedback-box">${esc(a.notes)}</div>` : ""}
      <div style="display:flex;gap:8px">
        <button id="act-save-btn">Save workout</button>
        <button id="act-discard" class="danger-btn">Discard</button>
      </div>
    </div>
  `;
  slot.scrollIntoView({ behavior: "smooth", block: "nearest" });

  slot.querySelector("#act-discard").onclick = () => { slot.innerHTML = ""; };
  slot.querySelector("#act-save-btn").onclick = async () => {
    const description = ActChat.messages.filter((m) => m.role === "user").map((m) => m.content).join("; ");
    await api.saveActivity({
      date: todayStr(),
      activity_type: "workout",
      description,
      exercises: a.exercises || null,
      calories_burned: Number(slot.querySelector("#act-cal").value) || 0,
    });
    location.hash = "#/";
  };
}
