const API_BASE = '/api/v1';

function showToast(message, type = 'info') {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    document.body.appendChild(container);
  }
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

async function apiFetch(path, options = {}) {
  try {
    const res = await fetch(`${API_BASE}${path}`, options);
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { const j = await res.json(); detail = j.detail || JSON.stringify(j); } catch {}
      throw new Error(detail);
    }
    const ct = res.headers.get('content-type') || '';
    if (ct.includes('application/json')) return res.json();
    return res;
  } catch (err) {
    showToast(err.message || 'Request failed', 'error');
    throw err;
  }
}

const api = {
  get:    (path)         => apiFetch(path),
  post:   (path, body)   => apiFetch(path, { method: 'POST',   headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  put:    (path, body)   => apiFetch(path, { method: 'PUT',    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  delete: (path)         => apiFetch(path, { method: 'DELETE' }),
};

function formatBytes(b) {
  if (!b) return '—';
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
  return (b / 1048576).toFixed(1) + ' MB';
}

function formatMs(ms) {
  if (!ms) return '—';
  if (ms < 1000) return ms + 'ms';
  return (ms / 1000).toFixed(1) + 's';
}

function formatDate(iso) {
  if (!iso) return '—';
  const normalized = /Z|[+-]\d{2}:\d{2}$/.test(iso) ? iso : iso + 'Z';
  return new Date(normalized).toLocaleString();
}

function relativeTime(iso) {
  if (!iso) return '—';
  const normalized = /Z|[+-]\d{2}:\d{2}$/.test(iso) ? iso : iso + 'Z';
  const diff = Date.now() - new Date(normalized).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60) return s + 's ago';
  const m = Math.floor(s / 60);
  if (m < 60) return m + 'm ago';
  const h = Math.floor(m / 60);
  if (h < 24) return h + 'h ago';
  return Math.floor(h / 24) + 'd ago';
}

function statusBadge(status) {
  return `<span class="badge badge-${status}">${status}</span>`;
}

function levelBadge(level) {
  return `<span class="badge badge-${level}">${level}</span>`;
}

// Mark active nav link based on current page
document.addEventListener('DOMContentLoaded', () => {
  const page = location.pathname.split('/').pop() || 'index.html';
  document.querySelectorAll('.sidebar-nav a').forEach(a => {
    const href = a.getAttribute('href') || '';
    if (href === page || (page === 'index.html' && href === '') || href.split('/').pop() === page) {
      a.classList.add('active');
    }
  });
});
