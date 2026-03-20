let selectedFiles = [];
let availableEntities = [];

async function loadEntities() {
  try {
    const entities = await api.get('/config/entities');
    availableEntities = entities;
    renderEntityGrid();
  } catch (e) { console.error(e); }
}

function renderEntityGrid() {
  const grid = document.getElementById('entity-grid');
  if (!grid) return;
  grid.innerHTML = availableEntities.map(e => `
    <label class="entity-checkbox" id="ec-${e.type}">
      <input type="checkbox" name="entity" value="${e.type}" onchange="updateEntityCheckbox('${e.type}')">
      <div>
        <div style="font-weight:600">${e.type}</div>
        <div style="color:var(--muted);font-size:11px">${e.description}</div>
      </div>
    </label>
  `).join('');
}

function updateEntityCheckbox(type) {
  const label = document.getElementById(`ec-${type}`);
  const cb = label.querySelector('input');
  label.classList.toggle('checked', cb.checked);
}

function selectAllEntities(sel) {
  document.querySelectorAll('#entity-grid input[type=checkbox]').forEach(cb => {
    cb.checked = sel;
    const label = cb.closest('.entity-checkbox');
    if (label) label.classList.toggle('checked', sel);
  });
}

function onLevelChange() {
  const level = document.querySelector('input[name=level]:checked')?.value;
  const customPanel = document.getElementById('custom-entities-panel');
  const profilePanel = document.getElementById('profile-level-panel');
  if (customPanel) customPanel.style.display = level === 'custom' ? 'block' : 'none';
  if (profilePanel) profilePanel.style.display = level === 'profile' ? 'block' : 'none';

  // Update radio option styling
  document.querySelectorAll('.radio-option').forEach(opt => {
    const input = opt.querySelector('input[name=level]');
    opt.classList.toggle('selected', input && input.checked);
  });
}

function onOutputModeChange() {
  const mode = document.querySelector('input[name=output_mode]:checked')?.value;
  const webhookPanel = document.getElementById('webhook-panel');
  if (webhookPanel) webhookPanel.style.display = mode === 'webhook' ? 'block' : 'none';

  document.querySelectorAll('.radio-option[data-mode]').forEach(opt => {
    const input = opt.querySelector('input[name=output_mode]');
    opt.classList.toggle('selected', input && input.checked);
  });
}

function setupDropZone() {
  const zone = document.getElementById('drop-zone');
  const fileInput = document.getElementById('file-input');
  if (!zone) return;

  zone.addEventListener('click', () => fileInput.click());
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('dragover');
    addFiles(Array.from(e.dataTransfer.files));
  });

  fileInput.addEventListener('change', () => {
    addFiles(Array.from(fileInput.files));
    fileInput.value = '';
  });
}

function addFiles(files) {
  const allowed = ['pdf', 'png', 'jpg', 'jpeg', 'tiff', 'tif', 'bmp'];
  files.forEach(f => {
    const ext = f.name.split('.').pop().toLowerCase();
    if (!allowed.includes(ext)) {
      showToast(`Unsupported file type: ${f.name}`, 'error');
      return;
    }
    if (!selectedFiles.find(sf => sf.name === f.name && sf.size === f.size)) {
      selectedFiles.push(f);
    }
  });
  renderFileList();
}

function removeFile(index) {
  selectedFiles.splice(index, 1);
  renderFileList();
}

function renderFileList() {
  const list = document.getElementById('file-list');
  const submitBtn = document.getElementById('submit-btn');
  if (!list) return;

  if (!selectedFiles.length) {
    list.innerHTML = '';
    if (submitBtn) submitBtn.disabled = true;
    return;
  }

  if (submitBtn) submitBtn.disabled = false;

  list.innerHTML = selectedFiles.map((f, i) => `
    <div class="file-item">
      <div class="file-info">
        <span class="file-icon">${f.name.endsWith('.pdf') ? '📄' : '🖼'}</span>
        <div>
          <div class="file-name">${f.name}</div>
          <div class="file-size">${formatBytes(f.size)}</div>
        </div>
      </div>
      <button onclick="removeFile(${i})" class="btn btn-ghost btn-sm">✕</button>
    </div>
  `).join('');
}

async function loadProfiles() {
  try {
    const profiles = await api.get('/config/profiles');
    const options = Object.entries(profiles).map(([name, p]) =>
      `<option value="${name}">${name} (${p.entities.length} entities)</option>`
    ).join('');

    const sel = document.getElementById('profile-select');
    if (sel) sel.innerHTML = '<option value="">None (use level settings)</option>' + options;

    const levelSel = document.getElementById('profile-level-select');
    if (levelSel) levelSel.innerHTML = '<option value="">— Select a profile —</option>' + options;
  } catch (e) { console.error(e); }
}

async function loadWebhookTemplates() {
  try {
    const templates = await api.get('/config/templates');
    const sel = document.getElementById('webhook-template');
    if (!sel) return;
    sel.innerHTML = '<option value="">— Standard payload —</option>' +
      templates.map(t =>
        `<option value="${t.name}">${t.name}${t.description ? ' — ' + t.description : ''}</option>`
      ).join('');
  } catch (e) { console.error(e); }
}

