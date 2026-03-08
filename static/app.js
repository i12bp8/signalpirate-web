/**
 * SignalPirate Web — Frontend
 */

const DEFAULT_CONFIG = {
  ai_key: '',
  ai_model: 'anthropic/claude-3.5-haiku',
  research_mode: false,
  rtl_autolevel: true,
  rtl_squelch: true,
  rtl_gain: 38,
  unique_scans_only: false,
  sniper_mode_model: '',
};

let ws = null;
let signals = [];
let protocolCatalog = [];
let selectedId = null;
let filterText = '';
let protocolFilter = '';
let sortBy = 'time';
let sortDesc = true;
let currentConfig = { ...DEFAULT_CONFIG };
let lastTxStatus = null;
let aiStreams = {};
let chatHistory = [];
let pendingSaveMessage = 'Settings saved';
let pendingSaveSilent = false;

const $ = id => document.getElementById(id);
const $tbody = $('signal-tbody');
const $count = $('signal-count');
const $protos = $('proto-count');
const $freq = $('freq-display');
const $devName = $('device-name');
const $dot = $('status-dot');
const $detail = $('detail-content');
const $status = $('table-status');
const $protocolTbody = $('protocol-tbody');
const $protocolStatus = $('protocol-status');

function connect() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.onopen = () => {
    $dot.className = 'dot online';
    updateSettingsSaveState('Connected · autosave enabled', 'idle');
  };
  ws.onclose = () => {
    $dot.className = 'dot offline';
    $devName.textContent = 'reconnecting';
    updateSettingsSaveState('Offline · changes cannot be saved', 'warn');
    setTimeout(connect, 2000);
  };
  ws.onerror = () => ws.close();
  ws.onmessage = event => handle(JSON.parse(event.data));
}

function send(payload) {
  if (ws?.readyState === 1) {
    ws.send(JSON.stringify(payload));
    return true;
  }
  return false;
}

function handle(msg) {
  if (msg.type === 'init') {
    const data = msg.data || {};
    updateDeviceState(data.device || {});
    updateStats(data.stats || {});
    signals = Array.isArray(data.signals) ? data.signals.slice() : [];
    if (selectedId != null && !signals.some(sig => sig._id === selectedId)) selectedId = null;
    applyConfig(data.config || {});
    renderTable();
    renderSelectedDetail();
    loadLibrary();
    loadProtocolCatalog();
    return;
  }

  if (msg.type === 'signal') {
    upsertSignal(msg.data);
    renderTable();
    if (selectedId === msg.data?._id) renderSelectedDetail();
    return;
  }

  if (msg.type === 'protocol_catalog') {
    protocolCatalog = Array.isArray(msg.data) ? msg.data : [];
    renderProtocolCatalog();
    return;
  }

  if (msg.type === 'ai_chunk') return handleAIChunk(msg.data);
  if (msg.type === 'ai_chunk_end') return handleAIChunkEnd(msg.data);
  if (msg.type === 'stats') return updateStats(msg.data);
  if (msg.type === 'frequency_changed') return setFreqDisplay(msg.data.frequency);
  if (msg.type === 'exported') {
    toast('Saved → ' + basename(msg.data.path), 'ok');
    loadLibrary();
    return;
  }
  if (msg.type === 'config_saved') {
    applyConfig(msg.data || {});
    renderTable();
    if (!pendingSaveSilent) toast(pendingSaveMessage, 'ok');
    const savedAt = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    updateSettingsSaveState(`Saved ${savedAt}`, 'ok');
    pendingSaveMessage = 'Settings saved';
    pendingSaveSilent = false;
    return;
  }
  if (msg.type === 'tx_status') {
    lastTxStatus = { ...(msg.data || {}), at: Date.now() };
    toast(lastTxStatus.success ? 'TX finished' : (lastTxStatus.error || 'TX failed'), lastTxStatus.success ? 'ok' : 'err');
    renderSelectedDetail();
    loadLibrary();
    return;
  }
  if (msg.type === 'tx_probe_result') {
    const data = msg.data || {};
    const probeHits = Array.isArray(data.probe_signals) ? data.probe_signals.length : 0;
    toast(data.success ? `Probe complete (${probeHits} decoded)` : (data.error || 'TX probe failed'), data.success ? 'ok' : 'err');
    renderSelectedDetail();
    return;
  }
  if (msg.type === 'cleared') {
    signals = [];
    selectedId = null;
    renderTable();
    renderSelectedDetail();
    $status.textContent = 'captures cleared';
    return;
  }
  if (msg.type === 'error') {
    toast(msg.data?.message || 'Error', 'err');
  }
}

function updateDeviceState(device) {
  const name = device?.name || 'No Device';
  $devName.textContent = name;
  $('workspace-device').textContent = name;
  $dot.className = device?.type === 'none' ? 'dot offline' : 'dot online';
}

function applyConfig(config) {
  currentConfig = { ...DEFAULT_CONFIG, ...(config || {}) };
  if ($('ai-key')) $('ai-key').value = currentConfig.ai_key || '';
  if ($('ai-model')) $('ai-model').value = currentConfig.ai_model || DEFAULT_CONFIG.ai_model;
  if ($('research-mode')) $('research-mode').checked = !!currentConfig.research_mode;
  if ($('unique-scans')) $('unique-scans').checked = !!currentConfig.unique_scans_only;
  if ($('quick-unique-scans')) $('quick-unique-scans').checked = !!currentConfig.unique_scans_only;
  if ($('sniper-model')) $('sniper-model').value = currentConfig.sniper_mode_model || '';
  if ($('quick-sniper-model')) $('quick-sniper-model').value = currentConfig.sniper_mode_model || '';
  if ($('rtl-autolevel')) $('rtl-autolevel').checked = !!currentConfig.rtl_autolevel;
  if ($('rtl-squelch')) $('rtl-squelch').checked = !!currentConfig.rtl_squelch;
  if ($('rtl-gain')) $('rtl-gain').value = currentConfig.rtl_gain ?? DEFAULT_CONFIG.rtl_gain;
  updateResearchModeUI();
  updateFilterBanner();
}

