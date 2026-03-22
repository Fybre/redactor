let allEntities = [];
let editingProfile = null;

// ── System Config ────────────────────────────────────────

async function loadConfig() {
  try {
    const config = await api.get('/config');
    document.getElementById('cfg-polling').classList.toggle('on', config.folder_polling_enabled);
    document.getElementById('cfg-polling').dataset.value = config.folder_polling_enabled ? '1' : '0';
    document.getElementById('cfg-interval').value = config.poll_interval_seconds;
    document.getElementById('cfg-level').value = config.default_redaction_level;
    document.getElementById('cfg-retain').classList.toggle('on', config.retain_originals);
    document.getElementById('cfg-retain').dataset.value = config.retain_originals ? '1' : '0';
    document.getElementById('cfg-retention-days').value = config.retention_days;
    document.getElementById('cfg-concurrency').value = config.worker_concurrency;
    document.getElementById('cfg-max-size').value = config.max_file_size_mb;
    document.getElementById('cfg-ocr-lang').value = config.ocr_language || 'eng';
    document.getElementById('cfg-detection-strategy').value = config.detection_strategy || 'presidio';
    const threshold = config.auto_approve_threshold != null ? config.auto_approve_threshold : 0.85;
    document.getElementById('cfg-auto-approve-threshold').value = threshold;
    document.getElementById('cfg-auto-approve-threshold-val').textContent = (threshold * 100).toFixed(0) + '%';
    document.getElementById('cfg-llm-base-url').value = config.llm_base_url || 'http://ollama:11434/v1';
    document.getElementById('cfg-llm-model').value = config.llm_model || 'llama3.2:3b';
    updateLLMFields();
    if ((config.detection_strategy || 'presidio') !== 'presidio') loadOllamaModels();
  } catch (e) { console.error(e); }
}

function updateLLMFields() {
  const strategy = document.getElementById('cfg-detection-strategy').value;
  const visible = strategy !== 'presidio';
  document.getElementById('llm-fields').style.display = visible ? '' : 'none';
  if (visible) loadOllamaModels();
}

// ── Ollama model management ───────────────────────────────

let _ollamaModels = [];

async function loadOllamaModels() {
  const statusEl  = document.getElementById('ollama-model-status');
  const pullBtn   = document.getElementById('ollama-pull-btn');
  const select    = document.getElementById('ollama-models-select');
  const availDiv  = document.getElementById('ollama-models-available');
  if (!statusEl) return;
  statusEl.textContent = '';
  try {
    const res = await api.get('/config/ollama/models');
    _ollamaModels = res.models || [];
    select.innerHTML = '<option value="">— pulled models (select to use) —</option>' +
      _ollamaModels.map(m => `<option value="${m}">${m}</option>`).join('');
    availDiv.style.display = _ollamaModels.length ? '' : 'none';
    checkModelStatus();
  } catch {
    statusEl.textContent = 'Ollama unreachable';
    statusEl.style.color = 'var(--muted)';
    pullBtn.style.display = 'none';
    document.getElementById('ollama-models-available').style.display = 'none';
  }
}

function selectOllamaModel(val) {
  if (!val) return;
  document.getElementById('cfg-llm-model').value = val;
  document.getElementById('ollama-models-select').value = '';
  checkModelStatus();
}

function checkModelStatus() {
  const model    = (document.getElementById('cfg-llm-model')?.value || '').trim();
  const statusEl = document.getElementById('ollama-model-status');
  const pullBtn  = document.getElementById('ollama-pull-btn');
  if (!statusEl) return;
  if (!model || !_ollamaModels.length) { statusEl.textContent = ''; pullBtn.style.display = 'none'; return; }
  if (_ollamaModels.includes(model)) {
    statusEl.textContent = '✓ Available';
    statusEl.style.color = '#4caf50';
    pullBtn.style.display = 'none';
  } else {
    statusEl.textContent = 'Not pulled';
    statusEl.style.color = '#ff9800';
    pullBtn.style.display = '';
  }
}

async function pullOllamaModel() {
  const model      = (document.getElementById('cfg-llm-model')?.value || '').trim();
  const progressEl = document.getElementById('ollama-pull-progress');
  const statusEl   = document.getElementById('ollama-pull-status');
  const barEl      = document.getElementById('ollama-pull-bar');
  const pullBtn    = document.getElementById('ollama-pull-btn');
  if (!model) return;

  progressEl.style.display = '';
  pullBtn.disabled = true;
  statusEl.textContent = 'Starting…';
  barEl.style.width = '0%';

  try {
    const resp = await fetch('/api/v1/config/ollama/pull', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model }),
    });
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const d = JSON.parse(line.slice(6));
          if (d.error) throw new Error(d.error);
          if (d.done) {
            progressEl.style.display = 'none';
            pullBtn.disabled = false;
            await loadOllamaModels();
            showToast(`Model "${model}" pulled successfully`, 'success');
            return;
          }
          if (d.status) statusEl.textContent = d.status;
          if (d.total && d.completed)
            barEl.style.width = `${Math.round(d.completed / d.total * 100)}%`;
        } catch (e) {
          if (e.message !== 'Unexpected end of JSON input') throw e;
        }
      }
    }
  } catch (e) {
    progressEl.style.display = 'none';
    pullBtn.disabled = false;
    showToast(`Pull failed: ${e.message}`, 'error');
  }
}

