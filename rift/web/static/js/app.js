/* ═══════════════════════════════════════════
   RIFT EFFECT — Frontend Application
   ═══════════════════════════════════════════ */

const API = '/api/v1';

/* ── State ── */
const state = {
  token: localStorage.getItem('access_token'),
  refresh: localStorage.getItem('refresh_token'),
  user: null,
  uploads: [],
  jobs: [],
  selectedUpload: null,
  activeJob: null,
  pollTimer: null,
  previewParams: {},
};

/* ── API Client ── */
async function api(method, path, body, raw = false) {
  const headers = { 'Content-Type': 'application/json' };
  if (state.token) headers['Authorization'] = `Bearer ${state.token}`;
  const opts = { method, headers };
  if (body) opts.body = JSON.stringify(body);
  let res = await fetch(API + path, opts);

  if (res.status === 401 && state.refresh) {
    const ok = await tryRefresh();
    if (ok) {
      headers['Authorization'] = `Bearer ${state.token}`;
      res = await fetch(API + path, { method, headers, body: opts.body });
    } else {
      logout();
      return null;
    }
  }
  if (raw) return res;
  if (res.status === 204) return {};
  const data = await res.json();
  if (!res.ok) throw Object.assign(new Error(data.message || 'Request failed'), { data });
  return data;
}

async function apiUpload(path, formData) {
  const headers = {};
  if (state.token) headers['Authorization'] = `Bearer ${state.token}`;
  const res = await fetch(API + path, { method: 'POST', headers, body: formData });
  const data = await res.json();
  if (!res.ok) throw Object.assign(new Error(data.message || 'Upload failed'), { data });
  return data;
}

async function tryRefresh() {
  try {
    const res = await fetch(API + '/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: state.refresh }),
    });
    if (!res.ok) return false;
    const { access_token } = await res.json();
    state.token = access_token;
    localStorage.setItem('access_token', access_token);
    return true;
  } catch { return false; }
}

/* ── Router ── */
function showPage(id) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById('page-' + id)?.classList.add('active');
}

function showPanel(id) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('panel-' + id)?.classList.add('active');
  document.querySelectorAll('.nav-item').forEach(n => {
    n.classList.toggle('active', n.dataset.panel === id);
  });
  const titles = {
    studio: 'Studio', jobs: 'My Renders', billing: 'Billing',
    account: 'Account', admin: 'Admin Dashboard',
  };
  document.getElementById('topbar-title').textContent = titles[id] || id;
}

/* ── Auth ── */
async function initAuth() {
  if (!state.token) { showPage('auth'); return; }
  try {
    state.user = await api('GET', '/auth/me');
    initApp();
  } catch {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    state.token = null;
    showPage('auth');
  }
}

