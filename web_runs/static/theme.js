/* Read the saved theme before the first paint; keep the existing dark default. */
(function () {
  'use strict';
  const key = 'bazaarqiubot-theme';
  const root = document.documentElement;
  const valid = value => value === 'light' || value === 'dark';
  let theme = 'dark';
  try {
    const saved = localStorage.getItem(key);
    if (valid(saved)) theme = saved;
  } catch (_) { /* Private browsing may block storage; switching still works. */ }

  // Canvas text cannot inherit CSS colours. Keep each chart's dark palette.
  const chartPalettes = new WeakMap();
  function chartPalette(chart) {
    const options = chart.config.options;
    if (!chartPalettes.has(chart)) {
      const colours = [];
      const remember = (path, target, property, light) => {
        if (target) colours.push({ path, property, dark: target[property], light });
      };
      remember(['plugins', 'legend', 'labels'], options.plugins?.legend?.labels, 'color', '#536879');
      for (const [id, scale] of Object.entries(options.scales || {})) {
        remember(['scales', id, 'ticks'], scale.ticks, 'color', '#536879');
        remember(['scales', id, 'grid'], scale.grid, 'color', '#dce7ed');
      }
      chartPalettes.set(chart, colours);
    }
    for (const colour of chartPalettes.get(chart)) {
      const target = colour.path.reduce((value, key) => value?.[key], options);
      if (target) target[colour.property] = theme === 'light' ? colour.light : colour.dark;
    }
  }

  function apply(value) {
    theme = value;
    root.dataset.theme = value;
    root.style.colorScheme = value;
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.themeChoice === value));
    });
    let meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) {
      meta = document.createElement('meta');
      meta.name = 'theme-color';
      document.head.appendChild(meta);
    }
    meta.content = value === 'light' ? '#f5fafc' : '#090a2a';
    if (window.Chart?.instances) {
      Object.values(window.Chart.instances).forEach(chart => chart.update('none'));
    }
    window.dispatchEvent(new CustomEvent('bazaar-theme-change', { detail: { theme: value } }));
  }
  apply(theme);

  function mount() {
    if (document.querySelector('.theme-switch')) return;
    if (window.Chart?.register) window.Chart.register({ id: 'bazaarTheme', beforeUpdate: chartPalette });
    const group = document.createElement('div');
    group.className = 'theme-switch';
    group.setAttribute('role', 'group');
    group.setAttribute('aria-label', '外观主题');
    const icons = {
      light: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
      dark: '<path d="M20.9 13A9 9 0 0 1 11 3.1 9 9 0 1 0 20.9 13Z"/>'
    };
    ['light', 'dark'].forEach(value => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'theme-choice';
      button.dataset.themeChoice = value;
      button.title = button.ariaLabel = value === 'light' ? '明亮主题' : '暗色主题';
      button.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + icons[value] + '</svg>';
      button.addEventListener('click', () => {
        apply(value);
        try { localStorage.setItem(key, value); } catch (_) { /* Optional persistence. */ }
      });
      group.appendChild(button);
    });
    const navbar = document.querySelector('.navbar');
    if (navbar) {
      navbar.classList.add('has-theme-switch');
      navbar.appendChild(group);
    } else {
      group.classList.add('theme-switch-floating');
      document.body.appendChild(group);
    }
    apply(theme);
  }
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) apply(valid(event.newValue) ? event.newValue : 'dark');
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true });
  else mount();
})();
