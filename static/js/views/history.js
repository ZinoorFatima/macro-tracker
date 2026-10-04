"use strict";

async function renderHistory(section) {
  section.innerHTML = `<p class="muted">Loading...</p>`;
  const end = todayStr();
  const start = shiftDate(end, -60);
  let days, weights;
  try {
    [days, weights] = await Promise.all([api.listDays(start, end), api.listWeights()]);
  } catch (err) {
    section.innerHTML = `<p class="error-text">${esc(err.message)}</p>`;
    return;
  }

  const dayRows = days.length
    ? days.map((d) => `
      <div class="item-row hist-row" data-date="${d.date}">
        <div class="item-main">
          <div class="item-title">${prettyDate(d.date)} <span class="muted" style="font-weight:400">${d.date}</span></div>
          <div class="item-sub">${d.totals.calories} / ${d.calorie_target} kcal · ${d.totals.protein_g}g P
            · ${d.meal_count} meal${d.meal_count === 1 ? "" : "s"} · ${d.activity_count} activit${d.activity_count === 1 ? "y" : "ies"}
            ${d.totals.calories_burned ? `· 🔥 ${d.totals.calories_burned} kcal` : ""}</div>
        </div>
        <div class="item-actions">
          ${d.score ? `<span class="score-badge ${scoreClass(d.score)}">${d.score}/10</span>` : ""}
        </div>
      </div>`).join("")
    : `<p class="muted">Nothing logged in the last 60 days yet.</p>`;

  section.innerHTML = `
    <h1>History</h1>
    <p class="subtitle">Your last 60 days. Click a day to view or edit it.</p>
    <div class="card">
      <h2 style="margin-top:0">Weight trend</h2>
      <div class="chart-wrap" id="weight-chart">${weightChartSVG(weights)}</div>
    </div>
    <div class="card">${dayRows}</div>
  `;

  section.querySelectorAll(".hist-row").forEach((row) => {
    row.onclick = () => { location.hash = "#/day/" + row.dataset.date; };
  });
}

function weightChartSVG(weights) {
  if (!weights || weights.length < 2) {
    return `<p class="muted">Log your weight on at least 2 days to see the trend here.</p>`;
  }
  const W = 800, H = 260;
  const padL = 46, padR = 16, padT = 16, padB = 30;
  const goal = App.profile && App.profile.target_weight_kg;

  const vals = weights.map((w) => w.weight_kg);
  let lo = Math.min(...vals, goal || Infinity);
  let hi = Math.max(...vals, goal || -Infinity);
  const span = Math.max(hi - lo, 1);
  lo -= span * 0.1; hi += span * 0.1;

  const t0 = new Date(weights[0].date).getTime();
  const t1 = new Date(weights[weights.length - 1].date).getTime();
  const x = (dateStr) => {
    const t = new Date(dateStr).getTime();
    return padL + (t1 === t0 ? 0 : ((t - t0) / (t1 - t0)) * (W - padL - padR));
  };
  const y = (v) => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);

  // recessive horizontal gridlines at ~4 steps
  const gridLines = [];
  const steps = 4;
  for (let i = 0; i <= steps; i++) {
    const v = lo + ((hi - lo) * i) / steps;
    const yy = y(v);
    gridLines.push(`<line x1="${padL}" y1="${yy}" x2="${W - padR}" y2="${yy}" stroke="var(--border)" stroke-width="1"/>
      <text x="${padL - 8}" y="${yy + 4}" text-anchor="end" font-size="11" fill="var(--text-muted)">${v.toFixed(1)}</text>`);
  }

  const points = weights.map((w) => `${x(w.date).toFixed(1)},${y(w.weight_kg).toFixed(1)}`).join(" ");
  const markers = weights.map((w) =>
    `<circle cx="${x(w.date).toFixed(1)}" cy="${y(w.weight_kg).toFixed(1)}" r="4" fill="var(--c-cal)">
       <title>${w.date}: ${w.weight_kg} kg</title>
     </circle>`).join("");

  const goalLine = goal
    ? `<line x1="${padL}" y1="${y(goal)}" x2="${W - padR}" y2="${y(goal)}" stroke="var(--c-protein)" stroke-width="2" stroke-dasharray="6 5"/>
       <text x="${W - padR}" y="${y(goal) - 6}" text-anchor="end" font-size="11" fill="var(--c-protein)">goal ${goal} kg</text>`
    : "";

  const first = weights[0], last = weights[weights.length - 1];
  const xLabels = `
    <text x="${x(first.date)}" y="${H - 8}" text-anchor="start" font-size="11" fill="var(--text-muted)">${first.date}</text>
    <text x="${x(last.date)}" y="${H - 8}" text-anchor="end" font-size="11" fill="var(--text-muted)">${last.date}</text>`;

  const lastLabel = `<text x="${x(last.date) - 8}" y="${y(last.weight_kg) - 10}" text-anchor="end"
    font-size="12" font-weight="600" fill="var(--text)">${last.weight_kg} kg</text>`;

  return `<svg class="chart-svg" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" role="img"
            aria-label="Weight trend over time">
    ${gridLines.join("")}
    ${goalLine}
    <polyline points="${points}" fill="none" stroke="var(--c-cal)" stroke-width="2"
      stroke-linejoin="round" stroke-linecap="round"/>
    ${markers}
    ${lastLabel}
    ${xLabels}
  </svg>`;
}