function updateResearchModeUI() {
  const ready = !!currentConfig.research_mode;
  const modeBadge = $('mode-badge');
  modeBadge.textContent = ready ? 'Research Ready' : 'Research Locked';
  modeBadge.className = `mode-badge ${ready ? 'ready' : 'locked'}`;
  $('workspace-mode').textContent = ready ? 'enabled' : 'locked';
}

function updateSettingsSaveState(message = 'Autosave enabled', kind = 'idle') {
  const button = $('btn-save-settings');
  if (button) button.textContent = 'Save All Settings';
  const badge = $('settings-save-state');
  if (badge) {
    badge.textContent = message;
    badge.className = `save-state ${kind}`;
  }
}

function saveSettings(options = {}) {
  pendingSaveMessage = options.successMessage || 'Settings saved';
  pendingSaveSilent = !!options.silent;
  const button = $('btn-save-settings');
  if (button && !pendingSaveSilent) button.textContent = 'Saving...';
  const previousConfig = { ...currentConfig };
  currentConfig = { ...currentConfig, ...collectSettings() };
  updateResearchModeUI();
  updateFilterBanner();
  updateSettingsSaveState('Saving settings...', 'pending');
  if (!send({
    cmd: 'save_settings',
    data: {
      settings: collectSettings(),
    },
  })) {
    currentConfig = previousConfig;
    applyConfig(previousConfig);
    renderTable();
    updateSettingsSaveState('Offline · settings not saved', 'warn');
    if (!pendingSaveSilent) toast('Connection offline, settings were not saved', 'err');
    pendingSaveMessage = 'Settings saved';
    pendingSaveSilent = false;
  }
}

function collectSettings() {
  return {
    ai_key: $('ai-key')?.value || '',
    ai_model: $('ai-model')?.value || DEFAULT_CONFIG.ai_model,
    research_mode: !!$('research-mode')?.checked,
    unique_scans_only: !!$('unique-scans')?.checked,
    sniper_mode_model: $('sniper-model')?.value.trim() || '',
    rtl_autolevel: !!$('rtl-autolevel')?.checked,
    rtl_squelch: !!$('rtl-squelch')?.checked,
    rtl_gain: parseInt($('rtl-gain')?.value || String(DEFAULT_CONFIG.rtl_gain), 10),
  };
}

function upsertSignal(sig) {
  if (!sig) return;
  const idx = signals.findIndex(item => item._id === sig._id);
  if (idx >= 0) signals[idx] = sig;
  else signals.push(sig);
}

function getSelectedSignal() {
  return signals.find(sig => sig._id === selectedId) || null;
}

function renderTable() {
  const visible = signals.filter(sig => matchesSignal(sig, filterText)).sort(compareSignals);
  $tbody.innerHTML = '';
  visible.slice(0, 300).forEach(renderRow);
  const uniqueModels = new Set(signals.map(sig => sig.model).filter(Boolean));
  $count.textContent = String(signals.length);
  $protos.textContent = String(uniqueModels.size);
  const scopeBits = [];
  if (currentConfig.sniper_mode_model) scopeBits.push(`focus model ${currentConfig.sniper_mode_model}`);
  if (currentConfig.unique_scans_only) scopeBits.push('duplicate suppression on');
  if (filterText) scopeBits.push(`search "${filterText}"`);
  const scopeText = scopeBits.length ? ` · ${scopeBits.join(' · ')}` : '';
  $status.textContent = visible.length
    ? `${visible.length} visible from ${signals.length} captures in current session${scopeText}`
    : `no captures match current filters${scopeText}`;
}

function matchesSignal(sig, text) {
  if (!text) return true;
  const hay = [
    sig.model,
    sig.modulation,
    sig.protocol_info?.name,
    sig.protocol_info?.category,
    sig.capabilities?.label,
  ].join(' ').toLowerCase();
  return hay.includes(text);
}

function compareSignals(a, b) {
  const secRank = { critical: 5, broken: 5, vulnerable: 4, moderate: 3, secure: 2, none: 1, unknown: 0 };
  const flowRank = { editable: 3, replay: 2, view: 1 };
  let va = 0;
  let vb = 0;

  if (sortBy === 'time') {
    va = a.timestamp || 0;
    vb = b.timestamp || 0;
  } else if (sortBy === 'model') {
    va = (a.model || '').toLowerCase();
    vb = (b.model || '').toLowerCase();
  } else if (sortBy === 'freq') {
    va = a.frequency || 0;
    vb = b.frequency || 0;
  } else if (sortBy === 'mod') {
    va = (a.modulation || '').toLowerCase();
    vb = (b.modulation || '').toLowerCase();
  } else if (sortBy === 'rssi') {
    va = a.rssi ?? -999;
    vb = b.rssi ?? -999;
  } else if (sortBy === 'sec') {
    va = secRank[getSec(a)] || 0;
    vb = secRank[getSec(b)] || 0;
  } else if (sortBy === 'flow') {
    va = flowRank[getFlow(a)] || 0;
    vb = flowRank[getFlow(b)] || 0;
  }

  if (va < vb) return sortDesc ? 1 : -1;
  if (va > vb) return sortDesc ? -1 : 1;
  return 0;
}

function getSec(sig) {
  if (sig.protocol_info?.security_level) return sig.protocol_info.security_level;
  const model = (sig.model || '').toLowerCase();
  if (['toyota', 'ford', 'kia', 'honda', 'nissan', 'vag', 'bmw', 'hyundai', 'mazda', 'subaru', 'gm'].some(v => model.includes(v))) return 'vulnerable';
  if (['auriol', 'oregon', 'infactory', 'acurite', 'lacrosse'].some(v => model.includes(v))) return 'none';
  return 'unknown';
}

