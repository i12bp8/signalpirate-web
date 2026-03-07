/**
 * SignalPirate Web — Frontend
 */

// ── State ────────────────────────────────────────
let ws = null;
let signals = [];
let selectedId = null;
let filterText = '';
let sortBy = 'time';
let sortDesc = true;

// ── DOM ──────────────────────────────────────────
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

// ── Tabs ─────────────────────────────────────────
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(tc => tc.classList.remove('active'));
    tab.classList.add('active');
    const t = $('tab-' + tab.dataset.tab);
    if (t) t.classList.add('active');
    if (tab.dataset.tab === 'settings') send({ cmd: 'get_stats' });
  });
});

// ── WebSocket ────────────────────────────────────
function connect() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.onopen = () => { $dot.className = 'dot online'; };
  ws.onclose = () => { $dot.className = 'dot offline'; $devName.textContent = 'reconnecting'; setTimeout(connect, 2000); };
  ws.onerror = () => ws.close();
  ws.onmessage = e => handle(JSON.parse(e.data));
}

function send(d) { if (ws?.readyState === 1) ws.send(JSON.stringify(d)); }

// ── Message Handler ──────────────────────────────
function handle(msg) {
  if (msg.type === 'init') {
    const d = msg.data;
    $devName.textContent = d.device?.name || 'No Device';
    $dot.className = d.device?.type === 'none' ? 'dot offline' : 'dot online';
    if (d.stats) updateStats(d.stats);
    if (d.signals) d.signals.forEach(s => addSignal(s, true));
    
    // Load config
    if (d.config) {
      if (d.config.ai_key) $('ai-key').value = d.config.ai_key;
      if (d.config.ai_model) $('ai-model').value = d.config.ai_model;
      if (d.config.rtl_autolevel !== undefined) $('rtl-autolevel').checked = !!d.config.rtl_autolevel;
      if (d.config.rtl_squelch !== undefined) $('rtl-squelch').checked = !!d.config.rtl_squelch;
      if (d.config.rtl_gain !== undefined) $('rtl-gain').value = d.config.rtl_gain;
    }
  }
  else if (msg.type === 'signal') addSignal(msg.data);
  else if (msg.type === 'ai_chunk') handleAIChunk(msg.data);
  else if (msg.type === 'ai_chunk_end') handleAIChunkEnd(msg.data);
  else if (msg.type === 'stats') updateStats(msg.data);
  else if (msg.type === 'frequency_changed') setFreqDisplay(msg.data.frequency);
  else if (msg.type === 'exported') toast('Saved → ' + msg.data.path.split('/').pop(), 'ok');
  else if (msg.type === 'config_saved') {
    toast('Settings saved', 'ok');
  }
  else if (msg.type === 'cleared') { signals = []; $tbody.innerHTML = ''; $count.textContent = '0'; $status.textContent = 'cleared'; }
  else if (msg.type === 'error') toast(msg.data.message, 'err');
}

// ── Signal Table ─────────────────────────────────
function addSignal(sig, silent) {
  sig._id = sig._id ?? signals.length;
  signals.push(sig);
  $count.textContent = signals.length;
  $protos.textContent = new Set(signals.map(s => s.model).filter(Boolean)).size;
  $status.textContent = signals.length + ' captured';

  // Sort
  let sortedSigs = filterText ? signals.filter(s => matches(s, filterText)) : [...signals];
  sortedSigs.sort((a,b) => {
    let va = a[sortBy], vb = b[sortBy];
    if (sortBy === 'time') { va = a.timestamp||0; vb = b.timestamp||0; }
    else if (sortBy === 'model') { va = (a.model||'').toLowerCase(); vb = (b.model||'').toLowerCase(); }
    else if (sortBy === 'freq') { va = a.frequency||0; vb = b.frequency||0; }
    else if (sortBy === 'mod') { va = (a.modulation||'').toLowerCase(); vb = (b.modulation||'').toLowerCase(); }
    else if (sortBy === 'rssi') { va = a.rssi??-999; vb = b.rssi??-999; }
    else if (sortBy === 'sec') {
      const v = { critical:5, broken:5, vulnerable:4, moderate:3, secure:2, none:1, unknown:0 };
      va = v[getSec(a)]; vb = v[getSec(b)];
    }
    // Correct descending/ascending ordering (latest/highest first when sortDesc=true).
    if (va < vb) return sortDesc ? 1 : -1;
    if (va > vb) return sortDesc ? -1 : 1;
    return 0;
  });

  $tbody.innerHTML = '';
  sortedSigs.slice(0, 300).forEach(s => renderRow(s, silent && s._id === signals[signals.length-1]?._id));
}