function setAuthTab(tab) {
  document.querySelectorAll('.auth-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  document.getElementById('login-form').style.display = tab === 'login' ? '' : 'none';
  document.getElementById('register-form').style.display = tab === 'register' ? '' : 'none';
}

async function handleLogin(e) {
  e.preventDefault();
  const btn = e.target.querySelector('[type=submit]');
  const email = document.getElementById('login-email').value;
  const password = document.getElementById('login-password').value;
  setLoading(btn, true);
  clearErrors('login-form');
  try {
    const data = await api('POST', '/auth/login', { email, password });
    state.token = data.access_token;
    state.refresh = data.refresh_token;
    localStorage.setItem('access_token', data.access_token);
    localStorage.setItem('refresh_token', data.refresh_token);
    state.user = await api('GET', '/auth/me');
    initApp();
  } catch (err) {
    showFormError('login-err', err.message);
  } finally {
    setLoading(btn, false);
  }
}

async function handleRegister(e) {
  e.preventDefault();
  const btn = e.target.querySelector('[type=submit]');
  const email = document.getElementById('reg-email').value;
  const password = document.getElementById('reg-password').value;
  const full_name = document.getElementById('reg-name').value;
  setLoading(btn, true);
  clearErrors('register-form');
  try {
    await api('POST', '/auth/register', { email, password, full_name });
    toast('success', 'Account created!', 'Check your email to verify your account.');
    setAuthTab('login');
  } catch (err) {
    showFormError('reg-err', err.message);
  } finally {
    setLoading(btn, false);
  }
}

function logout() {
  localStorage.removeItem('access_token');
  localStorage.removeItem('refresh_token');
  state.token = null;
  state.user = null;
  clearPoll();
  showPage('auth');
}

/* ── App Init ── */
function initApp() {
  showPage('app');
  const u = state.user;
  document.getElementById('user-name').textContent = u.full_name || u.email.split('@')[0];
  document.getElementById('user-plan').textContent = u.plan;
  document.getElementById('user-avatar').textContent = (u.full_name || u.email)[0].toUpperCase();
  if (!u.is_admin) {
    document.querySelectorAll('.admin-only').forEach(el => el.style.display = 'none');
  }
  loadStudio();
  showPanel('studio');
}

/* ── Studio ── */
async function loadStudio() {
  await Promise.all([loadUploads(), loadJobs(), loadBillingStatus()]);
  renderUploadList();
  renderJobList();
}

async function loadUploads() {
  try {
    state.uploads = await api('GET', '/videos');
  } catch { state.uploads = []; }
}

async function loadJobs() {
  try {
    const data = await api('GET', '/jobs?per_page=20');
    state.jobs = data.jobs || [];
  } catch { state.jobs = []; }
}

async function loadBillingStatus() {
  try {
    const data = await api('GET', '/billing/status');
    renderQuota(data);
  } catch {}
}

function renderQuota(data) {
  const wrap = document.getElementById('quota-wrap');
  if (!wrap) return;
  if (data.plan === 'free') {
    wrap.innerHTML = `<div class="alert alert-warning">⚡ Free plan — upgrade to start rendering</div>`;
    return;
  }
  if (data.plan === 'pay_per_video') {
    wrap.innerHTML = `<div class="quota-wrap"><div class="quota-header"><span class="quota-label">Render Credits</span><span class="quota-num">${data.credits}</span></div></div>`;
    return;
  }
  const pct = data.monthly_limit > 0 ? Math.round((data.renders_this_month / data.monthly_limit) * 100) : 0;
  wrap.innerHTML = `
    <div class="quota-wrap">
      <div class="quota-header">
        <span class="quota-label">Monthly Renders</span>
        <span class="quota-num">${data.renders_this_month} / ${data.monthly_limit}</span>
      </div>
      <div class="progress-bar-wrap">
        <div class="progress-bar" style="width:${pct}%"></div>
      </div>
    </div>`;
}

/* ── Upload ── */
function initDropzone() {
  const zone = document.getElementById('drop-zone');
  if (!zone) return;
  zone.addEventListener('click', () => document.getElementById('file-input').click());
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', e => {
    e.preventDefault(); zone.classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file) uploadVideo(file);
  });
  document.getElementById('file-input').addEventListener('change', e => {
    if (e.target.files[0]) uploadVideo(e.target.files[0]);
  });
}

async function uploadVideo(file) {
  const zone = document.getElementById('drop-zone');
  const orig = zone.innerHTML;
  zone.innerHTML = `<div style="text-align:center"><div class="spinner" style="width:32px;height:32px;border-width:3px;margin:0 auto 12px"></div><div style="color:var(--text2)">Uploading ${file.name}…</div></div>`;
  const fd = new FormData();
  fd.append('file', file);
  try {
    const upload = await apiUpload('/videos/upload', fd);
    state.uploads.unshift(upload);
    renderUploadList();
    selectUpload(upload);
    toast('success', 'Video uploaded', `${upload.filename} ready for rendering`);
  } catch (err) {
    toast('error', 'Upload failed', err.message);
  } finally {
    zone.innerHTML = orig;
    initDropzone();
  }
}

function renderUploadList() {
  const el = document.getElementById('upload-list');
  if (!el) return;
  if (!state.uploads.length) {
    el.innerHTML = '<div style="color:var(--text3);font-size:.8rem;padding:8px 0">No uploads yet</div>';
    return;
  }
  el.innerHTML = state.uploads.map(u => `
    <div class="upload-item ${state.selectedUpload?.id === u.id ? 'selected' : ''}"
         onclick="selectUpload(${JSON.stringify(u).replace(/"/g, '&quot;')})"
         data-id="${u.id}"
         style="padding:10px 12px;border-radius:8px;cursor:pointer;margin-bottom:4px;border:1px solid ${state.selectedUpload?.id === u.id ? 'var(--accent)' : 'transparent'};background:${state.selectedUpload?.id === u.id ? 'var(--accent-dim)' : 'var(--bg3)'}">
      <div style="font-weight:600;font-size:.85rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${u.filename}</div>
      <div style="font-size:.75rem;color:var(--text3)">${u.width}×${u.height} · ${u.fps?.toFixed(0)}fps · ${fmtDuration(u.duration)}</div>
    </div>`).join('');
}

