/**
 * SignalPirate Web — Frontend
 */

let ws = null;
let signals = [];
let protocolCatalog = [];
let selectedId = null;
let filterText = '';
let protocolFilter = '';
let sortBy = 'time';
let sortDesc = true;
let currentConfig = {};
let lastTxStatus = null;
let aiStreams = {};
let chatHistory = [];

const $ = id => document.getElementById(id);
const $tbody = $('signal-tbody');
const $count = $('signal-count');
const $protos = $('proto-count');
const $freq = $('freq-display');
const $devName = $('device-name');
const $dot = $('status-dot');
const $title = $('detail-title');
const $detail = $('detail-content');
const $status = $('table-status');
const $protocolTbody = $('protocol-tbody');
const $protocolStatus = $('protocol-status');

function connect() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.onopen = () => { $dot.className = 'dot online'; };
  ws.onclose = () => {
    $dot.className = 'dot offline';
    $devName.textContent = 'reconnecting';
    setTimeout(connect, 2000);
  };
  ws.onerror = () => ws.close();
  ws.onmessage = event => handle(JSON.parse(event.data));
}

function send(payload) {
  if (ws?.readyState === 1) ws.send(JSON.stringify(payload));
}

function handle(msg) {
  if (msg.type === 'init') {
    const d = msg.data || {};
    $devName.textContent = d.device?.name || 'No Device';
    $dot.className = d.device?.type === 'none' ? 'dot offline' : 'dot online';
    if (d.stats) updateStats(d.stats);
    signals = [];
    (d.signals || []).forEach(sig => upsertSignal(sig));
    renderTable();
    applyConfig(d.config || {});
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
    toast('Settings saved', 'ok');
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
    const d = msg.data || {};
    const probeHits = Array.isArray(d.probe_signals) ? d.probe_signals.length : 0;
    toast(d.success ? `Probe complete (${probeHits} decoded)` : (d.error || 'TX probe failed'), d.success ? 'ok' : 'err');
    renderSelectedDetail();
    return;
  }
  if (msg.type === 'cleared') {
    signals = [];
    selectedId = null;
    $tbody.innerHTML = '';
    $detail.innerHTML = placeholderDetail();
    $count.textContent = '0';
    $status.textContent = 'cleared';
    return;
  }
  if (msg.type === 'error') {
    toast(msg.data?.message || 'Error', 'err');
  }
}

function applyConfig(config) {
  currentConfig = { ...currentConfig, ...(config || {}) };
  if ($('ai-key')) $('ai-key').value = currentConfig.ai_key || '';
  if ($('ai-model')) $('ai-model').value = currentConfig.ai_model || 'anthropic/claude-3.5-haiku';
  if ($('rtl-autolevel')) $('rtl-autolevel').checked = !!currentConfig.rtl_autolevel;
  if ($('rtl-squelch')) $('rtl-squelch').checked = !!currentConfig.rtl_squelch;
  if ($('rtl-gain')) $('rtl-gain').value = currentConfig.rtl_gain ?? 38;
  if ($('unique-scans')) $('unique-scans').checked = !!currentConfig.unique_scans_only;
  if ($('research-mode')) $('research-mode').checked = !!currentConfig.research_mode;
  if ($('sniper-model')) $('sniper-model').value = currentConfig.sniper_mode_model || '';
  updateSniperUI(currentConfig.sniper_mode_model || '');
}

function upsertSignal(sig) {
  if (!sig) return;
  const idx = signals.findIndex(item => item._id === sig._id);
  if (idx >= 0) signals[idx] = sig;
  else signals.push(sig);
  $count.textContent = String(signals.length);
  $protos.textContent = String(new Set(signals.map(s => s.model).filter(Boolean)).size);
  $status.textContent = `${signals.length} captured`;
}

function getSelectedSignal() {
  return signals.find(sig => sig._id === selectedId) || null;
}

function renderSelectedDetail() {
  const sig = getSelectedSignal();
  if (!sig) {
    $title.textContent = 'Signal Inspection';
    $detail.innerHTML = placeholderDetail();
    return;
  }
  $title.textContent = `#${sig._id} — ${sig.model || 'Unknown'}`;
  $detail.innerHTML = renderDetail(sig);
}

function placeholderDetail() {
  return `<div class="placeholder-msg"><span class="placeholder-icon">◉</span><p>Select a signal to inspect</p></div>`;
}

