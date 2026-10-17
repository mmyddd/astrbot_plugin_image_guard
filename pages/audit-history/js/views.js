
/* ============================================================
   ImageGuard · 视图层 v3
   概览 Bento / 记录双栏 / 详情轨迹 / 实时队列
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

  const ICON_IMAGE = '<svg class="thumb-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="2.5"></rect><circle cx="9" cy="10" r="1.6"></circle><path d="M4.5 17l4.6-4.2 3.2 2.8 3.1-3 4.1 4"></path></svg>';

  /* ── 基础片段 ─────────────────────────────────────────── */
  function setText(id, text) {
    const node = document.getElementById(id);
    if (node) node.textContent = text;
  }

  function emptyState(kind, title, message) {
    const mark = kind === 'error' ? '!' : kind === 'loading' ? '···' : '∅';
    return '<div class="empty-state" data-tone="' + esc(kind) + '">' +
      '<span class="empty-mark" aria-hidden="true">' + mark + '</span>' +
      '<strong>' + esc(title) + '</strong>' +
      '<p>' + esc(message) + '</p>' +
      '</div>';
  }

  function statusBadge(status) {
    const cls = status === 'success' ? 'badge-ok' : status === 'partial' ? 'badge-partial' : 'badge-fail';
    return '<span class="badge ' + cls + '">' + esc(derive.disposeLabel(status)) + '</span>';
  }

  function sourceLabel(record) {
    return record.group_id ? '群 ' + record.group_id : '私聊';
  }

  function dateLabel(date) {
    const weekdays = ['日', '一', '二', '三', '四', '五', '六'];
    return (date.getMonth() + 1) + ' 月 ' + date.getDate() + ' 日 星期' + weekdays[date.getDay()];
  }

  function isToday(value) {
    const date = format.parseTime(value);
    if (!date) return false;
    const now = new Date();
    return date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate();
  }

  function timeOnly(value) {
    const date = format.parseTime(value);
    if (!date) return '—';
    const pad = n => String(n).padStart(2, '0');
    return pad(date.getHours()) + ':' + pad(date.getMinutes()) + ':' + pad(date.getSeconds());
  }

  function imageSource(record) {
    const local = String(record.local_image_url || '');
    if (local) return local;
    const raw = String(record.image_url || '').trim();
    return /^(https?:|data:|\/)/i.test(raw) ? raw : '';
  }

  function thumbMarkup(url, extraClass) {
    const cls = 'thumb' + (extraClass ? ' ' + extraClass : '');
    if (!url) return '<span class="' + cls + '" aria-hidden="true">' + ICON_IMAGE + '</span>';
    return '<span class="' + cls + '" data-preview="' + esc(url) + '" aria-hidden="true">' + ICON_IMAGE + '</span>';
  }

  function recordThumb(record) {
    return thumbMarkup(imageSource(record));
  }

  function hydrateThumbs(host) {
    if (!host) return;
    $$('.thumb[data-preview]', host).forEach(span => {
      const url = span.getAttribute('data-preview');
      if (!url || span.dataset.hydrated === '1') return;
      span.dataset.hydrated = '1';
      IG.imageAsset.resolve(url).then(resolved => {
        if (!resolved || !span.isConnected) return;
        const img = document.createElement('img');
        img.alt = '';
        img.loading = 'lazy';
        img.decoding = 'async';
        img.src = resolved;
        img.addEventListener('error', () => {
          span.classList.add('thumb-broken');
          span.replaceChildren();
        }, { once: true });
        span.replaceChildren(img);
      }).catch(() => {
        span.classList.add('thumb-broken');
        span.replaceChildren();
      });
    });
  }

  /* ── 概览 · Bento ─────────────────────────────────────── */
  function pickLabels(series) {
    if (!series.length) return [];
    const indexes = [0, Math.floor((series.length - 1) / 3), Math.floor((series.length - 1) * 2 / 3), series.length - 1];
    return indexes.filter((value, index, arr) => arr.indexOf(value) === index).map(i => series[i].label);
  }

  function renderTrend() {
    const series = derive.dailySeries(state.trendRange);
    const max = series.reduce((acc, item) => Math.max(acc, item.value), 0);
    const host = $('#trend-chart');
    if (!host) return;
    host.innerHTML = series.map((item, index) => {
      const height = item.value ? Math.max(6, Math.round(item.value / Math.max(max, 1) * 100)) : 2;
      let cls = 'mini-bar';
      if (!item.value) cls += ' is-zero';
      else if (index === series.length - 1) cls += ' is-today';
      return '<span class="' + cls + '" style="--h:' + height + '%"></span>';
    }).join('');
    const labels = $('#trend-labels');
    if (labels) labels.innerHTML = pickLabels(series).map(label => '<span>' + esc(label) + '</span>').join('');
    const range = $('#trend-range');
    if (range) {
      $$('.seg-btn', range).forEach(button => {
        const active = Number(button.dataset.range) === state.trendRange;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
    }
  }

  function renderHero() {
    const total = state.records.length;
    const today = state.records.filter(record => isToday(record.time)).length;
    const latest = state.records.reduce((acc, record) => {
      const stamp = format.parseTime(record.time);
      if (!stamp) return acc;
      return !acc || stamp > acc.stamp ? { record: record, stamp: stamp } : acc;
    }, null);
    setText('stat-total', format.int(total));
    setText('stat-total-chip', '今天 ' + format.int(today) + ' 条');
    setText('stat-total-foot', latest ? '最近一次 ' + format.relative(latest.record.time) : '暂无违规记录');
    renderTrend();
  }

  function renderSafe() {
    const funnel = derive.auditFunnel();
    const safe = funnel.safe || 0;
    const total = funnel.auditTotal || 0;
    const percent = total > 0 ? Math.round(safe / total * 100) : 0;
    setText('stat-safe', format.int(safe));
    setText('safe-foot', total > 0 ? '占送审 ' + percent + '%' : '等待审核样本');
    setText('safe-percent', percent + '%');
    const ring = $('#safe-ring');
    if (ring) ring.style.setProperty('--safe-p', percent + '%');
    setText('safe-note', funnel.hasData
      ? '模型判定安全后直接丢弃，只保留缓存计数'
      : '还没有足够的审核样本，先让插件跑起来');
  }

  function renderQueue() {
    const host = $('#queue-list');
    if (!host) return;
    const items = derive.pendingItems();
    const images = items.reduce((sum, item) => sum + (Number(item.image_count) || 1), 0);
    const count = $('#queue-count');
    if (count) count.textContent = items.length ? (items.length + ' 个任务') : '0 个任务';
    if (!items.length) {
      host.innerHTML = '<div class="queue-empty"><strong>当前没有正在审核的任务</strong><span>新图片进入审核时会显示在这里。</span></div>';
      return;
    }
    host.innerHTML = items.slice(0, 3).map(item => {
      const who = item.user_name || item.user_id || '未知用户';
      const source = item.group_id ? '群 ' + item.group_id : '私聊';
      const preview = item.preview ? thumbMarkup(item.preview) : thumbMarkup('');
      return '<div class="queue-item">' +
        preview +
        '<div class="queue-body"><strong>' + esc(who) + '</strong><span>' + esc(source) + ' · ' + format.int(item.image_count || 1) + ' 张图片</span></div>' +
        '<div class="queue-time"><strong>' + esc(format.elapsed(item.elapsed_seconds)) + '</strong><span>进行中</span></div>' +
        '<div class="queue-progress"><i></i></div>' +
        '</div>';
    }).join('') +
      (items.length > 3 ? '<p class="tile-sub" style="margin-top:6px">另有 ' + format.int(items.length - 3) + ' 个任务</p>' : '');
    hydrateThumbs(host);
    const imagesHint = $('#queue-count');
    if (imagesHint && images > items.length) imagesHint.title = '共 ' + format.int(images) + ' 张图片正在审核';
  }

  function renderDisposal() {
    const counts = { success: 0, partial: 0, failed: 0 };
    state.records.forEach(record => { counts[derive.disposeStatus(record)] += 1; });
    const total = state.records.length;
    const rate = total > 0 ? Math.round(counts.success / total * 100) : 0;
    setText('stat-dispose', total > 0 ? rate + '%' : '—');
    setText('dispose-count', counts.success + ' / ' + total + ' 条');
    const bar = $('#dispose-bar');
    if (bar) {
      bar.innerHTML = total > 0
        ? '<i style="width:' + (counts.success / total * 100) + '%;background:var(--green)"></i>' +
          '<i style="width:' + (counts.partial / total * 100) + '%;background:var(--amber)"></i>' +
          '<i style="width:' + (counts.failed / total * 100) + '%;background:var(--red)"></i>'
        : '<i style="width:100%;background:var(--line)"></i>';
    }
    const legend = $('#dispose-legend');
    if (legend) {
      legend.innerHTML =
        '<span><b style="background:var(--green)"></b>成功 ' + counts.success + '</span>' +
        '<span><b style="background:var(--amber)"></b>部分 ' + counts.partial + '</span>' +
        '<span><b style="background:var(--red)"></b>失败 ' + counts.failed + '</span>';
    }
  }

  function renderSavings() {
    const funnel = derive.auditFunnel();
    const saved = funnel.skipped || 0;
    setText('stat-saved', format.int(saved));
    setText('savings-breakdown', '缓存 ' + format.int(funnel.skippedCache) + ' · 门控 ' + format.int(funnel.gateSkipped) + ' · 抽查 ' + format.int(funnel.skippedProbability));
    const bar = $('#savings-bar');
    if (bar) {
      const max = Math.max(funnel.skippedCache + funnel.gateSkipped + funnel.skippedProbability, 1);
      bar.innerHTML = saved
        ? '<i style="width:' + (funnel.skippedCache / max * 100) + '%;background:var(--brand)"></i>' +
          '<i style="width:' + (funnel.gateSkipped / max * 100) + '%;background:var(--accent)"></i>' +
          '<i style="width:' + (funnel.skippedProbability / max * 100) + '%;background:var(--violet)"></i>'
        : '<i style="width:100%;background:var(--line)"></i>';
    }
  }

  function renderRecent() {
    const host = $('#recent-list');
    if (!host) return;
    const recent = state.records.slice().sort((a, b) => {
      const ta = format.parseTime(a.time);
      const tb = format.parseTime(b.time);
      return (tb ? tb.getTime() : 0) - (ta ? ta.getTime() : 0);
    }).slice(0, 4);
    if (!recent.length) {
      host.innerHTML = emptyState('empty', '暂无违规记录', '命中违规并执行处置后，记录会出现在这里。');
      return;
    }
    host.innerHTML = recent.map(record => {
      const status = derive.disposeStatus(record);
      return '<button class="recent-row" type="button" data-open-record="' + esc(record.id || '') + '">' +
        recordThumb(record) +
        '<span class="recent-body"><span class="recent-name"><strong>' + esc(record.user_name || record.user_id || '未知用户') + '</strong>' +
        '<span>' + esc(sourceLabel(record)) + ' · ' + esc(format.relative(record.time)) + '</span></span>' +
        '<span class="recent-reason">' + esc(record.reason || '未说明理由') + '</span></span>' +
        '<span class="recent-side">' + statusBadge(status) + '<time>' + esc(format.ms(record.audit_ms)) + '</time></span>' +
        '</button>';
    }).join('');
    hydrateThumbs(host);
    $$('[data-open-record]', host).forEach(button => {
      button.addEventListener('click', () => IG.app.openRecord(button.dataset.openRecord));
    });
  }

  function renderPolicy() {
    const host = $('#config-snapshot');
    if (!host) return;
    const config = state.auditConfig || {};
    if (!state.configLoaded && !config.rule_count && !config.provider_count) {
      host.innerHTML = emptyState('empty', '正在读取配置', '进入配置中心或刷新后会显示当前策略。');
      return;
    }
    const groupScope = (config.group_scope || []).map(String);
    const privateScope = (config.private_scope || []).map(String);
    const groupText = groupScope.indexOf('0') >= 0 ? '全部群聊' : (groupScope.length ? groupScope.length + ' 个群' : '未启用');
    const privateText = privateScope.indexOf('0') >= 0 ? '全部私聊' : (privateScope.length ? privateScope.length + ' 个私聊' : '未启用');
    const providerName = config.provider_names && config.provider_names.length ? config.provider_names[0] : 'AstrBot 当前 Provider';
    const disposal = (config.enable_recall ? '撤回消息' : '不撤回') +
      (Number(config.ban_duration) > 0 ? ' · 禁言 ' + format.duration(config.ban_duration) : ' · 不禁言');
    const cost = (config.cache_enabled ? '缓存 ' + format.int(config.cache_threshold || 3) + ' 次' : '缓存关闭') +
      ' · ' + (config.gate_enabled ? '门控 ' + format.percent(Number(config.gate_threshold || 0) * 100) : '门控关闭');
    const rows = [
      ['审查范围', groupText + ' · ' + privateText],
      ['违规规则', format.int(config.rule_count || 0) + ' 条'],
      ['优先模型', providerName],
      ['自动处置', disposal],
      ['成本控制', cost]
    ];
    host.innerHTML = rows.map(row =>
      '<div class="policy-row"><span>' + esc(row[0]) + '</span><strong>' + esc(row[1]) + '</strong></div>'
    ).join('');
    const health = $('#policy-health');
    if (health) {
      const healthy = Number(config.provider_count) > 0;
      health.innerHTML = '<i style="background:' + (healthy ? 'var(--green)' : 'var(--amber)') + '"></i>' +
        (healthy ? '运行正常' : '未配置供应商') +
        '<span>最近同步 ' + esc(state.lastSyncAt ? format.time(state.lastSyncAt) : '—') + '</span>';
    }
  }

  function renderOverview() {
    setText('nav-record-count', format.int(state.totalRecords || state.records.length));
    renderHero();
    renderSafe();
    renderQueue();
    renderDisposal();
    renderSavings();
    renderRecent();
    renderPolicy();
    const subtitle = $('#ov-subtitle');
    if (subtitle) {
      const count = state.records.length;
      const total = state.totalRecords || count;
      subtitle.textContent = count
        ? dateLabel(new Date()) + ' · 共 ' + format.int(total) + ' 条违规记录 · 同步于 ' + (state.lastSyncAt ? format.time(state.lastSyncAt) : '刚刚')
        : dateLabel(new Date()) + ' · 暂无违规记录 · 同步于 ' + (state.lastSyncAt ? format.time(state.lastSyncAt) : '刚刚');
    }
  }

  /* ── 记录 ─────────────────────────────────────────────── */
  function renderRecordsSummary() {
    setText('nav-record-count', format.int(state.totalRecords || state.records.length));
    const funnel = derive.auditFunnel();
    const total = state.records.length;
    const today = state.records.filter(record => isToday(record.time)).length;
    const success = state.records.filter(record => derive.disposeStatus(record) === 'success').length;
    const pending = derive.pendingItems();
    setText('rec-kpi-total', format.int(today));
    setText('rec-kpi-safe', format.int(funnel.safe || 0));
    setText('rec-kpi-dispose', total ? Math.round(success / total * 100) + '%' : '—');
    setText('rec-kpi-duration', funnel.msCount ? format.ms(funnel.msAvg) : '—');
    setText('rec-kpi-pending', format.int(pending.length));
    const totalNode = $('#rec-kpi-total');
    if (totalNode) totalNode.classList.toggle('danger', today > 0);
    const safeNode = $('#rec-kpi-safe');
    if (safeNode) safeNode.classList.toggle('ok', (funnel.safe || 0) > 0);
    const spark = $('#rec-spark');
    if (spark) {
      const series = derive.dailySeries(14);
      const max = series.reduce((acc, item) => Math.max(acc, item.value), 0);
      spark.innerHTML = series.map((item, index) => {
        const height = item.value ? Math.max(14, Math.round(item.value / Math.max(max, 1) * 100)) : 8;
        const cls = index === series.length - 1 ? ' class="is-peak"' : (item.value === max && max > 0 ? ' class="is-peak"' : '');
        return '<i' + cls + ' style="height:' + height + '%"></i>';
      }).join('');
    }
    setText('rec-tab-all-count', format.int(total));
    setText('rec-tab-pending-count', format.int(pending.length));
    setText('rec-tab-failed-count', format.int(state.records.filter(record => derive.disposeStatus(record) === 'failed').length));
  }

  function renderRecordRow(record) {
    const selected = record.id === state.selectedRecordId;
    const status = derive.disposeStatus(record);
    const tags = (record.tags || []).slice(0, 2).map(tag => '<span class="tag">' + esc(tag) + '</span>').join('');
    return '<button class="record-row' + (selected ? ' is-selected' : '') + '" type="button" data-record-id="' + esc(record.id || '') + '">' +
      recordThumb(record) +
      '<span class="record-row-body">' +
      '<span class="record-row-title"><strong>' + esc(record.user_name || record.user_id || '未知用户') + '</strong>' +
      '<span>' + esc(sourceLabel(record)) + ' · ' + esc(format.relative(record.time)) + '</span></span>' +
      '<span class="record-row-reason">' + esc(record.reason || '未说明理由') + '</span>' +
      (tags ? '<span class="record-row-tags">' + tags + '</span>' : '') +
      '</span>' +
      '<span class="record-row-side">' + statusBadge(status) +
      '<time>' + esc(format.ms(record.audit_ms)) + '</time></span>' +
      '</button>';
  }

  function renderPendingRow(item) {
    const selected = item.id === state.selectedRecordId;
    const who = item.user_name || item.user_id || '未知用户';
    const source = item.group_id ? '群 ' + item.group_id : '私聊';
    const preview = item.preview ? thumbMarkup(item.preview) : thumbMarkup('');
    return '<button class="record-row' + (selected ? ' is-selected' : '') + '" type="button" data-pending-id="' + esc(item.id || '') + '">' +
      preview +
      '<span class="record-row-body"><span class="record-row-title"><strong>' + esc(who) + '</strong>' +
      '<span>' + esc(source) + ' · 已用 ' + esc(format.elapsed(item.elapsed_seconds)) + '</span></span>' +
      '<span class="record-row-reason">正在审核 ' + format.int(item.image_count || 1) + ' 张图片，完成后会写入记录</span>' +
      '</span><span class="record-row-side"><span class="badge badge-live">审核中</span><time>进行中</time></span></button>';
  }

  function renderTagBar() {
    const bar = $('#tag-bar');
    const container = $('#tag-filter');
    const toggle = $('#rec-tag-toggle');
    if (!bar || !container) return;
    const counts = derive.tagCounts();
    const shouldShow = Boolean(state.tagBarOpen || state.tagFilter) && counts.length > 0;
    if (!shouldShow) {
      bar.setAttribute('hidden', '');
      container.innerHTML = '';
      if (toggle) toggle.setAttribute('aria-expanded', 'false');
      return;
    }
    bar.removeAttribute('hidden');
    if (toggle) toggle.setAttribute('aria-expanded', 'true');
    const chips = ['<button class="chip' + (state.tagFilter ? '' : ' is-active') + '" type="button" data-tag="">全部<span class="chip-count">' + format.int(state.records.length) + '</span></button>'];
    counts.slice(0, 18).forEach(item => {
      chips.push('<button class="chip' + (state.tagFilter === item[0] ? ' is-active' : '') + '" type="button" data-tag="' + esc(item[0]) + '">' + esc(item[0]) + '<span class="chip-count">' + format.int(item[1]) + '</span></button>');
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

  function renderTabs() {
    $$('#records-tabs .list-tab').forEach(button => {
      button.classList.toggle('is-active', button.dataset.recTab === state.recordsTab);
    });
  }

  function renderPager(total, totalPages, page) {
    const pager = $('#pager');
    if (!pager) return;
    if (!total) {
      pager.innerHTML = '<span class="pager-info">暂无记录</span>';
      return;
    }
    const from = (page - 1) * state.pageSize + 1;
    const to = Math.min(total, page * state.pageSize);
    pager.innerHTML = '<span class="pager-info">' + format.int(from) + '–' + format.int(to) + ' / ' + format.int(total) + '</span>' +
      '<div class="pager-actions">' +
      '<button class="btn btn-sm btn-ghost" type="button" data-page="' + (page - 1) + '" aria-label="上一页"' + (page <= 1 ? ' disabled' : '') + '>‹</button>' +
      '<span class="pager-page">' + page + ' / ' + totalPages + '</span>' +
      '<button class="btn btn-sm btn-ghost" type="button" data-page="' + (page + 1) + '" aria-label="下一页"' + (page >= totalPages ? ' disabled' : '') + '>›</button>' +
      '</div>';
    $$('[data-page]', pager).forEach(button => {
      button.addEventListener('click', () => {
        state.route.page = Number(button.dataset.page) || 1;
        IG.app.syncRoute(true);
        renderRecords();
      });
    });
  }

  function renderDetail(record) {
    const content = $('#detail-content');
    const placeholder = $('#detail-empty');
    if (!content || !placeholder) return;
    if (!record) {
      content.setAttribute('hidden', '');
      content.innerHTML = '';
      placeholder.removeAttribute('hidden');
      placeholder.innerHTML = '<span class="detail-placeholder-mark">⌕</span><strong>选择一条记录查看详情</strong><p>图片、判定理由、处置结果与处理轨迹会显示在这里。</p>';
      return;
    }
    placeholder.setAttribute('hidden', '');
    content.removeAttribute('hidden');
    if (record.__pending) {
      renderPendingDetail(record);
      return;
    }
    const status = derive.disposeStatus(record);
    const url = imageSource(record);
    const tags = (record.tags || []).map(tag => '<span class="tag">' + esc(tag) + '</span>').join('');
    const media = url
      ? '<div class="detail-media" data-detail-media><div class="detail-media-empty">正在加载图片…</div><div class="detail-media-foot"><span>' + esc(record.local_image_size ? format.bytes(record.local_image_size) : (record.local_image_url ? '本地已保存' : '远程图片')) + '</span><button class="link-btn" type="button" data-detail-open>查看大图</button></div></div>'
      : '<div class="detail-media"><div class="detail-media-empty">该记录没有关联图片</div></div>';
    content.innerHTML =
      '<div class="detail-head"><div><div class="detail-title"><h2>' + esc(record.user_name || record.user_id || '未知用户') + '</h2>' + statusBadge(status) + '</div>' +
      '<div class="detail-meta">' + esc(format.fullTime(record.time)) + ' · ' + esc(format.relative(record.time)) + ' · ' + esc(sourceLabel(record)) + ' · 记录 ' + esc(record.id || '—') + '</div></div>' +
      '<div class="detail-actions"><button class="btn btn-sm btn-ghost" type="button" data-detail-delete>删除</button></div></div>' +
      media +
      '<div class="detail-facts">' +
      '<div class="detail-fact"><span>判定结果</span><strong class="danger">违规</strong></div>' +
      '<div class="detail-fact"><span>处置</span><strong>' + (record.recalled ? '已撤回' : '未撤回') + ' · ' + (Number(record.ban_duration) > 0 ? (record.banned ? '已禁言' : '未禁言') : '不禁言') + '</strong></div>' +
      '<div class="detail-fact"><span>禁言时长</span><strong>' + esc(format.duration(record.ban_duration)) + '</strong></div>' +
      '<div class="detail-fact"><span>审核用时</span><strong>' + esc(Number(record.audit_ms) > 0 ? format.ms(record.audit_ms) : '未记录') + '</strong></div>' +
      '<div class="detail-fact"><span>来源</span><strong>' + esc(sourceLabel(record)) + '</strong></div>' +
      '</div>' +
      '<div class="detail-reason"><span class="label">判定理由</span><p>' + esc(record.reason || '未说明理由') + '</p>' + (tags ? '<div class="tag-list">' + tags + '</div>' : '') + '</div>' +
      renderDetailTimeline(record) +
      '<div class="detail-foot"><span>记录 ID ' + esc(record.id || '—') + '</span><span>处理状态：' + esc(derive.disposeLabel(status)) + '</span></div>';

    const openBtn = $('[data-detail-open]', content);
    if (openBtn) {
      openBtn.addEventListener('click', () => IG.lightbox.open(url,
        (record.user_name || record.user_id || '') + ' · ' + format.fullTime(record.time), openBtn));
    }
    const deleteBtn = $('[data-detail-delete]', content);
    if (deleteBtn) deleteBtn.addEventListener('click', () => IG.app.deleteRecord(record.id));

    if (url) {
      const holder = $('[data-detail-media]', content);
      IG.imageAsset.resolve(url).then(resolved => {
        if (!resolved || !holder || !holder.isConnected) return;
        const img = document.createElement('img');
        img.alt = '审核图片';
        img.addEventListener('error', () => {
          const empty = document.createElement('div');
          empty.className = 'detail-media-empty';
          empty.textContent = '图片加载失败';
          holder.replaceChildren(empty);
        }, { once: true });
        img.src = resolved;
        const placeholder = holder.querySelector('.detail-media-empty');
        if (placeholder) placeholder.remove();
        const foot = holder.querySelector('.detail-media-foot');
        if (foot) holder.insertBefore(img, foot);
        else holder.replaceChildren(img);
      }).catch(() => {
        if (holder && holder.isConnected) holder.querySelector('.detail-media-empty').textContent = '图片加载失败';
      });
    }
  }

  function renderDetailTimeline(record) {
    const start = format.parseTime(record.time);
    const auditMs = Number(record.audit_ms) || 0;
    const judgeAt = start && auditMs ? new Date(start.getTime() + auditMs) : null;
    const events = [
      { time: start ? timeOnly(start) : '—', dot: 'blue', title: '收到消息', detail: sourceLabel(record) + ' · 1 张图片' },
      { time: judgeAt ? timeOnly(judgeAt) : '—', dot: 'blue', title: '审核完成', detail: auditMs ? '用时 ' + format.ms(auditMs) : '未记录耗时' },
      { time: judgeAt ? timeOnly(judgeAt) : '—', dot: 'green', title: '判定违规', detail: '命中规则，进入处置阶段' },
      { time: '—', dot: record.recalled ? 'green' : 'red', title: '撤回消息', detail: record.recalled ? '成功' : '未成功' },
      { time: '—', dot: Number(record.ban_duration) > 0 ? (record.banned ? 'green' : 'red') : 'amber', title: '禁言', detail: Number(record.ban_duration) > 0 ? (record.banned ? '成功 · ' + format.duration(record.ban_duration) : '未成功') : '未配置禁言' }
    ];
    return '<div class="detail-timeline"><h3>处理轨迹</h3><div class="detail-events">' + events.map(event =>
      '<div class="detail-event"><time>' + esc(event.time) + '</time><span class="dot ' + event.dot + '"></span><div><strong>' + esc(event.title) + '</strong><span>' + esc(event.detail) + '</span></div></div>'
    ).join('') + '</div></div>';
  }

  function renderPendingDetail(item) {
    const content = $('#detail-content');
    const who = item.user_name || item.user_id || '未知用户';
    const source = item.group_id ? '群 ' + item.group_id : '私聊';
    content.innerHTML =
      '<div class="detail-head"><div><div class="detail-title"><h2>' + esc(who) + '</h2><span class="badge badge-live">审核中</span></div><div class="detail-meta">' + esc(source) + ' · 已用 ' + esc(format.elapsed(item.elapsed_seconds)) + ' · ' + format.int(item.image_count || 1) + ' 张图片</div></div></div>' +
      '<div class="detail-media"><div class="detail-media-empty">' + (item.preview ? '正在加载预览…' : '审核完成后会保存图片') + '</div></div>' +
      '<div class="detail-facts">' +
      '<div class="detail-fact"><span>状态</span><strong class="danger">审核中</strong></div>' +
      '<div class="detail-fact"><span>图片</span><strong>' + format.int(item.image_count || 1) + ' 张</strong></div>' +
      '<div class="detail-fact"><span>已用时</span><strong>' + esc(format.elapsed(item.elapsed_seconds)) + '</strong></div>' +
      '<div class="detail-fact"><span>来源</span><strong>' + esc(source) + '</strong></div>' +
      '</div>' +
      '<div class="detail-empty-note">审核完成后会自动写入记录；命中违规会立即执行撤回与禁言。</div>' +
      '<div class="detail-foot"><span>任务 ' + esc(item.id || '—') + '</span><span>等待模型返回</span></div>';
    if (item.preview) {
      const holder = $('.detail-media', content);
      IG.imageAsset.resolve(item.preview).then(resolved => {
        if (!resolved || !holder || !holder.isConnected) return;
        const img = document.createElement('img');
        img.alt = '待审核图片';
        img.src = resolved;
        holder.replaceChildren(img);
      }).catch(() => {
        if (holder && holder.isConnected) holder.innerHTML = '<div class="detail-media-empty">预览加载失败</div>';
      });
    }
  }

  function renderRecords() {
    const host = $('#records-list');
    if (!host) return;
    renderRecordsSummary();
    renderTabs();
    renderStatusChips();
    renderSortButton();
    const pendingTab = state.recordsTab === 'pending';
    if (state.recordsTab === 'failed') state.statusFilter = 'failed';
    const list = pendingTab ? [] : derive.filteredRecords();
    const total = list.length;
    const totalPages = Math.max(1, Math.ceil(total / state.pageSize));
    const page = Math.min(Math.max(1, state.route.page), totalPages);
    state.route.page = page;

    const summary = $('#rec-summary');
    if (summary) {
      summary.textContent = pendingTab
        ? (derive.pendingItems().length ? '正在审核 ' + format.int(derive.pendingItems().length) + ' 个任务' : '当前没有正在审核的任务')
        : (state.records.length
          ? (total !== state.records.length
            ? '筛选出 ' + format.int(total) + ' / ' + format.int(state.records.length) + ' 条记录'
            : '共 ' + format.int(state.totalRecords || state.records.length) + ' 条违规记录')
          : '暂无历史记录');
    }

    renderTagBar();
    if (pendingTab) {
      const items = derive.pendingItems();
      host.innerHTML = items.length ? items.map(renderPendingRow).join('') : emptyState('empty', '当前没有正在审核的任务', '新的图片进入审核后会出现在这里。');
      renderPager(0, 1, 1);
      const selected = items.filter(item => item.id === state.selectedRecordId)[0] || items[0];
      state.selectedRecordId = selected ? selected.id : '';
      renderDetail(selected ? Object.assign({ __pending: true }, selected) : null);
      hydrateThumbs(host);
      bindRecordRows(host, items, true);
      return;
    }
    if (!total) {
      const filtered = Boolean(state.searchQuery || state.tagFilter || state.statusFilter);
      host.innerHTML = emptyState('empty', filtered ? '没有匹配的记录' : '暂无审核记录', filtered ? '换个关键词，或清空筛选条件再试。' : '当前还没有命中违规的图片记录。');
      renderPager(0, 1, page);
      state.selectedRecordId = '';
      renderDetail(null);
      return;
    }
    const slice = list.slice((page - 1) * state.pageSize, page * state.pageSize);
    let selected = slice.filter(record => record.id === state.selectedRecordId)[0];
    if (!selected) selected = slice[0] || list[0];
    state.selectedRecordId = selected ? selected.id : '';
    host.innerHTML = slice.map(record => renderRecordRow(record)).join('');
    renderPager(total, totalPages, page);
    renderDetail(selected);
    hydrateThumbs(host);
    bindRecordRows(host, slice, false);
    $$('#status-filter .chip').forEach(button => {
      button.classList.toggle('is-active', (button.dataset.status || '') === state.statusFilter);
    });
  }

  function bindRecordRows(host, items, pending) {
    $$('.record-row', host).forEach(row => {
      row.addEventListener('click', () => {
        const id = pending ? row.dataset.pendingId : row.dataset.recordId;
        if (!id) return;
        state.selectedRecordId = id;
        $$('.record-row', host).forEach(other => other.classList.toggle('is-selected', other === row));
        if (pending) {
          const item = items.filter(entry => entry.id === id)[0];
          renderDetail(item ? Object.assign({ __pending: true }, item) : null);
        } else {
          const record = state.records.filter(entry => entry.id === id)[0];
          renderDetail(record || null);
        }
      });
    });
  }

  function renderPending() {
    renderQueue();
    const pending = derive.pendingItems();
    setText('rec-tab-pending-count', format.int(pending.length));
    setText('rec-kpi-pending', format.int(pending.length));
    if (state.route.view === 'records' && state.recordsTab === 'pending') renderRecords();
  }

  function setDataState(kind, message) {
    const subtitle = $('#ov-subtitle');
    if (subtitle) subtitle.textContent = kind === 'error' ? '审核数据加载失败：' + message : '正在同步审核数据…';
    const recent = $('#recent-list');
    if (recent) recent.innerHTML = emptyState(kind, kind === 'error' ? '审核数据加载失败' : '正在读取审核记录', message || '正在同步审核历史、供应商统计与本地图片。');
    const queue = $('#queue-list');
    if (queue) queue.innerHTML = kind === 'error'
      ? emptyState('error', '队列不可用', message)
      : '<div class="queue-empty"><strong>正在同步实时队列…</strong></div>';
    const records = $('#records-list');
    if (records) records.innerHTML = emptyState(kind, kind === 'error' ? '审核数据加载失败' : '正在读取审核记录', message || '正在同步审核历史、供应商统计与本地图片。');
    const detailEmpty = $('#detail-empty');
    const detailContent = $('#detail-content');
    if (detailContent) detailContent.setAttribute('hidden', '');
    if (detailEmpty) {
      detailEmpty.removeAttribute('hidden');
      detailEmpty.innerHTML = '<span class="detail-placeholder-mark">' + (kind === 'error' ? '!' : '···') + '</span><strong>' + (kind === 'error' ? '审核数据加载失败' : '正在同步记录') + '</strong><p>' + esc(message || '请稍候…') + '</p>';
    }
  }

  IG.views = {
    renderOverview: renderOverview,
    renderRecords: renderRecords,
    renderStats: renderRecordsSummary,
    renderPending: renderPending,
    renderTrend: renderTrend,
    setDataState: setDataState,
    renderPolicy: renderPolicy,
    hydrateThumbs: hydrateThumbs
  };
})();