function selectUpload(upload) {
  if (typeof upload === 'string') upload = JSON.parse(upload);
  state.selectedUpload = upload;
  renderUploadList();
  loadThumbnail();
  document.getElementById('render-btn').disabled = false;
}

async function loadThumbnail() {
  if (!state.selectedUpload) return;
  const img = document.getElementById('preview-img');
  const ph = document.getElementById('preview-placeholder');
  if (img) img.style.display = 'none';
  if (ph) ph.style.display = '';
  try {
    const data = await api('POST', '/jobs/thumbnail', {
      upload_id: state.selectedUpload.id,
      time_seconds: 0,
      max_width: 1280,
    });
    if (img) {
      img.src = 'data:image/jpeg;base64,' + data.image;
      img.style.display = '';
    }
    if (ph) ph.style.display = 'none';
  } catch {}
}

/* ── Preview ── */
async function generatePreview() {
  if (!state.selectedUpload) return;
  const btn = document.getElementById('preview-btn');
  const overlay = document.getElementById('preview-loading');
  setLoading(btn, true);
  if (overlay) overlay.style.display = 'flex';
  const params = collectParams();
  try {
    const data = await api('POST', '/jobs/preview', {
      upload_id: state.selectedUpload.id,
      frame_index: 0,
      params,
    });
    const img = document.getElementById('preview-img');
    if (img) {
      img.src = 'data:image/jpeg;base64,' + data.image;
      img.style.display = '';
      document.getElementById('preview-placeholder')?.style && (document.getElementById('preview-placeholder').style.display = 'none');
    }
    toast('info', 'Preview updated', `${data.width}×${data.height}`);
  } catch (err) {
    toast('error', 'Preview failed', err.message);
  } finally {
    setLoading(btn, false);
    if (overlay) overlay.style.display = 'none';
  }
}

/* ── Params ── */
function collectParams() {
  const get = id => {
    const el = document.getElementById(id);
    if (!el) return undefined;
    return el.type === 'checkbox' ? el.checked : (el.type === 'range' || el.type === 'number' ? Number(el.value) : el.value);
  };
  return {
    extract_fps: get('p-extract-fps') ?? 10,
    output_fps: get('p-output-fps') ?? 30,
    target_resolution: get('p-resolution') ?? 'Original',
    output_format: get('p-format') ?? 'mp4',
    ink_technique: get('p-effect') ?? 'graphic_pen',
    ink_color: get('p-ink-color') ?? '#000000',
    paper_color: get('p-paper-color') ?? '#ffffff',
    ink_density: get('p-density') ?? 0.8,
    ink_contrast: get('p-contrast') ?? 1.3,
    ink_length: get('p-length') ?? 12,
    ink_angle: get('p-angle') ?? 45,
    stroke_spacing: get('p-spacing') ?? 4,
    levels: get('p-levels') ?? 6,
    halftone_pattern: get('p-halftone-pattern') ?? 'dot',
    halftone_frequency: get('p-halftone-freq') ?? 10,
    dither_type: get('p-dither-type') ?? 'floyd',
    overlay_type: get('p-overlay') ?? 'none',
    overlay_intensity: get('p-overlay-intensity') ?? 0.5,
    overlay_grain_size: get('p-grain-size') ?? 50,
    overlay_line_spacing: get('p-line-spacing') ?? 4,
    overlay_wavelength: get('p-wavelength') ?? 20,
    overlay_amplitude: get('p-amplitude') ?? 10,
    include_audio: get('p-audio') ?? true,
    normalize_audio: get('p-normalize') ?? false,
    upscale_model: get('p-upscale-model') ?? 'RealESRGAN_x4plus',
  };
}

