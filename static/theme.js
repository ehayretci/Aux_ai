// Shared theme controller for the AUX archive + canvas pages.
// Persists in localStorage and posts to the server so the Chrome
// extension popup can read the same value.
(function () {
  const KEY = 'aux-theme';
  const SERVER_PATH = '/api/theme';

  function currentTheme() {
    return document.documentElement.dataset.theme || 'dark';
  }

  function applyTheme(t) {
    const theme = t === 'light' ? 'light' : 'dark';
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem(KEY, theme); } catch (_) {}
    // Swap any brand logos that declare both variants
    document.querySelectorAll('img[data-logo-dark][data-logo-light]').forEach((img) => {
      img.src = theme === 'light'
        ? img.getAttribute('data-logo-light')
        : img.getAttribute('data-logo-dark');
    });
    // Update toggle icons (rotate the moon/sun glyph)
    document.querySelectorAll('[data-theme-toggle]').forEach((btn) => {
      btn.setAttribute('aria-label', theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode');
      const moon = btn.querySelector('.theme-ico-moon');
      const sun  = btn.querySelector('.theme-ico-sun');
      if (moon && sun) {
        moon.style.display = theme === 'light' ? 'none' : 'inline-flex';
        sun.style.display  = theme === 'light' ? 'inline-flex' : 'none';
      }
    });
    // Notify server so the extension popup picks it up.
    fetch(SERVER_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ theme }),
    }).catch(() => {});
  }

  function toggleTheme() {
    applyTheme(currentTheme() === 'light' ? 'dark' : 'light');
  }

  // Boot: prefer server-persisted value, fall back to localStorage, default dark.
  async function init() {
    let theme = null;
    try {
      const r = await fetch(SERVER_PATH, { cache: 'no-store' });
      if (r.ok) theme = (await r.json()).theme;
    } catch (_) {}
    if (!theme) {
      try { theme = localStorage.getItem(KEY); } catch (_) {}
    }
    applyTheme(theme || 'dark');
    document.querySelectorAll('[data-theme-toggle]').forEach((btn) => {
      btn.addEventListener('click', toggleTheme);
    });
  }

  // Apply immediately from localStorage to avoid a flash, then hydrate from server.
  try {
    const cached = localStorage.getItem(KEY);
    if (cached) document.documentElement.dataset.theme = cached;
  } catch (_) {}

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.AuxTheme = { apply: applyTheme, toggle: toggleTheme, current: currentTheme };
})();