function getSec(sig) {
  if (sig.protocol_info && sig.protocol_info.security_level) return sig.protocol_info.security_level;
  const m = (sig.model || '').toLowerCase();
  if (['toyota','ford','kia','honda','nissan','vag','bmw','hyundai','mazda','subaru','gm'].some(v => m.includes(v))) return 'vulnerable';
  if (m.includes('auriol') || m.includes('oregon') || m.includes('infactory') || m.includes('acurite') || m.includes('lacrosse')) return 'none';
  return 'unknown';
}

function renderRow(sig, animate) {
  const tr = document.createElement('tr');
  tr.dataset.id = sig._id;
  if (animate) tr.classList.add('new-signal');
  if (sig._id === selectedId) tr.classList.add('selected');

  const ts = sig.timestamp ? new Date(sig.timestamp * 1000).toLocaleTimeString() : '—';
  const freq = sig.frequency ? (sig.frequency / 1e6).toFixed(2) : '—';
  const rssi = sig.rssi != null ? Math.round(sig.rssi) : '—';
  const mod = sig.modulation || '?';

  // Security level
  let sec = getSec(sig);
  const cls = { critical:'sec-critical', broken:'sec-critical', none:'sec-none', vulnerable:'sec-vulnerable', moderate:'sec-moderate', secure:'sec-secure' }[sec] || 'sec-unknown';

  tr.innerHTML = `<td>${ts}</td><td class="model-cell">${esc(sig.model||'?')}</td><td class="freq-cell">${freq}</td><td>${mod}</td><td class="rssi-cell">${rssi}</td><td><span class="sec-dot ${cls}">●</span></td>`;
  tr.onclick = () => selectSignal(sig, tr);
  $tbody.appendChild(tr);
}

function selectSignal(sig, tr) {
  document.querySelectorAll('#signal-tbody tr.selected').forEach(r => r.classList.remove('selected'));
  if (tr) tr.classList.add('selected');
  selectedId = sig._id;
  $title.textContent = `#${sig._id} — ${sig.model || 'Unknown'}`;
  $detail.innerHTML = renderDetail(sig);
}

function renderDetail(sig) {
  const freq = sig.frequency ? (sig.frequency / 1e6).toFixed(3) + ' MHz' : '—';
  const ts = sig.timestamp ? new Date(sig.timestamp * 1000).toLocaleString() : '—';
  const rssi = sig.rssi != null ? sig.rssi.toFixed(1) + ' dBm' : '—';

  let h = `<div class="detail-actions">
    <button class="btn accent" onclick="doExport(${sig._id},'c8')">Save to Library</button>
  </div>`;

  h += sec('Metadata', grid({
    'Time': ts, 'Frequency': freq, 'RSSI': rssi,
    'Modulation': sig.modulation || '—', 'Protocol ID': sig.protocol_id ?? '—'
  }));

  if (sig.protocol_info) {
    const p = sig.protocol_info;
    h += sec('Protocol', grid({
      'Name': p.name||'—', 'Category': p.category||'—',
      'Security': p.security_level||'—', 'Info': p.description||'—'
    }));
  }

  if (sig.vuln_info && sig.vuln_info.length) {
    h += sec('Vulnerabilities', sig.vuln_info.map(v =>
      grid({ 'CVE': v.cve||'—', 'Severity': v.severity||'—', 'Impact': v.description||'—' })
    ).join(''));
  }

  // Payload data — exclude meta fields
  if (sig.data && typeof sig.data === 'object') {
    const skip = new Set(['time','model','freq','rssi','snr','mod','noise','protocol','modulation']);
    const payload = Object.entries(sig.data).filter(([k]) => !skip.has(k));
    if (payload.length) {
      h += sec('Payload', grid(Object.fromEntries(payload)));
    }
  }

  h += sec('Raw', `<div class="hex-dump">${esc(JSON.stringify(sig.data||sig, null, 2))}</div>`);
  return h;
}

