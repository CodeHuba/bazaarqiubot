(() => {
  'use strict';

  const DEFAULT_MESSAGES = [
    '食人鱼正在数据池里追逐高胜率阵容…',
    '双龙正在分头核对每一条战绩…',
    '管风琴正在为统计结果调音…',
    '火药桶正在计算爆炸性的搭配…',
    '皮皮虾正在翻找最新赛季记录…',
    '灯塔正在照亮隐藏的阵容思路…',
    '风洞装置正在吹走过期数据…',
    '高级红色小圆猪正在认真清点样本…',
    '赛博铁尺正在丈量卡牌之间的默契…',
    '装甲核心正在加固这次查询结果…',
    '牌桌正在重新洗牌，请稍候…',
    '钻石正在给高价值数据抛光…'
  ];

  const SCENES = {
    runs: [
      '食人鱼正在阵容海里追踪完整构筑…',
      '灯塔正在照亮最新赛季的战绩…',
      '双龙正在逐条核对阵容记录…'
    ],
    comp: [
      '赛博铁尺正在丈量阵容之间的相似度…',
      '牌桌正在把共同核心卡归到一起…',
      '双龙正在讨论哪套构筑更有代表性…'
    ],
    winrate: [
      '管风琴正在为胜率曲线调音…',
      '钻石正在给高价值样本抛光…',
      '高级红色小圆猪正在认真清点十胜局…'
    ],
    partner: [
      '皮皮虾正在寻找最默契的搭档…',
      '火药桶正在测试哪组组合更有爆发力…',
      '装甲核心正在校验搭档样本…'
    ],
    topcard: [
      '双龙正在争论谁该站上领奖台…',
      '钻石正在给榜单上的卡牌抛光…',
      '食人鱼正在追逐本职业的高胜率卡牌…'
    ],
    trivia: [
      '灯塔正在照亮一条尘封的冷知识…',
      '牌桌正在翻开下一张故事卡…',
      '管风琴正在为这段趣闻配乐…'
    ],
    stats: [
      '高级红色小圆猪正在清点站点数据…',
      '赛博铁尺正在校准统计刻度…',
      '双龙正在交叉核对报表…'
    ]
  };

  const ROUTES = [
    [/^\/api\/runs(?:\?|$)/, 'runs'],
    [/^\/api\/comp\/card(?:\?|$)/, 'comp'],
    [/^\/api\/comp(?:\?|$)/, 'comp'],
    [/^\/api\/winrate(?:\?|$)/, 'winrate'],
    [/^\/api\/partner(?:\?|$)/, 'partner'],
    [/^\/api\/topcard(?:\?|$)/, 'topcard'],
    [/^\/api\/trivia(?:\?|$)/, 'trivia'],
    [/^\/api\/stats\/overview(?:\?|$)/, 'stats']
  ];

  let root;
  let bar;
  let copy;
  let pending = 0;
  let progress = 8;
  let progressTimer = null;
  let messageTimer = null;
  let hideTimer = null;
  let scene = 'runs';
  let messageIndex = 0;

  function ensureDom() {
    if (root) return;
    root = document.createElement('div');
    root.className = 'bz-query-progress';
    root.setAttribute('role', 'status');
    root.setAttribute('aria-live', 'polite');
    root.innerHTML = '<div class="bz-query-progress-track"><div class="bz-query-progress-bar"></div></div><div class="bz-query-progress-copy"></div>';
    document.body.appendChild(root);
    bar = root.querySelector('.bz-query-progress-bar');
    copy = root.querySelector('.bz-query-progress-copy');
  }

  function messagesFor(nextScene) {
    const specific = SCENES[nextScene] || [];
    return specific.concat(DEFAULT_MESSAGES.filter(text => !specific.includes(text)));
  }

  function setMessage() {
    const messages = messagesFor(scene);
    copy.textContent = messages[messageIndex % messages.length];
    messageIndex += 1;
  }

  function begin(nextScene) {
    ensureDom();
    pending += 1;
    if (pending > 1) return;
    scene = nextScene || 'runs';
    progress = 8;
    messageIndex = Math.floor(Math.random() * 3);
    clearTimeout(hideTimer);
    bar.style.width = progress + '%';
    setMessage();
    root.classList.add('is-active');

    progressTimer = setInterval(() => {
      const step = progress < 45 ? 4 + Math.random() * 5 : progress < 75 ? 1.5 + Math.random() * 3 : .3 + Math.random() * 1.1;
      progress = Math.min(92, progress + step);
      bar.style.width = progress.toFixed(1) + '%';
    }, 420);
    messageTimer = setInterval(setMessage, 2500);
  }

  function end() {
    if (pending > 0) pending -= 1;
    if (pending > 0 || !root) return;
    clearInterval(progressTimer);
    clearInterval(messageTimer);
    progressTimer = null;
    messageTimer = null;
    bar.style.width = '100%';
    copy.textContent = '查询完成，双龙已经把结果送到了。';
    hideTimer = setTimeout(() => {
      root.classList.remove('is-active');
      bar.style.width = '8%';
    }, 420);
  }

  function matchScene(input) {
    let url = '';
    if (typeof input === 'string') url = input;
    else if (input && typeof input.url === 'string') url = input.url;
    try {
      const parsed = new URL(url, window.location.origin);
      if (parsed.origin !== window.location.origin) return null;
      const target = parsed.pathname + parsed.search;
      for (const [pattern, routeScene] of ROUTES) {
        if (pattern.test(target)) return routeScene;
      }
    } catch (_) {}
    return null;
  }

  const nativeFetch = window.fetch.bind(window);
  window.fetch = function(input, init) {
    const matched = matchScene(input);
    if (!matched) return nativeFetch(input, init);
    begin(matched);
    return nativeFetch(input, init).finally(end);
  };

  window.BZQueryProgress = { start: begin, finish: end, scenes: SCENES };
})();