async function submitFiles() {
  if (!selectedFiles.length) return;

  let level = document.querySelector('input[name=level]:checked')?.value || 'standard';
  const outputMode = document.querySelector('input[name=output_mode]:checked')?.value || 'directory';
  const webhookUrl = document.getElementById('webhook-url')?.value || '';
  const webhookTemplate = document.getElementById('webhook-template')?.value || '';
  const validationMode = document.getElementById('validation-mode')?.checked || false;
  const autoApplyIfClean = document.getElementById('auto-apply-if-clean')?.checked || false;

  // Profile level: send profile_name, backend sets level=custom
  let profileName = document.getElementById('profile-select')?.value || '';
  if (level === 'profile') {
    profileName = document.getElementById('profile-level-select')?.value || '';
    if (!profileName) {
      showToast('Select a profile', 'error');
      return;
    }
    level = 'custom';
  }

  let customEntities = null;
  if (level === 'custom' && !profileName) {
    customEntities = Array.from(document.querySelectorAll('#entity-grid input:checked')).map(cb => cb.value);
    if (!customEntities.length) {
      showToast('Select at least one entity type for custom redaction', 'error');
      return;
    }
  }

  const submitBtn = document.getElementById('submit-btn');
  const progressWrap = document.getElementById('progress-wrap');
  const progressBar = document.getElementById('progress-bar');
  const resultEl = document.getElementById('upload-result');

  submitBtn.disabled = true;
  submitBtn.textContent = validationMode ? 'Detecting regions…' : 'Submitting…';
  if (resultEl) resultEl.innerHTML = '';

  const results = [];
  for (let i = 0; i < selectedFiles.length; i++) {
    const file = selectedFiles[i];

    if (progressWrap) progressWrap.style.display = 'block';

    const formData = new FormData();
    formData.append('file', file);
    formData.append('level', level);
    formData.append('output_mode', outputMode);
    if (webhookUrl) formData.append('webhook_url', webhookUrl);
    if (webhookTemplate) formData.append('webhook_template', webhookTemplate);
    if (profileName) formData.append('profile_name', profileName);
    if (customEntities) formData.append('custom_entities', JSON.stringify(customEntities));
    if (validationMode) formData.append('validation_mode', 'true');
    if (validationMode && autoApplyIfClean) formData.append('auto_apply_if_clean', 'true');

    // Validation mode uses upload-sync (blocks during detection); standard uses upload (queued)
    const endpoint = validationMode ? '/api/v1/jobs/upload-sync' : '/api/v1/jobs/upload';

    try {
      await new Promise((resolve) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', endpoint);

        xhr.upload.onprogress = e => {
          if (e.lengthComputable && progressBar) {
            const pct = Math.round((e.loaded / e.total) * 100);
            progressBar.style.width = pct + '%';
          }
        };

        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            const data = JSON.parse(xhr.responseText);
            results.push({
              success: true,
              file: file.name,
              jobId: data.job_id,
              validationUrl: (data.status === 'pending_validation' && data.job_id) ? `/validate.html?id=${data.job_id}` : null,
              regionCount: data.region_count,
              bypassed: data.bypassed_validation || false,
            });
          } else {
            let detail = 'Upload failed';
            try { detail = JSON.parse(xhr.responseText).detail; } catch (_) {}
            results.push({ success: false, file: file.name, error: detail });
          }
          resolve();
        };
        xhr.onerror = () => { results.push({ success: false, file: file.name, error: 'Network error' }); resolve(); };
        xhr.send(formData);
      });
    } catch (e) {
      results.push({ success: false, file: file.name, error: e.message });
    }
  }

  if (progressBar) progressBar.style.width = '100%';

  const succeeded = results.filter(r => r.success);
  const failed = results.filter(r => !r.success);

  if (resultEl) {
    resultEl.className = `upload-result ${failed.length ? 'error' : 'success'}`;
    const queuedLabel = validationMode ? 'ready for review' : 'queued successfully';
    resultEl.innerHTML = `
      <strong>${succeeded.length} of ${results.length} file(s) ${queuedLabel}.</strong>
      ${succeeded.map(r => `<div style="margin-top:8px">
        ✓ ${r.file}
        ${r.bypassed
          ? ` — all ${r.regionCount} region(s) auto-approved, redaction applied —
              <a href="job_detail.html?id=${r.jobId}" style="color:var(--accent)">View job →</a>`
          : r.validationUrl
            ? ` — <strong>${r.regionCount} region(s) detected</strong> —
                <a href="${r.validationUrl}" style="color:var(--accent)">Review &amp; approve →</a>`
            : ` — <a href="job_detail.html?id=${r.jobId}" style="color:var(--accent)">View job →</a>`
        }
      </div>`).join('')}
      ${failed.map(r => `<div style="margin-top:8px;color:var(--danger)">✗ ${r.file}: ${r.error}</div>`).join('')}
      ${succeeded.length && !validationMode ? '<div style="margin-top:12px"><a href="index.html" class="btn btn-primary">View all jobs →</a></div>' : ''}
    `;
  }

  selectedFiles = [];
  renderFileList();
  submitBtn.disabled = false;
  submitBtn.textContent = 'Submit for Redaction';
}

document.addEventListener('DOMContentLoaded', () => {
  setupDropZone();
  loadEntities();
  loadProfiles();
  loadWebhookTemplates();
  onLevelChange();
  onOutputModeChange();
});
