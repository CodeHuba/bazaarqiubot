(function () {
  function renderMarkdown(text) {
    if (window.marked) return window.marked.parse(text);
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n/g, '<br>');
  }

  function getTodayKey(id) {
    const d = new Date();
    const dateStr = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    return 'announcement_dismissed_' + id + '_' + dateStr;
  }

  function isDismissedToday(id) {
    return localStorage.getItem(getTodayKey(id)) === '1';
  }

  function dismissToday(id) {
    localStorage.setItem(getTodayKey(id), '1');
  }

  function showAnnouncement(ann) {
    if (isDismissedToday(ann.id)) return;

    // 注入 marked.js（如果页面没有）
    function doShow() {
      const overlay = document.createElement('div');
      overlay.id = 'announcement-overlay';
      overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.72);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px;animation:annFadeIn .2s ease';

      overlay.innerHTML = `
        <style>
          @keyframes annFadeIn{from{opacity:0}to{opacity:1}}
          @keyframes annSlideUp{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}
          #announcement-box{animation:annSlideUp .25s ease}
          #announcement-body h1,#announcement-body h2,#announcement-body h3{color:var(--theme-text, #f3f4f6);margin:.8em 0 .4em}
          #announcement-body p{margin:.5em 0;line-height:1.7;color:var(--theme-muted, #d1d5db)}
          #announcement-body a{color:var(--theme-link, #60a5fa);text-decoration:underline}
          #announcement-body ul,#announcement-body ol{padding-left:1.4em;color:var(--theme-muted, #d1d5db)}
          #announcement-body li{margin:.3em 0}
          #announcement-body code{background:var(--theme-border, #1f2937);padding:2px 6px;border-radius:4px;font-size:.9em;color:var(--theme-gold, #f59e0b)}
          #announcement-body blockquote{border-left:3px solid var(--theme-border-strong, #374151);margin:.5em 0;padding:.4em 1em;color:var(--theme-muted, #9ca3af)}
          #announcement-body strong{color:var(--theme-text, #f9fafb)}
        </style>
        <div id="announcement-box" style="background:var(--theme-surface, #111827);border:1px solid var(--theme-border-strong, #374151);border-radius:12px;max-width:560px;width:100%;max-height:80vh;display:flex;flex-direction:column;box-shadow:0 20px 60px rgba(0,0,0,0.6)">
          <div style="padding:18px 22px 14px;border-bottom:1px solid var(--theme-border, #1f2937);display:flex;align-items:center;justify-content:space-between;flex-shrink:0">
            <div style="display:flex;align-items:center;gap:10px">
              <span style="font-size:20px">📢</span>
              <span style="font-size:16px;font-weight:700;color:var(--theme-text, #f9fafb)">${ann.title.replace(/</g,'&lt;').replace(/>/g,'&gt;')}</span>
            </div>
            <button id="ann-close-btn" style="background:none;border:none;color:var(--theme-dim, #6b7280);font-size:22px;cursor:pointer;line-height:1;padding:0 2px" aria-label="关闭">×</button>
          </div>
          <div id="announcement-body" style="padding:18px 22px;overflow-y:auto;flex:1;font-size:14px">
            ${renderMarkdown(ann.content)}
          </div>
          <div style="padding:12px 22px 16px;border-top:1px solid var(--theme-border, #1f2937);display:flex;align-items:center;justify-content:flex-end;gap:10px;flex-shrink:0">
            <button id="ann-dismiss-btn" style="padding:7px 18px;background:transparent;color:var(--theme-dim, #6b7280);border:1px solid var(--theme-border-strong, #374151);border-radius:6px;font-size:13px;cursor:pointer">今日不再显示</button>
            <button id="ann-confirm-btn" style="padding:7px 22px;background:var(--theme-gold, #f59e0b);color:var(--theme-bg, #0a0e1a);border:none;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer">我知道了</button>
          </div>
        </div>`;

      document.body.appendChild(overlay);

      function close(dismiss) {
        if (dismiss) dismissToday(ann.id);
        overlay.remove();
      }

      document.getElementById('ann-close-btn').addEventListener('click', () => close(false));
      document.getElementById('ann-confirm-btn').addEventListener('click', () => close(false));
      document.getElementById('ann-dismiss-btn').addEventListener('click', () => close(true));
      overlay.addEventListener('click', function (e) {
        if (e.target === overlay) close(false);
      });
    }

    if (!window.marked) {
      var s = document.createElement('script');
      s.src = 'https://cdn.jsdelivr.net/npm/marked@9/marked.min.js';
      s.onload = doShow;
      s.onerror = doShow; // fallback 也展示
      document.head.appendChild(s);
    } else {
      doShow();
    }
  }

  function init() {
    fetch('/api/announcement/latest')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.announcement) showAnnouncement(data.announcement);
      })
      .catch(function () { /* 静默失败 */ });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