function renderTable() {
  const visible = signals
    .filter(sig => matchesSignal(sig, filterText))
    .sort(compareSignals);

  $tbody.innerHTML = '';
  visible.slice(0, 300).forEach(sig => renderRow(sig));
  $count.textContent = String(signals.length);
  $protos.textContent = String(new Set(signals.map(s => s.model).filter(Boolean)).size);
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
  let va;
  let vb;
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
    const rank = { critical: 5, broken: 5, vulnerable: 4, moderate: 3, secure: 2, none: 1, unknown: 0 };
    va = rank[getSec(a)] || 0;
    vb = rank[getSec(b)] || 0;
  } else if (sortBy === 'flow') {
    const rank = { editable: 3, replay: 2, view: 1 };
    va = rank[getFlow(a)] || 0;
    vb = rank[getFlow(b)] || 0;
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
  const mod = sig.modulation || '?';
  const sec = getSec(sig);
  const secCls = {
    critical: 'sec-critical',
    broken: 'sec-critical',
    none: 'sec-none',
    vulnerable: 'sec-vulnerable',
    moderate: 'sec-moderate',
    secure: 'sec-secure',
  }[sec] || 'sec-unknown';
  const flow = sig.capabilities?.label || 'View Only';
  const flowTier = getFlow(sig);

  tr.innerHTML = `
    <td>${ts}</td>
    <td class="model-cell">${esc(sig.model || '?')}</td>
    <td class="freq-cell">${freq}</td>
    <td>${esc(mod)}</td>
    <td class="rssi-cell">${rssi}</td>
    <td><span class="sec-dot ${secCls}">●</span></td>
    <td><span class="flow-pill ${flowTier}">${esc(flow)}</span></td>
  `;
  tr.onclick = () => {
    selectedId = sig._id;
    renderTable();
    renderSelectedDetail();
  };
  $tbody.appendChild(tr);
}

function renderDetail(sig) {
  const freq = sig.frequency ? (sig.frequency / 1e6).toFixed(3) + ' MHz' : '—';
  const ts = sig.timestamp ? new Date(sig.timestamp * 1000).toLocaleString() : '—';
  const rssi = sig.rssi != null ? sig.rssi.toFixed(1) + ' dBm' : '—';
  const caps = sig.capabilities || {};
  const editor = caps.editor;

  let html = '';
  if (lastTxStatus) html += renderTxStatus(lastTxStatus);

  html += `<div class="cap-summary">
    <div class="cap-strip">
      <span class="cap-chip ${caps.tier || 'view'}">${esc(caps.label || 'View Only')}</span>
      <span class="cap-chip support">${esc(caps.protocol_support?.label || 'Research')}</span>
      ${editor?.experimental ? '<span class="editor-badge">experimental editor</span>' : ''}
    </div>
    <strong>${esc(caps.summary || 'Capture ready for analysis.')}</strong>
    <div class="detail-note">${esc(caps.protocol_support?.summary || 'Use the protocol atlas and tool cards below to decide the best workflow.')}</div>
  </div>`;

  html += `<div class="detail-actions">
    <button class="btn accent" onclick="doExport(${sig._id}, 'c8')">Save to Library</button>
    ${caps.can_replay_raw ? `<button class="btn" onclick="replaySignal(${sig._id})">Replay Raw</button>` : ''}
    ${caps.can_replay_raw ? `<button class="btn" onclick="probeSignal(${sig._id})">TX Probe</button>` : ''}
    <button class="btn" onclick="enableSniperById(${sig._id})" title="Only scan for this protocol">Sniper Mode</button>
  </div>`;

  if (editor) html += renderEditor(sig, editor);

  html += sec('Metadata', grid({
    'Time': ts,
    'Frequency': freq,
    'RSSI': rssi,
    'Modulation': sig.modulation || '—',
    'Protocol ID': sig.protocol_id ?? '—',
    'IQ File': sig.iq_file || '—',
  }));

  if (sig.protocol_info) {
    html += sec('Protocol', grid({
      'Name': sig.protocol_info.name || '—',
      'Category': sig.protocol_info.category || '—',
      'Security': sig.protocol_info.security_level || '—',
      'Encoding': sig.protocol_info.encoding || '—',
      'Description': sig.protocol_info.description || '—',
    }));
  }

  if (sig.data && typeof sig.data === 'object') {
    const skip = new Set(['time', 'model', 'freq', 'rssi', 'snr', 'mod', 'noise', 'protocol', 'modulation']);
    const payload = Object.fromEntries(Object.entries(sig.data).filter(([k]) => !skip.has(k)));
    if (Object.keys(payload).length) html += sec('Payload', grid(payload));
  }

  if (Array.isArray(sig.vuln_info) && sig.vuln_info.length) {
    html += sec('Vulnerabilities', sig.vuln_info.map(v => grid({
      'CVE': v.cve || '—',
      'Severity': v.severity || '—',
      'Impact': v.description || '—',
    })).join(''));
  }

  if (Array.isArray(caps.tools) && caps.tools.length) {
    html += sec('GitHub Tools', `<div class="tool-list">${caps.tools.slice(0, 5).map(renderToolCard).join('')}</div>`);
  }

  html += sec('Raw', `<div class="hex-dump">${esc(JSON.stringify(sig.data || sig, null, 2))}</div>`);
  return html;
}