function getFlow(sig) {
  return sig.capabilities?.tier || 'view';
}

function renderRow(sig) {
  const tr = document.createElement('tr');
  tr.dataset.id = sig._id;
  if (sig._id === selectedId) tr.classList.add('selected');

  const ts = sig.timestamp ? new Date(sig.timestamp * 1000).toLocaleTimeString() : '—';
  const freq = sig.frequency ? (sig.frequency / 1e6).toFixed(2) : '—';
  const rssi = sig.rssi != null ? Math.round(sig.rssi) : '—';
  const secCls = {
    critical: 'sec-critical',
    broken: 'sec-critical',
    none: 'sec-none',
    vulnerable: 'sec-vulnerable',
    moderate: 'sec-moderate',
    secure: 'sec-secure',
  }[getSec(sig)] || 'sec-unknown';

  tr.innerHTML = `
    <td>${ts}</td>
    <td class="model-cell">${esc(sig.model || 'Unknown')}</td>
    <td>${freq}</td>
    <td>${esc(sig.modulation || '?')}</td>
    <td>${rssi}</td>
    <td><span class="sec-dot ${secCls}">●</span></td>
    <td><span class="flow-pill ${getFlow(sig)}">${esc(sig.capabilities?.label || 'View Only')}</span></td>
  `;
  tr.onclick = () => {
    selectedId = sig._id;
    renderTable();
    renderSelectedDetail();
  };
  $tbody.appendChild(tr);
}

function renderSelectedDetail() {
  const sig = getSelectedSignal();
  const selectedBadge = $('selected-flow-pill');
  const workspaceFlow = $('workspace-flow');

  if (!sig) {
    selectedBadge.className = 'selection-badge';
    selectedBadge.textContent = 'No capture selected';
    workspaceFlow.textContent = 'none';
    $detail.innerHTML = `
      <div class="placeholder-msg compact">
        <span class="placeholder-icon">◎</span>
        <p>Select a capture to inspect, export, replay, or edit.</p>
      </div>
    `;
    return;
  }

  const caps = sig.capabilities || {};
  selectedBadge.className = `flow-pill ${caps.tier || 'view'}`;
  selectedBadge.textContent = caps.label || 'View Only';
  workspaceFlow.textContent = caps.label || 'View Only';
  $detail.innerHTML = renderDetail(sig);
}

function renderDetail(sig) {
  const caps = sig.capabilities || {};
  const editor = caps.editor;
  const payload = getPayloadEntries(sig);
  const tools = Array.isArray(caps.tools) ? caps.tools : [];
  const freq = sig.frequency ? (sig.frequency / 1e6).toFixed(3) + ' MHz' : '—';
  const ts = sig.timestamp ? new Date(sig.timestamp * 1000).toLocaleString() : '—';
  const rssi = sig.rssi != null ? sig.rssi.toFixed(1) + ' dBm' : '—';

  let html = '';
  html += `
    <section class="hero-card">
      <div class="hero-head">
        <div>
          <div class="eyebrow">Selected Capture</div>
          <h3>${esc(sig.model || 'Unknown')}</h3>
          <p class="hero-subtitle">${esc(caps.protocol_support?.summary || caps.summary || 'Capture ready for analysis.')}</p>
        </div>
        <div class="cap-strip">
          <span class="cap-chip ${caps.tier || 'view'}">${esc(caps.label || 'View Only')}</span>
          <span class="cap-chip support">${esc(caps.protocol_support?.label || 'Research')}</span>
          ${editor?.experimental ? '<span class="editor-badge">experimental editor</span>' : ''}
        </div>
      </div>
      <div class="hero-metrics">
        ${quickStat('Time', ts)}
        ${quickStat('Frequency', freq)}
        ${quickStat('RSSI', rssi)}
        ${quickStat('Protocol', sig.protocol_info?.name || sig.protocol_id || 'Unknown')}
      </div>
    </section>
  `;

  if (lastTxStatus) html += renderTxStatus(lastTxStatus);

  html += `
    <section class="action-card">
      <div class="action-copy">Save raw IQ, replay exact capture, or build a supported variant.</div>
      <div class="action-row">
        <button class="btn accent" onclick="doExport(${sig._id}, 'c8')">Save to Library</button>
        ${caps.can_replay_raw ? `<button class="btn" onclick="replaySignal(${sig._id})">Replay Raw</button>` : ''}
        ${caps.can_replay_raw ? `<button class="btn" onclick="probeSignal(${sig._id})">TX Probe</button>` : ''}
        <button class="btn" onclick="enableSniperById(${sig._id})">Sniper Mode</button>
      </div>
    </section>
  `;

  html += '<div class="workbench-grid"><div class="detail-column">';
  html += renderDataCard('Decoded Payload', payload.length ? payload : [['Status', 'No decoded payload fields']]);
  html += renderDataCard('Protocol Context', [
    ['Name', sig.protocol_info?.name || '—'],
    ['Category', sig.protocol_info?.category || '—'],
    ['Encoding', sig.protocol_info?.encoding || '—'],
    ['Security', sig.protocol_info?.security_level || '—'],
    ['Description', sig.protocol_info?.description || '—'],
    ['IQ File', sig.iq_file || '—'],
  ]);
  if (Array.isArray(sig.vuln_info) && sig.vuln_info.length) {
    html += `
      <section class="detail-card">
        <h4>Vulnerabilities</h4>
        <div class="detail-stack">
          ${sig.vuln_info.map(v => `
            <div class="detail-card" style="padding:12px; background: rgba(255,255,255,0.02);">
              <div class="detail-grid">
                <span class="detail-key">CVE</span><span class="detail-val">${esc(v.cve || '—')}</span>
                <span class="detail-key">Severity</span><span class="detail-val">${esc(v.severity || '—')}</span>
                <span class="detail-key">Impact</span><span class="detail-val">${esc(v.description || '—')}</span>
              </div>
            </div>
          `).join('')}
        </div>
      </section>
    `;
  }
  html += '</div><div class="detail-column">';
  if (editor) html += renderEditor(sig, editor);
  if (tools.length) html += renderToolsCard(tools);
  html += `
    <section class="detail-card">
      <h4>Raw Capture Data</h4>
      <pre class="hex-dump">${esc(JSON.stringify(sig.data || sig, null, 2))}</pre>
    </section>
  `;
  html += '</div></div>';
  return html;
}

