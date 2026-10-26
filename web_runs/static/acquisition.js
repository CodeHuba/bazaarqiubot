/* First-touch attribution for this tab; no full referring URL is transmitted. */
(() => {
  'use strict';
  const nativeFetch = window.fetch.bind(window), key = '_bz_visit_v1';
  const label = value => /^[a-z0-9_-]{1,64}$/.test(value || '') ? value : '';
  const id = () => crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2);
  const params = new URLSearchParams(location.search);
  const campaign = Object.fromEntries(['source','medium','campaign','content'].map(k => [k, label((params.get('utm_' + k) || '').toLowerCase())]));
  let fp, previous;
  try {
    fp = localStorage.getItem('_bz_fp');
    if (!fp) { fp = id(); localStorage.setItem('_bz_fp', fp); }
  } catch (_) { fp = id(); }
  try { previous = JSON.parse(sessionStorage.getItem(key)); } catch (_) { /* storage can be disabled */ }
  const now = Date.now();
  const explicit = Boolean(campaign.source);
  const signature = explicit ? JSON.stringify(campaign) : '';
  const reusable = previous && /^[A-Za-z0-9_-]{8,128}$/.test(previous.id || '') &&
    now - previous.seen >= 0 && now - previous.seen < 30 * 60 * 1000 &&
    (!explicit || previous.signature === signature);
  const visit = reusable ? previous : {id:id(), signature, campaign};
  visit.seen = now;
  try { sessionStorage.setItem(key, JSON.stringify(visit)); } catch (_) { /* tab-local fallback */ }
  let referrer = '';
  try { referrer = new URL(document.referrer).origin; } catch (_) { /* direct visit */ }
  const payload = {visit_id:visit.id, view_id:id(), path:location.pathname, referrer, ...visit.campaign};
  let ready;
  function track() {
    if (!ready) ready = nativeFetch('/api/track/visit', {
      method:'POST', headers:{'Content-Type':'application/json','X-Fingerprint':fp},
      body:JSON.stringify(payload), keepalive:true,
    }).catch(() => {});
    return ready;
  }
  // Start before app queries; attribution failures never prevent an app request.
  track();
  window.fetch = async (input, options) => {
    const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith('/api/')) return nativeFetch(input, options);
    const headers = new Headers(input instanceof Request ? input.headers : undefined);
    new Headers(options?.headers).forEach((value, name) => headers.set(name, value));
    headers.set('X-Visit-Id', visit.id);
    if (!headers.has('X-Fingerprint')) headers.set('X-Fingerprint', fp);
    if (url.pathname === '/api/track/search') {
      await Promise.race([track(), new Promise(resolve => setTimeout(resolve, 800))]);
    }
    return nativeFetch(input, {...options, headers});
  };
  window.BazaarAcquisition = Object.freeze({
    shareUrl(path, content = 'run') {
      const url = new URL(path, location.origin);
      url.searchParams.set('utm_source', 'player_share');
      url.searchParams.set('utm_medium', 'share');
      url.searchParams.set('utm_content', label(content) || 'run');
      return url.href;
    },
  });
})();
