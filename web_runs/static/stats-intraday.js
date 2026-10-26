(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const names = {
    runs: '阵容查询', winrate: '胜率查询', partner: '最佳搭档', topcard: '职业榜单',
    routes: '阵容路线', feedback: '意见反馈', trivia: '冷知识', support: '支持',
  };
  const metrics = { uses: '核心功能使用次数', uv: '核心活跃 UV（估算）', pv: '页面 PV', api_calls: 'API 调用量' };
  const number = value => Number(value || 0).toLocaleString('zh-CN');
  const hourLabel = hour => String(hour).padStart(2, '0') + ':00';
  const shiftDate = (date, days) => {
    const value = new Date(date + 'T00:00:00Z');
    value.setUTCDate(value.getUTCDate() + days);
    return value.toISOString().slice(0, 10);
  };
  const beijingToday = () => new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Shanghai' });
  let today = beijingToday(), data = null, chart = null, requestId = 0, controller = null;
  let followToday = true;

  function updateDateBounds() {
    $('intraday-date').min = shiftDate(today, -89);
    $('intraday-date').max = today;
    $('intraday-today').classList.toggle('active', $('intraday-date').value === today);
    $('intraday-yesterday').classList.toggle('active', $('intraday-date').value === shiftDate(today, -1));
  }

  function populateModules() {
    const chosen = $('intraday-module').value;
    $('intraday-module').replaceChildren(new Option('全部模块', 'all'));
    for (const module of new Set([...Object.keys(names), ...(data?.modules || [])])) {
      if (module !== 'all') $('intraday-module').add(new Option(names[module] || module || '未标注模块', module));
    }
    if ([...$('intraday-module').options].some(option => option.value === chosen)) {
      $('intraday-module').value = chosen;
    }
  }

  function scope(period, module) {
    if (module === 'all') return period.all;
    return period.modules[module] || {
      summary: { uses: 0, uv: 0 },
      hours: period.all.hours.map(hour => ({ ...hour, uses: 0, uv: 0, successes: 0, empty_results: 0, errors: 0 })),
    };
  }

  function clearView() {
    if (chart) chart.destroy();
    chart = null;
    $('intraday-hourly-body').replaceChildren();
    for (const id of ['total', 'change', 'previous-total', 'peak', 'peak-note']) $('intraday-' + id).textContent = '—';
    $('intraday-total-note').textContent = '北京时间';
    $('intraday-chart-wrap').innerHTML = '<canvas id="intraday-chart" role="img" aria-label="单日逐小时使用趋势，数据详见下方逐小时明细"></canvas>';
  }

  function render() {
    const metric = $('intraday-metric').value;
    const core = metric === 'uses' || metric === 'uv';
    $('intraday-module').disabled = !core;
    $('intraday-module').title = core ? '' : '页面 PV 和 API 调用量展示全站数据';
    if (!data) return;
    const module = core ? $('intraday-module').value : 'all';
    const source = core ? 'core' : metric === 'pv' ? 'pv' : 'api';
    const current = scope(data.current, module), previous = scope(data.previous, module);
    const available = data.current.available[source], previousAvailable = data.previous.available[source];
    const compare = $('intraday-compare').checked;
    const total = current.summary[metric], previousTotal = previous.summary[metric];
    const display = (hour, period, key = metric) => (
      hour.state === 'future' || !period.available[source] ? '—' : number(hour[key])
    );
    $('intraday-total-label').textContent = metrics[metric] + ' · 当日累计';
    $('intraday-total').textContent = available ? number(total) : '—';
    $('intraday-total-note').textContent = data.date + (data.is_today ? ' 截至 ' + data.cutoff_time : ' 全天') + '（北京时间）';
    $('intraday-change').textContent = !compare ? '未开启对比'
      : !available || !previousAvailable ? '暂无可比数据'
      : previousTotal === 0 ? (total > 0 ? '前一天为 0' : '持平')
      : ((total - previousTotal) / previousTotal * 100 >= 0 ? '+' : '')
        + ((total - previousTotal) / previousTotal * 100).toFixed(1) + '%';
    $('intraday-previous-total').textContent = compare && previousAvailable
      ? data.previous_date + ' 同期：' + number(previousTotal) : '—';
    const observed = current.hours.filter(hour => hour.state !== 'future');
    const peak = observed.reduce((best, hour) => !best || hour[metric] > best[metric] ? hour : best, null);
    $('intraday-peak').textContent = !available ? '—' : !peak || !peak[metric] ? '暂无使用'
      : hourLabel(peak.hour) + '–' + hourLabel(peak.hour + 1);
    $('intraday-peak-note').textContent = available && peak && peak[metric]
      ? number(peak[metric]) + (peak.state === 'partial' ? ' · 进行中' : '') : '—';

    const updated = new Date(data.generated_at).toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
    $('intraday-status').textContent = (available ? '' : '此日期无采集记录。')
      + (compare && !previousAvailable ? '前一天无可比采集记录。' : '')
      + '数据更新于 ' + updated + '（北京时间）'
      + (data.is_today ? '；前一天按相同时间进度截取。' : '；按全天统计。')
      + (!core ? '当前指标展示全站数据。' : '');
    $('intraday-table-metric').textContent = metrics[metric];
    $('intraday-hourly-body').innerHTML = current.hours.map((hour, index) => {
      const coreDetail = key => core ? display(hour, data.current, key) : '—';
      return `<tr><td>${hourLabel(hour.hour)}–${hourLabel(hour.hour + 1)}${hour.state === 'partial' ? '（进行中）' : ''}</td>`
        + `<td>${display(hour, data.current)}</td><td>${compare ? display(previous.hours[index], data.previous) : '—'}</td>`
        + `<td>${coreDetail('uv')}</td><td>${coreDetail('successes')}</td><td>${coreDetail('empty_results')}</td><td>${coreDetail('errors')}</td></tr>`;
    }).join('');

    if (chart) chart.destroy();
    chart = null;
    $('intraday-chart-wrap').innerHTML = '<canvas id="intraday-chart" role="img" aria-label="单日逐小时使用趋势，数据详见下方逐小时明细"></canvas>';
    try {
      if (typeof window.Chart !== 'function') throw new Error('图表组件暂不可用');
      const values = (hours, enabled) => hours.map(hour => enabled && hour.state !== 'future' ? hour[metric] : null);
      const datasets = [{ label: data.date, data: values(current.hours, available), backgroundColor: '#f59e0b88', borderColor: '#f59e0b', borderWidth: 1 }];
      if (compare) datasets.push({ label: data.previous_date + ' 同期', type: 'line', data: values(previous.hours, previousAvailable),
        borderColor: '#60a5fa', backgroundColor: 'transparent', borderDash: [6, 4], borderWidth: 2, pointRadius: 2, spanGaps: false });
      chart = new window.Chart($('intraday-chart'), {
        type: 'bar', data: { labels: current.hours.map(hour => hourLabel(hour.hour)), datasets },
        options: {
          responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
          plugins: {
            legend: { labels: { color: '#9ca3af', font: { size: 11 } } },
            tooltip: { callbacks: {
              title: items => {
                const hour = items[0]?.dataIndex;
                return hour == null ? '' : hourLabel(hour) + '–' + hourLabel(hour + 1) + (current.hours[hour].state === 'partial' ? '（进行中）' : '');
              },
              label: context => {
                const hour = (context.datasetIndex === 0 ? current : previous).hours[context.dataIndex];
                const detail = core ? ` · UV ${number(hour.uv)} · 成功 ${number(hour.successes)} / 空结果 ${number(hour.empty_results)} / 错误 ${number(hour.errors)}` : '';
                return context.dataset.label + '：' + number(context.parsed.y) + detail;
              },
            } },
          },
          scales: {
            x: { ticks: { color: '#9ca3af', maxRotation: 0, font: { size: 10 } }, grid: { display: false } },
            y: { beginAtZero: true, ticks: { color: '#9ca3af', precision: 0 }, grid: { color: '#1f2937' } },
          },
        },
      });
    } catch (error) {
      console.warn('日内统计图表加载失败:', error);
      $('intraday-chart-wrap').innerHTML = '<div class="loading" role="status">图表暂不可用，请查看下方逐小时明细</div>';
      $('intraday-details').open = true;
    }
  }

  async function load() {
    const id = ++requestId;
    if (controller) controller.abort();
    controller = new AbortController();
    const selected = $('intraday-date').value;
    if (data && data.date !== selected) { data = null; clearView(); }
    updateDateBounds();
    $('intraday-refresh').disabled = true;
    $('intraday-status').textContent = '正在加载 ' + selected + ' 的日内统计…';
    try {
      const response = await fetch('/api/stats/intraday?date=' + encodeURIComponent(selected), { signal: controller.signal });
      const payload = await response.json();
      if (id !== requestId) return;
      if (!response.ok) throw new Error(payload.error || '请求失败');
      data = payload;
      today = data.today;
      updateDateBounds();
      populateModules();
      render();
    } catch (error) {
      if (id !== requestId || error.name === 'AbortError') return;
      $('intraday-status').textContent = '日内统计加载失败：' + error.message + (data ? '；保留上次数据。' : '');
    } finally {
      if (id === requestId) $('intraday-refresh').disabled = false;
    }
  }

  function chooseDate(date) {
    $('intraday-date').value = date;
    followToday = date === today;
    load();
  }

  $('intraday-date').value = today;
  populateModules();
  updateDateBounds();
  $('intraday-today').addEventListener('click', () => { today = beijingToday(); chooseDate(today); });
  $('intraday-yesterday').addEventListener('click', () => { today = beijingToday(); chooseDate(shiftDate(today, -1)); });
  $('intraday-date').addEventListener('change', () => { followToday = $('intraday-date').value === today; load(); });
  $('intraday-refresh').addEventListener('click', () => {
    today = beijingToday();
    if (followToday) $('intraday-date').value = today;
    load();
  });
  for (const id of ['intraday-metric', 'intraday-module', 'intraday-compare']) $(id).addEventListener('change', render);
  setInterval(() => {
    if (document.hidden) return;
    today = beijingToday();
    if (followToday) $('intraday-date').value = today;
    if ($('intraday-date').value === today) load();
    else updateDateBounds();
  }, 300000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && followToday) { today = beijingToday(); chooseDate(today); }
  });
  window.StatsIntraday = { refresh: load };
  load();
})();