// detail helpers
function sec(title, content) { return `<div class="detail-section"><h3>${title}</h3>${content}</div>`; }
function grid(obj) {
  return `<div class="detail-grid">${Object.entries(obj).map(([k,v]) =>
    `<span class="detail-key">${esc(k)}</span><span class="detail-val">${esc(String(v))}</span>`
  ).join('')}</div>`;
}

// ── Actions ──────────────────────────────────────
// doExport moved to bottom of file

// ── AI Stream Handling ───────────────────────────
let aiStreams = {};

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

  // Chat architecture
  if (!aiStreams[d.id]) {
    aiStreams[d.id] = '';
    appendChatMsg('', false, d.id); // Create empty bubble
  }
  aiStreams[d.id] += d.chunk;
  const bubble = $(`msg-${d.id}`)?.querySelector('.chat-bubble');
  if (bubble) {
    bubble.innerHTML = renderMarkdown(aiStreams[d.id]);
    const cw = $('chat-window');
    if (cw) cw.scrollTop = cw.scrollHeight;
  }
}
function handleAIChunkEnd(d) {
  if (d.id && d.id.startsWith('chat_')) {
     chatHistory.push({ role: 'assistant', content: aiStreams[d.id] });
  }
  delete aiStreams[d.id];
}
function renderMarkdown(tx) {
  if (typeof marked !== 'undefined') {
    return marked.parse(tx);
  }
  return esc(tx);
}



// ── Filter & Sort ────────────────────────────────
$('signal-search').addEventListener('input', e => { filterText = e.target.value.toLowerCase(); renderTable(); });
function matches(s, t) { return (s.model||'').toLowerCase().includes(t) || (s.modulation||'').toLowerCase().includes(t); }

['time','model','freq','mod','rssi','sec'].forEach(key => {
  const el = $(`th-${key}`);
  if (el) el.addEventListener('click', () => {
    if (sortBy === key) sortDesc = !sortDesc;
    else { sortBy = key; sortDesc = (key === 'time' || key === 'rssi' || key === 'sec'); }
    
    document.querySelectorAll('.sort-arrow').forEach(a => a.textContent = '');
    const arrow = el.querySelector('.sort-arrow');
    if (arrow) arrow.textContent = sortDesc ? ' ▼' : ' ▲';
    
    renderTable();
  });
});

function renderTable() {
  $tbody.innerHTML = '';
  // Re-run the filter/sort/render logic on the existing signals
  // by calling addSignal with our latest actual signal, just keeping it silent.
  // Because we do early returns, if the array is empty we handle that:
  if (!signals.length) return;
  const dummy = {...signals[signals.length-1]};
  dummy._id = signals[signals.length-1]._id; // don't increment id
  addSignal(dummy, true);
  // remove the duplicate added by hacky addSignal
  signals.pop();
  $count.textContent = signals.length;
}

// ── Stats ────────────────────────────────────────
function updateStats(st) {
  $count.textContent = st.signals_decoded || signals.length;
  $protos.textContent = st.unique_protocols || 0;
  if (st.frequency) setFreqDisplay(st.frequency);
  const r = $('st-running'); if (!r) return;
  r.textContent = st.running ? 'active' : 'stopped';
  $('st-uptime').textContent = fmtUp(st.uptime_seconds);
  $('st-signals').textContent = st.signals_decoded;
  $('st-protocols').textContent = (st.protocols_seen||[]).join(', ') || '—';
  $('st-device').textContent = st.device_type || '—';
  $('st-freq').textContent = (st.frequency/1e6).toFixed(3) + ' MHz';
}

function setFreqDisplay(f) {
  $freq.textContent = (f/1e6).toFixed(3);
  document.querySelectorAll('.freq-btn').forEach(b => b.classList.toggle('active', +b.dataset.freq === f));
  const cf = $('custom-freq'); if (cf) cf.value = f;
}