function quickStat(label, value) {
  return `<div class="quick-stat"><span class="label">${esc(label)}</span><span class="value">${esc(value)}</span></div>`;
}

function getPayloadEntries(sig) {
  if (!sig.data || typeof sig.data !== 'object') return [];
  const skip = new Set(['time', 'model', 'freq', 'rssi', 'snr', 'mod', 'noise', 'protocol', 'modulation']);
  return Object.entries(sig.data)
    .filter(([key]) => !skip.has(key))
    .map(([key, value]) => [titleCase(key), formatValue(value)]);
}

function renderDataCard(title, rows) {
  return `
    <section class="detail-card">
      <h4>${esc(title)}</h4>
      <div class="detail-grid">
        ${rows.map(([key, value]) => `
          <span class="detail-key">${esc(key)}</span>
          <span class="detail-val">${esc(value)}</span>
        `).join('')}
      </div>
    </section>
  `;
}

function renderEditor(sig, editor) {
  const fields = editor.fields || [];
  return `
    <section class="detail-card">
      <div class="editor-shell">
        <div class="editor-title">
          <h4>${esc(editor.title || 'Structured Editor')}</h4>
          ${editor.experimental ? '<span class="editor-badge">experimental synthesis</span>' : ''}
        </div>
        <div class="detail-note">${esc(editor.summary || '')}</div>
        <div class="editor-fields">
          ${fields.map(field => renderEditorField(sig._id, field)).join('')}
        </div>
        <div class="editor-actions">
          <button class="btn accent" onclick="buildVariant(${sig._id}, false)">Build Variant</button>
          <button class="btn" onclick="buildVariant(${sig._id}, true)">Build + Replay</button>
        </div>
        <div class="editor-notes">${(editor.notes || []).map(note => `• ${esc(note)}`).join('<br>')}</div>
      </div>
    </section>
  `;
}

function renderEditorField(signalId, field) {
  const id = editorFieldId(signalId, field.name);
  if (field.type === 'select') {
    return `
      <div class="form-group">
        <label for="${id}">${esc(field.label || field.name)}</label>
        <select id="${id}">${(field.options || []).map(option => `<option value="${esc(option)}"${option === field.value ? ' selected' : ''}>${esc(option)}</option>`).join('')}</select>
      </div>
    `;
  }
  if (field.type === 'boolean') {
    return `
      <div class="setting-row">
        <div>
          <strong>${esc(field.label || field.name)}</strong>
          <p>Toggle before build.</p>
        </div>
        <label class="toggle-switch">
          <input type="checkbox" id="${id}"${field.value ? ' checked' : ''}>
          <span class="toggle-slider"></span>
        </label>
      </div>
    `;
  }
  const min = field.min != null ? `min="${field.min}"` : '';
  const max = field.max != null ? `max="${field.max}"` : '';
  return `
    <div class="form-group">
      <label for="${id}">${esc(field.label || field.name)}</label>
      <input type="number" id="${id}" value="${esc(String(field.value ?? ''))}" ${min} ${max}>
    </div>
  `;
}

function renderToolsCard(tools) {
  return `
    <section class="detail-card">
      <h4>GitHub Toolchain</h4>
      <div class="tool-list">${tools.slice(0, 5).map(renderToolCard).join('')}</div>
    </section>
  `;
}

function renderToolCard(tool) {
  return `
    <div class="tool-card">
      <div class="tool-card-head">
        <h4><a href="${esc(tool.repo)}" target="_blank" rel="noreferrer">${esc(tool.name)}</a></h4>
        <span class="tool-chip">score ${esc(String(tool.score || 0))}</span>
      </div>
      <div class="tool-summary">${esc(tool.summary || '')}</div>
      <div class="tool-reasons">${(tool.reasons || []).map(reason => `• ${esc(reason)}`).join('<br>')}</div>
    </div>
  `;
}

function renderTxStatus(status) {
  const models = Array.isArray(status.rx_models_2s) ? status.rx_models_2s.map(item => `${item.model} (${item.count})`).join(', ') : '—';
  return `
    <section class="tx-status-card ${status.success ? 'ok' : 'err'}">
      <div class="tx-status-title">${esc(status.success ? 'Last TX action completed' : 'Last TX action failed')}</div>
      <div class="tx-status-meta">
        <span class="detail-key">Status</span><span class="detail-val">${esc(status.success ? 'success' : (status.error || 'error'))}</span>
        <span class="detail-key">Observed RX Delta</span><span class="detail-val">${esc(String(status.rx_delta_2s ?? '—'))}</span>
        <span class="detail-key">Target Hits</span><span class="detail-val">${esc(String(status.rx_target_hits_2s ?? '—'))}</span>
        <span class="detail-key">Models Seen</span><span class="detail-val">${esc(models)}</span>
      </div>
    </section>
  `;
}

