/* ============================================================
   ImageGuard · 配置中心
   规则编辑 / 供应商管理 / 脏数据跟踪 / 维护操作
   ============================================================ */
(function () {
  'use strict';

  const IG = window.IG;
  const $ = IG.$;
  const $$ = IG.$$;
  const esc = IG.esc;
  const show = IG.show;
  const format = IG.format;
  const state = IG.state;
  const toast = IG.toast;

  const TEMPLATES = [
    {
      key: 'openai_compatible',
      name: 'OpenAI 兼容',
      desc: '任何 OpenAI 格式的接口：GPT、DeepSeek、GLM、通义千问等',
      fields: ['api_key', 'base_url', 'model']
    },
    {
      key: 'modelscope',
      name: 'ModelScope',
      desc: '魔搭推理 API，格式兼容 OpenAI，支持 Qwen / DeepSeek-VL',
      fields: ['api_key', 'base_url', 'model']
    },
    {
      key: 'astrbot_provider',
      name: 'AstrBot 当前 Provider',
      desc: '直接复用 AstrBot 已配置的对话模型，无需填写 Key',
      fields: []
    }
  ];

  const TEMPLATE_LABELS = {};
  TEMPLATES.forEach(item => { TEMPLATE_LABELS[item.key] = item.name; });

  const RULE_FIELDS = ['sensitive_texts', 'forbidden_descriptions'];

  const NUMERIC_FIELDS = [
    'llm_max_tokens', 'llm_timeout_seconds', 'check_probability', 'ban_duration',
    'compressed_image_max_bytes', 'audit_history_max_records',
    'audit_cache_threshold', 'audit_cache_max_entries'
  ];

  const TEXTAREA_FIELDS = ['cfg-group_scope', 'cfg-private_scope'];
  const CHECKBOX_FIELDS = [
    'cfg-enable_recall', 'cfg-keep_compressed_image_in_temp',
    'cfg-debug_log_llm_response', 'cfg-audit_cache_enabled'
  ];

  let baseline = '';
  let rules = { sensitive_texts: [], forbidden_descriptions: [] };
  let reportTargets = [];

  /* ── 规则编辑器 ───────────────────────────────────────── */
  function mountRuleEditors() {
    RULE_FIELDS.forEach(field => {
      const host = document.getElementById('editor-' + field);
      if (!host) return;
      host.innerHTML =
        '<div class="rule-input-row">' +
        '<input class="input" type="text" data-rule-input="' + field + '" placeholder="输入内容后回车添加" spellcheck="false">' +
        '<button class="btn" type="button" data-rule-add="' + field + '">添加</button>' +
        '</div>' +
        '<div class="rule-tags" data-rule-tags="' + field + '"></div>';
      const input = host.querySelector('[data-rule-input]');
      input.addEventListener('keydown', event => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        addRule(field, input.value);
      });
      host.querySelector('[data-rule-add]').addEventListener('click', () => addRule(field, input.value));
    });
  }

  function addRule(field, rawValue) {
    const value = String(rawValue || '').trim();
    if (!value) return;
    if (rules[field].indexOf(value) >= 0) {
      toast('该规则已存在', 'info');
      return;
    }
    rules[field].push(value);
    const input = document.querySelector('[data-rule-input="' + field + '"]');
    if (input) input.value = '';
    renderRules();
    markDirty();
  }

  function removeRule(field, value) {
    rules[field] = rules[field].filter(item => item !== value);
    renderRules();
    markDirty();
  }

  function renderRules() {
    RULE_FIELDS.forEach(field => {
      const host = document.querySelector('[data-rule-tags="' + field + '"]');
      if (!host) return;
      if (!rules[field].length) {
        host.innerHTML = '<span class="rule-empty">尚未配置，未配置时该维度不参与判定</span>';
        return;
      }
      host.innerHTML = rules[field].map(value =>
        '<span class="rule-tag"><span title="' + esc(value) + '">' + esc(value) + '</span>' +
        '<button type="button" data-rule-remove="' + esc(value) + '" aria-label="删除规则 ' + esc(value) + '">' +
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"></path></svg>' +
        '</button></span>'
      ).join('');
      host.querySelectorAll('[data-rule-remove]').forEach(button => {
        button.addEventListener('click', () => removeRule(field, button.dataset.ruleRemove));
      });
    });
  }

  /* ── 群上报映射 ───────────────────────────────────────── */
  function renderMapper() {
    const host = $('#report-mapper');
    if (!host) return;
    if (!reportTargets.length) {
      host.innerHTML = '<div class="mapper-empty">暂无映射，未命中映射时使用上方的战报接收人</div>';
      return;
    }
    const rows = reportTargets.map((item, index) =>
      '<div class="mapper-row" data-mapper="' + index + '">' +
      '<input class="input" type="text" inputmode="numeric" data-map-field="source_group_id" placeholder="来源群号" value="' + esc(item.source_group_id || '') + '">' +
      '<select class="select" data-map-field="target_type">' +
      '<option value="private"' + (item.target_type === 'group' ? '' : ' selected') + '>私聊</option>' +
      '<option value="group"' + (item.target_type === 'group' ? ' selected' : '') + '>群聊</option>' +
      '</select>' +
      '<input class="input" type="text" inputmode="numeric" data-map-field="target_id" placeholder="接收目标 ID" value="' + esc(item.target_id || '') + '">' +
      '<button class="icon-btn" type="button" data-map-remove="' + index + '" aria-label="删除该映射">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"></path></svg>' +
      '</button></div>'
    ).join('');
    host.innerHTML = rows;
    $$('[data-map-field]', host).forEach(control => {
      control.addEventListener('input', () => {
        const row = control.closest('[data-mapper]');
        const index = Number(row.dataset.mapper);
        reportTargets[index][control.dataset.mapField] = control.value;
        markDirty();
      });
      control.addEventListener('change', () => {
        const row = control.closest('[data-mapper]');
        const index = Number(row.dataset.mapper);
        reportTargets[index][control.dataset.mapField] = control.value;
        markDirty();
      });
    });
    $$('[data-map-remove]', host).forEach(button => {
      button.addEventListener('click', () => {
        reportTargets.splice(Number(button.dataset.mapRemove), 1);
        renderMapper();
        markDirty();
      });
    });
  }

  /* ── 供应商列表 ───────────────────────────────────────── */
  function providerList() {
    return state.providerDraft || [];
  }

  function renderProviders() {
    const host = $('#provider-list');
    if (!host) return;
    const list = providerList();
    if (!list.length) {
      host.innerHTML = '<div class="mapper-empty">还没有配置供应商，插件会回退到 AstrBot 当前 Provider</div>';
      return;
    }
    host.innerHTML = list.map((provider, index) => {
      const template = provider.__template_key || 'openai_compatible';
      const status = state.providerStatus[index];
      const meta = [];
      if (provider.model) meta.push('<span>模型 <code>' + esc(provider.model) + '</code></span>');
      if (provider.base_url) meta.push('<span title="' + esc(provider.base_url) + '">地址 <code>' + esc(provider.base_url) + '</code></span>');
      if (provider.api_key) meta.push('<span>Key <code>' + esc(provider.api_key) + '</code></span>');
      if (!meta.length) meta.push('<span>复用 AstrBot 已配置的模型</span>');
      return '<div class="provider-card" data-provider="' + index + '">' +
        '<span class="provider-order">' + (index + 1) + '</span>' +
        '<div class="provider-main">' +
        '<strong>' + esc(provider.name || '未命名') +
        '<span class="provider-status" data-ok="' + (status ? (status.ok ? '1' : '0') : '2') + '">' +
        (status ? (status.ok ? '连通' : '异常') : esc(TEMPLATE_LABELS[template] || '自定义')) + '</span></strong>' +
        '<div class="provider-meta">' + meta.join('') + '</div>' +
        '</div>' +
        '<div class="provider-ops">' +
        '<button class="icon-btn" type="button" data-provider-move="up" data-index="' + index + '" aria-label="上移" title="上移"' + (index === 0 ? ' disabled' : '') + '>' +
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V6M6 12l6-6 6 6"></path></svg></button>' +
        '<button class="icon-btn" type="button" data-provider-move="down" data-index="' + index + '" aria-label="下移" title="下移"' + (index === list.length - 1 ? ' disabled' : '') + '>' +
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v13M6 12l6 6 6-6"></path></svg></button>' +
        '<button class="icon-btn" type="button" data-provider-edit="' + index + '" aria-label="编辑" title="编辑">' +
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4l10-10-4-4L4 16v4z"></path><path d="M14 6l4 4"></path></svg></button>' +
        '<button class="icon-btn" type="button" data-provider-copy="' + index + '" aria-label="复制" title="复制为副本">' +
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="8.5" y="8.5" width="11" height="11" rx="2.4"></rect><path d="M15.5 5.6A2.1 2.1 0 0 0 13.4 4H6.6A2.6 2.6 0 0 0 4 6.6v6.8c0 .9.7 1.7 1.6 2.1"></path></svg></button>' +
        '<button class="icon-btn" type="button" data-provider-drop="' + index + '" aria-label="删除" title="删除">' +
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 7h14M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"></path></svg></button>' +
        '</div></div>';
    }).join('');

    $$('[data-provider-move]', host).forEach(button => {
      button.addEventListener('click', () => {
        const index = Number(button.dataset.index);
        const target = button.dataset.providerMove === 'up' ? index - 1 : index + 1;
        const list2 = providerList().slice();
        if (target < 0 || target >= list2.length) return;
        const moved = list2.splice(index, 1)[0];
        list2.splice(target, 0, moved);
        state.providerDraft = list2;
        state.providerStatus = {};
        renderProviders();
        markDirty();
      });
    });
    $$('[data-provider-edit]', host).forEach(button => {
      button.addEventListener('click', () => openProviderModal(Number(button.dataset.providerEdit)));
    });
    $$('[data-provider-copy]', host).forEach(button => {
      button.addEventListener('click', () => duplicateProvider(Number(button.dataset.providerCopy)));
    });
    $$('[data-provider-drop]', host).forEach(button => {
      button.addEventListener('click', async () => {
        const index = Number(button.dataset.providerDrop);
        const provider = providerList()[index];
        const ok = await IG.confirm({
          title: '删除供应商',
          message: '将从调用顺序中移除「' + (provider.name || '未命名') + '」，保存后生效。',
          confirmText: '删除'
        });
        if (!ok) return;
        state.providerDraft = providerList().filter((item, i) => i !== index);
        state.providerStatus = {};
        renderProviders();
        markDirty();
      });
    });
  }

  function nextCopyName(baseName, list) {
    const taken = list.map(item => String(item.name || ''));
    const base = baseName ? baseName.replace(/ 副本( \d+)?$/, '') : '未命名供应商';
    let candidate = base + ' 副本';
    let index = 2;
    while (taken.indexOf(candidate) >= 0) {
      candidate = base + ' 副本 ' + index;
      index += 1;
    }
    return candidate;
  }

  function duplicateProvider(index) {
    const list = providerList().slice();
    const source = list[index];
    if (!source) return;
    const copy = Object.assign({}, source);
    delete copy.__index;
    copy.name = nextCopyName(source.name, list);
    list.splice(index + 1, 0, copy);
    state.providerDraft = list;
    state.providerStatus = {};
    renderProviders();
    markDirty();
    toast('已复制「' + (source.name || '未命名') + '」，保存后生效', 'success');
  }

  /* ── 供应商编辑弹窗 ───────────────────────────────────── */
  function currentTemplateKey() {
    const active = $('#provider-templates .template-card.is-active');
    return active ? active.dataset.template : 'openai_compatible';
  }

  function renderTemplates() {
    const host = $('#provider-templates');
    if (!host) return;
    host.innerHTML = TEMPLATES.map(item =>
      '<button class="template-card" type="button" role="radio" aria-checked="false" data-template="' + item.key + '">' +
      '<strong>' + esc(item.name) + '</strong><span>' + esc(item.desc) + '</span></button>'
    ).join('');
    $$('[data-template]', host).forEach(button => {
      button.addEventListener('click', () => {
        $$('[data-template]', host).forEach(other => {
          const active = other === button;
          other.classList.toggle('is-active', active);
          other.setAttribute('aria-checked', active ? 'true' : 'false');
        });
        applyTemplateVisibility(button.dataset.template);
      });
    });
  }

  function selectTemplate(key) {
    const host = $('#provider-templates');
    if (!host) return;
    $$('[data-template]', host).forEach(button => {
      const active = button.dataset.template === key;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-checked', active ? 'true' : 'false');
    });
    applyTemplateVisibility(key);
  }

  function applyTemplateVisibility(key) {
    const template = TEMPLATES.filter(item => item.key === key)[0] || TEMPLATES[0];
    $$('[data-prov-field]').forEach(field => {
      const needed = template.fields.indexOf(field.dataset.provField) >= 0;
      show(field, needed);
    });
    const keyInput = $('#prov-api_key');
    if (keyInput) keyInput.setAttribute('autocomplete', 'new-password');
    $('#provider-modal-sub').textContent = template.key === 'astrbot_provider'
      ? '复用 AstrBot 中已配置的对话模型，无需填写地址与密钥'
      : '填写接口地址与密钥后可直接测试连通性';
    if (template.key === 'modelscope' && !$('#prov-base_url').value) {
      $('#prov-base_url').value = 'https://api-inference.modelscope.cn';
    }
  }

  function openProviderModal(index) {
    state.providerEditIndex = typeof index === 'number' ? index : -1;
    const provider = state.providerEditIndex >= 0 ? providerList()[state.providerEditIndex] : null;
    $('#provider-modal-title').textContent = provider ? '编辑供应商' : '新增供应商';
    $('#prov-name').value = provider ? (provider.name || '') : '';
    $('#prov-api_key').value = provider ? (provider.api_key || '') : '';
    $('#prov-base_url').value = provider ? (provider.base_url || '') : '';
    $('#prov-model').value = provider ? (provider.model || '') : '';
    const note = $('#provider-test-result');
    note.textContent = '';
    note.removeAttribute('data-state');
    show(note, false);
    selectTemplate(provider ? (provider.__template_key || 'openai_compatible') : 'openai_compatible');
    show($('#provider-modal'), true);
    window.requestAnimationFrame(() => $('#prov-name').focus());
  }

  function closeProviderModal() {
    show($('#provider-modal'), false);
    state.providerEditIndex = -1;
  }

  function readProviderForm() {
    const template = currentTemplateKey();
    const provider = {
      __template_key: template,
      name: $('#prov-name').value.trim()
    };
    if (template !== 'astrbot_provider') {
      provider.api_key = $('#prov-api_key').value.trim();
      provider.base_url = $('#prov-base_url').value.trim();
      provider.model = $('#prov-model').value.trim();
    }
    if (!provider.name) {
      provider.name = template === 'astrbot_provider' ? 'AstrBot Provider' : 'OpenAI API';
    }
    return provider;
  }

  function confirmProvider() {
    const provider = readProviderForm();
    if (provider.__template_key === 'astrbot_provider') {
      // 复用 AstrBot Provider 不需要额外字段
    } else if (!provider.base_url) {
      toast('请填写 API 地址', 'error');
      $('#prov-base_url').focus();
      return;
    } else if (!provider.api_key) {
      toast('请填写 API Key', 'error');
      $('#prov-api_key').focus();
      return;
    }
    const list = providerList().slice();
    if (state.providerEditIndex >= 0) list[state.providerEditIndex] = provider;
    else list.push(provider);
    list.forEach(item => { delete item.__index; });
    state.providerDraft = list;
    state.providerStatus = {};
    renderProviders();
    markDirty();
    closeProviderModal();
    toast(state.providerEditIndex >= 0 ? '供应商已更新，保存后生效' : '供应商已添加，保存后生效', 'success');
  }

  async function testProvider() {
    const button = $('#provider-test');
    const note = $('#provider-test-result');
    const provider = readProviderForm();
    if (provider.__template_key !== 'astrbot_provider' && (!provider.base_url || !provider.api_key)) {
      note.textContent = '请先填写 API 地址与 API Key';
      note.dataset.state = 'error';
      show(note, true);
      return;
    }
    button.classList.add('is-busy');
    note.textContent = '正在发起最小请求…';
    note.dataset.state = 'loading';
    show(note, true);
    try {
      const result = await IG.api.providerTest(provider);
      note.textContent = result.message || (result.ok ? '调用成功' : '调用失败');
      note.dataset.state = result.ok ? 'success' : 'error';
      if (state.providerEditIndex >= 0) {
        state.providerStatus[state.providerEditIndex] = { ok: Boolean(result.ok), message: result.message };
        renderProviders();
      }
    } catch (error) {
      note.textContent = '测试失败：' + (error && error.message ? error.message : error);
      note.dataset.state = 'error';
    } finally {
      button.classList.remove('is-busy');
    }
  }

  /* ── 表单填充与读取 ───────────────────────────────────── */
  function fillForm(config) {
    rules = {
      sensitive_texts: (config.sensitive_texts || []).slice(),
      forbidden_descriptions: (config.forbidden_descriptions || []).slice()
    };
    renderRules();

    $('#cfg-custom_vision_prompt').value = config.custom_vision_prompt || '';
    $('#cfg-reasoning_effort').value = config.reasoning_effort || '';
    $('#cfg-llm_max_tokens').value = valueOr(config.llm_max_tokens, '');
    $('#cfg-llm_timeout_seconds').value = valueOr(config.llm_timeout_seconds, '');
    $('#cfg-group_scope').value = (config.group_scope || []).join('\n');
    $('#cfg-private_scope').value = (config.private_scope || []).join('\n');

    const probability = Number(config.check_probability);
    const prob = Number.isFinite(probability) ? probability : 1;
    $('#cfg-check_probability').value = String(prob);
    $('#prob-value').textContent = Math.round(prob * 100) + '%';

    $('#cfg-enable_recall').checked = truthy(config.enable_recall);
    $('#cfg-ban_duration').value = valueOr(config.ban_duration, '');
    $('#cfg-report_target_id').value = config.report_target_id || '';
    $('#cfg-compressed_image_max_bytes').value = valueOr(config.compressed_image_max_bytes, '');
    $('#cfg-audit_history_max_records').value = valueOr(config.audit_history_max_records, '');
    $('#cfg-keep_compressed_image_in_temp').checked = truthy(config.keep_compressed_image_in_temp);
    $('#cfg-debug_log_llm_response').checked = truthy(config.debug_log_llm_response);
    $('#cfg-audit_cache_enabled').checked = truthy(config.audit_cache_enabled);
    $('#cfg-audit_cache_threshold').value = valueOr(config.audit_cache_threshold, '');
    $('#cfg-audit_cache_max_entries').value = valueOr(config.audit_cache_max_entries, '');

    reportTargets = (config.group_report_targets || [])
      .filter(item => item && typeof item === 'object')
      .map(item => ({
        source_group_id: String(item.source_group_id || ''),
        target_type: item.target_type === 'group' ? 'group' : 'private',
        target_id: String(item.target_id || '')
      }));
    renderMapper();

    state.providerDraft = (config.llm_providers || []).map(item => {
      const copy = Object.assign({}, item);
      delete copy.__index;
      return copy;
    });
    state.providerStatus = {};
    renderProviders();

    baseline = JSON.stringify(collectPatch({ includeProviders: false }));
    state.configDirty = false;
    updateDirtyUi();
  }

  function valueOr(value, fallback) {
    return value === undefined || value === null ? fallback : value;
  }

  function truthy(value) {
    return value === true || value === 'true' || value === 1;
  }

  function intOr(value, fallback) {
    const parsed = parseInt(value, 10);
    return Number.isNaN(parsed) ? fallback : parsed;
  }

  function floatOr(value, fallback) {
    const parsed = parseFloat(value);
    return Number.isNaN(parsed) ? fallback : parsed;
  }

  function collectPatch(options) {
    const opts = options || {};
    const patch = {
      sensitive_texts: rules.sensitive_texts.slice(),
      forbidden_descriptions: rules.forbidden_descriptions.slice(),
      custom_vision_prompt: $('#cfg-custom_vision_prompt').value,
      reasoning_effort: $('#cfg-reasoning_effort').value,
      llm_max_tokens: intOr($('#cfg-llm_max_tokens').value, 512),
      llm_timeout_seconds: floatOr($('#cfg-llm_timeout_seconds').value, 120),
      group_scope: lines($('#cfg-group_scope').value),
      private_scope: lines($('#cfg-private_scope').value),
      check_probability: floatOr($('#cfg-check_probability').value, 1),
      enable_recall: $('#cfg-enable_recall').checked,
      ban_duration: intOr($('#cfg-ban_duration').value, 86400),
      report_target_id: $('#cfg-report_target_id').value.trim(),
      group_report_targets: reportTargets.map(item => ({
        source_group_id: String(item.source_group_id || '').trim(),
        target_type: item.target_type === 'group' ? 'group' : 'private',
        target_id: String(item.target_id || '').trim()
      })).filter(item => item.source_group_id && item.target_id),
      compressed_image_max_bytes: intOr($('#cfg-compressed_image_max_bytes').value, 1048576),
      audit_history_max_records: intOr($('#cfg-audit_history_max_records').value, 500),
      keep_compressed_image_in_temp: $('#cfg-keep_compressed_image_in_temp').checked,
      debug_log_llm_response: $('#cfg-debug_log_llm_response').checked,
      audit_cache_enabled: $('#cfg-audit_cache_enabled').checked,
      audit_cache_threshold: intOr($('#cfg-audit_cache_threshold').value, 3),
      audit_cache_max_entries: intOr($('#cfg-audit_cache_max_entries').value, 10000)
    };
    if (opts.includeProviders !== false) {
      // __index 提示后端：掩码 Key 应该回填哪一条已保存的明文（重命名/复制后依然成立）
      patch.llm_providers = providerList().map((provider, index) =>
        Object.assign({}, provider, { __index: index }));
    }
    return patch;
  }

  function lines(value) {
    return String(value || '').split('\n').map(item => item.trim()).filter(Boolean);
  }

  function diffPatch(full) {
    const base = baseline ? JSON.parse(baseline) : {};
    const patch = {};
    Object.keys(full).forEach(key => {
      if (key === 'llm_providers') return;
      if (JSON.stringify(full[key]) !== JSON.stringify(base[key])) patch[key] = full[key];
    });
    return patch;
  }

  /* ── 脏状态 ───────────────────────────────────────────── */
  function markDirty() {
    if (!state.configLoaded || state.saving) return;
    const current = JSON.stringify(collectPatch({ includeProviders: false }));
    state.configDirty = current !== baseline || providersChanged();
    updateDirtyUi();
  }

  function providersChanged() {
    const original = state.fullConfig && state.fullConfig.llm_providers ? state.fullConfig.llm_providers : [];
    return JSON.stringify(original) !== JSON.stringify(providerList());
  }

  function updateDirtyUi() {
    const badge = $('#nav-dirty');
    show(badge, Boolean(state.configDirty));
    const label = $('#save-state');
    if (!label) return;
    if (state.saving) {
      label.textContent = '正在保存并重载插件…';
      label.dataset.state = '';
      return;
    }
    if (state.configDirty) {
      label.textContent = '有未保存的修改，保存后插件会热重载';
      label.dataset.state = 'dirty';
      return;
    }
    label.textContent = state.configLoaded ? '配置已是最新' : '尚未读取配置';
    label.dataset.state = state.configLoaded ? 'success' : '';
  }

  /* ── 载入 / 保存 ──────────────────────────────────────── */
  async function loadConfig(force) {
    if (!IG.bridge.isEmbedded()) return;
    if (state.configLoading) return;
    if (state.configLoaded && !force) return;
    if (state.configDirty && force) {
      const ok = await IG.confirm({
        title: '放弃修改',
        message: '当前配置有未保存的修改，重新读取会丢弃这些改动。',
        confirmText: '放弃并重读'
      });
      if (!ok) return;
    }
    state.configLoading = true;
    try {
      const config = await IG.api.configGet();
      state.fullConfig = config || {};
      state.configLoaded = true;
      fillForm(state.fullConfig);
      IG.state.auditConfig = Object.assign({}, state.auditConfig, {
        rule_count: (state.fullConfig.sensitive_texts || []).length + (state.fullConfig.forbidden_descriptions || []).length,
        provider_count: (state.fullConfig.llm_providers || []).length,
        provider_names: (state.fullConfig.llm_providers || []).map(item => item.name).slice(0, 5),
        cache_enabled: truthy(state.fullConfig.audit_cache_enabled),
        enable_recall: truthy(state.fullConfig.enable_recall),
        ban_duration: state.fullConfig.ban_duration,
        check_probability: state.fullConfig.check_probability,
        group_scope: state.fullConfig.group_scope,
        private_scope: state.fullConfig.private_scope
      });
      IG.views.renderSnapshot();
      IG.views.renderScopeSummary();
      await refreshStorage();
      updateDirtyUi();
    } catch (error) {
      toast('读取配置失败：' + (error && error.message ? error.message : error), 'error');
      const label = $('#save-state');
      if (label) {
        label.textContent = '读取配置失败';
        label.dataset.state = 'error';
      }
    } finally {
      state.configLoading = false;
    }
  }

  async function refreshStorage() {
    const host = $('#store-grid');
    if (!host) return;
    const storage = state.storageSize || {};
    const cache = state.cacheStats || {};
    const items = [
      ['审核记录数据', format.bytes(storage.audit_records)],
      ['缓存数据', format.bytes(storage.cache_data)],
      ['缓存条目', format.int(cache.total_unique_images || 0) + ' 条'],
      ['已跳过审核', format.int(cache.images_skip_audit || 0) + ' 张']
    ];
    host.innerHTML = items.map(item =>
      '<div class="store-item"><span>' + esc(item[0]) + '</span><strong>' + esc(item[1]) + '</strong></div>'
    ).join('');
  }

  async function saveConfig() {
    if (state.saving) return;
    if (!state.configDirty) {
      toast('没有需要保存的修改', 'info');
      return;
    }
    const ok = await IG.confirm({
      title: '保存配置',
      message: '配置将写入插件配置并热重载插件，正在进行的审核请求可能被中断。',
      confirmText: '保存并重载',
      danger: false
    });
    if (!ok) return;

    const patch = diffPatch(collectPatch({ includeProviders: false }));
    const providersDirty = providersChanged();
    state.saving = true;
    updateDirtyUi();
    const button = $('#set-save');
    if (button) button.classList.add('is-busy');

    try {
      let response = null;
      if (Object.keys(patch).length) {
        response = await IG.api.configUpdate(patch);
      }
      if (providersDirty) {
        await IG.api.providersSave(providerList());
      }
      const reloaded = response && response.reloaded !== undefined ? response.reloaded : true;
      baseline = JSON.stringify(collectPatch({ includeProviders: false }));
      state.configDirty = false;
      if (state.fullConfig) state.fullConfig.llm_providers = providerList().map(item => Object.assign({}, item));
      toast(reloaded ? '配置已保存，插件已重载' : '配置已保存，请在 Dashboard 手动重载插件', reloaded ? 'success' : 'info');
      const label = $('#save-state');
      if (label) {
        label.textContent = reloaded ? '已保存并重载' : '已保存，待手动重载';
        label.dataset.state = 'success';
      }
    } catch (error) {
      toast('保存失败：' + (error && error.message ? error.message : error), 'error');
      const label = $('#save-state');
      if (label) {
        label.textContent = '保存失败';
        label.dataset.state = 'error';
      }
    } finally {
      state.saving = false;
      if (button) button.classList.remove('is-busy');
      updateDirtyUi();
    }
  }

  async function revertConfig() {
    if (!state.configDirty) {
      toast('没有需要撤销的修改', 'info');
      return;
    }
    const ok = await IG.confirm({
      title: '撤销修改',
      message: '将丢弃当前页面上的所有未保存改动，恢复到最近一次读取的配置。',
      confirmText: '撤销修改'
    });
    if (!ok) return;
    fillForm(state.fullConfig || {});
    toast('已恢复到上次读取的配置', 'success');
  }

  /* ── 维护操作 ─────────────────────────────────────────── */
  async function clearCache() {
    const ok = await IG.confirm({
      title: '清空审核缓存',
      message: '所有图片指纹计数会被重置，之前被跳过的重复图片会重新送审。',
      confirmText: '清空缓存'
    });
    if (!ok) return;
    try {
      const result = await IG.api.cacheClear();
      toast('已清空 ' + format.int(result && result.removed ? result.removed : 0) + ' 条缓存记录', 'success');
      await IG.app.reloadRecords();
      await refreshStorage();
    } catch (error) {
      toast('清空缓存失败：' + (error && error.message ? error.message : error), 'error');
    }
  }

  async function pruneStorage() {
    const ok = await IG.confirm({
      title: '清理孤立图片',
      message: '删除 data/plugin_data/image_guard/audit_images 下没有被任何审核记录引用的图片文件。',
      confirmText: '开始清理'
    });
    if (!ok) return;
    try {
      const result = await IG.api.storagePrune();
      toast('已清理 ' + format.int(result && result.removed ? result.removed : 0) + ' 个文件，释放 ' +
        format.bytes(result && result.freed_bytes ? result.freed_bytes : 0), 'success');
    } catch (error) {
      toast('清理失败：' + (error && error.message ? error.message : error), 'error');
    }
  }

  /* ── 初始化 ───────────────────────────────────────────── */
  function init() {
    mountRuleEditors();
    renderTemplates();

    $('#provider-add').addEventListener('click', () => openProviderModal(-1));
    $('#provider-modal-close').addEventListener('click', closeProviderModal);
    $('#provider-cancel').addEventListener('click', closeProviderModal);
    $('#provider-confirm').addEventListener('click', confirmProvider);
    $('#provider-test').addEventListener('click', testProvider);
    $('#provider-modal').addEventListener('click', event => {
      if (event.target === $('#provider-modal')) closeProviderModal();
    });

    $('#report-add').addEventListener('click', () => {
      reportTargets.push({ source_group_id: '', target_type: 'private', target_id: '' });
      renderMapper();
      markDirty();
    });

    $('#set-save').addEventListener('click', saveConfig);
    $('#set-revert').addEventListener('click', revertConfig);
    $('#set-reload').addEventListener('click', () => loadConfig(true));
    $('#cache-clear').addEventListener('click', clearCache);
    $('#storage-prune').addEventListener('click', pruneStorage);

    const probability = $('#cfg-check_probability');
    probability.addEventListener('input', () => {
      $('#prob-value').textContent = Math.round(Number(probability.value) * 100) + '%';
    });

    const form = $('#section-rules').closest('.settings-main');
    if (form) {
      form.addEventListener('input', event => {
        if (event.target.closest('#provider-list') || event.target.closest('#provider-modal')) return;
        markDirty();
      });
      form.addEventListener('change', markDirty);
    }
  }

  IG.settings = {
    init: init,
    loadConfig: loadConfig,
    saveConfig: saveConfig,
    revertConfig: revertConfig,
    refreshStorage: refreshStorage,
    renderProviders: renderProviders,
    closeProviderModal: closeProviderModal,
    markDirty: markDirty,
    updateDirtyUi: updateDirtyUi,
    isDirty() { return state.configDirty; },
    fillForm: fillForm
  };
})();
