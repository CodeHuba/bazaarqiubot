(() => {
  'use strict';

  // 本地组合文案：不调用 AI，不增加查询延迟，但可组合出大量不同句子。
  const CAST = ['食人鱼', '双龙', '管风琴', '火药桶', '皮皮虾', '灯塔', '风洞装置', '高级红色小圆猪', '赛博铁尺', '装甲核心', '牌桌', '钻石'];
  const ACTIONS = {
    runs: ['正在阵容海里追踪', '正在翻找', '正在核对', '正在整理', '正在打捞'],
    comp: ['正在丈量', '正在聚拢', '正在拆解', '正在筛选', '正在讨论'],
    winrate: ['正在调校', '正在清点', '正在筛查', '正在抛光', '正在复核'],
    partner: ['正在寻找', '正在测试', '正在撮合', '正在校验', '正在排查'],
    topcard: ['正在争夺', '正在打磨', '正在盘点', '正在筛选', '正在检阅'],
    trivia: ['正在翻开', '正在照亮', '正在挖掘', '正在整理', '正在讲述'],
    stats: ['正在清点', '正在校准', '正在交叉核对', '正在汇总', '正在整理']
  };
  const OBJECTS = {
    runs: ['完整构筑', '最新赛季战绩', '隐藏的阵容路线', '高胜率对局', '卡牌组合'],
    comp: ['共同核心', '相似阵容思路', '代表性构筑', '卡牌之间的默契', '高出场率变体'],
    winrate: ['胜率曲线', '十胜样本', '高价值数据', '卡牌表现', '统计结果'],
    partner: ['最默契的搭档', '爆发性的组合', '搭档样本', '卡牌之间的化学反应', '隐藏协同'],
    topcard: ['领奖台位置', '职业榜单', '高胜率卡牌', '卡牌排名', '本职业的明星选手'],
    trivia: ['尘封的冷知识', '下一张故事卡', '这段趣闻', '游戏里的小秘密', '一条新发现'],
    stats: ['站点数据', '统计刻度', '报表数字', '访问记录', '查询概况']
  };
  const SPECIAL_MESSAGES = {
    runs: ['食人鱼正在阵容海里捞完整构筑…', '双龙正在把最新战绩分成两队…'],
    comp: ['赛博铁尺正在给共同核心量尺寸…', '牌桌正在把相似构筑重新洗牌…'],
    winrate: ['管风琴正在为胜率曲线调音…', '高级红色小圆猪正在认真数十胜局…'],
    partner: ['皮皮虾正在给卡牌牵红线…', '火药桶正在寻找最有爆发力的搭档…'],
    topcard: ['双龙正在争论谁该站上领奖台…', '钻石正在给榜单上的卡牌抛光…'],
    trivia: ['灯塔正在照亮一条尘封的冷知识…', '管风琴正在为这段趣闻配乐…'],
    stats: ['赛博铁尺正在校准统计刻度…', '双龙正在交叉核对报表…']
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
  let activeMessages = [];

  function ensureDom() {
    if (root) return;
    root = document.createElement('div');
    root.className = 'bz-query-progress';
    root.setAttribute('role', 'status');
    root.setAttribute('aria-live', 'polite');
    root.innerHTML = '<div class="bz-query-progress-panel"><div class="bz-query-progress-kicker">正在整理战利品</div><div class="bz-query-progress-copy"></div><div class="bz-query-progress-track"><div class="bz-query-progress-bar"></div></div></div>';
    document.body.appendChild(root);
    bar = root.querySelector('.bz-query-progress-bar');
    copy = root.querySelector('.bz-query-progress-copy');
  }

  function pick(list) { return list[Math.floor(Math.random() * list.length)]; }

  function messagesFor(nextScene) {
    const actions = ACTIONS[nextScene] || ACTIONS.runs;
    const objects = OBJECTS[nextScene] || OBJECTS.runs;
    const messages = [...(SPECIAL_MESSAGES[nextScene] || [])];
    const seen = new Set(messages);
    // 每次查询启动时本地生成一批候选，避免固定顺序循环。
    for (let i = 0; i < 80; i += 1) {
      const text = `${pick(CAST)}${pick(actions)}${pick(objects)}…`;
      if (!seen.has(text)) {
        seen.add(text);
        messages.push(text);
      }
    }
    return messages;
  }

  function setMessage() {
    const messages = activeMessages.length ? activeMessages : messagesFor(scene);
    const next = messages[messageIndex % messages.length];
    messageIndex += 1;
    if (!copy.textContent) {
      copy.textContent = next;
      return;
    }
    copy.classList.add('is-swapping');
    setTimeout(() => {
      copy.textContent = next;
      copy.classList.remove('is-swapping');
    }, 180);
  }

  function begin(nextScene) {
    ensureDom();
    pending += 1;
    if (pending > 1) return;
    scene = nextScene || 'runs';
    progress = 8;
    activeMessages = messagesFor(scene);
    // 洗牌后轮换，单次查询中尽量不重复。
    activeMessages.sort(() => Math.random() - 0.5);
    messageIndex = 0;
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

  window.BZQueryProgress = { start: begin, finish: end, scenes: { actions: ACTIONS, objects: OBJECTS, specials: SPECIAL_MESSAGES } };
})();
