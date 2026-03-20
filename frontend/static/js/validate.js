/**
 * Validation UI for human-in-the-loop redaction review.
 *
 * Loads detected regions for a job, lets the user approve/reject/draw,
 * then POSTs to /apply to finalise redaction.
 */

const params = new URLSearchParams(location.search);
const JOB_ID = params.get('id');

let job = null;
let allRegions = [];     // {id, page, x0,y0,x1,y1, entity_type, original_text, score, source, status}
let currentPage = 0;
let pageCount = 1;

// Drawing state
let drawing = false;
let drawStart = null;   // {x, y} in normalised coords
let drawRect = null;    // the in-progress SVG rect element

// ── Bootstrap ─────────────────────────────────────────────────────────────────

async function init() {
  if (!JOB_ID) {
    document.getElementById('loading').textContent = 'No job ID provided.';
    return;
  }
  try {
    job = await api.get(`/jobs/${JOB_ID}`);
    if (!job) throw new Error('Job not found');
    pageCount = job.page_count || 1;

    const data = await api.get(`/jobs/${JOB_ID}/regions`);
    allRegions = data.regions;

    buildPageStrip();
    switchPage(0);        // also calls updatePageNav()
    document.getElementById('loading').style.display = 'none';
  } catch (e) {
    document.getElementById('loading').textContent = `Error: ${e.message}`;
  }
}

// ── Page strip ────────────────────────────────────────────────────────────────

function buildPageStrip() {
  const strip = document.getElementById('page-strip');
  strip.innerHTML = '';
  for (let i = 0; i < pageCount; i++) {
    const div = document.createElement('div');
    div.className = 'page-thumb' + (i === 0 ? ' active' : '');
    div.dataset.page = i;
    div.onclick = () => switchPage(i);

    const img = document.createElement('img');
    img.src = `/api/v1/jobs/${JOB_ID}/preview/${i}`;
    img.alt = `Page ${i + 1}`;

    const label = document.createElement('div');
    label.className = 'page-label';
    label.textContent = `Page ${i + 1}`;

    const count = document.createElement('div');
    count.className = 'badge-count';
    count.id = `thumb-count-${i}`;
    count.textContent = '';

    div.appendChild(img);
    div.appendChild(label);
    div.appendChild(count);
    strip.appendChild(div);
  }
  updateThumbCounts();
}

function updateThumbCounts() {
  for (let i = 0; i < pageCount; i++) {
    const el = document.getElementById(`thumb-count-${i}`);
    if (!el) continue;
    const n = allRegions.filter(r => r.page === i && isApproved(r)).length;
    el.textContent = n > 0 ? n : '';
  }
}

// ── Page navigation ───────────────────────────────────────────────────────────

function switchPage(page) {
  currentPage = page;
  document.querySelectorAll('.page-thumb').forEach(el => {
    el.classList.toggle('active', parseInt(el.dataset.page) === page);
  });

  const img = document.getElementById('page-img');
  img.onload = () => renderOverlay();
  img.src = `/api/v1/jobs/${JOB_ID}/preview/${page}`;

  // If already loaded (cached), render immediately
  if (img.complete && img.naturalWidth) renderOverlay();

  renderRegionList();
  updatePageNav();
}

function prevPage() {
  if (currentPage > 0) switchPage(currentPage - 1);
}

function nextPage() {
  if (currentPage < pageCount - 1) switchPage(currentPage + 1);
}

function updatePageNav() {
  const indicator = document.getElementById('page-nav-indicator');
  const btnPrev = document.getElementById('btn-prev-page');
  const btnNext = document.getElementById('btn-next-page');
  if (!indicator) return;
  indicator.textContent = `${currentPage + 1} / ${pageCount}`;
  if (btnPrev) btnPrev.disabled = currentPage === 0;
  if (btnNext) btnNext.disabled = currentPage >= pageCount - 1;
}

// ── SVG overlay ───────────────────────────────────────────────────────────────

function renderOverlay() {
  const svg = document.getElementById('overlay');
  // Keep only the drawing rect if present
  const existing = svg.querySelector('#draw-rect');
  svg.innerHTML = '';
  if (existing) svg.appendChild(existing);

  const pageRegions = allRegions.filter(r => r.page === currentPage);
  for (const r of pageRegions) {
    svg.appendChild(makeRect(r));
  }
  setupDrawing();
}

