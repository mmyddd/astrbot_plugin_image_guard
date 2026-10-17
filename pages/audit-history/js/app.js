
/* ============================================================
   ImageGuard · 应用编排 v3
   路由 / 主题 / 数据刷新 / 启动
   ============================================================ */
(function () {
  'use strict';

  const IG = window.IG;
  const $ = IG.$;
  const $$ = IG.$$;
  const show = IG.show;
  const format = IG.format;
  const state = IG.state;
  const toast = IG.toast;

  const VIEWS = ['overview', 'records', 'settings', 'wizard'];
  const SECTIONS = ['rules', 'providers', 'gate', 'scope', 'media', 'cache', 'dataset'];
  const RECORD_TABS = ['all', 'pending', 'failed'];

  let statusTimer = 0;
  let lastHash = '';
  let loaded = false;

  /* ── 状态提示 ─────────────────────────────────────────── */
  function setStatus(text, kind) {
    const chip = $('#data-status');
    if (!chip) return;
    chip.textContent = text;
    chip.dataset.state = kind || 'idle';
    chip.title = text;
    if (statusTimer) window.clearTimeout(statusTimer);
    if (kind === 'success') {
      statusTimer = window.setTimeout(() => {
        chip.textContent = state.lastSyncAt ? '同步于 ' + format.time(state.lastSyncAt) : '已就绪';
        chip.dataset.state = 'idle';
      }, 4000);
    }
  }

  function setBusy(busy) {
    const button = $('#refresh-btn');
    if (button) button.classList.toggle('is-busy', Boolean(busy));
  }

  /* ── 路由 ─────────────────────────────────────────────── */
  function parseHash() {
    const raw = String(window.location.hash || '').replace(/^#/, '');
    const parts = raw.split('?');
    const segments = (parts[0] || '').split('/').filter(Boolean);
    let view = segments[0] || 'overview';
    if (view === 'settings' && segments[1] === 'wizard') view = 'wizard';
    if (VIEWS.indexOf(view) < 0) view = 'overview';
    const params = new URLSearchParams(parts[1] || '');
    let section = params.get('section') || 'scope';
    if (SECTIONS.indexOf(section) < 0) section = 'scope';
    const page = Math.max(1, Number(params.get('page') || 1) || 1);
    const tab = params.get('tab') || '';
    const recordId = view === 'records' && segments[1] ? decodeURIComponent(segments[1]) : '';
    return {
      view: view,
      section: section,
      page: page,
      tab: tab,
      tag: params.get('tag') || '',
      query: params.get('q') || '',
      status: params.get('status') || '',
      recordId: recordId
    };
  }

  function currentHash() {
    if (state.route.view === 'records') {
      const params = new URLSearchParams();
      if (state.route.page > 1) params.set('page', String(state.route.page));
      if (state.recordsTab && state.recordsTab !== 'all') params.set('tab', state.recordsTab);
      if (state.tagFilter) params.set('tag', state.tagFilter);
      if (state.statusFilter && state.recordsTab !== 'failed') params.set('status', state.statusFilter);
      if (state.searchQuery.trim()) params.set('q', state.searchQuery.trim());
      const suffix = params.toString();
      return '#/records' + (suffix ? '?' + suffix : '');
    }
    if (state.route.view === 'settings') {
      return '#/settings?section=' + state.route.section;
    }
    if (state.route.view === 'wizard') return '#/wizard';
    return '#/' + state.route.view;
  }

  function syncRoute(replace) {
    const hash = currentHash();
    lastHash = hash;
    if (window.location.hash === hash) return;
    if (replace) window.history.replaceState(null, '', hash);
    else window.location.hash = hash;
  }

  function applyRoute(scroll) {
    const route = parseHash();
    const previousView = state.route.view;
    state.route.view = route.view;
    state.route.section = route.section;
    if (route.view === 'records') {
      state.route.page = route.page;
      state.recordsTab = RECORD_TABS.indexOf(route.tab) >= 0 ? route.tab : 'all';
      state.tagFilter = route.tag;
      state.searchQuery = route.query;
      state.statusFilter = route.status || (state.recordsTab === 'failed' ? 'failed' : '');
      if (route.recordId) state.selectedRecordId = route.recordId;
      const input = $('#rec-search');
      if (input && input.value !== route.query) input.value = route.query;
    }
    lastHash = currentHash();

    $$('[data-view-panel]').forEach(panel => {
      show(panel, panel.dataset.viewPanel === route.view);
    });
    const activeNav = route.view === 'wizard' ? 'settings' : route.view;
    $$('[data-nav]').forEach(link => {
      const active = link.dataset.nav === activeNav;
      link.classList.toggle('is-active', active);
      if (active) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
    $$('[data-section-tab]').forEach(tab => {
      const active = (route.view === 'settings' || route.view === 'wizard') && tab.dataset.section === route.section;
      tab.classList.toggle('is-active', active);
      if (active) tab.setAttribute('aria-current', 'true');
      else tab.removeAttribute('aria-current');
    });
    $$('[data-section-panel]').forEach(panel => {
      show(panel, route.view === 'settings' && panel.dataset.sectionPanel === route.section);
    });

    if (route.view === 'overview') {
      IG.views.renderOverview();
    }
    if (route.view === 'records') {
      IG.views.renderRecords();
    }
    if (route.view === 'settings') {
      if (!state.configLoaded) {
        const label = $('#save-state');
        if (label) {
          label.textContent = '正在读取配置…';
          label.dataset.state = '';
        }
      }
      const pending = IG.settings.loadConfig(false);
      if (pending && typeof pending.then === 'function') {
        pending.then(() => IG.settings.renderSettingsNavMeta()).catch(() => {});
      } else {
        IG.settings.renderSettingsNavMeta();
      }
      if (previousView !== 'settings' && scroll) {
        window.requestAnimationFrame(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
      }
    }
    if (route.view === 'wizard') {
      IG.settings.openWizard();
      if (previousView !== 'wizard' && scroll) {
        window.requestAnimationFrame(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
      }
    }
    document.title = route.view === 'records'
      ? '审核记录 · ImageGuard'
      : route.view === 'settings'
        ? '配置中心 · ImageGuard'
        : route.view === 'wizard'
          ? '配置向导 · ImageGuard'
          : '审核概览 · ImageGuard';
  }

  function handleHashChange() {
    const next = window.location.hash || '#/overview';
    if (next === lastHash) return;
    if (state.route.view === 'settings' && IG.settings.isDirty()) {
      const nextRoute = parseHash();
      if (nextRoute.view !== 'settings') {
        const previous = lastHash;
        window.history.replaceState(null, '', previous);
        IG.confirm({
          title: '未保存的修改',
          message: '当前配置有未保存的修改，离开将丢弃这些改动。',
          confirmText: '放弃并离开'
        }).then(ok => {
          if (!ok) return;
          state.configDirty = false;
          if (state.fullConfig) IG.settings.fillForm(state.fullConfig);
          window.location.hash = next;
        });
        return;
      }
    }
    applyRoute(true);
  }

  /* ── 数据刷新 ─────────────────────────────────────────── */
  async function reloadRecords() {
    if (!IG.bridge.isEmbedded()) {
      setStatus('需要从 AstrBot 插件页打开', 'error');
      IG.views.setDataState('error', '请从 AstrBot 面板的插件页进入本页面。');
      return;
    }
    if (state.recordsLoading) return;
    state.recordsLoading = true;
    setBusy(true);
    setStatus('正在同步审核数据…', 'loading');
    try {
      const response = await IG.api.list() || {};
      IG.imageAsset.clear();
      const records = Array.isArray(response.records) ? response.records.slice() : [];
      state.records = records.map(record => Object.assign({}, record, {
        tags: Array.isArray(record.tags) ? record.tags : []
      }));
      state.providerStats = response.provider_stats || {};
      state.cacheStats = response.cache_stats || {};
      state.storageSize = response.storage_size || {};
      state.runtimeStats = response.runtime_stats || {};
      if (Array.isArray(response.pending)) state.pending = response.pending;
      state.auditConfig = response.audit_config || {};
      state.totalRecords = Number(response.total_records || state.records.length);
      state.serverTime = response.server_time || '';
      state.lastSyncAt = response.server_time || new Date().toISOString();
      if (state.selectedRecordId && !state.records.some(record => record.id === state.selectedRecordId)) {
        state.selectedRecordId = '';
      }
      IG.views.renderOverview();
      IG.views.renderRecords();
      IG.settings.refreshStorage();
      IG.settings.renderSettingsNavMeta();
      setStatus('已同步 ' + format.int(state.records.length) + ' 条记录', 'success');
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      setStatus('同步失败', 'error');
      IG.views.setDataState('error', message);
      toast('加载失败：' + message, 'error');
    } finally {
      state.recordsLoading = false;
      loaded = true;
      setBusy(false);
    }
  }

  /* ── 处理中任务轮询 ───────────────────────────────────── */
  let pendingTimer = 0;
  let pendingTicking = 0;

  function startPendingTicker() {
    if (pendingTicking) return;
    pendingTicking = window.setInterval(() => {
      if (!state.pending.length) return;
      if (document.hidden) return;
      state.pending = state.pending.map(item => Object.assign({}, item, {
        elapsed_seconds: (Number(item.elapsed_seconds) || 0) + 1
      }));
      IG.views.renderPending();
    }, 1000);
  }

  function hasActivePending() {
    return Boolean(state.pending && state.pending.length);
  }

  function schedulePendingPoll() {
    if (pendingTimer) return;
    pendingTimer = window.setTimeout(async () => {
      pendingTimer = 0;
      if (!IG.bridge.isEmbedded() || document.hidden) {
        schedulePendingPoll();
        return;
      }
      const before = state.pending.length;
      try {
        const response = await IG.api.pending() || {};
        state.pending = Array.isArray(response.pending) ? response.pending : [];
        IG.views.renderPending();
        if (before > 0 && !state.pending.length) await reloadRecords();
      } catch (error) {
        // 轮询失败不打扰用户，等下一次
      }
      schedulePendingPoll();
    }, hasActivePending() ? 1500 : 4000);
  }

  function stopPendingPoll() {
    if (pendingTimer) window.clearTimeout(pendingTimer);
    pendingTimer = 0;
  }

  /* ── 记录操作 ─────────────────────────────────────────── */
  async function deleteRecord(id) {
    if (!id) return;
    const ok = await IG.confirm({
      title: '删除记录',
      message: '仅删除这条审核历史，已保存的图片文件会在下次清理孤立图片时移除。',
      confirmText: '删除'
    });
    if (!ok) return;
    try {
      await IG.api.deleteRecord(id);
      state.records = state.records.filter(record => record.id !== id);
      state.totalRecords = Math.max(0, state.totalRecords - 1);
      if (state.selectedRecordId === id) state.selectedRecordId = '';
      IG.views.renderOverview();
      IG.views.renderRecords();
      toast('记录已删除', 'success');
    } catch (error) {
      toast('删除失败：' + (error && error.message ? error.message : error), 'error');
    }
  }

  async function clearRecords() {
    const ok = await IG.confirm({
      title: '清空审核历史',
      message: '将删除全部审核记录，此操作不可撤销。已保存的图片文件不会被立即删除。',
      confirmText: '全部清空'
    });
    if (!ok) return;
    try {
      await IG.api.clearRecords();
      IG.imageAsset.clear();
      state.records = [];
      state.totalRecords = 0;
      state.tagFilter = '';
      state.statusFilter = '';
      state.searchQuery = '';
      state.recordsTab = 'all';
      state.selectedRecordId = '';
      state.route.page = 1;
      const input = $('#rec-search');
      if (input) input.value = '';
      syncRoute(true);
      IG.views.renderOverview();
      IG.views.renderRecords();
      toast('审核历史已清空', 'success');
    } catch (error) {
      toast('清空失败：' + (error && error.message ? error.message : error), 'error');
    }
  }

  function exportRecords() {
    const list = IG.derive.filteredRecords();
    if (!list.length) {
      toast('当前没有可导出的记录', 'info');
      return;
    }
    IG.exportCsv(list, 'image-guard-records-' + format.dayKey(new Date()) + '.csv');
    toast('已导出 ' + format.int(list.length) + ' 条记录', 'success');
  }

  function openRecord(id) {
    if (!id) return;
    state.recordsTab = 'all';
    state.statusFilter = '';
    state.selectedRecordId = id;
    if (state.route.view !== 'records') {
      window.location.hash = '#/records';
      return;
    }
    IG.views.renderRecords();
  }

  function openPluginConfig() {
    try {
      window.parent.postMessage({
        channel: 'astrbot-plugin-page',
        type: 'navigate',
        payload: { path: '/plugins' }
      }, '*');
      toast('已请求打开插件配置页', 'info');
    } catch (error) {
      toast('无法打开插件配置页：' + (error && error.message ? error.message : error), 'error');
    }
  }

  /* ── 交互绑定 ─────────────────────────────────────────── */
  function bindGlobal() {
    $('#refresh-btn').addEventListener('click', reloadRecords);
    const ovRefresh = $('#ov-refresh');
    if (ovRefresh) ovRefresh.addEventListener('click', reloadRecords);
    $('#theme-toggle').addEventListener('click', () => {
      const dark = document.documentElement.getAttribute('data-theme') !== 'dark';
      IG.applyTheme(dark);
      toast(dark ? '已切换到深色主题' : '已切换到浅色主题', 'info');
    });
    const pluginConfig = $('#open-plugin-config');
    if (pluginConfig) pluginConfig.addEventListener('click', openPluginConfig);

    $$('[data-goto]').forEach(button => {
      button.addEventListener('click', () => {
        const view = button.dataset.goto;
        if (view === 'settings' && button.dataset.section) state.route.section = button.dataset.section;
        if (view === 'records') state.route.page = 1;
        window.location.hash = view === 'settings' && button.dataset.section
          ? '#/settings?section=' + button.dataset.section
          : '#/' + view;
      });
    });

    $$('[data-section-tab]').forEach(tab => {
      tab.addEventListener('click', () => {
        state.route.section = tab.dataset.section;
        syncRoute(true);
        applyRoute(false);
      });
    });

    $$('#trend-range .seg-btn').forEach(button => {
      button.addEventListener('click', () => {
        state.trendRange = Number(button.dataset.range) || 14;
        IG.views.renderTrend();
      });
    });

    const search = $('#rec-search');
    let searchTimer = 0;
    search.addEventListener('input', () => {
      if (searchTimer) window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(() => {
        state.searchQuery = search.value;
        state.route.page = 1;
        syncRoute(true);
        IG.views.renderRecords();
      }, 180);
    });

    $$('#status-filter .chip').forEach(button => {
      button.addEventListener('click', () => {
        state.statusFilter = button.dataset.status || '';
        state.recordsTab = state.statusFilter === 'failed' ? 'failed' : 'all';
        state.route.page = 1;
        syncRoute(true);
        IG.views.renderRecords();
      });
    });

    $$('#records-tabs .list-tab').forEach(button => {
      button.addEventListener('click', () => {
        state.recordsTab = button.dataset.recTab || 'all';
        state.statusFilter = state.recordsTab === 'failed' ? 'failed' : '';
        state.route.page = 1;
        syncRoute(true);
        IG.views.renderRecords();
      });
    });

    const tagToggle = $('#rec-tag-toggle');
    if (tagToggle) {
      tagToggle.addEventListener('click', () => {
        state.tagBarOpen = !state.tagBarOpen;
        IG.views.renderRecords();
      });
    }

    $('#rec-sort').addEventListener('click', () => {
      state.sortDesc = !state.sortDesc;
      IG.views.renderRecords();
    });

    $('#rec-page-size').addEventListener('change', event => {
      state.pageSize = Number(event.target.value) || 15;
      state.route.page = 1;
      syncRoute(true);
      IG.views.renderRecords();
    });

    $('#rec-export').addEventListener('click', exportRecords);
    $('#rec-clear').addEventListener('click', clearRecords);

    const openWizard = $('#open-wizard');
    if (openWizard) {
      openWizard.addEventListener('click', () => {
        window.location.hash = '#/wizard';
      });
    }

    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && IG.bridge.isEmbedded()) {
        IG.api.pending().then(response => {
          state.pending = Array.isArray(response && response.pending) ? response.pending : [];
          IG.views.renderPending();
        }).catch(() => {});
      }
    });

    $('#lightbox-close').addEventListener('click', IG.lightbox.close);
    $('#lightbox').addEventListener('click', event => {
      if (event.target === event.currentTarget) IG.lightbox.close();
    });

    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        if (IG.lightbox.isOpen()) {
          IG.lightbox.close();
        } else if (!$('#provider-modal').hasAttribute('hidden')) {
          IG.settings.closeProviderModal();
        } else if (IG.isConfirmOpen()) {
          IG.closeConfirm(false);
        } else if (state.route.view === 'wizard') {
          window.location.hash = '#/settings?section=scope';
        }
        return;
      }
      if (event.key === 'r' && !event.metaKey && !event.ctrlKey && !event.altKey) {
        const tag = (event.target.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
        reloadRecords();
      }
    });

    window.addEventListener('hashchange', handleHashChange);
    window.addEventListener('beforeunload', event => {
      stopPendingPoll();
      if (!IG.settings.isDirty()) return;
      event.preventDefault();
      event.returnValue = '';
    });
  }

  /* ── 启动 ─────────────────────────────────────────────── */
  function bootstrapBridge() {
    IG.bridge.ready().then(context => {
      IG.applyTheme(Boolean(context && context.isDark));
      IG.bridge.onContext(next => IG.applyTheme(Boolean(next && next.isDark)));
    }).catch(() => {
      IG.applyTheme(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
      setStatus('页面初始化失败', 'error');
    }).then(() => {
      applyRoute(false);
      IG.views.setDataState('loading', '正在同步审核历史、供应商统计与本地图片。');
      return reloadRecords();
    }).then(() => {
      const route = parseHash();
      if (route.recordId) {
        state.selectedRecordId = route.recordId;
        IG.views.renderRecords();
      }
      startPendingTicker();
      schedulePendingPoll();
    });
  }

  function start() {
    IG.initConfirm();
    IG.settings.init();
    bindGlobal();
    const version = $('#brand-version');
    if (version) version.textContent = 'v1.13.0';

    if (!IG.bridge.isEmbedded()) {
      IG.applyTheme(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
      applyRoute(false);
      setStatus('请从 AstrBot 插件页打开', 'error');
      IG.views.setDataState('error', '本页面需要通过 AstrBot 面板的插件页进入，才能读取审核数据。');
      return;
    }
    bootstrapBridge();
  }

  IG.app = {
    reloadRecords: reloadRecords,
    deleteRecord: deleteRecord,
    clearRecords: clearRecords,
    exportRecords: exportRecords,
    setStatus: setStatus,
    syncRoute: syncRoute,
    applyRoute: applyRoute,
    openRecord: openRecord
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