function toggleSwitch(id) {
  const el = document.getElementById(id);
  const isOn = el.dataset.value === '1';
  el.dataset.value = isOn ? '0' : '1';
  el.classList.toggle('on', !isOn);
}

async function saveConfig() {
  const config = {
    folder_polling_enabled: document.getElementById('cfg-polling').dataset.value === '1',
    poll_interval_seconds: parseInt(document.getElementById('cfg-interval').value),
    default_redaction_level: document.getElementById('cfg-level').value,
    retain_originals: document.getElementById('cfg-retain').dataset.value === '1',
    retention_days: parseInt(document.getElementById('cfg-retention-days').value),
    worker_concurrency: parseInt(document.getElementById('cfg-concurrency').value),
    max_file_size_mb: parseInt(document.getElementById('cfg-max-size').value),
    default_output_mode: document.getElementById('cfg-output-mode').value,
    ocr_language: document.getElementById('cfg-ocr-lang').value,
    detection_strategy: document.getElementById('cfg-detection-strategy').value,
    auto_approve_threshold: parseFloat(document.getElementById('cfg-auto-approve-threshold').value),
    llm_base_url: document.getElementById('cfg-llm-base-url').value || 'http://ollama:11434/v1',
    llm_model: document.getElementById('cfg-llm-model').value || 'llama3.2:3b',
    llm_api_key: 'ollama',
    redaction_color: [0, 0, 0],
    allowed_extensions: ['pdf', 'png', 'jpg', 'jpeg', 'tiff', 'tif'],
    webhooks: await api.get('/config/webhooks').catch(() => []) || [],
    profiles: {},
    default_profile: null,
  };

  try {
    await api.put('/config', config);
    showToast('Settings saved', 'success');
  } catch (e) { console.error(e); }
}

// ── Tabs ─────────────────────────────────────────────────

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === `tab-${name}`));
  if (name === 'folders') loadWatchedFolders();
  if (name === 'entities') loadEntities();
}

// ── Profiles ─────────────────────────────────────────────

let _systemStrategy = 'presidio';

async function loadProfiles() {
  try {
    const [profiles, cfg] = await Promise.all([
      api.get('/config/profiles'),
      api.get('/config'),
    ]);
    _systemStrategy = cfg.detection_strategy || 'presidio';
    renderProfiles(profiles);
  } catch (e) { console.error(e); }
}

function _strategyLabel(s) {
  return s === 'presidio' ? 'Presidio' : s === 'llm' ? 'LLM' : s === 'both' ? 'Both' : s;
}

function _strategyCompatible(profileStrategy) {
  if (!profileStrategy) return true;
  const sysLlm = _systemStrategy === 'llm' || _systemStrategy === 'both';
  const sysPresidio = _systemStrategy === 'presidio' || _systemStrategy === 'both';
  const needsLlm = profileStrategy === 'llm' || profileStrategy === 'both';
  const needsPresidio = profileStrategy === 'presidio' || profileStrategy === 'both';
  return (!needsLlm || sysLlm) && (!needsPresidio || sysPresidio);
}

function renderProfiles(profiles) {
  const el = document.getElementById('profiles-list');
  const entries = Object.entries(profiles);
  if (!entries.length) {
    el.innerHTML = `<div class="empty-state"><div class="empty-state-icon">📋</div><h3>No profiles</h3><p>Restore defaults to bring back built-in level profiles.</p></div>`;
    return;
  }
  el.innerHTML = `<table><thead><tr><th>Name</th><th>Entities</th><th>Strategy</th><th>Description</th><th>Actions</th></tr></thead><tbody>` +
    entries.map(([name, p]) => {
      const compatible = _strategyCompatible(p.strategy);
      const strategyCell = p.strategy
        ? `<span style="font-size:11px">${_strategyLabel(p.strategy)}${!compatible ? ' <span title="Incompatible with system strategy — will fall back to system default" style="color:#e67e22">⚠</span>' : ''}</span>`
        : `<span style="font-size:11px;color:var(--muted)">System default</span>`;
      return `
      <tr${!compatible ? ' style="opacity:0.75"' : ''}>
        <td>
          <strong>${name}</strong>
          ${p.builtin ? '<span style="font-size:10px;color:var(--muted);margin-left:6px">built-in</span>' : ''}
        </td>
        <td>${p.entities.length} types</td>
        <td>${strategyCell}</td>
        <td style="color:var(--muted)">${p.description || '—'}</td>
        <td>
          <div style="display:flex;gap:6px">
            ${!p.builtin ? `<button onclick="editProfile('${name}')" class="btn btn-ghost btn-sm">Edit</button>` : ''}
            <button onclick="duplicateProfile('${name}')" class="btn btn-ghost btn-sm">Duplicate</button>
            <button onclick="deleteProfile('${name}')" class="btn btn-danger btn-sm">Delete</button>
          </div>
        </td>
      </tr>`;
    }).join('') + `</tbody></table>`;
}

