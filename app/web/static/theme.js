(function () {
  "use strict";

  const storageKey = "xuanpin-theme";
  const defaultTheme = "obsidian";
  const allowedThemes = new Set(["obsidian", "ocean", "forest", "amber"]);

  function normalizeTheme(value) {
    return allowedThemes.has(value) ? value : defaultTheme;
  }

  function readTheme() {
    try {
      return normalizeTheme(window.localStorage.getItem(storageKey));
    } catch (_error) {
      return defaultTheme;
    }
  }

  function writeTheme(theme) {
    try {
      window.localStorage.setItem(storageKey, theme);
    } catch (_error) {
      // The selected theme still applies for this page when storage is unavailable.
    }
  }

  function applyTheme(theme) {
    const selectedTheme = normalizeTheme(theme);
    document.documentElement.dataset.theme = selectedTheme;
    document.querySelectorAll("[data-theme-choice]").forEach(function (button) {
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.themeChoice === selectedTheme)
      );
    });
    return selectedTheme;
  }

  applyTheme(readTheme());

  document.addEventListener("DOMContentLoaded", function () {
    applyTheme(document.documentElement.dataset.theme);
    document.querySelectorAll("[data-theme-choice]").forEach(function (button) {
      button.addEventListener("click", function () {
        const selectedTheme = applyTheme(button.dataset.themeChoice);
        writeTheme(selectedTheme);
      });
    });
  });
})();
