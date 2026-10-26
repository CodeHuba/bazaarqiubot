/* Ten-slot placement. Keep other items in order and move only as much as needed. */
(function (root) {
  'use strict';
  function planPlacement(items, movingUid, width, requestedStart) {
    if (!Number.isInteger(width) || width < 1 || width > 3 || !Number.isFinite(requestedStart)) return null;
    const others = items.filter(item => item.uid !== movingUid).sort((a,b) => a.start-b.start);
    if (others.reduce((sum,item) => sum+item.width, width) > 10) return null;
    const target = Math.max(0, Math.min(10-width, Math.round(requestedStart)));
    let best = null, bestScore = null;
    for (let start = 0; start <= 10-width; start++) {
      for (let split = 0; split <= others.length; split++) {
        const positions = new Map();
        let edge = start, valid = true;
        for (let i = split-1; i >= 0; i--) {
          const item = others[i], position = Math.min(item.start, edge-item.width);
          if (position < 0) { valid = false; break; }
          positions.set(item.uid, position); edge = position;
        }
        if (!valid) continue;
        edge = start+width;
        for (let i = split; i < others.length; i++) {
          const item = others[i], position = Math.max(item.start, edge);
          if (position+item.width > 10) { valid = false; break; }
          positions.set(item.uid, position); edge = position+item.width;
        }
        if (!valid) continue;
        let distance = 0, shifted = 0, leftward = 0;
        for (const item of others) {
          const delta = positions.get(item.uid)-item.start;
          distance += Math.abs(delta); shifted += Number(delta !== 0); leftward += Math.max(0,-delta);
        }
        const score = [Math.abs(start-target),distance,shifted,leftward];
        const better = !bestScore || score.some((value,i) => value < bestScore[i] && score.slice(0,i).every((v,j) => v === bestScore[j]));
        if (better) {
          bestScore = score;
          best = {start,positions:others.map(item => ({uid:item.uid,start:positions.get(item.uid)})),shifted};
        }
      }
    }
    return best;
  }
  if (typeof module === 'object' && module.exports) module.exports = {planPlacement};
  else root.BazaarBoard = {planPlacement};
})(typeof window === 'undefined' ? this : window);