function initRangeDisplays() {
  document.querySelectorAll('input[type=range]').forEach(r => {
    const display = document.getElementById(r.id + '-val');
    if (display) {
      display.textContent = r.value;
      r.addEventListener('input', () => { display.textContent = r.value; });
    }
  });
  document.querySelectorAll('.color-preview').forEach(cp => {
    const inputId = cp.dataset.input;
    const input = document.getElementById(inputId);
    if (input) {
      cp.style.background = input.value;
      cp.addEventListener('click', () => input.click());
      input.addEventListener('input', () => { cp.style.background = input.value; });
    }
  });
}

function toggleSection(id) {
  const body = document.getElementById('cs-' + id);
  const chev = document.getElementById('chev-' + id);
  if (!body) return;
  body.classList.toggle('open');
  chev?.classList.toggle('open');
}

/* ── Render ── */
async function startRender() {
  if (!state.selectedUpload) { toast('warning', 'Select a video first', ''); return; }
  const btn = document.getElementById('render-btn');
  setLoading(btn, true);
  const params = collectParams();
  try {
    const job = await api('POST', '/jobs', { upload_id: state.selectedUpload.id, params });
    toast('success', 'Render queued!', `Job ID: ${job.job_id.slice(0,8)}`);
    await loadJobs();
    renderJobList();
    showPanel('jobs');
    startPoll(job.job_id);
  } catch (err) {
    if (err.data?.upgrade_required) {
      showUpgradeModal();
    } else {
      toast('error', 'Render failed', err.message);
    }
  } finally {
    setLoading(btn, false);
  }
}

/* ── Jobs ── */
function renderJobList() {
  const el = document.getElementById('job-list');
  if (!el) return;
  if (!state.jobs.length) {
    el.innerHTML = `<div class="empty-state"><div class="icon">🎬</div><h3>No renders yet</h3><p>Upload a video and choose your effect to get started.</p><button class="btn btn-primary" onclick="showPanel('studio')">Go to Studio</button></div>`;
    return;
  }
  el.innerHTML = state.jobs.map(j => renderJobCard(j)).join('');
}

function renderJobCard(j) {
  const statusColors = { pending:'pending', queued:'queued', processing:'processing', complete:'complete', failed:'failed', cancelled:'cancelled' };
  const pct = Math.round(j.progress || 0);
  const isActive = ['queued', 'processing', 'pending'].includes(j.status);
  return `
    <div class="job-card" id="job-${j.id}">
      <div class="job-header">
        <span class="job-status status-${statusColors[j.status] || 'pending'}">${statusIcons[j.status] || ''} ${j.status}</span>
        <span style="color:var(--text3);font-size:.75rem">${fmtDate(j.created_at)}</span>
        <span style="margin-left:auto;color:var(--text3);font-size:.8rem">${j.out_format?.toUpperCase() || 'MP4'} · ${j.out_width ? j.out_width+'×'+j.out_height : '–'}</span>
      </div>
      ${isActive ? `
        <div style="margin-bottom:12px">
          <div style="display:flex;justify-content:space-between;font-size:.8rem;margin-bottom:6px">
            <span style="color:var(--text2)">${j.message || 'Processing...'}</span>
            <span style="color:var(--accent2);font-weight:700">${pct}%</span>
          </div>
          <div class="progress-bar-wrap"><div class="progress-bar" style="width:${pct}%"></div></div>
          ${j.render_fps > 0 ? `<div style="font-size:.75rem;color:var(--text3);margin-top:4px">${j.render_fps} fps · ${j.frames_done}/${j.frames_total} frames</div>` : ''}
        </div>` : ''}
      ${j.status === 'failed' ? `<div class="alert alert-danger" style="margin-bottom:12px;font-size:.8rem">${j.error?.slice(0,200) || 'Processing error'}</div>` : ''}
      <div class="job-actions">
        ${j.status === 'complete' ? `
          <button class="btn btn-success btn-sm" onclick="downloadJob('${j.id}')">⬇ Download ${fmtBytes(j.out_size_bytes)}</button>
          <button class="btn btn-secondary btn-sm" onclick="previewJobOutput('${j.id}')">▶ Preview</button>` : ''}
        ${isActive ? `<button class="btn btn-danger btn-sm" onclick="cancelJob('${j.id}')">✕ Cancel</button>` : ''}
        <button class="btn btn-secondary btn-sm" onclick="deleteJob('${j.id}')">🗑 Delete</button>
        <span style="margin-left:auto;font-size:.75rem;color:var(--text3)">${j.out_expires_at ? 'Expires ' + fmtDate(j.out_expires_at) : ''}</span>
      </div>
    </div>`;
}

