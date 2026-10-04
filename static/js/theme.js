"use strict";

/* Theme toggle.
 *
 * Three states, not two: with nothing stored the page follows the OS
 * preference, and clicking the toggle pins an explicit choice. The initial
 * class is applied by an inline script in index.html so a light-theme user
 * never sees a flash of dark before this file loads.
 */

const THEME_KEY = "macro-tracker-theme";

function storedTheme() {
  try {
    return localStorage.getItem(THEME_KEY);
  } catch (e) {
    // Private browsing can throw on access rather than returning null.
    return null;
  }
}

function systemPrefersDark() {
  return !window.matchMedia || !window.matchMedia("(prefers-color-scheme: light)").matches;
}

function activeTheme() {
  return storedTheme() || (systemPrefersDark() ? "dark" : "light");
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch (e) { /* nothing to do: the choice just won't survive a reload */ }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === "light" ? "#f5f7fa" : "#0e1216");
  updateToggle(theme);
}

function updateToggle(theme) {
  const button = document.getElementById("theme-toggle");
  if (!button) return;
  const goingTo = theme === "light" ? "dark" : "light";
  button.textContent = theme === "light" ? "☾" : "☀";
  button.title = `Switch to ${goingTo} theme`;
  button.setAttribute("aria-label", `Switch to ${goingTo} theme`);
}

function initTheme() {
  updateToggle(activeTheme());
  const button = document.getElementById("theme-toggle");
  if (button) {
    button.addEventListener("click", () => {
      applyTheme(activeTheme() === "light" ? "dark" : "light");
    });
  }
  // Follow the OS while the user has not pinned a choice.
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
      if (!storedTheme()) updateToggle(activeTheme());
    });
  }
}