function fmtUp(s) { if (!s) return '—'; const m=Math.floor(s/60),h=Math.floor(m/60); return h?h+'h '+m%60+'m':m+'m '+Math.floor(s%60)+'s'; }

// ── Frequency ────────────────────────────────────
document.querySelectorAll('.freq-btn').forEach(b => b.addEventListener('click', () => send({ cmd:'set_frequency', frequency:+b.dataset.freq })));
$('btn-set-freq')?.addEventListener('click', () => { const v=+$('custom-freq').value; if(v>0) send({cmd:'set_frequency',frequency:v}); });



// ── Settings ─────────────────────────────────────
document.addEventListener('click', (e) => {
  if (e.target.id === 'btn-save-config') {
    send({ cmd: 'save_settings', data: { settings: { 
      ai_key: $('ai-key').value, 
      ai_model: $('ai-model').value,
      rtl_autolevel: $('rtl-autolevel') ? $('rtl-autolevel').checked : true,
      rtl_squelch: $('rtl-squelch') ? $('rtl-squelch').checked : true,
      rtl_gain: $('rtl-gain') ? parseInt($('rtl-gain').value, 10) : 38
    }}});
  }
});

// ── AI Chat Event Listeners ──────────────────────
const chatInput = $('chat-input');
const chatSuggest = document.createElement('div');
chatSuggest.className = 'chat-suggest hidden';
chatSuggest.innerHTML = `
  <div class="suggest-item" data-val="@C_CAPTURE"><strong>@C_CAPTURE</strong> - Attach currently selected signal</div>
  <div class="suggest-item" data-val="@ALL_CAPTURES"><strong>@ALL_CAPTURES</strong> - Attach unique captured signals</div>
`;
chatInput?.parentNode.insertBefore(chatSuggest, chatInput);

chatSuggest.addEventListener('click', (e) => {
  const item = e.target.closest('.suggest-item');
  if (!item) return;
  const val = item.dataset.val;
  chatInput.value = chatInput.value.replace(/@\w*$/, val + ' ');
  chatSuggest.classList.add('hidden');
  chatInput.focus();
});

$('btn-chat-send')?.addEventListener('click', sendChatMessage);
chatInput?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatMessage(); }
});

chatInput?.addEventListener('input', (e) => {
  const val = chatInput.value;
  const lastAt = val.lastIndexOf('@');
  if (lastAt >= 0 && !val.includes(' ', lastAt)) {
    chatSuggest.classList.remove('hidden');
  } else {
    chatSuggest.classList.add('hidden');
  }
});

let chatHistory = [];

function sendChatMessage() {
  const input = $('chat-input');
  const txt = input.value.trim();
  if (!txt) return;
  
  input.value = '';
  chatSuggest.classList.add('hidden');
  appendChatMsg(txt, true);
  
  // Build context based on @ mentions
  let context = null;
  if (txt.includes('@C_CAPTURE')) {
    if (selectedId && signals.length) {
      const sig = signals.find(s => s._id === selectedId);
      if (sig) context = { type: 'current_capture', data: sig };
    }
  } else if (txt.includes('@ALL_CAPTURES')) {
    // Deduplicate captures by model & freq
    const seen = new Set();
    const uniqueSigs = [];
    for (const s of signals) {
      const key = `${s.model}_${s.frequency}`;
      if (!seen.has(key)) {
        seen.add(key);
        uniqueSigs.push({ time: s.timestamp, freq: s.frequency, model: s.model, modulation: s.modulation });
      }
    }
    context = { type: 'all_captures', data: uniqueSigs };
  }
  
  const mid = 'chat_' + Date.now();
  send({ cmd: 'ai_chat', data: { chat_id: mid, prompt: txt, history: chatHistory, context: context } });
  
  // Add user prompt to frontend history
  chatHistory.push({ role: 'user', content: txt });
}

// ── Clear ────────────────────────────────────────
$('btn-clear')?.addEventListener('click', () => send({ cmd: 'clear_signals' }));