const statusIcons = { pending:'⏳', queued:'📋', processing:'⚙️', complete:'✅', failed:'❌', cancelled:'🚫' };

function startPoll(jobId) {
  clearPoll();
  state.pollTimer = setInterval(async () => {
    try {
      const job = await api('GET', '/jobs/' + jobId);
      const idx = state.jobs.findIndex(j => j.id === jobId);
      if (idx >= 0) state.jobs[idx] = job;
      else state.jobs.unshift(job);
      updateJobCard(job);
      if (!['pending','queued','processing'].includes(job.status)) {
        clearPoll();
        if (job.status === 'complete') toast('success', 'Render complete! 🎉', job.out_filename || '');
        else if (job.status === 'failed') toast('error', 'Render failed', 'Check job details');
      }
    } catch {}
  }, 2000);
}

function clearPoll() {
  if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
}

function updateJobCard(job) {
  const el = document.getElementById('job-' + job.id);
  if (el) el.outerHTML = renderJobCard(job);
}

async function cancelJob(id) {
  try {
    await api('POST', `/jobs/${id}/cancel`);
    await loadJobs();
    renderJobList();
    toast('info', 'Job cancelled', '');
    clearPoll();
  } catch (err) { toast('error', 'Cancel failed', err.message); }
}

async function deleteJob(id) {
  if (!confirm('Delete this job and output file?')) return;
  try {
    await api('DELETE', `/jobs/${id}`);
    state.jobs = state.jobs.filter(j => j.id !== id);
    renderJobList();
    toast('info', 'Deleted', '');
  } catch (err) { toast('error', 'Delete failed', err.message); }
}

function downloadJob(id) {
  window.open(API + '/jobs/' + id + '/download', '_blank');
}

function previewJobOutput(id) {
  const job = state.jobs.find(j => j.id === id);
  if (!job) return;
  const modal = document.getElementById('video-modal');
  const video = document.getElementById('modal-video');
  if (video) video.src = API + '/jobs/' + id + '/stream';
  openModal('video-modal');
}

/* ── Billing ── */
async function loadBillingPanel() {
  try {
    const [status, plans, history] = await Promise.all([
      api('GET', '/billing/status'),
      api('GET', '/billing/plans'),
      api('GET', '/billing/history?per_page=10'),
    ]);
    renderBillingPanel(status, plans.plans, history.payments);
  } catch (err) { toast('error', 'Failed to load billing', err.message); }
}

