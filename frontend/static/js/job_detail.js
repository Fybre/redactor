const jobId = new URLSearchParams(location.search).get('id');
let pollTimer = null;

async function loadJob() {
  if (!jobId) { document.getElementById('job-content').innerHTML = '<p>No job ID specified.</p>'; return; }

  try {
    const job = await api.get(`/jobs/${jobId}`);
    renderJob(job);

    if (['queued', 'processing'].includes(job.status)) {
      pollTimer = setTimeout(loadJob, 3000);
    } else {
      clearTimeout(pollTimer);
    }
  } catch {
    document.getElementById('job-content').innerHTML = '<p style="color:var(--danger)">Failed to load job.</p>';
  }
}

async function loadReport() {
  try {
    const report = await api.get(`/jobs/${jobId}/report`);
    document.getElementById('report-json').textContent = JSON.stringify(report, null, 2);
  } catch {}
}

function renderJob(job) {
  document.title = `Job ${job.id.slice(0,8)} — Redactor`;
  document.getElementById('job-filename').textContent = job.filename;
  document.getElementById('job-id-display').textContent = job.id.slice(0,8) + '…';

  const content = document.getElementById('job-content');

  const entityRows = job.entities_found
    ? renderEntityBars(job.entities_found)
    : '<p style="color:var(--muted)">No entities found yet.</p>';

  const totalEntities = job.entities_found
    ? Object.values(job.entities_found).reduce((a, b) => a + b, 0)
    : 0;

  const actions = [];
  if (job.status === 'completed') {
    actions.push(`<a href="/api/v1/jobs/${job.id}/view" target="_blank" class="btn btn-primary">👁 View Redacted</a>`);
    actions.push(`<a href="/api/v1/jobs/${job.id}/download" class="btn btn-ghost">⬇ Download Redacted</a>`);
    if (job.original_path !== null) {
      actions.push(`<a href="/api/v1/jobs/${job.id}/original" class="btn btn-ghost">⬇ Download Original</a>`);
    }
  }
  if (['failed', 'cancelled'].includes(job.status)) {
    actions.push(`<button onclick="retryJob()" class="btn btn-ghost">↺ Retry</button>`);
  }
  actions.push(`<button onclick="deleteJob()" class="btn btn-danger">✕ Delete</button>`);

  content.innerHTML = `
    <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:20px">
      ${statusBadge(job.status)}
      ${levelBadge(job.level)}
      <span class="badge badge-${job.source}">${job.source}</span>
      ${actions.join('')}
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px">
      <div class="card">
        <div class="card-title">Job Details</div>
        <div class="detail-grid" style="grid-template-columns:1fr 1fr">
          ${detail('File', job.filename)}
          ${detail('Job ID', `<span style="font-family:monospace">${job.id}</span>`)}
          ${detail('Redaction Level', levelBadge(job.level))}
          ${detail('Output Mode', job.output_mode)}
          ${detail('Source', job.source)}
          ${detail('Pages', job.page_count || '—')}
          ${detail('Entities Found', totalEntities)}
          ${detail('Processing Time', formatMs(job.processing_ms))}
          ${detail('Created', formatDate(job.created_at))}
          ${detail('Completed', formatDate(job.completed_at))}
        </div>
        ${job.error_message ? `<div style="margin-top:12px;padding:10px;background:rgba(226,74,74,.1);border:1px solid #e24a4a;border-radius:6px;color:var(--danger);font-size:12px"><strong>Error:</strong> ${job.error_message}</div>` : ''}
      </div>

      <div class="card">
        <div class="card-title">Entities Detected</div>
        ${entityRows}
      </div>
    </div>

    <div class="card">
      <div class="card-title">Redaction Report</div>
      <pre id="report-json" style="font-size:12px;color:var(--muted);overflow-x:auto;padding:4px 0">Loading…</pre>
    </div>
  `;

  loadReport();
}

function detail(label, value) {
  return `<div class="detail-item"><label>${label}</label><div class="value">${value}</div></div>`;
}

function renderEntityBars(entities) {
  const entries = Object.entries(entities).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return '<p style="color:var(--muted)">None detected.</p>';
  const max = entries[0][1];
  return `<div class="entity-bars">` +
    entries.map(([type, count]) => `
      <div class="entity-row">
        <div class="entity-row-label">
          <span class="entity-row-name">${type}</span>
          <span class="entity-row-count">${count}</span>
        </div>
        <div class="entity-bar">
          <div class="entity-bar-fill" style="width:${Math.round(count/max*100)}%"></div>
        </div>
      </div>`).join('') + `</div>`;
}

async function retryJob() {
  try {
    await api.post(`/jobs/${jobId}/retry`, {});
    showToast('Job requeued', 'success');
    loadJob();
  } catch {}
}

async function deleteJob() {
  if (!confirm('Delete this job and its files?')) return;
  try {
    await api.delete(`/jobs/${jobId}`);
    showToast('Deleted', 'success');
    setTimeout(() => location.href = 'index.html', 800);
  } catch {}
}

document.addEventListener('DOMContentLoaded', loadJob);