function renderEditor(sig, editor) {
  const fields = editor.fields || [];
  return sec('Protocol Editor', `
    <div class="editor-shell">
      <div class="editor-title">
        <strong>${esc(editor.title || 'Structured Editor')}</strong>
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
  `);
}

function renderEditorField(signalId, field) {
  const id = editorFieldId(signalId, field.name);
  const label = esc(field.label || field.name);
  if (field.type === 'select') {
    return `<div class="form-group"><label for="${id}">${label}</label><select id="${id}">${(field.options || []).map(opt => `<option value="${esc(opt)}"${opt === field.value ? ' selected' : ''}>${esc(opt)}</option>`).join('')}</select></div>`;
  }
  if (field.type === 'boolean') {
    return `<div class="form-group"><label>${label}</label><label class="toggle-switch"><input type="checkbox" id="${id}"${field.value ? ' checked' : ''}><span class="toggle-slider"></span></label></div>`;
  }
  const attrs = [];
  if (field.min != null) attrs.push(`min="${field.min}"`);
  if (field.max != null) attrs.push(`max="${field.max}"`);
  return `<div class="form-group"><label for="${id}">${label}</label><input type="number" id="${id}" value="${esc(String(field.value ?? ''))}" ${attrs.join(' ')}></div>`;
}

function renderTxStatus(status) {
  const models = Array.isArray(status.rx_models_2s) ? status.rx_models_2s.map(item => `${item.model} (${item.count})`).join(', ') : '—';
  return `
    <div class="tx-status-card ${status.success ? 'ok' : 'err'}">
      <div class="tx-status-title">${esc(status.success ? 'Last TX action completed' : 'Last TX action failed')}</div>
      <div class="tx-status-meta">
        <span class="detail-key">Status</span><span class="detail-val">${esc(status.success ? 'success' : (status.error || 'error'))}</span>
        <span class="detail-key">Observed RX Δ</span><span class="detail-val">${esc(String(status.rx_delta_2s ?? '—'))}</span>
        <span class="detail-key">Target Hits</span><span class="detail-val">${esc(String(status.rx_target_hits_2s ?? '—'))}</span>
        <span class="detail-key">Models Seen</span><span class="detail-val">${esc(models)}</span>
      </div>
    </div>
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

function sec(title, content) {
  return `<div class="detail-section"><h3>${title}</h3>${content}</div>`;
}

function grid(obj) {
  return `<div class="detail-grid">${Object.entries(obj).map(([k, v]) => `
    <span class="detail-key">${esc(k)}</span>
    <span class="detail-val">${esc(fmtVal(v))}</span>
  `).join('')}</div>`;
}

function fmtVal(v) {
  if (v == null || v === '') return '—';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

function updateStats(st) {
  if (!st) return;
  $count.textContent = String(st.signals_decoded || signals.length);
  $protos.textContent = String(st.unique_protocols || $protos.textContent || 0);
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

async function loadProtocolCatalog(forceWs) {
  if (forceWs) {
    send({ cmd: 'get_protocol_catalog' });
    return;
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

  $protocolStatus.textContent = `${filtered.length} of ${protocolCatalog.length} protocols mapped`;
}

function supportTierClass(tier) {
  if (tier === 'editable') return 'editable';
  if (tier === 'replay') return 'replay';
  return 'view';
}

async function loadLibrary() {
  try {
    const files = await (await fetch('/api/library')).json();
    const tb = $('library-tbody');
    if (!tb) return;
    tb.innerHTML = '';
    if (!files.length) {
      tb.innerHTML = '<tr><td colspan="6" class="muted" style="text-align:center;padding:2rem">No captures yet — save signals from the Dashboard</td></tr>';
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
          <div style="display:flex; gap:4px; justify-content:flex-end; flex-wrap:wrap">
            <button class="btn-sm" onclick="downloadLibraryFile('${jsq(file.name)}')">Download</button>
            ${file.has_urh_zip ? `<button class="btn-sm" onclick="downloadLibraryFile('${jsq(file.name.replace(/\.[^.]+$/, '.urh.zip'))}')">URH Zip</button>` : ''}
            ${file.can_tx ? `<button class="btn-sm accent" onclick="txLibrary('${jsq(file.name)}')">Replay</button>` : ''}
            ${file.can_tx ? `<button class="btn-sm" onclick="probeLibrary('${jsq(file.name)}')">Probe</button>` : ''}
          </div>
        </td>
      `;
      tb.appendChild(tr);
    });
  } catch (err) {
    console.error(err);
  }
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
  send({ cmd: 'tx_signal', data: { filename: name } });
};