function renderBillingPanel(status, plans, history) {
  const el = document.getElementById('billing-content');
  if (!el) return;
  const planBadge = { free:'badge-free', starter:'badge-starter', pro:'badge-pro', studio:'badge-studio', pay_per_video:'badge-ppv' };
  el.innerHTML = `
    <div class="stats-grid" style="margin-bottom:28px">
      <div class="stat-card"><div class="stat-label">Current Plan</div><div class="stat-value stat-accent" style="font-size:1.4rem;text-transform:capitalize">${status.plan_name}</div></div>
      <div class="stat-card"><div class="stat-label">Renders This Month</div><div class="stat-value">${status.renders_this_month}<span style="font-size:1rem;color:var(--text3)"> / ${status.monthly_limit || '∞'}</span></div></div>
      <div class="stat-card"><div class="stat-label">${status.plan === 'pay_per_video' ? 'Credits' : 'Status'}</div><div class="stat-value stat-success">${status.plan === 'pay_per_video' ? status.credits : (status.sub_status || 'Active')}</div></div>
      <div class="stat-card"><div class="stat-label">Total Spent</div><div class="stat-value">$${status.total_spent_dollars?.toFixed(2)}</div></div>
    </div>

    ${status.sub_expires ? `<div class="alert alert-info" style="margin-bottom:24px">Subscription renews ${fmtDate(status.sub_expires)}</div>` : ''}
    ${!status.can_render ? `<div class="alert alert-warning" style="margin-bottom:24px">⚡ ${status.quota_message}</div>` : ''}

    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
      <h2>Plans</h2>
      ${status.plan !== 'free' ? `<button class="btn btn-secondary" onclick="openBillingPortal()">Manage Subscription ↗</button>` : ''}
    </div>
    <div class="plans-grid" style="margin-bottom:32px">
      ${plans.map(p => `
        <div class="plan-card ${p.id === 'pro' ? 'featured' : ''}">
          ${p.id === 'pro' ? '<div class="plan-featured-tag">Most Popular</div>' : ''}
          <div class="plan-name">${p.name}</div>
          <div class="plan-price">${p.price_display.replace('/mo','').replace('/video','')}</div>
          <div class="plan-period">${p.id === 'free' ? 'Forever free' : p.id === 'pay_per_video' ? 'per video' : 'per month'}</div>
          <ul class="plan-features">${p.features.map(f => `<li>${f}</li>`).join('')}</ul>
          ${p.id !== 'free' ? `<button class="btn btn-primary btn-full" onclick="checkout('${p.id}')">
            ${p.id === state.user?.plan ? 'Current Plan' : 'Get ' + p.name}
          </button>` : `<button class="btn btn-secondary btn-full" disabled>Current Plan</button>`}
        </div>`).join('')}
    </div>

    ${history.length ? `
    <h2 style="margin-bottom:16px">Payment History</h2>
    <div class="card"><div class="table-wrap"><table>
      <thead><tr><th>Date</th><th>Type</th><th>Amount</th><th>Status</th></tr></thead>
      <tbody>${history.map(p => `
        <tr>
          <td class="td-mono">${fmtDate(p.created_at)}</td>
          <td>${p.description || p.payment_type}</td>
          <td style="color:var(--success);font-weight:600">$${(p.amount_cents/100).toFixed(2)}</td>
          <td><span class="badge badge-${p.status === 'paid' ? 'starter' : 'free'}">${p.status}</span></td>
        </tr>`).join('')}
      </tbody>
    </table></div></div>` : ''}`;
}

async function checkout(planId, quantity = 1) {
  try {
    const data = await api('POST', '/billing/checkout', { plan_id: planId, quantity });
    window.location.href = data.checkout_url;
  } catch (err) { toast('error', 'Checkout failed', err.message); }
}

async function openBillingPortal() {
  try {
    const data = await api('POST', '/billing/portal', { return_url: window.location.href });
    window.location.href = data.portal_url;
  } catch (err) { toast('error', 'Cannot open portal', err.message); }
}

function showUpgradeModal() {
  openModal('upgrade-modal');
}

/* ── Admin ── */
async function loadAdminPanel() {
  try {
    const [metrics, users] = await Promise.all([
      api('GET', '/admin/metrics'),
      api('GET', '/admin/users?per_page=20'),
    ]);
    renderAdminMetrics(metrics);
    renderAdminUsers(users.users);
  } catch (err) { toast('error', 'Failed to load admin data', err.message); }
}

function renderAdminMetrics(m) {
  const el = document.getElementById('admin-metrics');
  if (!el) return;
  el.innerHTML = `
    <div class="stats-grid">
      <div class="stat-card"><div class="stat-label">Total Users</div><div class="stat-value stat-accent">${m.users.total}</div><div class="stat-sub">${m.users.verified} verified</div></div>
      <div class="stat-card"><div class="stat-label">Revenue</div><div class="stat-value stat-success">$${m.revenue.total_dollars?.toFixed(2)}</div></div>
      <div class="stat-card"><div class="stat-label">Total Renders</div><div class="stat-value">${m.jobs.total}</div><div class="stat-sub">${m.jobs.success_rate}% success</div></div>
      <div class="stat-card"><div class="stat-label">Active Jobs</div><div class="stat-value stat-warning">${m.jobs.active}</div></div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:20px">
      <div class="card"><div class="card-body">
        <h3 style="margin-bottom:16px">Users by Plan</h3>
        ${Object.entries(m.users.by_plan).map(([p,c]) => `
          <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border)">
            <span style="text-transform:capitalize">${p.replace('_',' ')}</span>
            <span style="font-weight:700;color:var(--accent2)">${c}</span>
          </div>`).join('')}
      </div></div>
      <div class="card"><div class="card-body">
        <h3 style="margin-bottom:16px">System</h3>
        <div style="font-size:.85rem">
          <div style="display:flex;justify-content:space-between;padding:4px 0"><span style="color:var(--text2)">GPU</span><span>${m.system.gpu.name || 'CPU only'}</span></div>
          <div style="display:flex;justify-content:space-between;padding:4px 0"><span style="color:var(--text2)">CPU</span><span>${m.system.cpu_percent}%</span></div>
          <div style="display:flex;justify-content:space-between;padding:4px 0"><span style="color:var(--text2)">RAM</span><span>${m.system.ram.used_percent}%</span></div>
          ${m.system.storage?.total_gb ? `<div style="display:flex;justify-content:space-between;padding:4px 0"><span style="color:var(--text2)">Storage</span><span>${m.system.storage.used_gb}/${m.system.storage.total_gb} GB</span></div>` : ''}
        </div>
      </div></div>
    </div>`;
}