async function loadProtocolCatalog(forceWs = false) {
  if (forceWs) {
    if (send({ cmd: 'get_protocol_catalog' })) return;
  }
  try {
    const res = await fetch('/api/protocols');
    protocolCatalog = await res.json();
    renderProtocolCatalog();
  } catch (err) {
    console.error(err);
    $protocolStatus.textContent = 'failed to load protocol atlas';
  }
}

function renderProtocolCatalog() {
  const filtered = protocolCatalog.filter(proto => {
    if (!protocolFilter) return true;
    const hay = [
      proto.name,
      proto.category,
      proto.modulation,
      proto.encoding,
      proto.support?.label,
      ...(proto.tools || []).map(tool => tool.name),
    ].join(' ').toLowerCase();
    return hay.includes(protocolFilter);
  });

  $protocolTbody.innerHTML = '';
  if (!filtered.length) {
    $protocolTbody.innerHTML = '<tr><td colspan="6" class="muted" style="text-align:center;padding:2rem">No protocol rows matched the current filter</td></tr>';
    $protocolStatus.textContent = '0 protocols visible';
    return;
  }

  filtered.forEach(proto => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>
        <div class="protocol-name">${esc(proto.name || 'Unknown')}</div>
        <div class="protocol-sub">${esc(proto.description || 'No description')}</div>
      </td>
      <td>${esc(proto.category || '—')}</td>
      <td>${esc(proto.modulation || '—')}</td>
      <td>${esc(proto.encoding || '—')}</td>
      <td><span class="flow-pill ${supportTierClass(proto.support?.tier)}">${esc(proto.support?.label || 'Research')}</span></td>
      <td><div class="table-tools">${(proto.tools || []).slice(0, 3).map(tool => `<a class="tool-chip protocol-table-link" href="${esc(tool.repo)}" target="_blank" rel="noreferrer">${esc(tool.name)}</a>`).join('')}</div></td>
    `;
    $protocolTbody.appendChild(tr);
  });

  const counts = {
    editable: filtered.filter(item => item.support?.tier === 'editable').length,
    replay: filtered.filter(item => item.support?.tier === 'replay').length,
    research: filtered.filter(item => item.support?.tier === 'research').length,
  };
  $protocolStatus.textContent = `${filtered.length} of ${protocolCatalog.length} protocols mapped · ${counts.editable} editable · ${counts.replay} replay-after-capture · ${counts.research} research/capture`;
}

function supportTierClass(tier) {
  if (tier === 'editable') return 'editable';
  if (tier === 'replay') return 'replay';
  return 'view';
}

async function loadLibrary() {
  try {
    const files = (await (await fetch('/api/library')).json()).filter(file => !/\.urh\.zip$/i.test(file.name));
    const tbody = $('library-tbody');
    tbody.innerHTML = '';
    if (!files.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="muted" style="text-align:center;padding:2rem">No captures saved yet. Use Save to Library from the workbench.</td></tr>';
      return;
    }

    files.forEach(file => {
      const tr = document.createElement('tr');
      const size = file.size > 1024 ? (file.size / 1024).toFixed(1) + ' KB' : file.size + ' B';
      const flags = [];
      if (file.can_tx) flags.push('<span class="flag-chip">tx</span>');
      if (file.has_urh_zip) flags.push('<span class="flag-chip">urh</span>');
      if (file.is_variant) flags.push('<span class="flag-chip variant">variant</span>');
      tr.innerHTML = `
        <td class="model-cell">${esc(file.name)}</td>
        <td>${esc(String(file.format || '').toUpperCase())}</td>
        <td>${esc(size)}</td>
        <td>${esc(new Date(file.modified * 1000).toLocaleString())}</td>
        <td><div class="flag-stack">${flags.join('') || '<span class="muted">—</span>'}</div></td>
        <td style="text-align:right">
          <div class="table-tools" style="justify-content:flex-end;">
            <button class="btn-sm" onclick="downloadLibraryFile('${jsq(file.name)}')">Download</button>
            ${file.has_urh_zip ? `<button class="btn-sm" onclick="downloadLibraryFile('${jsq(file.name.replace(/\.[^.]+$/, '.urh.zip'))}')">URH Zip</button>` : ''}
            ${file.can_tx ? `<button class="btn-sm accent" onclick="txLibrary('${jsq(file.name)}')">Replay</button>` : ''}
            ${file.can_tx ? `<button class="btn-sm" onclick="probeLibrary('${jsq(file.name)}')">Probe</button>` : ''}
          </div>
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    console.error(err);
  }
}

function updateStats(st) {
  if (!st) return;
  $count.textContent = String(st.signals_decoded || signals.length || 0);
  if (st.unique_protocols != null) $protos.textContent = String(st.unique_protocols);
  if (st.frequency) setFreqDisplay(st.frequency);
  if ($('st-running')) $('st-running').textContent = st.running ? 'active' : 'stopped';
  if ($('st-uptime')) $('st-uptime').textContent = fmtUp(st.uptime_seconds);
  if ($('st-signals')) $('st-signals').textContent = st.signals_decoded ?? '—';
  if ($('st-protocols')) $('st-protocols').textContent = (st.protocols_seen || []).join(', ') || '—';
  if ($('st-device')) $('st-device').textContent = st.device_type || '—';
  if ($('st-freq')) $('st-freq').textContent = st.frequency ? (st.frequency / 1e6).toFixed(3) + ' MHz' : '—';
}

function setFreqDisplay(frequency) {
  if (!frequency) return;
  $freq.textContent = (frequency / 1e6).toFixed(3);
  document.querySelectorAll('.freq-btn').forEach(btn => btn.classList.toggle('active', Number(btn.dataset.freq) === Number(frequency)));
  if ($('custom-freq')) $('custom-freq').value = frequency;
}

function fmtUp(seconds) {
  if (!seconds) return '—';
  const mins = Math.floor(seconds / 60);
  const hours = Math.floor(mins / 60);
  return hours ? `${hours}h ${mins % 60}m` : `${mins}m ${Math.floor(seconds % 60)}s`;
}

