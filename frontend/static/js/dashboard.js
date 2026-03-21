let currentPage = 1;
let currentStatus = '';
let autoRefresh = true;
let refreshInterval = null;

async function loadStats() {
  try {
    const stats = await api.get('/stats');
    document.getElementById('stat-today').textContent              = stats.jobs_today;
    document.getElementById('stat-queued').textContent             = stats.jobs_queued + stats.jobs_processing;
    document.getElementById('stat-pending-validation').textContent = stats.jobs_pending_validation;
    document.getElementById('stat-completed').textContent          = stats.jobs_completed;
    document.getElementById('stat-entities').textContent           = stats.total_entities_found.toLocaleString();
  } catch (e) { console.error(e); }
}

async function loadJobs() {
  const params = new URLSearchParams({ page: currentPage, per_page: 20 });
  if (currentStatus) params.set('status', currentStatus);

  try {
    const data = await api.get(`/jobs?${params}`);
    renderTable(data.jobs);
    renderPagination(data.total, data.page, data.per_page);
  } catch (e) { console.error(e); }
}

function renderTable(jobs) {
  const tbody = document.getElementById('jobs-tbody');
  if (!jobs.length) {
    tbody.innerHTML = `<tr><td colspan="7">
      <div class="empty-state">
        <div class="empty-state-icon">📄</div>
        <h3>No jobs yet</h3>
        <p>Upload a document or drop files into the input folder to get started.</p>
        <a href="upload.html" class="btn btn-primary">Upload Document</a>
      </div>
    </td></tr>`;
    return;
  }

  tbody.innerHTML = jobs.map(j => `
    <tr>
      <td>
        <div style="font-weight:500;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${j.filename}">${j.filename}</div>
        <div style="font-size:11px;color:var(--muted)">${j.id.slice(0,8)}</div>
      </td>
      <td>${statusBadge(j.status)}</td>
      <td>${levelBadge(j.level)}</td>
      <td><span class="badge badge-${j.source}">${j.source}</span></td>
      <td>${j.entities_found ? Object.values(j.entities_found).reduce((a,b)=>a+b,0) : '—'}</td>
      <td>${relativeTime(j.created_at)}</td>
      <td>
        <div style="display:flex;gap:6px;align-items:center">
          <a href="job_detail.html?id=${j.id}" class="btn btn-ghost btn-sm">View</a>
          ${j.status === 'pending_validation' ? `<a href="validate.html?id=${j.id}" class="btn btn-primary btn-sm">Review</a>` : ''}
          ${j.status === 'completed' ? `<a href="/api/v1/jobs/${j.id}/view" target="_blank" class="btn btn-ghost btn-sm" title="View in browser">👁</a>` : ''}
          ${j.status === 'completed' ? `<a href="/api/v1/jobs/${j.id}/download" class="btn btn-ghost btn-sm" title="Download">⬇</a>` : ''}
          <button onclick="deleteJob('${j.id}')" class="btn btn-danger btn-sm">✕</button>
        </div>
      </td>
    </tr>
  `).join('');
}

function renderPagination(total, page, perPage) {
  const totalPages = Math.ceil(total / perPage);
  const el = document.getElementById('pagination');
  if (totalPages <= 1) { el.innerHTML = ''; return; }

  let html = `<span class="page-info">Page ${page} of ${totalPages}</span>`;
  if (page > 1) html += `<button class="page-btn" onclick="changePage(${page-1})">‹</button>`;

  const start = Math.max(1, page - 2);
  const end = Math.min(totalPages, page + 2);
  for (let i = start; i <= end; i++) {
    html += `<button class="page-btn ${i===page?'active':''}" onclick="changePage(${i})">${i}</button>`;
  }
  if (page < totalPages) html += `<button class="page-btn" onclick="changePage(${page+1})">›</button>`;

  el.innerHTML = html;
}

function changePage(p) {
  currentPage = p;
  loadJobs();
}

function setFilter(status) {
  currentStatus = status;
  currentPage = 1;
  document.querySelectorAll('.filter-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.status === status);
  });
  loadJobs();
}

async function clearHistory() {
  if (!confirm('Delete all completed, failed, and cancelled jobs and their files? This cannot be undone.')) return;
  try {
    const res = await api.delete('/jobs');
    showToast(`Cleared ${res.count} job(s)`, 'success');
    loadJobs();
    loadStats();
  } catch (e) { console.error(e); }
}

async function deleteJob(id) {
  if (!confirm('Delete this job and its files?')) return;
  try {
    await api.delete(`/jobs/${id}`);
    showToast('Job deleted', 'success');
    loadJobs();
    loadStats();
  } catch (e) { console.error(e); }
}

function toggleAutoRefresh() {
  autoRefresh = !autoRefresh;
  const btn = document.getElementById('refresh-btn');
  btn.textContent = autoRefresh ? '⏸ Pause' : '▶ Resume';
  if (autoRefresh) startAutoRefresh();
  else stopAutoRefresh();
}

function startAutoRefresh() {
  if (refreshInterval) return;
  refreshInterval = setInterval(() => { loadJobs(); loadStats(); }, 5000);
}

function stopAutoRefresh() {
  clearInterval(refreshInterval);
  refreshInterval = null;
}

document.addEventListener('DOMContentLoaded', () => {
  const initialStatus = new URLSearchParams(location.search).get('status') || '';
  if (initialStatus) setFilter(initialStatus);
  loadStats();
  if (!initialStatus) loadJobs();
  startAutoRefresh();
});