function makeRect(region) {
  const img = document.getElementById('page-img');
  const W = img.naturalWidth || img.width;
  const H = img.naturalHeight || img.height;

  const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  rect.setAttribute('x', `${region.x0 * 100}%`);
  rect.setAttribute('y', `${region.y0 * 100}%`);
  rect.setAttribute('width',  `${(region.x1 - region.x0) * 100}%`);
  rect.setAttribute('height', `${(region.y1 - region.y0) * 100}%`);
  rect.setAttribute('rx', '2');

  const cls = (region.source === 'user' && region.status !== 'rejected')
    ? 'overlay-user'
    : `overlay-${region.status}`;
  rect.setAttribute('class', cls);
  rect.dataset.regionId = region.id;

  rect.addEventListener('click', (e) => {
    e.stopPropagation();
    cycleStatus(region);
  });

  rect.addEventListener('mouseenter', () => {
    highlightRegionListItem(region.id, true);
  });

  rect.addEventListener('mouseleave', () => {
    highlightRegionListItem(region.id, false);
  });

  const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
  title.textContent = `${region.entity_type}${region.original_text ? ': ' + region.original_text : ''} (${region.status})`;
  rect.appendChild(title);

  return rect;
}

function cycleStatus(region) {
  // auto_approved and approved both toggle to rejected; rejected toggles to approved
  region.status = isApproved(region) ? 'rejected' : 'approved';
  renderOverlay();
  renderRegionList();
  updateThumbCounts();
  updateSummary();
}

function isApproved(region) {
  return region.status === 'approved' || region.status === 'auto_approved';
}

// ── Drawing new regions ───────────────────────────────────────────────────────

function setupDrawing() {
  const svg = document.getElementById('overlay');
  const img = document.getElementById('page-img');

  svg.onmousedown = (e) => {
    if (e.target !== svg) return; // clicked an existing rect — let cycleStatus handle it
    drawing = true;
    const rect = img.getBoundingClientRect();
    drawStart = {
      x: (e.clientX - rect.left) / rect.width,
      y: (e.clientY - rect.top)  / rect.height,
    };
    drawRect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    drawRect.id = 'draw-rect';
    drawRect.setAttribute('class', 'overlay-drawing');
    drawRect.setAttribute('rx', '2');
    svg.appendChild(drawRect);
  };

  svg.onmousemove = (e) => {
    if (!drawing || !drawRect) return;
    const rect = img.getBoundingClientRect();
    const cx = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const cy = Math.max(0, Math.min(1, (e.clientY - rect.top)  / rect.height));
    const x = Math.min(drawStart.x, cx);
    const y = Math.min(drawStart.y, cy);
    const w = Math.abs(cx - drawStart.x);
    const h = Math.abs(cy - drawStart.y);
    drawRect.setAttribute('x', `${x * 100}%`);
    drawRect.setAttribute('y', `${y * 100}%`);
    drawRect.setAttribute('width',  `${w * 100}%`);
    drawRect.setAttribute('height', `${h * 100}%`);
  };

  svg.onmouseup = (e) => {
    if (!drawing) return;
    drawing = false;
    if (!drawRect) return;

    const rect = img.getBoundingClientRect();
    const cx = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const cy = Math.max(0, Math.min(1, (e.clientY - rect.top)  / rect.height));

    const x0 = Math.min(drawStart.x, cx);
    const y0 = Math.min(drawStart.y, cy);
    const x1 = Math.max(drawStart.x, cx);
    const y1 = Math.max(drawStart.y, cy);

    drawRect.remove();
    drawRect = null;
    drawStart = null;

    const minSize = 0.005;
    if ((x1 - x0) < minSize || (y1 - y0) < minSize) return;

    // Generate a temporary negative id so we can reference it locally
    const tempId = -(Date.now());
    allRegions.push({
      id: tempId,
      page: currentPage,
      x0, y0, x1, y1,
      entity_type: 'USER_DEFINED',
      original_text: '',
      score: 1.0,
      source: 'user',
      status: 'approved',
    });

    renderOverlay();
    renderRegionList();
    updateThumbCounts();
    updateSummary();
  };
}

// ── Region list panel ─────────────────────────────────────────────────────────