function updateFilterBanner() {
  const banner = $('filter-banner');
  if (!banner) return;
  const localChips = [];
  const streamChips = [];
  if (filterText) localChips.push(`search: ${filterText}`);
  if (currentConfig.sniper_mode_model) streamChips.push(`focus model: ${currentConfig.sniper_mode_model}`);
  if (currentConfig.unique_scans_only) streamChips.push('unique only');
  const chips = [...streamChips, ...localChips];

  if (!chips.length) {
    banner.classList.add('hidden');
    banner.innerHTML = '';
    return;
  }

  banner.innerHTML = `
    <div>
      <strong>${streamChips.length ? 'Live stream is scoped before captures reach the dashboard.' : 'Local table filter active.'}</strong>
      <div class="table-tools" style="margin-top:8px;">${chips.map(chip => `<span class="tool-chip">${esc(chip)}</span>`).join('')}</div>
    </div>
    <button class="btn-sm" onclick="resetCaptureFilters()">Reset Filters</button>
  `;
  banner.classList.remove('hidden');
}

window.resetCaptureFilters = function() {
  filterText = '';
  if ($('signal-search')) $('signal-search').value = '';
  if ($('quick-sniper-model')) $('quick-sniper-model').value = '';
  if ($('sniper-model')) $('sniper-model').value = '';
  if ($('quick-unique-scans')) $('quick-unique-scans').checked = false;
  if ($('unique-scans')) $('unique-scans').checked = false;
  currentConfig.sniper_mode_model = '';
  currentConfig.unique_scans_only = false;
  renderTable();
  updateFilterBanner();
  saveSettings({ successMessage: 'Capture filters cleared' });
};

function applyStreamFilters(successMessage = null) {
  const sniperValue = $('quick-sniper-model')?.value.trim() || '';
  const uniqueOnly = !!$('quick-unique-scans')?.checked;
  if ($('sniper-model')) $('sniper-model').value = sniperValue;
  if ($('unique-scans')) $('unique-scans').checked = uniqueOnly;
  saveSettings({
    successMessage: successMessage || (sniperValue ? `Stream focus set to ${sniperValue}` : 'Stream filters saved'),
  });
}

function ensureResearchMode() {
  if (currentConfig.research_mode) return true;
  toast('Enable Research Mode in Settings to unlock TX actions', 'warn');
  return false;
}

function downloadFile(url, name) {
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  setTimeout(() => a.remove(), 1000);
}

window.downloadLibraryFile = function(name) {
  downloadFile('/api/library/' + encodeURIComponent(name), name);
};

window.txLibrary = function(name) {
  if (!ensureResearchMode()) return;
  if (!send({ cmd: 'tx_signal', data: { filename: name } })) toast('Connection offline, could not start replay', 'err');
};

window.probeLibrary = function(name) {
  if (!ensureResearchMode()) return;
  if (!send({ cmd: 'tx_probe', data: { filename: name } })) toast('Connection offline, could not start probe', 'err');
};

async function ensureSignalExport(id) {
  const result = await doExport(id, 'c8', { download: false, toastLabel: 'Preparing replay file...', silentSuccess: true });
  return result ? basename(result.path) : null;
}

window.replaySignal = async function(id) {
  if (!ensureResearchMode()) return;
  const filename = await ensureSignalExport(id);
  if (filename && !send({ cmd: 'tx_signal', data: { filename } })) toast('Connection offline, could not start replay', 'err');
};

window.probeSignal = async function(id) {
  if (!ensureResearchMode()) return;
  const filename = await ensureSignalExport(id);
  if (filename && !send({ cmd: 'tx_probe', data: { filename } })) toast('Connection offline, could not start probe', 'err');
};

window.enableSniperById = function(id) {
  const sig = signals.find(item => item._id === id);
  if (!sig) return;
  if ($('quick-sniper-model')) $('quick-sniper-model').value = sig.model || '';
  if ($('sniper-model')) $('sniper-model').value = sig.model || '';
  saveSettings({ successMessage: `Sniper mode set to ${sig.model || 'selected model'}` });
};

window.enableSniper = function(model) {
  if ($('quick-sniper-model')) $('quick-sniper-model').value = model || '';
  if (!$('sniper-model')) return;
  $('sniper-model').value = model || '';
  saveSettings({ successMessage: model ? `Sniper mode set to ${model}` : 'Sniper mode disabled' });
};

async function doExport(id, format, opts = {}) {
  try {
    if (opts.toastLabel !== false) toast(opts.toastLabel || 'Saving signal...', 'ok');
    const res = await fetch('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ signal_id: id, format }),
    });
    const data = await res.json();
    if (!data.ok) {
      toast('Export failed: ' + (data.error || 'unknown error'), 'err');
      return null;
    }
    const name = basename(data.path);
    if (!opts.silentSuccess) toast('Saved → ' + name, 'ok');
    loadLibrary();
    if (opts.download !== false) {
      downloadFile('/api/library/' + encodeURIComponent(name), name);
      if (/\.(?:cs8|c8)$/i.test(name)) {
        const zipName = name.replace(/\.(?:cs8|c8)$/i, '.urh.zip');
        setTimeout(() => downloadFile('/api/library/' + encodeURIComponent(zipName), zipName), 400);
      }
    }
    return data;
  } catch (err) {
    console.error(err);
    toast('Error exporting signal', 'err');
    return null;
  }
}

window.doExport = doExport;