window.probeLibrary = function(name) {
  if (!ensureResearchMode()) return;
  send({ cmd: 'tx_probe', data: { filename: name } });
};

function ensureResearchMode() {
  if (currentConfig.research_mode) return true;
  toast('Enable Research Mode in Settings to unlock TX actions', 'warn');
  return false;
}

async function ensureSignalExport(id) {
  const result = await doExport(id, 'c8', { download: false, toastLabel: 'Preparing replay file...' });
  return result ? basename(result.path) : null;
}

window.replaySignal = async function(id) {
  if (!ensureResearchMode()) return;
  const filename = await ensureSignalExport(id);
  if (filename) send({ cmd: 'tx_signal', data: { filename } });
};

window.probeSignal = async function(id) {
  if (!ensureResearchMode()) return;
  const filename = await ensureSignalExport(id);
  if (filename) send({ cmd: 'tx_probe', data: { filename } });
};

window.enableSniperById = function(id) {
  const sig = signals.find(item => item._id === id);
  if (!sig) return;
  enableSniper(sig.model || '');
};

window.enableSniper = function(model) {
  if (!$('sniper-model')) return toast('Settings tab missing sniper field', 'warn');
  $('sniper-model').value = model;
  saveSettings();
  toast(model ? `Sniper Mode: ${model}` : 'Sniper Mode disabled', 'ok');
  updateSniperUI(model);
};

function updateSniperUI(model) {
  const banner = $('sniper-banner');
  if (!banner) return;
  if (model) {
    banner.innerHTML = `<span><strong>Sniper Mode:</strong> filtering capture stream for <code>${esc(model)}</code></span><button class="btn-sm" style="background:#ff4444;color:#fff" onclick="enableSniper('')">Disable</button>`;
    banner.style.display = 'flex';
  } else {
    banner.style.display = 'none';
  }
}

function saveSettings() {
  send({
    cmd: 'save_settings',
    data: {
      settings: {
        ai_key: $('ai-key')?.value || '',
        ai_model: $('ai-model')?.value || 'anthropic/claude-3.5-haiku',
        rtl_autolevel: !!$('rtl-autolevel')?.checked,
        rtl_squelch: !!$('rtl-squelch')?.checked,
        rtl_gain: parseInt($('rtl-gain')?.value || '38', 10),
        unique_scans_only: !!$('unique-scans')?.checked,
        research_mode: !!$('research-mode')?.checked,
        sniper_mode_model: $('sniper-model')?.value.trim() || '',
      },
    },
  });
}

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
    toast('Saved → ' + name, 'ok');
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
  const cw = $('chat-window');
  if (!cw) return null;
  const outer = document.createElement('div');
  outer.className = `chat-msg ${isUser ? 'user-msg' : 'ai-msg'}`;
  if (id) outer.id = `msg-${id}`;
  const inner = document.createElement('div');
  inner.className = 'chat-bubble';
  inner.innerHTML = isUser ? esc(text) : renderMarkdown(text);
  outer.appendChild(inner);
  cw.appendChild(outer);
  cw.scrollTop = cw.scrollHeight;
  return inner;
}

