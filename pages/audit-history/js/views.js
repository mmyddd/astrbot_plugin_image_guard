/* ============================================================
   ImageGuard · 视图层 v2
   概览 / 记录卡片流 / 抽屉 / 图表 / 状态
   ============================================================ */
(function () {
  'use strict';

  const IG = window.IG;
  const $ = IG.$;
  const $$ = IG.$$;
  const esc = IG.esc;
  const format = IG.format;
  const state = IG.state;
  const derive = IG.derive;

  const PROVIDER_TOP_N = 8;

  const ICON_IMAGE = '<svg class="thumb-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="4.5" width="17" height="15" rx="2.5"></rect><circle cx="9" cy="10" r="1.6"></circle><path d="M4.5 17l4.6-4.2 3.2 2.8 3.1-3 4.1 4"></path></svg>';

  /* ── 状态占位 ─────────────────────────────────────────── */
  function stateMarkup(kind, title, message) {
    const mark = kind === 'error' ? '!' : kind === 'loading' ? '···' : '∅';
    return '<div class="state">' +
      '<span class="state-mark" data-state="' + kind + '" aria-hidden="true">' + mark + '</span>' +
      '<strong>' + esc(title) + '</strong>' +
      '<p>' + esc(message) + '</p>' +
      (kind === 'error' ? '<button class="btn btn-sm btn-tonal" type="button" data-retry>重新加载</button>' : '') +
      '</div>';
  }

  function fillList(host, kind, title, message) {
    if (!host) return;
    host.innerHTML = stateMarkup(kind, title, message);
    const retry = host.querySelector('[data-retry]');
    if (retry) retry.addEventListener('click', () => IG.app.reloadRecords());
  }

  /* ── 处理中任务 ───────────────────────────────────────── */
  function renderPending() {
    const strip = $('#pending-strip');
    const list = $('#pending-list');
    if (!strip || !list) return;
    const items = derive.pendingItems();
    const count = items.reduce((sum, item) => sum + (Number(item.image_count) || 1), 0);

    if (!items.length) {
      if (!strip.hasAttribute('hidden')) {
        strip.setAttribute('hidden', '');
        list.innerHTML = '';
      }
      state.pendingSince = 0;
      return;
    }

    strip.removeAttribute('hidden');
    strip.dataset.collapsed = state.pendingCollapsed ? '1' : '0';
    const toggle = $('#pending-toggle');
    if (toggle) {
      toggle.textContent = state.pendingCollapsed ? '展开' : '收起';
      toggle.setAttribute('aria-expanded', state.pendingCollapsed ? 'false' : 'true');
    }
    const title = $('#pending-count');
    if (title) {
      title.textContent = format.int(items.length) + ' 个任务 · ' + format.int(count) + ' 张图片正在审核';
    }

    if (state.pendingCollapsed) {
      list.innerHTML = '';
      return;
    }

    list.innerHTML = items.map(item => {
      const who = item.user_name || item.user_id || '未知用户';
      const source = item.group_id ? '群 ' + item.group_id : '私聊';
      const thumb = item.preview
        ? '<span class="pending-thumb"><img alt="待审图片预览" src="' + esc(item.preview) + '"></span>'
        : '<span class="pending-thumb"><span class="pending-thumb-empty"></span></span>';
      return '<article class="pending-card" data-pending="' + esc(item.id || '') + '">' +
        thumb +
        '<div class="pending-main">' +
        '<strong>' + esc(who) + ' · ' + esc(source) + '</strong>' +
        '<div class="pending-meta"><span>' + format.int(item.image_count || 1) + ' 张图片</span>' +
        '<span>已用时 ' + esc(format.elapsed(item.elapsed_seconds)) + '</span>' +
        '<span>开始于 ' + esc(format.time(new Date(Date.now() - (Number(item.elapsed_seconds) || 0) * 1000))) + '</span></div>' +
        '</div>' +
        '<span class="pending-badge">审核中</span>' +
        '</article>';
    }).join('');
    state.pendingSince = Date.now();
  }

  /* ── KPI ──────────────────────────────────────────────── */
  function renderStats() {
    const records = state.records;
    const total = records.length;
    let recalled = 0;
    let banned = 0;
    let success = 0;
    let latest = null;
    let latestAt = 0;
    records.forEach(record => {
      if (record.recalled) recalled += 1;
      if (record.banned) banned += 1;
      if (derive.disposeStatus(record) === 'success') success += 1;
      const parsed = format.parseTime(record.time);
      const stamp = parsed ? parsed.getTime() : 0;
      if (stamp >= latestAt) { latestAt = stamp; latest = record; }
    });

    const cache = state.cacheStats || {};
    const unique = Number(cache.total_unique_images) || 0;
    const skipped = Number(cache.images_skip_audit) || 0;
    const provider = derive.providerEntries();
    const cacheEnabled = Number(cache.threshold) > 0;

    const setText = (id, text) => {
      const node = document.getElementById(id);
      if (node) node.textContent = text;
    };
    const setBar = (id, percent) => {
      const node = document.getElementById(id);
      if (node) node.style.width = Math.max(0, Math.min(100, percent)) + '%';
    };

    setText('stat-total', format.int(total));
    setText('stat-total-foot', latest ? '最近一次 ' + format.relative(latest.time) : '暂无违规记录');
    setBar('stat-total-bar', total > 0 ? 100 : 0);

    if (total > 0) {
      const rate = Math.round(success / total * 100);
      setText('stat-dispose', rate + '%');
      setText('stat-dispose-foot', '撤回 ' + format.int(recalled) + ' · 禁言 ' + format.int(banned));
      setBar('stat-dispose-bar', rate);
    } else {
      setText('stat-dispose', '—');
      setText('stat-dispose-foot', '撤回与禁言执行情况');
      setBar('stat-dispose-bar', 0);
    }

    setText('stat-cache', format.int(skipped));
    setText('stat-cache-foot', cacheEnabled
      ? '唯一图片 ' + format.int(unique) + ' 张 · 阈值 ' + format.int(cache.threshold) + ' 次'
      : '审核缓存已关闭');
    setBar('stat-cache-bar', unique > 0 ? Math.round(skipped / unique * 100) : 0);

    setText('stat-calls', format.int(provider.total));
    if (provider.entries.length) {
      const top = provider.entries[0];
      const share = provider.total > 0 ? Math.round(top.count / provider.total * 100) : 0;
      setText('stat-calls-foot', '主力 ' + top.name + ' · ' + share + '%');
      setBar('stat-calls-bar', share);
    } else {
      setText('stat-calls-foot', '尚未记录调用');
      setBar('stat-calls-bar', 0);
    }

    // ── 审核漏斗：送审了多少、跳过了多少、多少被判安全丢弃 ──
    const funnel = derive.auditFunnel();
    if (funnel.hasData) {
      setText('stat-audited', format.int(funnel.audited));
      setText('stat-audited-foot',
        '送审 ' + format.int(funnel.auditTotal) +
        ' · 跳过 ' + format.int(funnel.skipped) +
        (funnel.noRules ? ' · 无规则 ' + format.int(funnel.noRules) : ''));
      const skipRate = funnel.audited > 0 ? Math.round(funnel.skipped / funnel.audited * 100) : 0;
      setBar('stat-audited-bar', skipRate);
      setText('stat-safe', format.int(funnel.safe));
      setText('stat-safe-foot', funnel.auditTotal > 0
        ? '占送审 ' + format.percent(funnel.safe / funnel.auditTotal * 100) + ' · 未写入历史'
        : '未写入历史记录');
      setBar('stat-safe-bar', funnel.auditTotal > 0
        ? Math.round(funnel.safe / funnel.auditTotal * 100) : 0);
    } else {
      setText('stat-audited', format.int(funnel.audited));
      setText('stat-audited-foot', '尚无审核统计');
      setBar('stat-audited-bar', 0);
      setText('stat-safe', format.int(funnel.safe));
      setText('stat-safe-foot', '未写入历史记录');
      setBar('stat-safe-bar', 0);
    }

    renderPending();

    const navCount = $('#nav-record-count');
    if (navCount) navCount.textContent = state.totalRecords ? format.int(state.totalRecords) : '0';
  }

  /* ── 趋势图 ───────────────────────────────────────────── */
  function renderTrend() {
    const chart = $('#trend-chart');
    if (!chart) return;
    const series = derive.dailySeries(state.trendRange);
    const max = series.reduce((acc, item) => Math.max(acc, item.value), 0);
    chart.style.setProperty('--cols', String(series.length));
    chart.innerHTML = series.map(item => {
      const height = max > 0 ? Math.max(3, Math.round(item.value / max * 100)) : 3;
      return '<div class="chart-col" data-empty="' + (item.value ? '0' : '1') + '">' +
        '<div class="chart-bar-wrap">' +
        '<div class="chart-bar" style="height:' + height + '%"><span class="chart-tip">' +
        esc(item.label) + ' · ' + format.int(item.value) + ' 条</span></div></div>' +
        '<span class="chart-axis">' + esc(item.label) + '</span></div>';
    }).join('');
    chart.setAttribute('aria-label', '最近 ' + state.trendRange + ' 天违规趋势，峰值 ' + format.int(max) + ' 条');
  }

  function renderTrendRange() {
    $$('#trend-range .seg-btn').forEach(button => {
      const active = Number(button.dataset.range) === state.trendRange;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  }

  /* ── 供应商统计 ───────────────────────────────────────── */
  function renderProviderStats() {
    const container = $('#provider-stats');
    const more = $('#provider-more');
    if (!container) return;
    if (more) more.setAttribute('hidden', '');
    const data = derive.providerEntries(PROVIDER_TOP_N);
    if (!data.entries.length) {
      container.innerHTML = '<div class="state state-inline">' +
        '<span class="state-mark" aria-hidden="true">∅</span>' +
        '<strong>暂无调用记录</strong><p>审核成功一次后，这里会显示各供应商的命中占比。</p></div>';
      return;
    }
    container.innerHTML = data.entries.map((entry, index) => {
      const percent = data.total > 0 ? Math.round(entry.count / data.total * 100) : 0;
      return '<div class="provider-row">' +
        '<div class="provider-name"><span class="provider-rank">' + (index + 1) + '</span>' +
        '<span title="' + esc(entry.name) + '">' + esc(entry.name) + '</span></div>' +
        '<strong class="provider-count">' + format.int(entry.count) + '</strong>' +
        '<div class="provider-sub"><span class="provider-track"><i class="provider-fill" style="width:' + percent + '%"></i></span>' +
        '<span class="provider-pct">' + percent + '%</span></div></div>';
    }).join('');

    if (more && data.truncated) {
      more.textContent = '另有 ' + format.int(data.hiddenCount) + ' 个供应商未显示，占比按全部 ' +
        format.int(data.allTotal) + ' 个供应商的 ' + format.int(data.total) + ' 次调用计算';
      more.removeAttribute('hidden');
    }
  }

  /* ── 配置快照 / 运行状态 / 顶栏标签 ───────────────────── */
  function renderSnapshot() {
    const container = $('#config-snapshot');
    if (!container) return;
    const config = state.auditConfig || {};
    if (!config.rule_count && !config.provider_count && !state.configLoaded) {
      container.innerHTML = '<div class="state state-inline"><span class="state-mark" aria-hidden="true">∅</span>' +
        '<strong>尚未读取配置</strong><p>进入配置中心或刷新页面后会显示当前生效的策略。</p></div>';
      return;
    }
    const groups = (config.group_scope || []).join('、');
    const privateScope = (config.private_scope || []).join('、');
    const providers = config.provider_names && config.provider_names.length
      ? config.provider_names.join('、') + (config.provider_count > config.provider_names.length ? ' 等' : '')
      : '回退 AstrBot Provider';
    const rows = [
      ['审查群范围', groups === '0' || !groups ? '全部群聊' : groups],
      ['审查私聊', privateScope === '0' ? '全部私聊' : (privateScope || '未启用')],
      ['审查规则', format.int(config.rule_count || 0) + ' 条'],
      ['LLM 供应商', providers],
      ['抽查概率', format.percent(Number(config.check_probability || 0) * 100)],
      ['自动处置', (config.enable_recall ? '撤回消息 · ' : '不撤回 · ') + format.duration(config.ban_duration)],
      ['战报转发', config.report_enabled ? '已配置接收目标' : '未配置'],
      ['审核缓存', config.cache_enabled ? '已启用' : '已关闭']
    ];
    container.innerHTML = '<div class="snapshot-list">' + rows.map(row =>
      '<div class="snapshot-row"><span>' + esc(row[0]) + '</span><strong>' + esc(row[1]) + '</strong></div>'
    ).join('') + '</div>' +
      (state.truncated
        ? '<p class="snapshot-note">历史记录超过单次返回上限，页面按最新 ' + format.int(state.records.length) + ' 条统计。</p>'
        : '');
  }

  function renderSideHealth() {
    const container = $('#side-health');
    if (!container) return;
    const provider = derive.providerEntries();
    const cache = state.cacheStats || {};
    const rows = [
      ['违规记录', format.int(state.records.length) + ' 条', ''],
      ['供应商', provider.entries.length ? provider.entries.length + ' 个' : '未配置', provider.entries.length ? '' : 'warn'],
      ['缓存条目', format.int(cache.total_unique_images || 0) + ' 条', Number(cache.total_unique_images) > 0 ? '' : 'off'],
      ['最近同步', state.lastSyncAt ? format.time(state.lastSyncAt) : '—', '']
    ];
    container.innerHTML = rows.map(row =>
      '<div class="health-row"><span class="health-dot" data-state="' + row[2] + '">' + esc(row[0]) + '</span>' +
      '<strong>' + esc(row[1]) + '</strong></div>'
    ).join('');
  }

  function renderScopeSummary() {
    const container = $('#scope-summary');
    if (!container) return;
    const config = state.auditConfig || {};
    if (!config.group_scope && !state.configLoaded) {
      container.innerHTML = '<span class="meta-pill" data-tone="muted">正在读取配置…</span>';
      return;
    }
    const groups = (config.group_scope || []).join('、');
    const pills = [
      { text: groups === '0' || !groups ? '群聊 · 全部' : '群聊 · ' + groups, tone: 'brand', title: '启用审查的群号' },
      { text: format.int(config.rule_count || 0) + ' 条规则', tone: 'accent', title: '敏感文字与违规画面描述总量' },
      { text: (config.provider_count || 0) + ' 个供应商', tone: config.provider_count ? 'muted' : 'warn', title: (config.provider_names || []).join('、') || '未配置供应商' },
      { text: config.cache_enabled ? '缓存已启用' : '缓存已关闭', tone: config.cache_enabled ? 'muted' : 'warn', title: '审核缓存状态' }
    ];
    container.innerHTML = pills.map(pill =>
      '<span class="meta-pill" data-tone="' + pill.tone + '" title="' + esc(pill.title) + '">' + esc(pill.text) + '</span>'
    ).join('');
  }

  /* ── 记录卡片 ─────────────────────────────────────────── */
  // 历史记录里的 image_url 可能是本地文件路径（无法在 iframe 中加载），只接受可访问的 URL。
  function imageSource(record) {
    const local = String(record.local_image_url || '');
    if (local) return local;
    const raw = String(record.image_url || '').trim();
    return /^(https?:|data:|\/)/i.test(raw) ? raw : '';
  }

  function thumbMarkup(record) {
    const url = imageSource(record);
    if (!url) {
      return '<div class="thumb-col"><span class="thumb-meta">无图片</span></div>';
    }
    const size = record.local_image_size
      ? format.bytes(record.local_image_size)
      : (record.local_image_url ? '' : '远程');
    return '<div class="thumb-col">' +
      '<button class="thumb" type="button" data-preview="' + esc(url) + '" data-caption="' +
      esc((record.user_name || record.user_id || '') + ' · ' + format.fullTime(record.time)) + '" aria-label="预览审核图片">' +
      ICON_IMAGE + '</button>' +
      (size ? '<span class="thumb-meta" data-size="' + esc(size) + '">' + esc(size) + '</span>' : '') +
      '</div>';
  }

  function disposeBadges(record) {
    const wantedBan = Number(record.ban_duration) > 0;
    const parts = ['<span class="badge ' + (record.recalled ? 'badge-ok' : 'badge-fail') + '">' +
      (record.recalled ? '已撤回' : '撤回失败') + '</span>'];
    if (wantedBan) {
      parts.push('<span class="badge ' + (record.banned ? 'badge-ok' : 'badge-fail') + '">' +
        (record.banned ? '已禁言' : '禁言失败') + '</span>');
    } else {
      parts.push('<span class="badge badge-idle">未禁言</span>');
    }
    return parts.join('');
  }

  function recordCard(record, compact) {
    const status = derive.disposeStatus(record);
    const time = format.time(record.time);
    const pieces = time.split(' ');
    const user = record.user_name || record.user_id || '未知用户';
    const userId = record.user_id || '';
    const source = record.group_id ? '群 ' + record.group_id : '私聊';
    const reason = record.reason || '未说明理由';
    const tags = (record.tags || []).slice(0, compact ? 3 : 6);
    const tagsHtml = tags.length
      ? '<div class="tag-list">' + tags.map(tag =>
        '<span class="tag" title="' + esc(tag) + '">' + esc(tag) + '</span>').join('') + '</div>'
      : '';

    return '<article class="record" data-state="' + status + '" data-detail="' + esc(record.id || '') + '" tabindex="0" role="button" ' +
      'aria-label="' + esc(user + ' · ' + derive.disposeLabel(status) + ' · ' + reason) + '">' +
      '<div class="record-time"><strong>' + esc(pieces[0] || time) + '</strong>' +
      (pieces[1] ? '<span>' + esc(pieces[1]) + '</span>' : '') +
      '<span>' + esc(format.relative(record.time)) + '</span></div>' +
      '<div class="record-main">' +
      '<div class="record-user"><span title="' + esc(user) + '">' + esc(user) + '</span>' +
      '<span class="record-uid">' + esc(userId) + '</span>' +
      '<span class="record-source">' + esc(source) + '</span></div>' +
      '<p class="record-reason" title="' + esc(reason) + '">' + esc(reason) + '</p>' +
      tagsHtml +
      '</div>' +
      thumbMarkup(record) +
      '<div class="record-actions">' +
      '<div class="badge-stack">' + disposeBadges(record) + '</div>' +
      '<div class="record-ops">' +
      '<button class="btn btn-sm btn-ghost" type="button" data-detail-btn="' + esc(record.id || '') + '">详情</button>' +
      '<button class="btn btn-sm btn-danger" type="button" data-delete="' + esc(record.id || '') + '">删除</button>' +
      '</div></div></article>';
  }

  /* ── 概览 ─────────────────────────────────────────────── */
  function renderOverview() {
    renderStats();
    renderTrend();
    renderTrendRange();
    renderProviderStats();
    renderSnapshot();
    renderSideHealth();
    renderScopeSummary();

    const subtitle = $('#ov-subtitle');
    if (subtitle) {
      subtitle.textContent = state.records.length
        ? '共 ' + format.int(state.totalRecords) + ' 条违规记录，本页展示最近 ' + format.int(state.records.length) +
          ' 条 · 同步于 ' + (state.lastSyncAt ? format.time(state.lastSyncAt) : '刚刚')
        : '暂无违规记录，命中违规后会自动生成审计条目';
    }

    const host = $('#recent-list');
    if (!host) return;
    const recent = state.records.slice().sort((a, b) => {
      const ta = format.parseTime(a.time);
      const tb = format.parseTime(b.time);
      return (tb ? tb.getTime() : 0) - (ta ? ta.getTime() : 0);
    }).slice(0, 6);
    if (!recent.length) {
      fillList(host, 'empty', '暂无违规记录', '当图片被判定为违规并执行处置后，记录会出现在这里。');
      return;
    }
    host.innerHTML = recent.map(record => recordCard(record, true)).join('');
    bindCards(host);
    hydrateThumbs(host);
  }

  /* ── 记录页 ───────────────────────────────────────────── */
  function renderRecords() {
    const host = $('#records-list');
    if (!host) return;
    const list = derive.filteredRecords();
    const total = list.length;
    const pageSize = state.pageSize;
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    const page = Math.min(Math.max(1, state.route.page), totalPages);
    state.route.page = page;
    const slice = list.slice((page - 1) * pageSize, page * pageSize);

    const summary = $('#rec-summary');
    if (summary) {
      const filtered = total !== state.records.length;
      summary.textContent = state.records.length
        ? (filtered
          ? '筛选出 ' + format.int(total) + ' / ' + format.int(state.records.length) + ' 条记录'
          : '共 ' + format.int(state.totalRecords) + ' 条违规记录' +
            (state.truncated ? '（仅加载最近 ' + format.int(state.records.length) + ' 条）' : ''))
        : '暂无历史记录';
    }

    renderTagBar();
    renderStatusChips();
    renderSortButton();

    if (!total) {
      const filtered = Boolean(state.searchQuery || state.tagFilter || state.statusFilter);
      fillList(host, 'empty',
        filtered ? '没有匹配的记录' : '暂无审核记录',
        filtered ? '换个关键词，或清空筛选条件再试。' : '当前还没有命中违规的图片记录。');
      renderPager(0, 1, page);
      return;
    }

    host.innerHTML = slice.map(record => recordCard(record, false)).join('');
    bindCards(host);
    hydrateThumbs(host);
    renderPager(total, totalPages, page);
  }

  function renderTagBar() {
    const bar = $('#tag-bar');
    const container = $('#tag-filter');
    if (!bar || !container) return;
    const counts = derive.tagCounts();
    if (!counts.length) {
      bar.setAttribute('hidden', '');
      container.innerHTML = '';
      return;
    }
    bar.removeAttribute('hidden');
    const chips = ['<button class="chip' + (state.tagFilter ? '' : ' is-active') + '" type="button" data-tag="">全部' +
      '<span class="chip-count">' + format.int(state.records.length) + '</span></button>'];
    counts.slice(0, 18).forEach(item => {
      chips.push('<button class="chip' + (state.tagFilter === item[0] ? ' is-active' : '') + '" type="button" data-tag="' +
        esc(item[0]) + '">' + esc(item[0]) + '<span class="chip-count">' + format.int(item[1]) + '</span></button>');
    });
    container.innerHTML = chips.join('');
    $$('[data-tag]', container).forEach(button => {
      button.addEventListener('click', () => {
        state.tagFilter = button.dataset.tag || '';
        state.route.page = 1;
        IG.app.syncRoute(true);
        renderRecords();
      });
    });
  }

  function renderStatusChips() {
    $$('#status-filter .chip').forEach(button => {
      button.classList.toggle('is-active', (button.dataset.status || '') === state.statusFilter);
    });
  }

  function renderSortButton() {
    const button = $('#rec-sort');
    if (!button) return;
    button.textContent = state.sortDesc ? '时间 · 新→旧' : '时间 · 旧→新';
    button.setAttribute('aria-pressed', state.sortDesc ? 'false' : 'true');
  }

  function renderPager(total, totalPages, page) {
    const pager = $('#pager');
    if (!pager) return;
    const pageSize = state.pageSize;
    const from = total ? (page - 1) * pageSize + 1 : 0;
    const to = Math.min(total, page * pageSize);
    pager.innerHTML =
      '<span class="pager-info">' + (total
        ? '第 ' + format.int(from) + '–' + format.int(to) + ' 条 / 共 ' + format.int(total) + ' 条'
        : '暂无记录') + '</span>' +
      '<div class="pager-actions">' +
      '<button class="btn btn-sm btn-ghost" type="button" data-page="1"' + (page <= 1 ? ' disabled' : '') + '>首页</button>' +
      '<button class="btn btn-sm btn-ghost" type="button" data-page="' + (page - 1) + '"' + (page <= 1 ? ' disabled' : '') + '>上一页</button>' +
      '<span class="pager-page">' + page + ' / ' + totalPages + '</span>' +
      '<button class="btn btn-sm btn-ghost" type="button" data-page="' + (page + 1) + '"' + (page >= totalPages ? ' disabled' : '') + '>下一页</button>' +
      '<button class="btn btn-sm btn-ghost" type="button" data-page="' + totalPages + '"' + (page >= totalPages ? ' disabled' : '') + '>末页</button>' +
      '</div>';
    $$('[data-page]', pager).forEach(button => {
      button.addEventListener('click', () => {
        state.route.page = Number(button.dataset.page) || 1;
        IG.app.syncRoute(true);
        renderRecords();
        const main = $('#main');
        if (main) main.scrollIntoView({ block: 'start', behavior: 'smooth' });
      });
    });
  }

  /* ── 卡片交互 ─────────────────────────────────────────── */
  function bindCards(host) {
    $$('[data-preview]', host).forEach(button => {
      button.addEventListener('click', event => {
        event.stopPropagation();
        IG.lightbox.open(button.dataset.preview, button.dataset.caption, button);
      });
    });
    $$('[data-delete]', host).forEach(button => {
      button.addEventListener('click', event => {
        event.stopPropagation();
        IG.app.deleteRecord(button.dataset.delete);
      });
    });
    $$('[data-detail-btn]', host).forEach(button => {
      button.addEventListener('click', event => {
        event.stopPropagation();
        openDrawer(button.dataset.detailBtn);
      });
    });
    $$('.record[data-detail]', host).forEach(card => {
      card.addEventListener('click', event => {
        if (event.target.closest('button')) return;
        openDrawer(card.dataset.detail);
      });
      card.addEventListener('keydown', event => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        openDrawer(card.dataset.detail);
      });
    });
  }

  function markBroken(button) {
    if (!button || !button.isConnected) return;
    button.classList.add('thumb-broken');
    button.replaceChildren();
    const meta = button.parentElement && button.parentElement.querySelector('[data-size]');
    if (meta) meta.textContent = '加载失败';
  }

  function hydrateThumbs(host) {
    $$('.thumb', host).forEach(button => {
      const url = button.dataset.preview;
      if (!url) return;
      IG.imageAsset.resolve(url).then(resolved => {
        if (!resolved || !button.isConnected) return;
        const img = document.createElement('img');
        img.alt = '审核图片缩略图';
        img.loading = 'lazy';
        img.decoding = 'async';
        img.src = resolved;
        img.addEventListener('error', () => markBroken(button), { once: true });
        button.replaceChildren(img);
      }).catch(() => markBroken(button));
    });
  }

  /* ── 抽屉 ─────────────────────────────────────────────── */
  let drawerRecordId = '';

  function openDrawer(id) {
    const record = state.records.filter(item => item.id === id)[0];
    if (!record) return;
    drawerRecordId = id;
    const drawer = $('#record-drawer');
    const body = $('#drawer-body');
    const foot = $('#drawer-foot');
    if (!drawer || !body || !foot) return;

    $('#drawer-title').textContent = '记录详情';
    $('#drawer-sub').textContent = format.fullTime(record.time) + ' · ' + format.relative(record.time);

    const url = imageSource(record);
    const status = derive.disposeStatus(record);
    const tags = record.tags || [];

    let media;
    if (url) {
      const sizeText = record.local_image_size ? format.bytes(record.local_image_size)
        : (record.local_image_url ? '本地已保存' : '远程图片');
      media = '<div class="detail-media">' +
        '<div class="detail-media-empty" data-detail-image>正在加载图片…</div>' +
        '<div class="detail-media-foot"><span>' + esc(sizeText) + '</span>' +
        '<button class="link-btn" type="button" data-detail-open>查看大图</button></div></div>';
    } else {
      media = '<div class="detail-media"><div class="detail-media-empty">该记录没有关联图片</div></div>';
    }

    body.innerHTML = media +
      '<div class="detail-block"><span class="detail-block-title">判定理由</span>' +
      '<p class="detail-note">' + esc(record.reason || '未说明理由') + '</p>' +
      (tags.length ? '<div class="tag-list">' + tags.map(tag =>
        '<span class="tag">' + esc(tag) + '</span>').join('') + '</div>' : '') + '</div>' +
      '<div class="detail-block"><span class="detail-block-title">记录信息</span>' +
      '<dl class="kv">' +
      '<dt>用户</dt><dd>' + esc(record.user_name || '未知') + '</dd>' +
      '<dt>用户 ID</dt><dd>' + esc(record.user_id || '—') + '</dd>' +
      '<dt>来源</dt><dd>' + esc(record.group_id ? '群 ' + record.group_id : '私聊') + '</dd>' +
      '<dt>处置结果</dt><dd>' + esc(derive.disposeLabel(status)) + '</dd>' +
      '<dt>禁言时长</dt><dd>' + esc(format.duration(record.ban_duration)) + '</dd>' +
      '<dt>记录 ID</dt><dd>' + esc(record.id || '—') + '</dd>' +
      '</dl></div>';

    foot.innerHTML =
      '<button class="btn btn-danger" type="button" data-detail-delete>删除记录</button>' +
      '<button class="btn btn-primary" type="button" data-detail-close>关闭</button>';

    if (url) {
      const holder = body.querySelector('[data-detail-image]');
      IG.imageAsset.resolve(url).then(resolved => {
        if (!resolved || !holder || !holder.isConnected) return;
        const img = document.createElement('img');
        img.alt = '审核图片';
        img.addEventListener('error', () => { if (holder.isConnected) holder.textContent = '图片加载失败'; }, { once: true });
        img.src = resolved;
        const openBtn = body.querySelector('[data-detail-open]');
        if (openBtn) {
          openBtn.addEventListener('click', () => IG.lightbox.open(url,
            (record.user_name || record.user_id || '') + ' · ' + format.fullTime(record.time), openBtn));
        }
        holder.replaceWith(img);
      }).catch(() => {
        if (holder && holder.isConnected) holder.textContent = '图片加载失败';
      });
    }

    body.querySelector('[data-detail-delete]').addEventListener('click', () => {
      IG.app.deleteRecord(record.id);
    });
    body.querySelector('[data-detail-close]').addEventListener('click', closeDrawer);

    IG.show($('#drawer-scrim'), true);
    IG.show(drawer, true);
    const closeBtn = $('#drawer-close');
    if (closeBtn) closeBtn.focus();
  }

  function closeDrawer() {
    drawerRecordId = '';
    IG.show($('#drawer-scrim'), false);
    IG.show($('#record-drawer'), false);
  }

  function drawerOpen() {
    const drawer = $('#record-drawer');
    return Boolean(drawer && !drawer.hasAttribute('hidden'));
  }

  IG.views = {
    renderOverview: renderOverview,
    renderRecords: renderRecords,
    renderStats: renderStats,
    renderPending: renderPending,
    renderTrend: renderTrend,
    renderTrendRange: renderTrendRange,
    renderSnapshot: renderSnapshot,
    renderSideHealth: renderSideHealth,
    renderScopeSummary: renderScopeSummary,
    fillList: fillList,
    openDrawer: openDrawer,
    closeDrawer: closeDrawer,
    drawerOpen: drawerOpen,
    currentDrawerId() { return drawerRecordId; },
    hydrateThumbs: hydrateThumbs
  };
})();