async function loadEntitiesForModal() {
  if (allEntities.length) return;
  try {
    allEntities = await api.get('/config/entities');
  } catch (e) { console.error(e); }
}

async function _populateStrategyDropdown(currentStrategy) {
  // Fetch current system strategy to know what options are valid
  let systemStrategy = 'presidio';
  try {
    const cfg = await api.get('/config');
    systemStrategy = cfg.detection_strategy || 'presidio';
  } catch (e) { /* fallback */ }

  const sel = document.getElementById('modal-profile-strategy');
  const LABELS = { presidio: 'Presidio (pattern & NER)', llm: 'LLM (AI detection)', both: 'Both (Presidio + LLM)' };
  // Which options are enabled given the system strategy
  const allowed = systemStrategy === 'both' ? ['presidio', 'llm', 'both']
                : systemStrategy === 'llm'  ? ['llm']
                : ['presidio'];

  sel.innerHTML = '<option value="">Inherit system default</option>' +
    allowed.map(v => `<option value="${v}">${LABELS[v]}</option>`).join('');

  sel.value = allowed.includes(currentStrategy) ? (currentStrategy || '') : '';

  // Warn if the saved strategy is incompatible with the current system setting
  const warn = document.getElementById('modal-profile-strategy-warn');
  if (currentStrategy && !allowed.includes(currentStrategy)) {
    warn.style.display = '';
    // Add the incompatible option so the user can see it
    sel.insertAdjacentHTML('beforeend', `<option value="${currentStrategy}" disabled>${LABELS[currentStrategy] || currentStrategy} (unavailable)</option>`);
    sel.value = '';
  } else {
    warn.style.display = 'none';
  }

  return systemStrategy;
}

function openProfileModal(name = null, existing = null) {
  editingProfile = name;
  document.getElementById('modal-title').textContent = name ? 'Edit Profile' : 'New Profile';
  document.getElementById('modal-profile-name').value = name || '';
  document.getElementById('modal-profile-desc').value = existing?.description || '';
  document.getElementById('modal-profile-name').disabled = false;
  _populateStrategyDropdown(existing?.strategy || '');

  const grid = document.getElementById('modal-entity-grid');
  grid.innerHTML = allEntities.map(e => {
    const checked = existing?.entities?.includes(e.type) || false;
    return `
      <label class="entity-checkbox ${checked ? 'checked' : ''}" id="mec-${e.type}">
        <input type="checkbox" value="${e.type}" ${checked ? 'checked' : ''} onchange="updateModalCheckbox('${e.type}')">
        <div>
          <div style="font-weight:600;font-size:11px">${e.type}</div>
          ${e.description ? `<div style="font-size:10px;color:var(--muted);margin-top:2px;line-height:1.3">${e.description}</div>` : ''}
        </div>
      </label>`;
  }).join('');

  document.getElementById('profile-modal').classList.add('open');
}

function updateModalCheckbox(type) {
  const label = document.getElementById(`mec-${type}`);
  const cb = label.querySelector('input');
  label.classList.toggle('checked', cb.checked);
}

function closeProfileModal() {
  document.getElementById('profile-modal').classList.remove('open');
  editingProfile = null;
}

async function editProfile(name) {
  await loadEntitiesForModal();
  try {
    const profiles = await api.get('/config/profiles');
    openProfileModal(name, profiles[name]);
  } catch (e) { console.error(e); }
}

async function openNewProfile() {
  await loadEntitiesForModal();
  openProfileModal(null, null);
}

async function saveProfile() {
  const name = document.getElementById('modal-profile-name').value.trim();
  const desc = document.getElementById('modal-profile-desc').value.trim();
  if (!name) { showToast('Profile name is required', 'error'); return; }

  const entities = Array.from(document.querySelectorAll('#modal-entity-grid input:checked')).map(cb => cb.value);
  if (!entities.length) { showToast('Select at least one entity', 'error'); return; }

  const strategy = document.getElementById('modal-profile-strategy').value || null;
  const payload = { name, entities, description: desc, ...(strategy ? { strategy } : {}) };
  try {
    if (editingProfile) {
      await api.put(`/config/profiles/${editingProfile}`, payload);
      showToast('Profile updated', 'success');
    } else {
      await api.post('/config/profiles', payload);
      showToast('Profile created', 'success');
    }
    closeProfileModal();
    loadProfiles();
  } catch (e) { console.error(e); }
}