function renderRegionList() {
  const list = document.getElementById('region-list');
  const pageRegions = allRegions.filter(r => r.page === currentPage);

  if (!pageRegions.length) {
    list.innerHTML = '<div style="color:var(--muted);text-align:center;padding:20px;font-size:12px">No regions on this page</div>';
    updateSummary();
    return;
  }

  list.innerHTML = pageRegions.map((r, i) => `
    <div class="region-item status-${r.status}" onclick="highlightRegion(${r.id})" data-rid="${r.id}">
      <div class="region-entity">${r.entity_type}</div>
      ${r.original_text ? `<div class="region-text" title="${escHtml(r.original_text)}">${escHtml(r.original_text)}</div>` : ''}
      <div class="region-meta">
        <span class="badge badge-${r.source === 'user' ? 'user' : r.status}">${
          r.source === 'user' ? 'user' :
          r.status === 'auto_approved' ? 'auto-approved' :
          r.status
        }</span>
        <span>${r.score < 1 ? (r.score * 100).toFixed(0) + '%' : ''}</span>
      </div>
      <div class="region-actions">
        <button class="btn btn-ghost btn-sm" onclick="event.stopPropagation();setStatus(${r.id},'approved')" style="color:#22c55e">✓ Approve</button>
        <button class="btn btn-ghost btn-sm" onclick="event.stopPropagation();setStatus(${r.id},'rejected')" style="color:#ef4444">✗ Reject</button>
      </div>
    </div>
  `).join('');

  updateSummary();
}

function escHtml(s) {
  return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function setStatus(regionId, status) {
  const r = allRegions.find(r => r.id === regionId);
  if (r) {
    r.status = status;
    renderOverlay();
    renderRegionList();
    updateThumbCounts();
    updateSummary();
  }
}

function highlightRegion(regionId) {
  document.querySelectorAll('.region-item').forEach(el => {
    el.classList.toggle('selected', parseInt(el.dataset.rid) === regionId);
  });
  // Remove focus from any previously focused rect
  document.querySelectorAll('.overlay-focused').forEach(el => el.classList.remove('overlay-focused'));
  // Highlight and scroll the SVG rect into view
  const svgRect = document.querySelector(`[data-region-id="${regionId}"]`);
  if (svgRect) {
    svgRect.classList.add('overlay-focused');
    svgRect.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

// Highlight/unhighlight a region list item from SVG hover events
function highlightRegionListItem(regionId, on) {
  const listItem = document.querySelector(`.region-item[data-rid="${regionId}"]`);
  if (!listItem) return;
  if (on) {
    listItem.classList.add('hovered');
    listItem.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } else {
    listItem.classList.remove('hovered');
  }
}

function updateSummary() {
  const approved = allRegions.filter(r => isApproved(r)).length;
  const total = allRegions.length;
  document.getElementById('region-summary').textContent = `${approved}/${total} approved`;
}

// ── Bulk actions ──────────────────────────────────────────────────────────────

function approveAll() {
  allRegions.forEach(r => { r.status = 'approved'; });
  renderOverlay();
  renderRegionList();
  updateThumbCounts();
  updateSummary();
}

function rejectAll() {
  allRegions.forEach(r => { r.status = 'rejected'; });
  renderOverlay();
  renderRegionList();
  updateThumbCounts();
  updateSummary();
}

// ── Save & Apply ──────────────────────────────────────────────────────────────

async function saveAndApply() {
  const btn = document.getElementById('apply-btn');
  btn.disabled = true;
  btn.textContent = 'Saving…';

  try {
    // Split into updates (existing id) and new inserts (negative/null id)
    const updates = allRegions
      .filter(r => r.id > 0)
      .map(r => ({ id: r.id, status: r.status }));

    const inserts = allRegions
      .filter(r => r.id <= 0)
      .map(r => ({
        page: r.page,
        x0: r.x0, y0: r.y0, x1: r.x1, y1: r.y1,
        entity_type: r.entity_type,
        original_text: r.original_text || '',
        source: 'user',
        status: r.status,
      }));

    await api.put(`/jobs/${JOB_ID}/regions`, { regions: [...updates, ...inserts] });

    btn.textContent = 'Applying…';
    await api.post(`/jobs/${JOB_ID}/apply`, {});

    showToast('Redaction applied successfully', 'success');
    setTimeout(() => { location.href = `job_detail.html?id=${JOB_ID}`; }, 1000);
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Save & Apply';
    showToast(`Failed: ${e.message}`, 'error');
  }
}

// ── Start ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', init);