function renderAdminUsers(users) {
  const el = document.getElementById('admin-users');
  if (!el) return;
  const planBadge = { free:'badge-free', starter:'badge-starter', pro:'badge-pro', studio:'badge-studio', pay_per_video:'badge-ppv' };
  el.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
      <h2>Users</h2>
      <input class="form-input" style="width:260px" placeholder="Search by email…" oninput="searchAdminUsers(this.value)" id="admin-search">
    </div>
    <div class="card"><div class="table-wrap"><table>
      <thead><tr><th>Email</th><th>Plan</th><th>Renders</th><th>Spent</th><th>Joined</th><th>Status</th><th></th></tr></thead>
      <tbody>${users.map(u => `
        <tr>
          <td><span style="font-weight:600">${u.email}</span>${u.is_admin ? ' <span style="color:var(--warning);font-size:.7rem">[admin]</span>' : ''}</td>
          <td><span class="badge ${planBadge[u.plan] || 'badge-free'}">${u.plan}</span></td>
          <td>${u.total_renders}</td>
          <td style="color:var(--success)">$${((u.total_spent_cents||0)/100).toFixed(2)}</td>
          <td class="td-mono">${fmtDate(u.created_at)}</td>
          <td><span style="color:${u.is_active ? 'var(--success)' : 'var(--danger)'}">${u.is_active ? 'Active' : 'Banned'}</span></td>
          <td>
            <button class="btn btn-secondary btn-sm" onclick="toggleUserActive('${u.id}', ${u.is_active})">
              ${u.is_active ? 'Ban' : 'Unban'}
            </button>
          </td>
        </tr>`).join('')}
      </tbody>
    </table></div></div>`;
}

let searchTimer;
function searchAdminUsers(q) {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    try {
      const data = await api('GET', `/admin/users?search=${encodeURIComponent(q)}&per_page=50`);
      renderAdminUsers(data.users);
    } catch {}
  }, 350);
}

async function toggleUserActive(userId, isActive) {
  try {
    await api('PATCH', `/admin/users/${userId}`, { is_active: !isActive });
    toast('success', 'User updated', '');
    loadAdminPanel();
  } catch (err) { toast('error', 'Failed', err.message); }
}

/* ── Account ── */
function loadAccountPanel() {
  const u = state.user;
  setVal('acc-name', u.full_name || '');
  setVal('acc-email', u.email);
  document.getElementById('acc-plan-badge').textContent = u.plan;
  document.getElementById('acc-renders').textContent = u.total_renders;
}

async function saveAccount(e) {
  e.preventDefault();
  const btn = e.target.querySelector('[type=submit]');
  setLoading(btn, true);
  try {
    const updated = await api('PUT', '/auth/me', { full_name: document.getElementById('acc-name').value });
    state.user = { ...state.user, ...updated };
    document.getElementById('user-name').textContent = updated.full_name || updated.email.split('@')[0];
    toast('success', 'Profile saved', '');
  } catch (err) { toast('error', 'Save failed', err.message); }
  finally { setLoading(btn, false); }
}

async function changePassword(e) {
  e.preventDefault();
  const btn = e.target.querySelector('[type=submit]');
  const current = document.getElementById('pw-current').value;
  const newPw = document.getElementById('pw-new').value;
  const confirm = document.getElementById('pw-confirm').value;
  if (newPw !== confirm) { toast('error', 'Passwords do not match', ''); return; }
  setLoading(btn, true);
  try {
    await api('POST', '/auth/change-password', { current_password: current, new_password: newPw });
    e.target.reset();
    toast('success', 'Password changed', '');
  } catch (err) { toast('error', 'Failed', err.message); }
  finally { setLoading(btn, false); }
}

/* ── Modal ── */
function openModal(id) {
  document.getElementById(id)?.classList.add('open');
}
function closeModal(id) {
  document.getElementById(id)?.classList.remove('open');
  const video = document.getElementById('modal-video');
  if (video) { video.pause(); video.src = ''; }
}

/* ── Toast ── */
function toast(type, title, msg) {
  const icons = { success:'✅', error:'❌', info:'ℹ️', warning:'⚠️' };
  const container = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.innerHTML = `
    <span class="toast-icon">${icons[type]||'ℹ️'}</span>
    <div class="toast-text">
      ${title ? `<div class="toast-title">${title}</div>` : ''}
      ${msg ? `<div class="toast-msg">${msg}</div>` : ''}
    </div>
    <button class="toast-close" onclick="this.closest('.toast').remove()">×</button>`;
  container.appendChild(el);
  setTimeout(() => {
    el.classList.add('removing');
    setTimeout(() => el.remove(), 200);
  }, 4500);
}

/* ── Helpers ── */
function setLoading(btn, loading) {
  if (!btn) return;
  if (loading) {
    btn._txt = btn.innerHTML;
    btn.innerHTML = '<div class="spinner"></div>';
    btn.disabled = true;
  } else {
    btn.innerHTML = btn._txt || btn.innerHTML;
    btn.disabled = false;
  }
}
function setVal(id, val) { const el = document.getElementById(id); if (el) el.value = val; }
function showFormError(id, msg) { const el = document.getElementById(id); if (el) { el.textContent = msg; el.classList.add('show'); } }
function clearErrors(formId) { document.querySelectorAll(`#${formId} .form-error`).forEach(e => e.classList.remove('show')); }
function fmtBytes(b) { if (!b) return ''; if (b<1024) return b+'B'; if (b<1048576) return (b/1024).toFixed(1)+'KB'; if (b<1073741824) return (b/1048576).toFixed(1)+'MB'; return (b/1073741824).toFixed(2)+'GB'; }
function fmtDuration(s) { if (!s) return ''; const m=Math.floor(s/60), sec=Math.floor(s%60); return m>0?`${m}m${sec}s`:`${sec}s`; }
function fmtDate(d) { if (!d) return ''; return new Date(d).toLocaleDateString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}); }