async function duplicateProfile(name) {
  try {
    const res = await api.post(`/config/profiles/${name}/duplicate`, {});
    showToast(`Duplicated as "${res.name}"`, 'success');
    loadProfiles();
  } catch (e) { console.error(e); }
}

async function deleteProfile(name) {
  if (!confirm(`Delete profile "${name}"?`)) return;
  try {
    await api.delete(`/config/profiles/${name}`);
    showToast('Profile deleted', 'success');
    await loadProfiles();
  } catch (e) { showToast('Delete failed', 'error'); console.error(e); }
}

async function restoreDefaultProfiles() {
  if (!confirm('Restore all built-in default profiles? Your custom profiles will not be affected.')) return;
  try {
    await api.post('/config/profiles/_restore_defaults', {});
    showToast('Default profiles restored', 'success');
    loadProfiles();
  } catch (e) { console.error(e); }
}

// ── Webhooks ─────────────────────────────────────────────

async function loadWebhooks() {
  try {
    const webhooks = await api.get('/config/webhooks');
    renderWebhooks(webhooks);
  } catch (e) { console.error(e); }
}

function renderWebhooks(webhooks) {
  const el = document.getElementById('webhooks-list');
  if (!webhooks.length) {
    el.innerHTML = `<div class="empty-state"><div class="empty-state-icon">🔔</div><h3>No webhooks</h3><p>Add a webhook to receive notifications when jobs complete.</p></div>`;
    return;
  }
  el.innerHTML = `<table><thead><tr><th>Name</th><th>URL</th><th>Signed</th><th>Actions</th></tr></thead><tbody>` +
    webhooks.map(w => `
      <tr>
        <td><strong>${w.name || '—'}</strong></td>
        <td style="font-family:monospace;font-size:12px">${w.url}</td>
        <td>${w.secret ? '🔑 Yes' : 'No'}</td>
        <td>
          <div style="display:flex;gap:6px">
            <button onclick="testWebhook('${w.id}')" class="btn btn-ghost btn-sm">Test</button>
            <button onclick="deleteWebhook('${w.id}')" class="btn btn-danger btn-sm">Delete</button>
          </div>
        </td>
      </tr>`).join('') + `</tbody></table>`;
}

// ── Entities & Recognizers ────────────────────────────────────────────────────

async function loadEntities() {
  try {
    const [entities, recognizers] = await Promise.all([
      api.get('/config/entities'),
      api.get('/config/recognizers'),
    ]);
    renderEntityList(entities);
    renderCustomRecognizers(recognizers);
  } catch (e) { console.error(e); }
}

function recTypeBadge(type) {
  const map = { pattern: ['#1e3a5f','#93c5fd','Pattern'], ner: ['#134e4a','#5eead4','NER'], deny_list: ['#3b1f5e','#c4b5fd','Deny List'] };
  const [bg, color, label] = map[type] || ['#333','#ccc', type];
  return `<span style="font-size:10px;padding:1px 7px;border-radius:10px;background:${bg};color:${color}">${label}</span>`;
}

function renderEntityList(entities) {
  const el = document.getElementById('entities-list');
  el.innerHTML = `<table><thead><tr><th>Entity Type</th><th>Type</th><th>Description</th><th></th></tr></thead><tbody>` +
    entities.map(e => `
      <tr>
        <td style="font-family:monospace;font-size:12px;font-weight:600">${e.type}</td>
        <td>${recTypeBadge(e.recognizer_type)}</td>
        <td style="color:var(--muted);font-size:12px">${e.description || '—'}</td>
        <td>${e.custom ? '<span style="font-size:10px;color:var(--muted)">custom</span>' : ''}</td>
      </tr>`).join('') + `</tbody></table>`;
}

function renderCustomRecognizers(recognizers) {
  const el = document.getElementById('custom-recognizers-list');
  if (!recognizers.length) {
    el.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px 0">No custom recognizers defined.</div>';
    return;
  }
  el.innerHTML = `<table><thead><tr><th>Name</th><th>Entity Type</th><th>Type</th><th>Detail</th><th>Actions</th></tr></thead><tbody>` +
    recognizers.map(r => {
      const detail = r.type === 'pattern'
        ? (r.patterns || []).map(p => `<code style="font-size:11px">${p.regex}</code>`).join(', ')
        : `${(r.deny_list || []).length} values`;
      return `<tr>
        <td><strong>${r.name}</strong></td>
        <td style="font-family:monospace;font-size:12px">${r.entity_type}</td>
        <td>${recTypeBadge(r.type)}</td>
        <td style="font-size:12px;color:var(--muted)">${detail}</td>
        <td><button onclick="deleteRecognizer('${r.id}')" class="btn btn-danger btn-sm">Delete</button></td>
      </tr>`;
    }).join('') + `</tbody></table>`;
}

