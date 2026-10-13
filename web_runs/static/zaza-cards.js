'use strict';
window.zazaCardMarkup = function(card, skill = false) {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const name = card.name || card.zh || card.name_en || '未知卡牌';
  const tier = card.isLegendary === true ? 'legendary' : String(card.tier || '').toLowerCase();
  const quality = ['bronze','silver','gold','diamond','legendary'].includes(tier) ? tier : 'unknown';
  const labels = {bronze:'青铜',silver:'白银',gold:'黄金',diamond:'钻石',legendary:'传奇',unknown:'品质未知'};
  const size = skill ? 'skill' : String(card.size || '').toLowerCase();
  const units = ({small:1,medium:2,large:3,skill:1}[size] || 1);
  const source = card.img || card.art || '';
  const image = /^(https?:\/\/|\/[^/])/.test(source) ? source : '';
  const title = name + ' · ' + labels[quality];
  return `<span class="replay-card size-${esc(size)} tier-${quality}${card.is_core ? ' is-core' : ''}" style="--units:${units}" title="${esc(title)}" aria-label="${esc(title)}" tabindex="0">${image ? `<img src="${esc(image)}" alt="" loading="lazy">` : '<span class="card-missing">暂无图片</span>'}</span>`;
};
