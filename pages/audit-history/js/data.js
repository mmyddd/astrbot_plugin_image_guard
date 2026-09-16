/* ============================================================
   ImageGuard · 数据层
   状态容器 / Bridge 封装 / API 客户端 / 格式化 / 通用交互
   ============================================================ */
(function () {
  'use strict';

  const IG = (window.IG = window.IG || {});

  /* ── DOM 工具 ─────────────────────────────────────────── */
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));

  function esc(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function show(node, visible) {
    if (!node) return;
    if (visible) node.removeAttribute('hidden');
    else node.setAttribute('hidden', '');
  }

  /* ── 格式化 ───────────────────────────────────────────── */
  const numberFmt = new Intl.NumberFormat('zh-CN');

  const format = {
    int(value) {
      const n = Number(value);
      return numberFmt.format(Number.isFinite(n) ? Math.round(n) : 0);
    },
    percent(value, digits) {
      const n = Number(value);
      if (!Number.isFinite(n)) return '—';
      return n.toFixed(digits === undefined ? 0 : digits) + '%';
    },
    bytes(value) {
      const n = Number(value);
      if (!Number.isFinite(n) || n <= 0) return '0 B';
      if (n < 1024) return n + ' B';
      if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
      if (n < 1073741824) return (n / 1048576).toFixed(2) + ' MB';
      return (n / 1073741824).toFixed(2) + ' GB';
    },
    duration(seconds) {
      const n = Number(seconds);
      if (!Number.isFinite(n) || n <= 0) return '不禁言';
      if (n < 60) return n + ' 秒';
      if (n < 3600) return Math.round(n / 60) + ' 分钟';
      if (n < 86400) return (n / 3600).toFixed(n % 3600 === 0 ? 0 : 1) + ' 小时';
      return (n / 86400).toFixed(n % 86400 === 0 ? 0 : 1) + ' 天';
    },
    parseTime(value) {
      const raw = String(value || '').trim();
      if (!raw) return null;
      const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/);
      if (!m) {
        const fallback = new Date(raw);
        return Number.isNaN(fallback.getTime()) ? null : fallback;
      }
      return new Date(
        Number(m[1]), Number(m[2]) - 1, Number(m[3]),
        Number(m[4]), Number(m[5]), Number(m[6] || 0)
      );
    },
    time(value) {
      const date = format.parseTime(value);
      if (!date) return String(value || '—');
      const now = new Date();
      const sameYear = date.getFullYear() === now.getFullYear();
      const pad = n => String(n).padStart(2, '0');
      return (sameYear ? '' : date.getFullYear() + '-') +
        pad(date.getMonth() + 1) + '-' + pad(date.getDate()) + ' ' +
        pad(date.getHours()) + ':' + pad(date.getMinutes());
    },
    fullTime(value) {
      const date = format.parseTime(value);
      if (!date) return String(value || '—');
      const pad = n => String(n).padStart(2, '0');
      return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate()) +
        ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes()) + ':' + pad(date.getSeconds());
    },
    relative(value) {
      const date = format.parseTime(value);
      if (!date) return '—';
      const diff = Date.now() - date.getTime();
      if (diff < 0) return '刚刚';
      const minute = 60000, hour = 3600000, day = 86400000;
      if (diff < minute) return '刚刚';
      if (diff < hour) return Math.floor(diff / minute) + ' 分钟前';
      if (diff < day) return Math.floor(diff / hour) + ' 小时前';
      if (diff < day * 30) return Math.floor(diff / day) + ' 天前';
      return format.time(value);
    },
    dayKey(date) {
      const pad = n => String(n).padStart(2, '0');
      return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate());
    },
    initials(value) {
      const text = String(value || '').trim();
      if (!text) return '?';
      return text.slice(0, 2);
    }
  };

  /* ── 状态 ─────────────────────────────────────────────── */
  const state = {
    records: [],
    providerStats: {},
    cacheStats: {},
    storageSize: {},
    auditConfig: {},
    serverTime: '',
    totalRecords: 0,
    truncated: false,
    fullConfig: {},
    configLoaded: false,
    configDirty: false,
    recordsLoading: false,
    configLoading: false,
    saving: false,
    uploading: false,
    route: { view: 'overview', section: 'rules', page: 1, tag: '', query: '', status: '' },
    sortDesc: true,
    pageSize: 15,
    tagFilter: '',
    statusFilter: '',
    searchQuery: '',
    trendRange: 14,
    providerStatus: {},
    providerDraft: null,
    providerEditIndex: -1,
    lastSyncAt: null,
    bridgeContext: null
  };

  /* ── Bridge ───────────────────────────────────────────── */
  const bridge = {
    api: null,
    isEmbedded() {
      return Boolean(window.AstrBotPluginPage && typeof window.AstrBotPluginPage.apiGet === 'function');
    },
    context() {
      return state.bridgeContext;
    },
    isDark() {
      const ctx = state.bridgeContext;
      return Boolean(ctx && ctx.isDark);
    },
    async ready() {
      if (!this.isEmbedded()) return null;
      try {
        state.bridgeContext = await window.AstrBotPluginPage.ready();
      } catch (error) {
        state.bridgeContext = null;
        throw error;
      }
      return state.bridgeContext;
    },
    onContext(handler) {
      const api = window.AstrBotPluginPage;
      if (!api) return function () {};
      const listener = typeof api.onContext === 'function'
        ? api.onContext.bind(api)
        : api.onContextChange
          ? api.onContextChange.bind(api)
          : null;
      if (!listener) return function () {};
      const off = listener(handler);
      return typeof off === 'function' ? off : function () {};
    }
  };

  /* ── API 客户端 ───────────────────────────────────────── */
  function requireBridge() {
    if (!bridge.isEmbedded()) {
      throw new Error('页面需要通过 AstrBot 插件页打开');
    }
    return window.AstrBotPluginPage;
  }

  const api = {
    async get(endpoint, params) {
      return requireBridge().apiGet(endpoint, params);
    },
    async post(endpoint, body) {
      return requireBridge().apiPost(endpoint, body);
    },
    list() {
      return api.get('audit/list');
    },
    clearRecords() {
      return api.post('audit/clear', {});
    },
    deleteRecord(id) {
      return api.post('audit/delete', { id: id });
    },
    configGet() {
      return api.get('audit/config');
    },
    configUpdate(patch) {
      return api.post('audit/config/update', patch);
    },
    cacheClear() {
      return api.post('audit/cache/clear', {});
    },
    storagePrune() {
      return api.post('audit/storage/prune', {});
    },
    providerTest(provider) {
      return api.post('audit/providers/test', { provider: provider });
    },
    providersSave(providers) {
      return api.post('audit/providers/save', { providers: providers });
    }
  };

  /* ── 派生数据 ─────────────────────────────────────────── */
  const derive = {
    disposeStatus(record) {
      const recalled = Boolean(record.recalled);
      const banned = Boolean(record.banned);
      const wanted = Number(record.ban_duration) > 0;
      if (recalled && (banned || !wanted)) return 'success';
      if (!recalled && !banned) return 'failed';
      return 'partial';
    },
    disposeLabel(status) {
      return { success: '处置成功', failed: '处置失败', partial: '部分执行' }[status] || '未知';
    },
    dailySeries(days) {
      const buckets = new Map();
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      const labels = [];
      for (let i = days - 1; i >= 0; i -= 1) {
        const date = new Date(today.getTime() - i * 86400000);
        const key = format.dayKey(date);
        buckets.set(key, 0);
        labels.push({ key: key, date: date });
      }
      state.records.forEach(record => {
        const date = format.parseTime(record.time);
        if (!date) return;
        const key = format.dayKey(date);
        if (buckets.has(key)) buckets.set(key, buckets.get(key) + 1);
      });
      return labels.map(item => ({
        key: item.key,
        label: (item.date.getMonth() + 1) + '/' + item.date.getDate(),
        value: buckets.get(item.key) || 0
      }));
    },
    providerEntries() {
      const entries = Object.keys(state.providerStats || {}).map(name => ({
        name: name,
        count: Number(state.providerStats[name]) || 0
      }));
      entries.sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, 'zh-CN'));
      const total = entries.reduce((sum, item) => sum + item.count, 0);
      return { entries: entries, total: total };
    },
    tagCounts() {
      const counts = new Map();
      state.records.forEach(record => {
        (record.tags || []).forEach(tag => counts.set(tag, (counts.get(tag) || 0) + 1));
      });
      return Array.from(counts.entries())
        .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'zh-CN'));
    },
    filteredRecords() {
      const query = state.searchQuery.trim().toLowerCase();
      const tag = state.tagFilter;
      const status = state.statusFilter;
      const list = state.records.filter(record => {
        if (tag && !(record.tags || []).includes(tag)) return false;
        if (status && derive.disposeStatus(record) !== status) return false;
        if (!query) return true;
        const haystack = [
          record.user_name, record.user_id, record.group_id, record.reason,
          (record.tags || []).join(' ')
        ].filter(Boolean).join(' ').toLowerCase();
        return haystack.indexOf(query) >= 0;
      });
      list.sort((a, b) => {
        const ta = format.parseTime(a.time);
        const tb = format.parseTime(b.time);
        const va = ta ? ta.getTime() : 0;
        const vb = tb ? tb.getTime() : 0;
        if (va === vb) return String(a.id || '').localeCompare(String(b.id || ''));
        return state.sortDesc ? vb - va : va - vb;
      });
      return list;
    }
  };

  /* ── 轻提示 ───────────────────────────────────────────── */
  function toast(message, tone) {
    const stack = $('#toast-stack');
    if (!stack) return;
    const node = document.createElement('div');
    node.className = 'toast';
    node.dataset.tone = tone || 'info';
    node.setAttribute('role', 'status');
    node.textContent = message;
    stack.appendChild(node);
    window.setTimeout(() => {
      node.classList.add('is-leaving');
      window.setTimeout(() => node.remove(), 200);
    }, 3200);
  }

  /* ── 确认弹窗 ─────────────────────────────────────────── */
  let confirmResolver = null;
  let confirmPreviousFocus = null;

  function closeConfirm(value) {
    const overlay = $('#confirm-modal');
    if (!overlay || !confirmResolver) return;
    show(overlay, false);
    const resolve = confirmResolver;
    confirmResolver = null;
    if (confirmPreviousFocus && typeof confirmPreviousFocus.focus === 'function') {
      confirmPreviousFocus.focus();
    }
    confirmPreviousFocus = null;
    resolve(value);
  }

  function confirmAction(options) {
    const opts = options || {};
    const overlay = $('#confirm-modal');
    if (!overlay) return Promise.resolve(window.confirm(opts.message || '确定继续？'));
    if (confirmResolver) closeConfirm(false);
    confirmPreviousFocus = document.activeElement;
    $('#confirm-title').textContent = opts.title || '确认操作';
    $('#confirm-message').textContent = opts.message || '';
    $('#confirm-note').textContent = opts.note || '';
    const okBtn = $('#confirm-ok');
    okBtn.textContent = opts.confirmText || '确定';
    okBtn.className = 'btn ' + (opts.danger === false ? 'btn-primary' : 'btn-danger');
    show(overlay, true);
    return new Promise(resolve => {
      confirmResolver = resolve;
      window.requestAnimationFrame(() => okBtn.focus());
    });
  }

  function initConfirm() {
    const overlay = $('#confirm-modal');
    if (!overlay) return;
    $('#confirm-ok').addEventListener('click', () => closeConfirm(true));
    $('#confirm-cancel').addEventListener('click', () => closeConfirm(false));
    overlay.addEventListener('click', event => {
      if (event.target === overlay) closeConfirm(false);
    });
  }

  function isConfirmOpen() {
    const overlay = $('#confirm-modal');
    return Boolean(overlay && !overlay.hasAttribute('hidden'));
  }

  /* ── 图片资源（file_token 为一次性，需转 blob） ───────── */
  const imageAssets = new Map();

  function isSingleUseToken(url) {
    return typeof url === 'string' && /\/api\/file\/[^/?#]+/.test(url);
  }

  const imageAsset = {
    resolve(url) {
      if (!url) return Promise.resolve('');
      if (!isSingleUseToken(url)) return Promise.resolve(url);
      const cached = imageAssets.get(url);
      if (cached) return cached.promise;
      const entry = { promise: null, objectUrl: '' };
      entry.promise = fetch(url, { credentials: 'same-origin' })
        .then(response => {
          if (!response.ok) throw new Error('图片请求失败 (' + response.status + ')');
          return response.blob();
        })
        .then(blob => {
          entry.objectUrl = URL.createObjectURL(blob);
          if (imageAssets.get(url) !== entry) {
            URL.revokeObjectURL(entry.objectUrl);
            return '';
          }
          return entry.objectUrl;
        })
        .catch(error => {
          imageAssets.delete(url);
          throw error;
        });
      imageAssets.set(url, entry);
      return entry.promise;
    },
    clear() {
      imageAssets.forEach(entry => {
        if (entry.objectUrl) URL.revokeObjectURL(entry.objectUrl);
      });
      imageAssets.clear();
    }
  };

  /* ── 图片预览 ─────────────────────────────────────────── */
  let lightboxRequest = 0;
  let lightboxPreviousFocus = null;

  const lightbox = {
    open(url, caption, trigger) {
      if (!url) return;
      lightboxPreviousFocus = trigger || document.activeElement;
      const request = ++lightboxRequest;
      imageAsset.resolve(url).then(resolved => {
        if (request !== lightboxRequest || !resolved) return;
        const overlay = $('#lightbox');
        $('#lightbox-img').src = resolved;
        $('#lightbox-caption').textContent = caption || '';
        show(overlay, true);
        $('#lightbox-close').focus();
      }).catch(() => toast('图片加载失败', 'error'));
    },
    close() {
      lightboxRequest += 1;
      const overlay = $('#lightbox');
      if (!overlay) return;
      show(overlay, false);
      $('#lightbox-img').src = '';
      if (lightboxPreviousFocus && typeof lightboxPreviousFocus.focus === 'function') {
        lightboxPreviousFocus.focus();
      }
      lightboxPreviousFocus = null;
    },
    isOpen() {
      const overlay = $('#lightbox');
      return Boolean(overlay && !overlay.hasAttribute('hidden'));
    }
  };

  /* ── CSV 导出 ─────────────────────────────────────────── */
  function exportCsv(records, filename) {
    const header = ['时间', '用户', '用户ID', '来源', '判定理由', '标签', '撤回', '禁言', '禁言时长(秒)'];
    const rows = records.map(record => [
      record.time || '',
      record.user_name || '',
      record.user_id || '',
      record.group_id ? '群 ' + record.group_id : '私聊',
      record.reason || '',
      (record.tags || []).join(' '),
      record.recalled ? '已撤回' : '撤回失败',
      record.banned ? '已禁言' : '未禁言',
      record.ban_duration || 0
    ]);
    const csv = [header].concat(rows).map(line =>
      line.map(cell => '"' + String(cell).replace(/"/g, '""') + '"').join(',')
    ).join('\r\n');
    const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename || 'image-guard-records.csv';
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  /* ── 主题 ─────────────────────────────────────────────── */
  function applyTheme(dark) {
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    const meta = $('#theme-color');
    if (meta) meta.setAttribute('content', dark ? '#0b0f16' : '#f4f6fb');
  }

  IG.$ = $;
  IG.$$ = $$;
  IG.esc = esc;
  IG.show = show;
  IG.format = format;
  IG.state = state;
  IG.bridge = bridge;
  IG.api = api;
  IG.derive = derive;
  IG.toast = toast;
  IG.confirm = confirmAction;
  IG.closeConfirm = closeConfirm;
  IG.isConfirmOpen = isConfirmOpen;
  IG.initConfirm = initConfirm;
  IG.imageAsset = imageAsset;
  IG.lightbox = lightbox;
  IG.exportCsv = exportCsv;
  IG.applyTheme = applyTheme;
})();
