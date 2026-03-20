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
}

// ── Profiles ─────────────────────────────────────────────

async function loadProfiles() {
  try {
    const profiles = await api.get('/config/profiles');
    renderProfiles(profiles);
  } catch (e) { console.error(e); }
}

function renderProfiles(profiles) {
  const el = document.getElementById('profiles-list');
  const entries = Object.entries(profiles);
  if (!entries.length) {
    el.innerHTML = `<div class="empty-state"><div class="empty-state-icon">📋</div><h3>No profiles</h3><p>Create a custom redaction profile to save entity sets for reuse.</p></div>`;
    return;
  }
  el.innerHTML = `<table><thead><tr><th>Name</th><th>Entities</th><th>Description</th><th>Actions</th></tr></thead><tbody>` +
    entries.map(([name, p]) => `
      <tr>
        <td><strong>${name}</strong></td>
        <td>${p.entities.length} types</td>
        <td style="color:var(--muted)">${p.description || '—'}</td>
        <td>
          <div style="display:flex;gap:6px">
            <button onclick="editProfile('${name}')" class="btn btn-ghost btn-sm">Edit</button>
            <button onclick="deleteProfile('${name}')" class="btn btn-danger btn-sm">Delete</button>
          </div>
        </td>
      </tr>`).join('') + `</tbody></table>`;
}

async function loadEntitiesForModal() {
  if (allEntities.length) return;
  try {
    allEntities = await api.get('/config/entities');
  } catch (e) { console.error(e); }
}

function openProfileModal(name = null, existing = null) {
  editingProfile = name;
  document.getElementById('modal-profile-name').value = name || '';
  document.getElementById('modal-profile-desc').value = existing?.description || '';
  document.getElementById('modal-profile-name').disabled = !!name;

  const grid = document.getElementById('modal-entity-grid');
  grid.innerHTML = allEntities.map(e => {
    const checked = existing?.entities?.includes(e.type) || false;
    return `
      <label class="entity-checkbox ${checked ? 'checked' : ''}" id="mec-${e.type}">
        <input type="checkbox" value="${e.type}" ${checked ? 'checked' : ''} onchange="updateModalCheckbox('${e.type}')">
        <div>
          <div style="font-weight:600;font-size:11px">${e.type}</div>
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

  const payload = { name, entities, description: desc };
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

async function deleteProfile(name) {
  if (!confirm(`Delete profile "${name}"?`)) return;
  try {
    await api.delete(`/config/profiles/${name}`);
    showToast('Profile deleted', 'success');
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
    document.getElementById('tmpl-name').disabled = true;
    document.getElementById('tmpl-desc').value = t.description || '';
    renderHeaderEditor(t.headers && Object.keys(t.headers).length ? t.headers : {});
    document.getElementById('tmpl-body').value = t.body || '';
    document.getElementById('template-modal').classList.add('open');
  } catch (e) { console.error(e); }
}

function closeTemplateModal() {
  document.getElementById('template-modal').classList.remove('open');
  editingTemplate = null;
}

async function saveTemplate() {
  const name = document.getElementById('tmpl-name').value.trim();
  const desc = document.getElementById('tmpl-desc').value.trim();
  const body = document.getElementById('tmpl-body').value.trim();
  if (!name) { showToast('Template name is required', 'error'); return; }
  if (!body) { showToast('Template body is required', 'error'); return; }

  const headers = readHeadersFromEditor();

  const payload = { name, description: desc, body, headers };
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
  loadWebhooks();
  loadTemplates();
  switchTab('system');
});