window.buildVariant = async function(signalId, replayAfter) {
  const sig = signals.find(item => item._id === signalId);
  if (!sig?.capabilities?.editor) {
    toast('No structured editor for this signal', 'warn');
    return;
  }
  const edits = readEditorValues(signalId, sig.capabilities.editor.fields || []);
  try {
    const res = await fetch('/api/editor/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ signal_id: signalId, edits }),
    });
    const data = await res.json();
    if (!data.ok) {
      toast('Variant build failed: ' + (data.error || 'unknown error'), 'err');
      return;
    }
    toast('Variant built → ' + data.filename, 'ok');
    loadLibrary();
    downloadFile('/api/library/' + encodeURIComponent(data.filename), data.filename);
    const zipName = data.filename.replace(/\.(?:cs8|c8)$/i, '.urh.zip');
    setTimeout(() => downloadFile('/api/library/' + encodeURIComponent(zipName), zipName), 400);
    if (replayAfter) {
      if (!ensureResearchMode()) return;
      send({ cmd: 'tx_signal', data: { filename: data.filename } });
    }
  } catch (err) {
    console.error(err);
    toast('Variant build error', 'err');
  }
};

function readEditorValues(signalId, fields) {
  const edits = {};
  fields.forEach(field => {
    const input = $(editorFieldId(signalId, field.name));
    if (!input) return;
    if (field.type === 'boolean') edits[field.name] = !!input.checked;
    else if (field.type === 'number') edits[field.name] = Number(input.value);
    else edits[field.name] = input.value;
  });
  return edits;
}

function editorFieldId(signalId, name) {
  return `editor-${signalId}-${name}`;
}

function appendChatMsg(text, isUser, id = null) {
  const chatWindow = $('chat-window');
  if (!chatWindow) return null;
  const outer = document.createElement('div');
  outer.className = `chat-msg ${isUser ? 'user-msg' : 'ai-msg'}`;
  if (id) outer.id = `msg-${id}`;
  const inner = document.createElement('div');
  inner.className = 'chat-bubble';
  inner.innerHTML = isUser ? esc(text) : renderMarkdown(text);
  outer.appendChild(inner);
  chatWindow.appendChild(outer);
  chatWindow.scrollTop = chatWindow.scrollHeight;
  return inner;
}

function handleAIChunk(data) {
  if (!data?.id) return;
  if (!aiStreams[data.id]) {
    aiStreams[data.id] = '';
    appendChatMsg('', false, data.id);
  }
  aiStreams[data.id] += data.chunk || '';
  const bubble = $(`msg-${data.id}`)?.querySelector('.chat-bubble');
  if (bubble) {
    bubble.innerHTML = renderMarkdown(aiStreams[data.id]);
    const chatWindow = $('chat-window');
    if (chatWindow) chatWindow.scrollTop = chatWindow.scrollHeight;
  }
}

function handleAIChunkEnd(data) {
  if (data?.id && data.id.startsWith('chat_')) chatHistory.push({ role: 'assistant', content: aiStreams[data.id] || '' });
  if (data?.id) delete aiStreams[data.id];
}

function renderMarkdown(text) {
  if (typeof marked !== 'undefined') return marked.parse(text || '');
  return esc(text || '');
}

function sendChatMessage() {
  const input = $('chat-input');
  const text = input?.value.trim();
  if (!text) return;
  input.value = '';
  chatSuggest.classList.add('hidden');
  appendChatMsg(text, true);

  let context = null;
  if (text.includes('@C_CAPTURE')) {
    const sig = getSelectedSignal();
    if (sig) context = { type: 'current_capture', data: sig };
  } else if (text.includes('@ALL_CAPTURES')) {
    const seen = new Set();
    const unique = [];
    signals.forEach(sig => {
      const key = `${sig.model}_${sig.frequency}`;
      if (!seen.has(key)) {
        seen.add(key);
        unique.push({ time: sig.timestamp, freq: sig.frequency, model: sig.model, modulation: sig.modulation });
      }
    });
    context = { type: 'all_captures', data: unique };
  }

  const chatId = 'chat_' + Date.now();
  if (!send({ cmd: 'ai_chat', data: { chat_id: chatId, prompt: text, history: chatHistory, context } })) {
    toast('Connection offline, AI request was not sent', 'err');
    return;
  }
  chatHistory.push({ role: 'user', content: text });
}

const chatInput = $('chat-input');
const chatSuggest = document.createElement('div');
chatSuggest.className = 'chat-suggest hidden';
chatSuggest.innerHTML = `
  <div class="suggest-item" data-val="@C_CAPTURE"><strong>@C_CAPTURE</strong> Attach the selected capture</div>
  <div class="suggest-item" data-val="@ALL_CAPTURES"><strong>@ALL_CAPTURES</strong> Attach a session summary</div>
`;
chatInput?.parentNode.insertBefore(chatSuggest, chatInput);
chatSuggest.addEventListener('click', event => {
  const item = event.target.closest('.suggest-item');
  if (!item || !chatInput) return;
  chatInput.value = chatInput.value.replace(/@\w*$/, item.dataset.val + ' ');
  chatSuggest.classList.add('hidden');
  chatInput.focus();
});
chatInput?.addEventListener('input', () => {
  const val = chatInput.value;
  const lastAt = val.lastIndexOf('@');
  if (lastAt >= 0 && !val.includes(' ', lastAt)) chatSuggest.classList.remove('hidden');
  else chatSuggest.classList.add('hidden');
});
chatInput?.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendChatMessage();
  }
});
$('btn-chat-send')?.addEventListener('click', sendChatMessage);