function onRecTypeChange() {
  const type = document.getElementById('rec-type').value;
  document.getElementById('rec-pattern-fields').style.display = type === 'pattern' ? '' : 'none';
  document.getElementById('rec-denylist-fields').style.display = type === 'deny_list' ? '' : 'none';
}

async function addRecognizer() {
  const name = document.getElementById('rec-name').value.trim();
  const entity_type = document.getElementById('rec-entity-type').value.trim().toUpperCase().replace(/\s+/g, '_');
  const type = document.getElementById('rec-type').value;
  if (!name || !entity_type) { showToast('Name and entity type are required', 'error'); return; }

  const description = document.getElementById('rec-description').value.trim();
  let payload = { name, entity_type, type, ...(description ? { description } : {}) };
  if (type === 'pattern') {
    const regex = document.getElementById('rec-regex').value.trim();
    if (!regex) { showToast('Regex pattern is required', 'error'); return; }
    const score = parseFloat(document.getElementById('rec-score').value) || 0.5;
    const context = document.getElementById('rec-context').value.split(',').map(s => s.trim()).filter(Boolean);
    payload.patterns = [{ name: 'pattern', regex, score }];
    payload.context = context;
  } else {
    const raw = document.getElementById('rec-deny-list').value;
    const deny_list = raw.split('\n').map(s => s.trim()).filter(Boolean);
    if (!deny_list.length) { showToast('Deny list cannot be empty', 'error'); return; }
    const context = document.getElementById('rec-deny-context').value.split(',').map(s => s.trim()).filter(Boolean);
    payload.deny_list = deny_list;
    payload.context = context;
  }

  try {
    await api.post('/config/recognizers', payload);
    showToast('Recognizer added', 'success');
    document.getElementById('rec-name').value = '';
    document.getElementById('rec-entity-type').value = '';
    document.getElementById('rec-regex').value = '';
    document.getElementById('rec-deny-list').value = '';
    document.getElementById('rec-context').value = '';
    document.getElementById('rec-description').value = '';
    loadEntities();
  } catch (e) { console.error(e); }
}

async function deleteRecognizer(id) {
  if (!confirm('Delete this recognizer?')) return;
  try {
    await api.delete(`/config/recognizers/${id}`);
    showToast('Recognizer deleted', 'success');
    loadEntities();
  } catch (e) { console.error(e); }
}

// ── Watched Folders ───────────────────────────────────────────────────────────

async function loadWatchedFolders() {
  try {
    const [folders, profiles] = await Promise.all([
      api.get('/config/watched-folders'),
      api.get('/config/profiles'),
    ]);
    renderWatchedFolders(folders);
    populateFolderProfileDropdown(profiles);
  } catch (e) { console.error(e); }
}

function populateFolderProfileDropdown(profiles) {
  const sel = document.getElementById('wf-profile');
  if (!sel) return;
  const current = sel.value;
  const entries = Object.entries(profiles);
  const builtins = entries.filter(([, p]) => p.builtin);
  const custom = entries.filter(([, p]) => !p.builtin);
  let html = '<option value="">— System Default —</option>';
  if (builtins.length) {
    html += '<optgroup label="Built-in Levels">' +
      builtins.map(([name]) =>
        `<option value="${name}"${name === current ? ' selected' : ''}>${name.charAt(0).toUpperCase() + name.slice(1)}</option>`
      ).join('') + '</optgroup>';
  }
  if (custom.length) {
    html += '<optgroup label="Custom Profiles">' +
      custom.map(([name]) => `<option value="${name}"${name === current ? ' selected' : ''}>${name}</option>`).join('') +
      '</optgroup>';
  }
  sel.innerHTML = html;
}