function handleAIChunk(d) {
  if (!d?.id) return;
  if (!aiStreams[d.id]) {
    aiStreams[d.id] = '';
    appendChatMsg('', false, d.id);
  }
  aiStreams[d.id] += d.chunk || '';
  const bubble = $(`msg-${d.id}`)?.querySelector('.chat-bubble');
  if (bubble) {
    bubble.innerHTML = renderMarkdown(aiStreams[d.id]);
    const cw = $('chat-window');
    if (cw) cw.scrollTop = cw.scrollHeight;
  }
}

function handleAIChunkEnd(d) {
  if (d?.id && d.id.startsWith('chat_')) chatHistory.push({ role: 'assistant', content: aiStreams[d.id] || '' });
  if (d?.id) delete aiStreams[d.id];
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
  send({ cmd: 'ai_chat', data: { chat_id: chatId, prompt: text, history: chatHistory, context } });
  chatHistory.push({ role: 'user', content: text });
}

const chatInput = $('chat-input');
const chatSuggest = document.createElement('div');
chatSuggest.className = 'chat-suggest hidden';
chatSuggest.innerHTML = `
  <div class="suggest-item" data-val="@C_CAPTURE"><strong>@C_CAPTURE</strong> - Attach currently selected signal</div>
  <div class="suggest-item" data-val="@ALL_CAPTURES"><strong>@ALL_CAPTURES</strong> - Attach unique captured signals</div>
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
  filterText = event.target.value.toLowerCase();
  renderTable();
});
$('protocol-search')?.addEventListener('input', event => {
  protocolFilter = event.target.value.toLowerCase();
  renderProtocolCatalog();
});
$('btn-refresh-protocols')?.addEventListener('click', () => loadProtocolCatalog(true));
$('btn-refresh-lib')?.addEventListener('click', loadLibrary);
$('btn-clear')?.addEventListener('click', () => send({ cmd: 'clear_signals' }));
$('btn-save-config')?.addEventListener('click', saveSettings);
$('btn-set-freq')?.addEventListener('click', () => {
  const frequency = Number($('custom-freq')?.value || 0);
  if (frequency > 0) send({ cmd: 'set_frequency', data: { frequency } });
});
document.querySelectorAll('.freq-btn').forEach(btn => btn.addEventListener('click', () => {
  send({ cmd: 'set_frequency', data: { frequency: Number(btn.dataset.freq) } });
}));

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

['time', 'model', 'freq', 'mod', 'rssi', 'sec', 'flow'].forEach(key => {
  const el = $('th-' + key);
  if (!el) return;
  el.addEventListener('click', () => {
    if (sortBy === key) sortDesc = !sortDesc;
    else {
      sortBy = key;
      sortDesc = ['time', 'rssi', 'sec', 'flow'].includes(key);
    }
    document.querySelectorAll('.sort-arrow').forEach(arrow => { arrow.textContent = ''; });
    const arrow = el.querySelector('.sort-arrow');
    if (arrow) arrow.textContent = sortDesc ? ' ▼' : ' ▲';
    renderTable();
  });
});

document.addEventListener('keydown', event => {
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)) return;
  const tabs = ['dashboard', 'protocols', 'library', 'settings'];
  const idx = Number(event.key) - 1;
  if (idx >= 0 && idx < tabs.length) document.querySelector(`[data-tab="${tabs[idx]}"]`)?.click();
});

function toast(text, type) {
  const colors = { ok: '#3ddc84', err: '#ff4444', warn: '#ffab00' };
  const node = document.createElement('div');
  Object.assign(node.style, {
    position: 'fixed',
    bottom: '12px',
    right: '12px',
    padding: '8px 14px',
    background: colors[type] || '#ffffff',
    color: '#000000',
    fontSize: '12px',
    fontWeight: '600',
    borderRadius: '4px',
    zIndex: '100',
    fontFamily: 'var(--sans)',
    boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
    animation: 'fadeIn .2s ease',
  });
  node.textContent = text;
  document.body.appendChild(node);
  setTimeout(() => {
    node.style.opacity = '0';
    node.style.transition = 'opacity .2s';
    setTimeout(() => node.remove(), 200);
  }, 3000);
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