$('signal-search')?.addEventListener('input', event => {
  filterText = event.target.value.trim().toLowerCase();
  renderTable();
  updateFilterBanner();
});
$('protocol-search')?.addEventListener('input', event => {
  protocolFilter = event.target.value.trim().toLowerCase();
  renderProtocolCatalog();
});
$('btn-refresh-protocols')?.addEventListener('click', () => loadProtocolCatalog(true));
$('btn-refresh-lib')?.addEventListener('click', loadLibrary);
$('btn-clear')?.addEventListener('click', () => {
  if (!send({ cmd: 'clear_signals' })) toast('Connection offline, could not clear captures', 'err');
});
$('btn-reset-filters')?.addEventListener('click', () => window.resetCaptureFilters());
$('btn-apply-stream-filters')?.addEventListener('click', () => applyStreamFilters());
$('btn-save-settings')?.addEventListener('click', () => saveSettings({ successMessage: 'All settings saved' }));
$('btn-set-freq')?.addEventListener('click', () => {
  const frequency = Number($('custom-freq')?.value || 0);
  if (frequency > 0 && !send({ cmd: 'set_frequency', data: { frequency } })) {
    toast('Connection offline, could not change frequency', 'err');
  }
});

document.querySelectorAll('.freq-btn').forEach(btn => btn.addEventListener('click', () => {
  if (!send({ cmd: 'set_frequency', data: { frequency: Number(btn.dataset.freq) } })) {
    toast('Connection offline, could not change frequency', 'err');
  }
}));

['time', 'model', 'freq', 'mod', 'rssi', 'sec', 'flow'].forEach(key => {
  const header = $('th-' + key);
  if (!header) return;
  header.addEventListener('click', () => {
    if (sortBy === key) sortDesc = !sortDesc;
    else {
      sortBy = key;
      sortDesc = ['time', 'rssi', 'sec', 'flow'].includes(key);
    }
    document.querySelectorAll('.sort-arrow').forEach(node => { node.textContent = ''; });
    const arrow = header.querySelector('.sort-arrow');
    if (arrow) arrow.textContent = sortDesc ? ' ▼' : ' ▲';
    renderTable();
  });
});

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(node => node.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(node => node.classList.remove('active'));
    tab.classList.add('active');
    const panel = $('tab-' + tab.dataset.tab);
    if (panel) panel.classList.add('active');
    if (tab.dataset.tab === 'settings') send({ cmd: 'get_stats' });
    if (tab.dataset.tab === 'library') loadLibrary();
    if (tab.dataset.tab === 'protocols') loadProtocolCatalog();
  });
});

document.addEventListener('keydown', event => {
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)) return;
  const tabs = ['dashboard', 'protocols', 'library', 'settings'];
  const idx = Number(event.key) - 1;
  if (idx >= 0 && idx < tabs.length) document.querySelector(`[data-tab="${tabs[idx]}"]`)?.click();
});

['research-mode', 'unique-scans', 'rtl-autolevel', 'rtl-squelch'].forEach(id => {
  $(id)?.addEventListener('change', () => {
    const label = id === 'research-mode'
      ? ($('research-mode').checked ? 'Research mode enabled and saved' : 'Research mode disabled and saved')
      : 'Setting saved';
    if (id === 'unique-scans' && $('quick-unique-scans')) $('quick-unique-scans').checked = $('unique-scans').checked;
    saveSettings({ successMessage: label });
  });
});
$('rtl-gain')?.addEventListener('change', () => saveSettings({ successMessage: 'Radio gain saved', silent: true }));
$('sniper-model')?.addEventListener('change', () => {
  if ($('quick-sniper-model')) $('quick-sniper-model').value = $('sniper-model').value.trim();
  saveSettings({ successMessage: $('sniper-model').value.trim() ? `Sniper mode set to ${$('sniper-model').value.trim()}` : 'Sniper mode disabled' });
});
$('quick-unique-scans')?.addEventListener('change', () => applyStreamFilters($('quick-unique-scans').checked ? 'Unique live capture filter enabled' : 'Unique live capture filter disabled'));
$('quick-sniper-model')?.addEventListener('change', () => applyStreamFilters($('quick-sniper-model').value.trim() ? `Stream focus set to ${$('quick-sniper-model').value.trim()}` : 'Stream focus cleared'));
$('quick-sniper-model')?.addEventListener('keydown', event => {
  if (event.key === 'Enter') {
    event.preventDefault();
    applyStreamFilters($('quick-sniper-model').value.trim() ? `Stream focus set to ${$('quick-sniper-model').value.trim()}` : 'Stream focus cleared');
  }
});

function toast(text, type = 'ok') {
  const colors = { ok: '#7ce6d1', err: '#ff8e8e', warn: '#f4b261' };
  const node = document.createElement('div');
  Object.assign(node.style, {
    position: 'fixed',
    bottom: '18px',
    right: '18px',
    padding: '10px 14px',
    background: colors[type] || '#ffffff',
    color: '#0b1217',
    borderRadius: '12px',
    fontWeight: '700',
    zIndex: '200',
    boxShadow: '0 20px 44px rgba(0,0,0,0.35)',
    animation: 'fadeIn .18s ease',
  });
  node.textContent = text;
  document.body.appendChild(node);
  setTimeout(() => {
    node.style.opacity = '0';
    node.style.transition = 'opacity .2s ease';
    setTimeout(() => node.remove(), 220);
  }, 2600);
}

function formatValue(value) {
  if (value == null || value === '') return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function titleCase(text) {
  return String(text || '')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, char => char.toUpperCase());
}

function esc(value) {
  const div = document.createElement('div');
  div.textContent = value == null ? '' : String(value);
  return div.innerHTML;
}

function basename(path) {
  return String(path || '').split('/').pop();
}

function jsq(value) {
  return String(value || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

const injectedStyle = document.createElement('style');
injectedStyle.textContent = `
  @keyframes fadeIn { from { transform: translateY(8px); opacity: 0; } to { transform: none; opacity: 1; } }
  th { cursor: pointer; user-select: none; }
  th:hover { color: var(--text-2); }
  .sort-arrow { font-size: 0.8em; opacity: 0.7; }
`;
document.head.appendChild(injectedStyle);

connect();