function renderWatchedFolders(folders) {
  const el = document.getElementById('watched-folders-list');
  if (!folders.length) {
    el.innerHTML = `<div class="empty-state"><div class="empty-state-icon">📁</div><h3>No watched folders</h3><p>Add a folder to start auto-polling.</p></div>`;
    return;
  }
  el.innerHTML = `<table><thead><tr><th>Name</th><th>Input Path</th><th>Output Path</th><th>Profile</th><th>Status</th><th>Actions</th></tr></thead><tbody>` +
    folders.map(f => `
      <tr>
        <td><strong>${f.name}</strong></td>
        <td style="font-family:monospace;font-size:12px">${f.path}</td>
        <td style="font-family:monospace;font-size:12px;color:var(--muted)">${f.output_path || '— default —'}</td>
        <td>${f.profile ? `<span class="badge badge-api">${f.profile}</span>` : '<span style="color:var(--muted)">—</span>'}</td>
        <td><span class="badge ${f.enabled ? 'badge-completed' : 'badge-cancelled'}">${f.enabled ? 'enabled' : 'disabled'}</span></td>
        <td>
          <div style="display:flex;gap:6px">
            <button onclick="editWatchedFolder('${f.id}')" class="btn btn-ghost btn-sm">Edit</button>
            ${f.id !== 'default' ? `<button onclick="toggleWatchedFolder('${f.id}',${!f.enabled})" class="btn btn-ghost btn-sm">${f.enabled ? 'Disable' : 'Enable'}</button>` : ''}
            ${f.id !== 'default' ? `<button onclick="deleteWatchedFolder('${f.id}')" class="btn btn-danger btn-sm">Delete</button>` : ''}
          </div>
        </td>
      </tr>`).join('') + `</tbody></table>`;
}

async function addWatchedFolder() {
  const name = document.getElementById('wf-name').value.trim();
  const path = document.getElementById('wf-path').value.trim();
  const outputPath = document.getElementById('wf-output-path').value.trim();
  const profile = document.getElementById('wf-profile').value;
  if (!name) { showToast('Name is required', 'error'); return; }
  if (!path) { showToast('Input path is required', 'error'); return; }
  try {
    await api.post('/config/watched-folders', { name, path, output_path: outputPath, profile: profile || null, enabled: true });
    showToast('Folder added', 'success');
    document.getElementById('wf-name').value = '';
    document.getElementById('wf-path').value = '';
    document.getElementById('wf-output-path').value = '';
    document.getElementById('wf-profile').value = '';
    loadWatchedFolders();
  } catch (e) { console.error(e); }
}

async function toggleWatchedFolder(id, enabled) {
  try {
    const folders = await api.get('/config/watched-folders');
    const f = folders.find(f => f.id === id);
    if (!f) return;
    await api.put(`/config/watched-folders/${id}`, { ...f, enabled });
    loadWatchedFolders();
  } catch (e) { console.error(e); }
}

async function deleteWatchedFolder(id) {
  if (!confirm('Delete this watched folder?')) return;
  try {
    await api.delete(`/config/watched-folders/${id}`);
    showToast('Folder removed', 'success');
    loadWatchedFolders();
  } catch (e) { console.error(e); }
}

let _editingFolderId = null;

async function editWatchedFolder(id) {
  try {
    const [folders, profiles] = await Promise.all([
      api.get('/config/watched-folders'),
      api.get('/config/profiles'),
    ]);
    const f = folders.find(f => f.id === id);
    if (!f) return;
    _editingFolderId = id;
    document.getElementById('wf-edit-name').value = f.name || '';
    document.getElementById('wf-edit-path').value = f.path || '';
    document.getElementById('wf-edit-output-path').value = f.output_path || '';
    document.getElementById('wf-edit-enabled').checked = f.enabled !== false;

    // Populate profile dropdown (same grouping as add form)
    const sel = document.getElementById('wf-edit-profile');
    sel.innerHTML = '<option value="">— Default —</option>';
    const builtins = [], custom = [];
    Object.entries(profiles).forEach(([name, p]) => {
      (p.builtin ? builtins : custom).push(name);
    });
    if (builtins.length) {
      const g = document.createElement('optgroup'); g.label = 'Built-in levels';
      builtins.forEach(n => { const o = document.createElement('option'); o.value = n; o.textContent = n; g.appendChild(o); });
      sel.appendChild(g);
    }
    if (custom.length) {
      const g = document.createElement('optgroup'); g.label = 'Custom profiles';
      custom.forEach(n => { const o = document.createElement('option'); o.value = n; o.textContent = n; g.appendChild(o); });
      sel.appendChild(g);
    }
    sel.value = f.profile || '';

    document.getElementById('folder-modal').classList.add('open');
  } catch (e) { console.error(e); }
}

function closeFolderModal() {
  document.getElementById('folder-modal').classList.remove('open');
  _editingFolderId = null;
}

async function saveFolderEdit() {
  const name = document.getElementById('wf-edit-name').value.trim();
  const path = document.getElementById('wf-edit-path').value.trim();
  if (!name) { showToast('Name is required', 'error'); return; }
  if (!path) { showToast('Input path is required', 'error'); return; }
  const payload = {
    name,
    path,
    output_path: document.getElementById('wf-edit-output-path').value.trim(),
    profile: document.getElementById('wf-edit-profile').value || null,
    enabled: document.getElementById('wf-edit-enabled').checked,
  };
  try {
    await api.put(`/config/watched-folders/${_editingFolderId}`, payload);
    showToast('Folder updated', 'success');
    closeFolderModal();
    loadWatchedFolders();
  } catch (e) { showToast('Save failed', 'error'); console.error(e); }
}