// ── Library ──────────────────────────────────────
$('btn-refresh-lib')?.addEventListener('click', loadLibrary);
async function loadLibrary() {
  try {
    const files = await (await fetch('/api/library')).json();
    const tb = $('library-tbody'); if (!tb) return;
    tb.innerHTML = '';
    if (!files.length) { tb.innerHTML = '<tr><td colspan="6" class="muted" style="text-align:center;padding:2rem">No captures yet — save signals from the Dashboard</td></tr>'; return; }
    files.forEach(f => {
      const tr = document.createElement('tr');
      const sz = f.size>1024?(f.size/1024).toFixed(1)+' KB':f.size+' B';
      tr.innerHTML = `<td class="model-cell">${esc(f.name)}</td><td>${f.format.toUpperCase()}</td><td>${sz}</td><td>${new Date(f.modified*1000).toLocaleString()}</td>
        <td style="text-align:right">
          <div style="display:flex; gap:4px; justify-content:flex-end">
            <button class="btn-sm" onclick="window.open('/api/library/${encodeURIComponent(f.name)}')">↓ Download</button>
          </div>
        </td>`;
      tb.appendChild(tr);
    });
  } catch(e) { console.error(e); }
}

// ── Toast ────────────────────────────────────────
function toast(text, type) {
  const colors = { ok:'#3ddc84', err:'#ff4444', warn:'#ffab00' };
  const n = document.createElement('div');
  Object.assign(n.style, {
    position:'fixed', bottom:'12px', right:'12px', padding:'6px 14px',
    background: colors[type]||'#fff', color:'#000', fontSize:'11px', fontWeight:'600',
    borderRadius:'4px', zIndex:'100', fontFamily:'var(--sans)',
    boxShadow:'0 4px 16px rgba(0,0,0,0.5)', animation:'fadeIn .2s ease'
  });
  n.textContent = text;
  document.body.appendChild(n);
  setTimeout(() => { n.style.opacity='0'; n.style.transition='opacity .2s'; setTimeout(()=>n.remove(),200); }, 3000);
}

// ── Export ───────────────────────────────────────
async function doExport(id, format) {
  try {
    toast('Saving signal...', 'ok');
    const res = await fetch('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ signal_id: id, format: format })
    });
    const data = await res.json();
    if (data.ok) {
      toast('Saved → ' + data.path.split('/').pop(), 'ok');
      if (typeof loadLibrary === 'function') loadLibrary();
      
      const filename = encodeURIComponent(data.path.split('/').pop());
      // Auto-trigger download for the .c8 file
      window.open('/api/library/' + filename, '_blank');
      
      // Auto-trigger download for the companion .xml URH project file
      setTimeout(() => {
        window.open('/api/library/' + filename.replace('.c8', '.xml'), '_blank');
      }, 500);
      
    } else {
      toast('Export failed: ' + data.error, 'err');
    }
  } catch(e) { 
    console.error(e);
    toast('Error exporting signal', 'err'); 
  }
}

// ── Keyboard ─────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA') return;
  const tabs = ['dashboard','editor','library','payload','settings'];
  if (+e.key >= 1 && +e.key <= 5) document.querySelector(`[data-tab="${tabs[+e.key-1]}"]`).click();
});

// ── Utils ────────────────────────────────────────
function esc(s) { const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

// ── CSS inject ───────────────────────────────────
const st = document.createElement('style');
st.textContent = `
  @keyframes fadeIn { from{transform:translateY(8px);opacity:0} to{transform:none;opacity:1} }
  th { cursor: pointer; user-select: none; }
  th:hover { color: var(--text-2); }
  .sort-arrow { font-size: 0.8em; opacity: 0.7; }
  .hex-textarea { width:100%;background:var(--bg);border:1px solid var(--border);border-radius:3px;
    color:var(--text);padding:8px;font-size:11px;resize:vertical;outline:none;font-family:var(--mono) }
  .hex-textarea:focus { border-color:var(--border-focus) }
  .sec-none { color: #555; }
`;
document.head.appendChild(st);

// ── Boot ─────────────────────────────────────────
connect();