/* ── Init ── */
document.addEventListener('DOMContentLoaded', () => {
  // Auth
  document.querySelectorAll('.auth-tab').forEach(t => t.addEventListener('click', () => setAuthTab(t.dataset.tab)));
  document.getElementById('login-form')?.addEventListener('submit', handleLogin);
  document.getElementById('register-form')?.addEventListener('submit', handleRegister);

  // Nav
  document.querySelectorAll('.nav-item').forEach(n => {
    n.addEventListener('click', () => {
      const panel = n.dataset.panel;
      if (!panel) return;
      showPanel(panel);
      if (panel === 'jobs') loadJobs().then(renderJobList);
      if (panel === 'billing') loadBillingPanel();
      if (panel === 'admin') loadAdminPanel();
      if (panel === 'account') loadAccountPanel();
    });
  });

  document.getElementById('logout-btn')?.addEventListener('click', logout);
  document.getElementById('account-form')?.addEventListener('submit', saveAccount);
  document.getElementById('password-form')?.addEventListener('submit', changePassword);

  // Modals
  document.querySelectorAll('.modal-overlay').forEach(m => {
    m.addEventListener('click', e => { if (e.target === m) closeModal(m.id); });
  });

  // Control sections
  document.querySelectorAll('.control-section-header').forEach(h => {
    h.addEventListener('click', () => toggleSection(h.dataset.section));
  });

  initDropzone();
  initRangeDisplays();
  initAuth();
});