async function addWebhook() {
  const url = document.getElementById('wh-url').value.trim();
  const name = document.getElementById('wh-name').value.trim();
  const secret = document.getElementById('wh-secret').value.trim();
  if (!url) { showToast('URL is required', 'error'); return; }
  try {
    await api.post('/config/webhooks', { url, name, secret, enabled: true });
    showToast('Webhook added', 'success');
    document.getElementById('wh-url').value = '';
    document.getElementById('wh-name').value = '';
    document.getElementById('wh-secret').value = '';
    loadWebhooks();
  } catch (e) { console.error(e); }
}

async function testWebhook(id) {
  try {
    const res = await api.post(`/config/webhooks/${id}/test`, {});
    showToast(res.success ? 'Test webhook sent successfully' : 'Webhook test failed', res.success ? 'success' : 'error');
  } catch (e) { console.error(e); }
}

async function deleteWebhook(id) {
  if (!confirm('Delete this webhook?')) return;
  try {
    await api.delete(`/config/webhooks/${id}`);
    showToast('Webhook deleted', 'success');
    loadWebhooks();
  } catch (e) { console.error(e); }
}

// ── Templates ─────────────────────────────────────────────

const SENSITIVE_HEADER_PATTERNS = ['authorization', 'token', 'key', 'secret', 'password', 'credential', 'auth'];

function isSensitiveHeader(name) {
  const lower = name.toLowerCase();
  return SENSITIVE_HEADER_PATTERNS.some(p => lower.includes(p));
}

function addHeaderRow(name, value) {
  const isRedacted = value === '__redacted__';
  const isSensitive = isSensitiveHeader(name);

  const editor = document.getElementById('tmpl-headers-editor');
  const row = document.createElement('div');
  row.className = 'header-row';
  row.style.cssText = 'display:flex;gap:6px;align-items:center';

  const nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.className = 'header-name';
  nameInput.placeholder = 'Header name';
  nameInput.value = name;
  nameInput.style.flex = '1';

  const valueInput = document.createElement('input');
  valueInput.className = 'header-value';
  valueInput.style.flex = '2';
  valueInput.type = (isSensitive || isRedacted) ? 'password' : 'text';

  if (isRedacted) {
    valueInput.value = '';
    valueInput.placeholder = 'Saved — type to replace';
  } else {
    valueInput.value = value;
    valueInput.placeholder = 'Value';
  }

  nameInput.addEventListener('input', () => {
    if (valueInput.value !== '') {
      valueInput.type = isSensitiveHeader(nameInput.value) ? 'password' : 'text';
    }
  });

  const toggleBtn = document.createElement('button');
  toggleBtn.type = 'button';
  toggleBtn.className = 'btn btn-ghost btn-sm';
  toggleBtn.title = 'Show/hide';
  toggleBtn.textContent = '👁';
  toggleBtn.onclick = () => {
    valueInput.type = valueInput.type === 'password' ? 'text' : 'password';
  };
  if (isRedacted) toggleBtn.style.display = 'none';

  const removeBtn = document.createElement('button');
  removeBtn.type = 'button';
  removeBtn.className = 'btn btn-ghost btn-sm';
  removeBtn.textContent = '✕';
  removeBtn.onclick = () => row.remove();

  row.append(nameInput, valueInput, toggleBtn, removeBtn);
  editor.appendChild(row);
}

function renderHeaderEditor(headers) {
  document.getElementById('tmpl-headers-editor').innerHTML = '';
  Object.entries(headers).forEach(([k, v]) => addHeaderRow(k, v));
}

function readHeadersFromEditor() {
  const rows = document.querySelectorAll('#tmpl-headers-editor .header-row');
  const result = {};
  rows.forEach(row => {
    const name = row.querySelector('.header-name').value.trim();
    const value = row.querySelector('.header-value').value;
    if (name) result[name] = value;
  });
  return Object.keys(result).length ? result : null;
}

let editingTemplate = null;

async function loadTemplates() {
  try {
    const templates = await api.get('/config/templates');
    renderTemplates(templates);
  } catch (e) { console.error(e); }
}

function renderTemplates(templates) {
  const el = document.getElementById('templates-list');
  if (!templates.length) {
    el.innerHTML = `<div class="empty-state"><div class="empty-state-icon">📝</div><h3>No templates</h3><p>Create a Jinja2 template to customise the webhook payload for any job.</p></div>`;
    return;
  }
  el.innerHTML = `<table><thead><tr><th>Name</th><th>Description</th><th>Actions</th></tr></thead><tbody>` +
    templates.map(t => `
      <tr>
        <td><strong>${t.name}</strong></td>
        <td style="color:var(--muted)">${t.description || '—'}</td>
        <td>
          <div style="display:flex;gap:6px">
            <button onclick="editTemplate('${t.name}')" class="btn btn-ghost btn-sm">Edit</button>
            <button onclick="duplicateTemplate('${t.name}')" class="btn btn-ghost btn-sm">Duplicate</button>
            <button onclick="deleteTemplate('${t.name}')" class="btn btn-danger btn-sm">Delete</button>
          </div>
        </td>
      </tr>`).join('') + `</tbody></table>`;
}

