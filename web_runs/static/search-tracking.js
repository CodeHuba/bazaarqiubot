/* Explicit manual searches only. Analytics must never change the query response. */
(() => {
  'use strict';
  let temporaryVisitor;
  function identifier() {
    return globalThis.crypto?.randomUUID?.() || 'search_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
  }
  function visitor() {
    try {
      let value = localStorage.getItem('_bz_fp');
      if (!value) { value = identifier(); localStorage.setItem('_bz_fp', value); }
      return value;
    } catch (_) { return temporaryVisitor ||= identifier(); }
  }
  async function report(payload, fingerprint, retry = true) {
    try {
      const response = await fetch('/api/track/search', {
        method:'POST', headers:new Headers({'Content-Type':'application/json', 'X-Fingerprint':fingerprint}),
        body:JSON.stringify(payload), keepalive:true
      });
      if (response.status === 400 && payload.receipt) {
        // An old browser-cache entry may carry an expired receipt. Keep the action,
        // but never infer success/empty without a valid backend result.
        const unsigned = {...payload, receipt:null, fallback:'unverified'};
        await report(unsigned, fingerprint, false);
      } else if (response.status >= 500 && retry) {
        setTimeout(() => report(payload, fingerprint, false), 1000);
      }
    } catch (_) {
      if (retry) setTimeout(() => report(payload, fingerprint, false), 1000);
    }
  }
  async function query(feature, url, options = {}, countSearch = true) {
    if (!countSearch) return fetch(url, options);
    const fingerprint = visitor();
    const target = new URL(url, location.href);
    const payload = {id:identifier(), feature, params:Object.fromEntries(target.searchParams), source:'unknown'};
    const headers = new Headers(options.headers || {});
    headers.set('X-Search-Action', 'user');
    headers.set('X-Fingerprint', fingerprint);
    const started = performance.now();
    try {
      const response = await fetch(url, {...options, headers});
      payload.receipt = response.headers.get('X-Search-Receipt');
      payload.fallback = response.status >= 500 ? 'error' : 'unverified';
      // Invalid local/server inputs and throttled attempts are not legal submissions.
      if (![400, 429].includes(response.status)) {
        // Resource timing is populated after the body finishes; don't delay the UI.
        void response.clone().arrayBuffer().then(() => {
          payload.elapsed_ms = performance.now() - started;
          try {
            const resource = performance.getEntriesByName?.(target.href).filter(row => row.startTime >= started - 1).at(-1);
            if (resource) payload.source = resource.transferSize === 0 && resource.decodedBodySize > 0 ? 'browser_cache' : 'network';
          } catch (_) { /* Timing is optional; preserve the verified result. */ }
          return report(payload, fingerprint);
        }).catch(error => {
          payload.elapsed_ms = performance.now() - started;
          if (error.name !== 'AbortError') void report({...payload,receipt:null,fallback:'error'},fingerprint);
        });
      }
      return response;
    } catch (error) {
      payload.elapsed_ms = performance.now() - started;
      if (error.name !== 'AbortError') void report({...payload, fallback:'error'}, fingerprint);
      throw error;
    }
  }
  window.BazaarSearch = {query};
})();