function openNewTemplate() {
  editingTemplate = null;
  document.getElementById('template-modal-title').textContent = 'New Template';
  document.getElementById('tmpl-name').value = '';
  document.getElementById('tmpl-name').disabled = false;
  document.getElementById('tmpl-desc').value = '';
  document.getElementById('tmpl-headers-editor').innerHTML = '';
  document.getElementById('tmpl-pre-fetch-method').value = 'GET';
  document.getElementById('tmpl-pre-fetch-url').value = '';
  document.getElementById('tmpl-pre-fetch-body').value = '';
  document.getElementById('tmpl-pre-fetch-body-group').style.display = 'none';
  document.getElementById('tmpl-body').value = '';
  document.getElementById('template-modal').classList.add('open');
}

async function editTemplate(name) {
  try {
    const templates = await api.get('/config/templates');
    const t = templates.find(t => t.name === name);
    if (!t) return;
    editingTemplate = name;
    document.getElementById('template-modal-title').textContent = 'Edit Template';
    document.getElementById('tmpl-name').value = name;
    document.getElementById('tmpl-name').disabled = false;
    document.getElementById('tmpl-desc').value = t.description || '';
    renderHeaderEditor(t.headers && Object.keys(t.headers).length ? t.headers : {});
    document.getElementById('tmpl-pre-fetch-method').value = t.pre_fetch_method || 'GET';
    document.getElementById('tmpl-pre-fetch-url').value = t.pre_fetch_url || '';
    document.getElementById('tmpl-pre-fetch-body').value = t.pre_fetch_body || '';
    document.getElementById('tmpl-pre-fetch-body-group').style.display = (t.pre_fetch_method === 'POST') ? '' : 'none';
    document.getElementById('tmpl-body').value = t.body || '';
    document.getElementById('template-modal').classList.add('open');
  } catch (e) { console.error(e); }
}

function closeTemplateModal() {
  document.getElementById('template-modal').classList.remove('open');
  editingTemplate = null;
}

function onPreFetchMethodChange() {
  const method = document.getElementById('tmpl-pre-fetch-method').value;
  document.getElementById('tmpl-pre-fetch-body-group').style.display = method === 'POST' ? '' : 'none';
}

async function saveTemplate() {
  const name = document.getElementById('tmpl-name').value.trim();
  const desc = document.getElementById('tmpl-desc').value.trim();
  const body = document.getElementById('tmpl-body').value.trim();
  if (!name) { showToast('Template name is required', 'error'); return; }
  if (!body) { showToast('Template body is required', 'error'); return; }

  const headers = readHeadersFromEditor();
  const pre_fetch_url = document.getElementById('tmpl-pre-fetch-url').value.trim();
  const pre_fetch_method = document.getElementById('tmpl-pre-fetch-method').value;
  const pre_fetch_body = document.getElementById('tmpl-pre-fetch-body').value.trim();

  const payload = { name, description: desc, body, headers, pre_fetch_url, pre_fetch_method, pre_fetch_body };
  try {
    if (editingTemplate) {
      await api.put(`/config/templates/${editingTemplate}`, payload);
      showToast('Template updated', 'success');
    } else {
      await api.post('/config/templates', payload);
      showToast('Template created', 'success');
    }
    closeTemplateModal();
    loadTemplates();
  } catch (e) { console.error(e); }
}

async function duplicateTemplate(name) {
  try {
    const res = await api.post(`/config/templates/${name}/duplicate`, {});
    showToast(`Duplicated as "${res.name}"`, 'success');
    loadTemplates();
  } catch (e) { console.error(e); }
}

async function restoreDefaultTemplates() {
  if (!confirm('Restore all built-in default templates? Your custom templates will not be affected.')) return;
  try {
    await api.post('/config/templates/_restore_defaults', {});
    showToast('Default templates restored', 'success');
    loadTemplates();
  } catch (e) { console.error(e); }
}

async function deleteTemplate(name) {
  if (!confirm(`Delete template "${name}"?`)) return;
  try {
    await api.delete(`/config/templates/${name}`);
    showToast('Template deleted', 'success');
    loadTemplates();
  } catch (e) { console.error(e); }
}

document.addEventListener('DOMContentLoaded', () => {
  loadConfig();
  loadProfiles();
  loadWatchedFolders();
  loadWebhooks();
  loadTemplates();
  loadEntities();
  switchTab('system');
});
