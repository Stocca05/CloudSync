const $ = (id) => document.getElementById(id);
const state = {
  user: null,
  remotes: [],
  jobs: [],
  providers: {},
  cluster: null,
  page: 'overview',
  activeFilter: 'all',
  jobSearchQuery: '',
  events: [],
  browseCache: { source: [], destination: [] }
};

let timer, toastTimer, appleTimer, appleJob, appleChallenge, editingRemote = null, googlePolling = false;
const speedHistory = Array(35).fill(0);

const operationLabels = {
  copy: 'Copia',
  move: 'Spostamento',
  sync: 'Sincronizzazione speculare (1 via)',
  bisync: 'Sincronizzazione 2 vie (bidirezionale)',
  list: 'Esplorazione',
  configure: 'Accesso iCloud',
  mkdir: 'Nuova cartella',
  delete: 'Eliminazione'
};

const labels = {
  queued: 'In coda',
  running: 'In corso',
  completed: 'Completato',
  failed: 'Errore',
  cancelled: 'Annullato'
};

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function button(text, action, cls = 'secondary') {
  const b = el('button', cls, text);
  b.type = 'button';
  b.onclick = event => guard(() => action(event));
  return b;
}

function toast(message) {
  const t = $('toast');
  t.textContent = message;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.hidden = true, 6500);
}

async function guard(action) {
  try {
    await action();
  } catch (e) {
    toast(e.message);
  }
}

async function api(path, options = {}) {
  const res = await fetch('/api' + path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-CloudSync-Request': '1',
      ...options.headers
    }
  });
  if (!res.ok) {
    let data;
    try {
      data = await res.json();
    } catch {
      data = { detail: 'Servizio non raggiungibile' };
    }
    if (res.status === 401 && state.user) {
      state.user = null;
      clearInterval(timer);
      $('shell').hidden = true;
      $('auth').hidden = false;
    }
    throw new Error(typeof data.detail === 'string' ? data.detail : 'Controlla i campi inseriti');
  }
  return res.json();
}

const post = (path, data = {}) => api(path, { method: 'POST', body: JSON.stringify(data) });

function bytes(value) {
  const n = Number(value) || 0;
  if (n < 1024) return Math.round(n) + ' B';
  const i = Math.min(4, Math.floor(Math.log(n) / Math.log(1024)));
  return (n / 1024 ** i).toFixed(1) + ' ' + ['B', 'KiB', 'MiB', 'GiB', 'TiB'][i];
}

function formatDuration(sec) {
  const s = Number(sec);
  if (!s || s <= 0 || !isFinite(s)) return '--';
  if (s < 60) return Math.round(s) + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm ' + Math.round(s % 60) + 's';
  return Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm';
}

function remoteName(id) {
  return state.remotes.find(r => r.id === id)?.name || 'Collegamento';
}

function remoteProvider(id) {
  return state.remotes.find(r => r.id === id)?.provider || 'cloud';
}

function fileIcon(name, isDir) {
  if (isDir) return '📁';
  const ext = name.split('.').pop().toLowerCase();
  if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'heic'].includes(ext)) return '🖼️';
  if (['mp4', 'mkv', 'avi', 'mov', 'webm', 'wmv'].includes(ext)) return '🎬';
  if (['mp3', 'wav', 'flac', 'aac', 'm4a', 'ogg'].includes(ext)) return '🎵';
  if (['zip', 'tar', 'gz', '7z', 'rar', 'bz2', 'xz'].includes(ext)) return '📦';
  if (['pdf', 'doc', 'docx', 'txt', 'rtf', 'odt', 'md'].includes(ext)) return '📄';
  if (['py', 'js', 'json', 'html', 'css', 'ts', 'sh', 'sql', 'yaml', 'yml'].includes(ext)) return '💻';
  return '📄';
}

function empty(target, title, description) {
  const node = $('empty-template').content.cloneNode(true);
  node.querySelector('h3').textContent = title;
  node.querySelector('p').textContent = description;
  target.replaceChildren(node);
}

function logEvent(type, message) {
  const now = new Date().toLocaleTimeString('it-IT');
  state.events.unshift({ time: now, type, message });
  if (state.events.length > 50) state.events.pop();
  renderEvents();
}

function renderEvents() {
  const root = $('admin-events');
  if (!root) return;
  root.replaceChildren();
  if (!state.events.length) {
    root.append(el('div', 'muted', 'Nessun evento recente registrato'));
    return;
  }
  for (const ev of state.events) {
    const item = el('div', 'event-item');
    item.append(
      el('span', 'event-time', ev.time),
      el('span', 'event-tag ' + (ev.type === 'error' ? 'warn' : (ev.type === 'success' ? 'success' : 'info')), ev.type.toUpperCase()),
      el('span', 'event-text', ev.message)
    );
    root.append(item);
  }
}

// Authentication
async function enter() {
  state.user = await api('/me');
  $('auth').hidden = true;
  $('shell').hidden = false;
  $('welcome').textContent = 'Ciao, ' + state.user.username;
  $('avatar').textContent = state.user.username[0].toUpperCase();
  $('admin-nav').hidden = !state.user.admin;
  state.providers = await api('/providers');
  state.google = await api('/google/status');
  logEvent('info', `Accesso completato per l'utente '${state.user.username}'`);
  await loadRemotes();
  await refresh();
  clearInterval(timer);
  timer = setInterval(() => guard(refresh), 4000);
}

$('login-form').onsubmit = async event => {
  event.preventDefault();
  const b = event.submitter;
  b.disabled = true;
  $('auth-error').textContent = '';
  try {
    const data = Object.fromEntries(new FormData(event.target));
    await post('/auth/login', data);
    event.target.reset();
    await enter();
  } catch (e) {
    $('auth-error').textContent = e.message;
  } finally {
    b.disabled = false;
  }
};

$('register').onclick = async () => {
  const form = $('login-form');
  if (!form.reportValidity()) return;
  try {
    const data = Object.fromEntries(new FormData(form));
    await post('/auth/register', data);
    await post('/auth/login', data);
    form.reset();
    await enter();
  } catch (e) {
    $('auth-error').textContent = e.message;
  }
};

const doLogout = () => guard(async () => {
  await post('/auth/logout');
  clearInterval(timer);
  state.user = null;
  $('shell').hidden = true;
  $('auth').hidden = false;
});
if ($('logout')) $('logout').onclick = doLogout;
if ($('mobile-logout')) $('mobile-logout').onclick = doLogout;

async function page(name) {
  state.page = name;
  document.querySelectorAll('.page').forEach(p => p.hidden = p.id !== 'page-' + name);
  document.querySelectorAll('[data-page]').forEach(b => b.classList.toggle('selected', b.dataset.page === name));
  if (name === 'admin') await loadAdmin();
  if (name === 'schedules') await loadSchedules();
}

document.querySelectorAll('[data-page]').forEach(b => b.onclick = () => guard(() => page(b.dataset.page)));
document.querySelectorAll('[data-go]').forEach(b => b.onclick = () => guard(() => page(b.dataset.go)));
document.querySelectorAll('[data-close]').forEach(b => b.onclick = () => $(b.dataset.close).close());

// Refresh Data Loop
async function refresh() {
  if (!state.user) return;
  state.jobs = await api('/jobs');
  renderJobs();
  if (state.page === 'admin') await loadAdmin(false);
  if (state.page === 'schedules') await loadSchedules();
}

// Speed Chart with Canvas Gradient and Live Readouts
function updateChart(speedBytes) {
  const canvas = $('speedChart');
  if (!canvas) return;
  const currentSpeed = Math.max(0, Number(speedBytes) || 0);
  speedHistory.push(currentSpeed);
  speedHistory.shift();

  const maxSpeed = Math.max(1048576, ...speedHistory);
  const avgSpeed = speedHistory.filter(s => s > 0).length
    ? speedHistory.reduce((a, b) => a + b, 0) / Math.max(1, speedHistory.filter(s => s > 0).length)
    : 0;

  if ($('chart-cur-speed')) $('chart-cur-speed').textContent = bytes(currentSpeed) + '/s';
  if ($('chart-peak-speed')) $('chart-peak-speed').textContent = bytes(maxSpeed) + '/s';
  if ($('chart-avg-speed')) $('chart-avg-speed').textContent = bytes(avgSpeed) + '/s';

  const rect = canvas.getBoundingClientRect();
  if (!rect.width) return;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * ratio);
  canvas.height = Math.round(rect.height * ratio);
  const ctx = canvas.getContext('2d');
  ctx.scale(ratio, ratio);

  const w = rect.width, h = rect.height, padX = 18, padY = 22;
  const plotW = w - 2 * padX, plotH = h - 2 * padY;

  // Background Grid
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
  ctx.lineWidth = 1;
  for (let grid = 0; grid <= 3; grid++) {
    const y = padY + (plotH / 3) * grid;
    ctx.beginPath();
    ctx.moveTo(padX, y);
    ctx.lineTo(w - padX, y);
    ctx.stroke();
  }

  // Draw Area Fill (Gradient)
  const grad = ctx.createLinearGradient(0, padY, 0, h - padY);
  grad.addColorStop(0, 'rgba(14, 165, 233, 0.35)');
  grad.addColorStop(1, 'rgba(14, 165, 233, 0.00)');

  ctx.beginPath();
  ctx.moveTo(padX, h - padY);
  speedHistory.forEach((speed, i) => {
    const x = padX + i * (plotW / (speedHistory.length - 1));
    const y = h - padY - (speed / maxSpeed) * plotH;
    ctx.lineTo(x, y);
  });
  ctx.lineTo(w - padX, h - padY);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // Draw Line
  ctx.beginPath();
  speedHistory.forEach((speed, i) => {
    const x = padX + i * (plotW / (speedHistory.length - 1));
    const y = h - padY - (speed / maxSpeed) * plotH;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = '#38bdf8';
  ctx.lineWidth = 2.5;
  ctx.stroke();

  // Draw Pulse Dot on latest point
  const lastX = w - padX;
  const lastY = h - padY - (currentSpeed / maxSpeed) * plotH;
  ctx.beginPath();
  ctx.arc(lastX, lastY, 4, 0, Math.PI * 2);
  ctx.fillStyle = '#38bdf8';
  ctx.fill();
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = 1.5;
  ctx.stroke();
}

window.addEventListener('resize', () => {
  if (state.page === 'overview' && speedHistory.length) {
    updateChart(speedHistory[speedHistory.length - 1]);
  }
});

// Jobs Search & Filter Tabs
if ($('job-search')) {
  $('job-search').oninput = e => {
    state.jobSearchQuery = e.target.value.toLowerCase().trim();
    renderJobs();
  };
}

if ($('job-filter-tabs')) {
  $('job-filter-tabs').querySelectorAll('button').forEach(btn => {
    btn.onclick = () => {
      $('job-filter-tabs').querySelectorAll('button').forEach(b => b.classList.remove('selected'));
      btn.classList.add('selected');
      state.activeFilter = btn.dataset.filter;
      renderJobs();
    };
  });
}

// Overview & Jobs Rendering
function renderJobs() {
  const jobs = state.jobs;
  const activeJobs = jobs.filter(j => j.status === 'running');
  const queuedJobs = jobs.filter(j => j.status === 'queued');
  const completedJobs = jobs.filter(j => j.status === 'completed');
  const failedJobs = jobs.filter(j => ['failed', 'cancelled'].includes(j.status));

  const totalSpeed = activeJobs.reduce((sum, j) => sum + (j.stats?.speed || 0), 0);
  updateChart(totalSpeed);

  // Update Counters in Filter Tabs
  if ($('count-all')) $('count-all').textContent = jobs.length;
  if ($('count-running')) $('count-running').textContent = activeJobs.length;
  if ($('count-queued')) $('count-queued').textContent = queuedJobs.length;
  if ($('count-completed')) $('count-completed').textContent = completedJobs.length;
  if ($('count-failed')) $('count-failed').textContent = failedJobs.length;

  // Overview KPI Metrics
  $('metric-active').textContent = activeJobs.length;
  $('metric-queued').textContent = queuedJobs.length;
  $('metric-done').textContent = completedJobs.length;
  $('metric-speed').textContent = bytes(totalSpeed) + '/s';

  // KPI Subtitles & Percentages
  const totalFinished = completedJobs.length + failedJobs.length;
  const successPct = totalFinished > 0 ? ((completedJobs.length / totalFinished) * 100).toFixed(1) : '100';
  if ($('metric-success-rate')) $('metric-success-rate').textContent = `Tasso successo: ${successPct}%`;
  if ($('metric-done-badge')) $('metric-done-badge').textContent = `${successPct}%`;
  if ($('kpi-bar-done')) $('kpi-bar-done').style.width = `${successPct}%`;

  if ($('metric-queued-badge')) $('metric-queued-badge').textContent = queuedJobs.length ? `${queuedJobs.length} in attesa` : '0 in coda';
  if ($('kpi-bar-queued')) $('kpi-bar-queued').style.width = Math.min(100, queuedJobs.length * 25) + '%';

  // Active slots utilization
  const totalSlotsEst = Math.max(4, activeJobs.length + 2);
  const activePct = Math.min(100, Math.round((activeJobs.length / totalSlotsEst) * 100));
  if ($('metric-active-slots')) $('metric-active-slots').textContent = `${activeJobs.length} trasferimenti attivi`;
  if ($('metric-active-pct')) $('metric-active-pct').textContent = `${activePct}%`;
  if ($('kpi-bar-active')) $('kpi-bar-active').style.width = `${activePct}%`;

  // Speed Utilization
  const estBandCap = 50 * 1048576; // 50 MiB/s default reference
  const speedPct = Math.min(100, Math.round((totalSpeed / estBandCap) * 100));
  if ($('metric-speed-util')) $('metric-speed-util').textContent = `Traffico attivo: ${bytes(totalSpeed)}/s`;
  if ($('metric-speed-pct')) $('metric-speed-pct').textContent = `${speedPct}%`;
  if ($('kpi-bar-speed')) $('kpi-bar-speed').style.width = `${speedPct}%`;

  // Header status pill
  if ($('header-cluster-text')) {
    $('header-cluster-text').textContent = activeJobs.length
      ? `${activeJobs.length} attivi · ${bytes(totalSpeed)}/s`
      : 'Cluster pronto';
  }
  if ($('sidebar-node-summary')) {
    $('sidebar-node-summary').textContent = activeJobs.length
      ? `${activeJobs.length} in esecuzione (${bytes(totalSpeed)}/s)`
      : 'In attesa di trasferimenti';
  }

  // Filter Jobs
  let filtered = jobs;
  if (state.activeFilter === 'running') filtered = activeJobs;
  else if (state.activeFilter === 'queued') filtered = queuedJobs;
  else if (state.activeFilter === 'completed') filtered = completedJobs;
  else if (state.activeFilter === 'failed') filtered = failedJobs;

  if (state.jobSearchQuery) {
    const q = state.jobSearchQuery;
    filtered = filtered.filter(j =>
      j.id.toLowerCase().includes(q) ||
      (j.source_path || '').toLowerCase().includes(q) ||
      (j.destination_path || '').toLowerCase().includes(q) ||
      remoteName(j.source_id).toLowerCase().includes(q) ||
      remoteName(j.destination_id).toLowerCase().includes(q) ||
      (j.node_id || '').toLowerCase().includes(q)
    );
  }

  const root = $('jobs');
  root.replaceChildren();

  if (!filtered.length) {
    return empty(
      root,
      state.jobSearchQuery ? 'Nessun trasferimento corrisponde alla ricerca' : 'Nessuna attività recente',
      state.jobSearchQuery ? 'Prova a cercare un nome diverso o azzera i filtri.' : 'Avvia una copia o sincronizzazione continua dalla pagina Esplora.'
    );
  }

  for (const j of filtered) {
    const row = el('article', `job status-${j.status}`);
    const isBi = j.operation === 'bisync';
    const isSync = j.operation === 'sync';
    const arrow = isBi ? '⇄' : (isSync ? '➔' : (j.operation === 'move' ? '→' : '➔'));

    // Head Row
    const head = el('div', 'job-head');
    const titleGroup = el('div', 'job-title-group');
    titleGroup.append(el('div', 'job-icon', arrow));

    const headTitles = el('div');
    const routeText = ['copy', 'move', 'sync', 'bisync'].includes(j.operation)
      ? `${remoteName(j.source_id)} ${arrow} ${remoteName(j.destination_id)}`
      : `${operationLabels[j.operation] || j.operation} · ${remoteName(j.source_id)}`;

    const h3 = el('h3', '', routeText);
    headTitles.append(h3);

    const pathSub = el('div', 'job-paths', `${j.source_path || '/'} ${arrow} ${j.destination_path || '/'}`);
    headTitles.append(pathSub);
    titleGroup.append(headTitles);

    const badgeGroup = el('div', 'job-actions');
    const badge = el('span', `badge ${j.status}`, j.cancel_requested && j.status === 'running' ? 'Annullamento…' : labels[j.status]);
    badgeGroup.append(badge);

    head.append(titleGroup, badgeGroup);
    row.append(head);

    // Progress Bar with Percentage
    const st = j.stats || {};
    const totalB = st.totalBytes || 0;
    const curB = st.bytes || 0;
    let pct = 0;
    if (j.status === 'completed') pct = 100;
    else if (totalB > 0) pct = Math.min(100, Math.max(0, (curB / totalB) * 100));

    const progWrap = el('div', 'job-progress-wrapper');
    const p = el('progress');
    p.max = 100;
    p.value = pct;
    p.setAttribute('aria-label', 'Avanzamento ' + j.id);

    const pctSpan = el('span', 'progress-pct', `${pct.toFixed(1)}%`);
    progWrap.append(p, pctSpan);
    row.append(progWrap);

    // Micro-Telemetry Grid
    const telem = el('div', 'job-telemetry');
    telem.append(
      el('span', 'telem-pill', `📦 Transito: `).appendChild(el('strong', '', `${bytes(curB)} / ${bytes(totalB)}`)).parentNode,
      el('span', 'telem-pill', `⚡ Velocità: `).appendChild(el('strong', '', `${bytes(st.speed || 0)}/s`)).parentNode
    );

    if (j.status === 'running' && st.eta) {
      telem.append(el('span', 'telem-pill', `⏱️ ETA: `).appendChild(el('strong', '', formatDuration(st.eta))).parentNode);
    }
    if (st.transfers) {
      telem.append(el('span', 'telem-pill', `🗂️ File: `).appendChild(el('strong', '', `${st.transfers}`)).parentNode);
    }
    if (j.node_id) {
      telem.append(el('span', 'telem-pill', `🖥️ Nodo: `).appendChild(el('strong', '', j.node_id)).parentNode);
    }
    if (st.checkpoint_bytes > 0) {
      telem.append(el('span', 'telem-pill', `✓ Checkpoint: `).appendChild(el('strong', '', bytes(st.checkpoint_bytes))).parentNode);
    }

    const timeStr = new Date(j.created * 1000).toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' });
    telem.append(el('span', 'telem-pill muted', `Avviato: ${timeStr}`));
    row.append(telem);

    // Error callout
    if (j.error) {
      const errBox = el('p', 'error', '⚠️ ' + j.error);
      row.append(errBox);
    }

    // Actions Footer
    const foot = el('div', 'job-foot');
    const opInfo = el('span', 'muted dim', `${operationLabels[j.operation] || j.operation} · ID: ${j.id.slice(0, 8)}`);
    const actions = el('div', 'job-actions');

    if (j.operation === 'configure' && j.status === 'running') {
      actions.append(button('Completa accesso iCloud', () => watchApple(j.id), 'primary'));
    }

    if (j.status === 'running') {
      actions.append(
        button('Pausa ⏸', async () => {
          await post('/jobs/' + j.id + '/cancel');
          toast('Trasferimento messo in pausa. Il checkpoint è salvato.');
          await refresh();
        }, 'secondary'),
        button('Annulla', async () => {
          await post('/jobs/' + j.id + '/cancel');
          await refresh();
        }, 'quiet')
      );
    } else if (j.status === 'queued') {
      actions.append(
        button('Annulla', async () => {
          await post('/jobs/' + j.id + '/cancel');
          await refresh();
        }, 'quiet')
      );
    } else if (['failed', 'cancelled'].includes(j.status)) {
      actions.append(
        button('Riprendi ↺', async () => {
          await post('/jobs/' + j.id + '/retry');
          toast('Trasferimento ripreso dal checkpoint.');
          await refresh();
        }, 'primary'),
        button('Rimuovi', async () => {
          await api('/jobs/' + j.id, { method: 'DELETE' });
          toast('Lavoro rimosso dalla cronologia.');
          await refresh();
        }, 'quiet')
      );
    } else if (j.status === 'completed') {
      actions.append(
        button('Rimuovi', async () => {
          await api('/jobs/' + j.id, { method: 'DELETE' });
          await refresh();
        }, 'quiet')
      );
    }

    foot.append(opInfo, actions);
    row.append(foot);
    root.append(row);
  }
}

// Remotes Management
async function loadRemotes() {
  state.remotes = await api('/remotes');
  for (const name of ['source', 'destination']) {
    const select = $(name + '-remote'), old = select.value;
    select.replaceChildren(new Option('Scegli un collegamento cloud', ''));
    state.remotes.forEach(r => select.add(new Option(r.name + ' (' + (state.providers[r.provider]?.label || r.provider) + ')', r.id)));
    if (state.remotes.some(r => r.id === old)) select.value = old;
  }
  const root = $('remotes');
  root.replaceChildren();
  if (!state.remotes.length) return empty(root, 'Nessun cloud collegato', 'Aggiungi il tuo primo provider (Google Drive, iCloud, S3, WebDAV, SFTP).');
  
  for (const r of state.remotes) {
    const card = el('article', 'remote-card');
    card.append(
      el('div', 'remote-icon', '◇'),
      el('h3', '', r.name),
      el('p', 'muted', state.providers[r.provider]?.label || r.provider)
    );
    if (r.provider === 'iclouddrive') {
      card.append(
        button('Verifica / rinnova Apple', () => startApple(r.id)),
        button('Aggiorna credenziali', () => {
          openRemote();
          editingRemote = r.id;
          $('remote-form').elements.name.value = r.name;
          $('provider').value = 'iclouddrive';
          $('provider').disabled = true;
          providerFields();
        })
      );
    }
    card.append(
      button('Esplora cartelle', async () => {
        $('source-remote').value = r.id;
        await page('explorer');
        await browse('source');
      }, 'primary'),
      button('Rimuovi', async () => {
        if (confirm('Rimuovere il collegamento ' + r.name + '? I trasferimenti attivi saranno annullati.')) {
          await api('/remotes/' + r.id, { method: 'DELETE' });
          logEvent('warn', `Collegamento rimosso: '${r.name}'`);
          await loadRemotes();
        }
      }, 'quiet')
    );
    root.append(card);
  }
}

const fieldLabels = {
  provider: 'Provider S3 (AWS, Minio, Wasabi, Other…)',
  access_key_id: 'Access key ID',
  secret_access_key: 'Secret access key',
  region: 'Regione',
  endpoint: 'Endpoint HTTPS (facoltativo per AWS)',
  host: 'Host o IP pubblico',
  port: 'Porta',
  user: 'Nome utente',
  pass: 'Password',
  url: 'URL HTTPS del server',
  vendor: 'Vendor (other, nextcloud, owncloud)',
  token: 'Token JSON Rclone',
  client_id: 'Client ID (facoltativo)',
  client_secret: 'Client secret (facoltativo)',
  apple_id: 'Apple ID (indirizzo email)',
  password: 'Password del tuo account Apple',
  root_folder_id: 'ID cartella radice (facoltativo)'
};

function providerFields() {
  const type = $('provider').value, schema = state.providers[type], root = $('provider-fields');
  root.replaceChildren();
  $('remote-submit').textContent = type === 'drive' ? 'Accedi con Google' : 'Salva collegamento';
  $('remote-submit').disabled = type === 'drive' && !state.google.configured;
  $('provider-help').textContent = type === 'iclouddrive'
    ? 'Usa la password del tuo account Apple. Il passaggio successivo chiederà il codice 2FA ricevuto sul tuo dispositivo.'
    : type === 'drive'
      ? (state.google.configured
          ? (state.google.method === 'rclone'
              ? 'Accedi con il client Google di Rclone. L’assistente CloudSync deve essere attivo sul computer per completare l’autorizzazione locale.'
              : 'Scegli il tuo account Google e autorizza CloudSync. Non servono file JSON o credenziali manuali.')
          : 'L’amministratore deve abilitare l’accesso Google per questo server.')
      : type === 'sftp'
        ? 'Il nodo worker deve conoscere la chiave host SFTP. Sono accettati host pubblici con autenticazione password.'
        : 'Inserisci le credenziali del servizio di archiviazione.';

  if (type === 'drive' && state.google.method === 'rclone') {
    const link = el('a', '', 'Scarica assistente Google');
    link.href = '/api/google/helper';
    link.download = 'CloudSync-Google.py';
    root.append(link, el('p', 'muted', 'Su un nuovo computer: esegui python3 CloudSync-Google.py e lascia l’assistente aperto prima di premere Accedi con Google.'));
  }

  for (const field of (type === 'drive' ? [] : schema.fields)) {
    const label = el('label', '', fieldLabels[field] || field);
    const input = el(field === 'token' ? 'textarea' : 'input');
    input.name = field;
    input.required = schema.required.includes(field);
    if (['pass', 'password', 'secret_access_key', 'client_secret'].includes(field)) input.type = 'password';
    if (field === 'provider') input.value = 'AWS';
    if (field === 'vendor') input.value = 'other';
    if (field === 'port') input.value = '22';
    label.append(input);
    root.append(label);
  }
}

function openRemote() {
  editingRemote = null;
  $('provider').disabled = false;
  $('remote-form').reset();
  $('remote-error').textContent = '';
  $('provider').replaceChildren();
  Object.entries(state.providers).forEach(([id, p]) => $('provider').add(new Option(p.label, id)));
  providerFields();
  $('remote-dialog').showModal();
}
$('add-remote').onclick = openRemote;
$('provider').onchange = providerFields;

$('remote-form').onsubmit = async e => {
  e.preventDefault();
  if (googlePolling) return;
  const googleWindow = $('provider').value === 'drive' ? window.open('about:blank', 'cloudsync-google') : null;
  e.submitter.disabled = true;
  try {
    const config = {};
    $('provider-fields').querySelectorAll('input,textarea').forEach(i => {
      if (i.value) config[i.name] = i.value;
    });
    const provider = $('provider').value;
    if (provider === 'drive') {
      const auth = await post('/google/start', { name: e.target.elements.name.value });
      if (googleWindow) {
        googleWindow.opener = null;
        googleWindow.location = auth.url;
      } else {
        throw new Error('Consenti l’apertura della finestra popup Google nel browser e riprova.');
      }
      if (auth.method === 'rclone') await waitGoogle(auth.ticket);
      return;
    }
    const values = { name: e.target.elements.name.value, provider, config };
    const remote = editingRemote
      ? await api('/remotes/' + editingRemote, { method: 'PATCH', body: JSON.stringify(values) })
      : await post('/remotes', values);
    $('remote-dialog').close();
    e.target.reset();
    logEvent('success', `Collegamento salvato con successo: '${values.name}'`);
    await loadRemotes();
    if (provider === 'iclouddrive') await startApple(remote.id);
    else toast('Collegamento salvato. Apri Esplora per verificarlo.');
  } catch (err) {
    $('remote-error').textContent = err.message;
    if (googleWindow && !googleWindow.closed) googleWindow.close();
  } finally {
    googlePolling = false;
    e.submitter.disabled = false;
  }
};

async function waitGoogle(ticket) {
  googlePolling = true;
  $('remote-error').textContent = 'Completa l’accesso nella finestra Google aperta. In attesa di autorizzazione...';
  const reasons = {
    port_busy: 'Porta 53683 occupata sul client locale.',
    shared_client: 'Google ha rifiutato il client condiviso. Configura un client ID personale.',
    upload: 'Autorizzazione ricevuta ma il server non l’ha memorizzata.',
    authorization: 'Accesso Google annullato o rifiutato.',
    timeout: 'Tempo scaduto.'
  };
  for (let i = 0; i < 300; i++) {
    await new Promise(r => setTimeout(r, 2000));
    if (!state.user || !$('remote-dialog').open) return;
    const result = await post('/google/progress', { ticket });
    if (result.status === 'connected') {
      $('remote-dialog').close();
      logEvent('success', 'Google Drive collegato con successo');
      await loadRemotes();
      await page('remotes');
      toast('Google Drive collegato con successo.');
      return;
    }
    if (result.status === 'failed') throw new Error(reasons[result.reason] || reasons.authorization);
    if (result.status === 'expired') throw new Error(reasons.timeout);
  }
  throw new Error(reasons.timeout);
}

// Breadcrumbs and File Explorer
function renderBreadcrumbs(side, path) {
  const root = $(side + '-breadcrumbs');
  if (!root) return;
  root.replaceChildren();

  const rootCrumb = el('span', 'crumb root', 'Radice /');
  rootCrumb.onclick = () => {
    $(side + '-path').value = '';
    browse(side);
  };
  root.append(rootCrumb);

  if (!path) return;
  const parts = path.split('/').filter(Boolean);
  let accumulated = '';
  parts.forEach((part, idx) => {
    accumulated = [accumulated, part].filter(Boolean).join('/');
    const currentPath = accumulated;
    const crumb = el('span', 'crumb', part + ' /');
    crumb.onclick = () => {
      $(side + '-path').value = currentPath;
      browse(side);
    };
    root.append(crumb);
  });
}

function filterFileList(side, filterText) {
  const root = $(side + '-files');
  if (!root) return;
  const entries = root.querySelectorAll('.file-entry');
  const q = (filterText || '').toLowerCase().trim();
  entries.forEach(ent => {
    const text = ent.textContent.toLowerCase();
    ent.hidden = q && !text.includes(q);
  });
}

if ($('source-filter')) {
  $('source-filter').oninput = e => filterFileList('source', e.target.value);
}
if ($('destination-filter')) {
  $('destination-filter').oninput = e => filterFileList('destination', e.target.value);
}

async function browse(side) {
  const id = $(side + '-remote').value, path = ($(side + '-path').value || '').trim();
  if (!id) throw new Error('Scegli prima un collegamento cloud');
  renderBreadcrumbs(side, path);

  const root = $(side + '-files');
  root.replaceChildren(el('p', 'muted', 'Scansione cartella in corso via worker...'));
  const job = await post('/jobs', { operation: 'list', source_id: id, source_path: path });
  let data;
  for (let i = 0; i < 90; i++) {
    await new Promise(r => setTimeout(r, 1500));
    data = await api('/jobs/' + job.id);
    if (['completed', 'failed', 'cancelled'].includes(data.status)) break;
  }
  root.replaceChildren();
  if (data.status !== 'completed') {
    root.append(el('p', 'error', data.error || 'La lettura non è terminata. Riprova tra poco.'));
    return;
  }

  const items = data.result.items || [];
  state.browseCache[side] = items;

  let dirCount = 0, fileCount = 0, totalBytes = 0;
  items.forEach(it => {
    if (it.IsDir) dirCount++;
    else { fileCount++; totalBytes += (it.Size || 0); }
  });

  const summary = $(side + '-summary');
  if (summary) {
    summary.textContent = items.length
      ? `${dirCount} cartelle, ${fileCount} file · Totale: ${bytes(totalBytes)}`
      : 'Cartella vuota';
  }

  if (path) {
    const upBtn = button('↑ Livello superiore', () => {
      const parent = path.split('/').slice(0, -1).join('/');
      $(side + '-path').value = parent;
      return browse(side);
    }, 'quiet');
    root.append(upBtn);
  }

  for (const item of items) {
    const container = el('div', 'file-entry');
    const icon = fileIcon(item.Name, item.IsDir);
    const row = button(`${icon} ${item.Name}`, () => {
      const p = [path, item.Path].filter(Boolean).join('/');
      $(side + '-path').value = p;
      if (item.IsDir) return browse(side);
      if (side === 'source') {
        $('is-file').checked = true;
        $('destination-path').value = [$('destination-path').value, item.Name].filter(Boolean).join('/');
      }
    }, '');
    row.append(el('small', '', item.IsDir ? 'Cartella' : bytes(item.Size)));

    const delBtn = button('✕', e => {
      e.stopPropagation();
      return handleDelete(side, [path, item.Path].filter(Boolean).join('/'), item.IsDir);
    }, 'quiet');
    delBtn.title = 'Elimina';
    delBtn.classList.add('delete-file');
    delBtn.setAttribute('aria-label', 'Elimina ' + item.Name);

    container.append(row, delBtn);
    root.append(container);
  }

  if (!items.length) root.append(el('p', 'muted', 'Questa cartella è vuota.'));
  if (data.result.truncated) root.append(el('p', 'notice', 'Mostrati i primi 1.000 elementi.'));
}

$('browse-source').onclick = () => guard(() => browse('source'));
$('browse-destination').onclick = () => guard(() => browse('destination'));

async function handleMkdir(side) {
  const id = $(side + '-remote').value;
  let currentPath = $(side + '-path').value;
  if (!id) return toast('Scegli prima un collegamento cloud.');
  const folderName = prompt('Nome della nuova cartella (verrà creata in ' + (currentPath || 'radice') + '):');
  if (!folderName) return;
  const newPath = [currentPath, folderName].filter(Boolean).join('/');
  toast('Creazione cartella in corso...');
  try {
    const job = await post('/jobs', { operation: 'mkdir', source_id: id, source_path: newPath });
    let data;
    for (let i = 0; i < 30; i++) {
      await new Promise(r => setTimeout(r, 1000));
      data = await api('/jobs/' + job.id);
      if (['completed', 'failed', 'cancelled'].includes(data.status)) break;
    }
    if (data.status === 'completed') {
      $(side + '-path').value = newPath;
      toast('Cartella creata con successo.');
      logEvent('success', `Cartella creata: '${newPath}'`);
      await browse(side);
    } else {
      toast('Errore creazione: ' + (data.error || 'Operazione fallita'));
    }
  } catch (err) {
    toast(err.message);
  }
}

$('mkdir-source').onclick = () => guard(() => handleMkdir('source'));
$('mkdir-destination').onclick = () => guard(() => handleMkdir('destination'));

async function handleDelete(side, targetPath, isDir) {
  if (!confirm(`Vuoi davvero eliminare ${isDir ? 'la cartella' : 'il file'} '${targetPath}'?\nQuesta operazione è irreversibile.`)) return;
  const id = $(side + '-remote').value;
  toast('Eliminazione in corso...');
  try {
    const job = await post('/jobs', { operation: 'delete', source_id: id, source_path: targetPath, is_file: !isDir, confirm_delete: true });
    let data;
    for (let i = 0; i < 30; i++) {
      await new Promise(r => setTimeout(r, 1000));
      data = await api('/jobs/' + job.id);
      if (['completed', 'failed', 'cancelled'].includes(data.status)) break;
    }
    if (data.status === 'completed') {
      toast('Eliminato con successo.');
      logEvent('info', `File eliminato: '${targetPath}'`);
      await browse(side);
    } else {
      toast('Errore: ' + (data.error || 'Eliminazione fallita'));
    }
  } catch (err) {
    toast(err.message);
  }
}

function transferSpec() {
  const op = $('operation').value;
  return {
    source_id: $('source-remote').value,
    destination_id: $('destination-remote').value,
    source_path: $('source-path').value,
    destination_path: $('destination-path').value,
    operation: op,
    is_file: ['sync', 'bisync'].includes(op) ? false : $('is-file').checked,
    priority: Number($('priority').value)
  };
}

$('operation').onchange = () => {
  const isSync = ['sync', 'bisync'].includes($('operation').value);
  $('is-file').disabled = isSync;
  if (isSync) $('is-file').checked = false;
};

$('transfer-form').onsubmit = e => {
  e.preventDefault();
  guard(async () => {
    const data = transferSpec();
    if (!data.source_id || !data.destination_id) throw new Error('Scegli sia sorgente che destinazione');
    if (data.operation === 'move') {
      if (!confirm('Spostare i file? Gli originali trasferiti con successo saranno rimossi dalla sorgente.')) return;
      data.confirm_move = true;
    }
    await post('/jobs', data);
    toast('Trasferimento in coda. Puoi chiudere il browser.');
    logEvent('info', `Nuovo trasferimento avviato (${data.operation})`);
    await page('overview');
    await refresh();
  });
};

$('schedule-form').onsubmit = e => {
  e.preventDefault();
  guard(async () => {
    const job = transferSpec();
    if (!job.source_id || !job.destination_id) throw new Error('Scegli sorgente e destinazione per la sincronizzazione');
    if (job.operation === 'move') job.operation = 'sync';
    const intervalSec = Number($('schedule-interval').value || 60);
    await post('/schedules', { job, interval_seconds: intervalSec });
    toast('Sincronizzazione continua attivata e sempre in esecuzione.');
    logEvent('success', `Sincronizzazione continua registrata (ogni ${intervalSec}s)`);
    await page('schedules');
  });
};

// Schedules / Sincronizzazioni Page
async function loadSchedules() {
  const data = await api('/schedules'), root = $('schedules');
  root.replaceChildren();
  if (!data.length) return empty(root, 'Nessuna sincronizzazione continua attiva', 'Configura un processo sempre attivo o periodico dalla pagina Esplora.');

  for (const s of data) {
    const card = el('article', 'job');
    const isBi = s.template?.operation === 'bisync';
    const arrow = isBi ? '⇄' : (s.template?.operation === 'sync' ? '➔' : '◷');
    card.append(el('div', 'job-icon', arrow));

    const text = el('div');
    const title = remoteName(s.template?.source_id) + ' ' + (isBi ? '⇄' : '➔') + ' ' + remoteName(s.template?.destination_id);
    text.append(el('h3', '', title));
    text.append(el('p', 'job-paths', `${s.template?.source_path || '/'} ${arrow} ${s.template?.destination_path || '/'}`));

    const freq = s.interval_seconds < 60
      ? `Continuo ogni ${s.interval_seconds}s`
      : (s.interval_seconds === 60 ? 'Continuo ogni minuto' : `Ogni ${s.interval_seconds / 60} minuti`);
    const opName = operationLabels[s.template?.operation] || s.template?.operation || 'Sincronizzazione';
    const nextText = s.enabled ? `Prossimo ciclo: ${new Date(s.next_run * 1000).toLocaleTimeString('it-IT')}` : 'In pausa';

    text.append(el('p', 'muted', `${opName} · ${freq} · ${nextText}`));
    card.append(text);

    const actions = el('div', 'job-actions');
    actions.append(el('span', 'badge ' + (s.enabled ? 'running' : 'cancelled'), s.enabled ? '🟢 Sempre Attivo' : '⏸ In Pausa'));
    actions.append(button('Sincronizza ora', async () => {
      await post('/schedules/' + s.id + '/run');
      toast('Sincronizzazione avviata immediatamente.');
      logEvent('info', `Sync manuale attivato per ${title}`);
      await refresh();
      await page('overview');
    }, 'primary'));
    actions.append(button(s.enabled ? 'Pausa' : 'Riprendi', async () => {
      await post('/schedules/' + s.id + '/toggle');
      toast(s.enabled ? 'Sincronizzazione messa in pausa.' : 'Sincronizzazione riattivata.');
      await loadSchedules();
    }, 'secondary'));
    actions.append(button('Elimina', async () => {
      if (confirm('Eliminare definitivamente questa sincronizzazione continua?')) {
        await api('/schedules/' + s.id, { method: 'DELETE' });
        toast('Sincronizzazione eliminata.');
        logEvent('warn', `Sync eliminato per ${title}`);
        await loadSchedules();
      }
    }, 'quiet'));

    card.append(actions);
    root.append(card);
  }
}

// Admin Command Center
let adminJobOffset = 0;

async function loadAdmin(full = true) {
  if (!state.user?.admin) return;
  const data = await api('/admin/cluster');
  state.cluster = data;
  state.clusterNodes = data.nodes;

  if (full) {
    $('global-bandwidth').value = Math.round(data.global_bps / 1048576);
    $('max-active').value = data.max_active_jobs;
  }

  // Update Hero Metrics
  const onlineCount = data.online_nodes ?? data.nodes.filter(n => n.online).length;
  const totalNodesCount = data.total_nodes ?? data.nodes.length;
  const onlinePct = totalNodesCount > 0 ? Math.round((onlineCount / totalNodesCount) * 100) : 0;
  if ($('admin-metric-nodes')) $('admin-metric-nodes').textContent = `${onlineCount} / ${totalNodesCount}`;
  if ($('admin-metric-nodes-sub')) $('admin-metric-nodes-sub').textContent = `${onlineCount} nodi operativi (${onlinePct}%)`;
  if ($('admin-metric-nodes-badge')) $('admin-metric-nodes-badge').textContent = `${onlinePct}% Online`;
  if ($('admin-bar-nodes')) $('admin-bar-nodes').style.width = `${onlinePct}%`;

  const totalSlots = data.total_active_slots || 1;
  const usedSlots = data.used_slots || 0;
  const slotPct = Math.min(100, Math.round((usedSlots / totalSlots) * 100));
  if ($('admin-metric-slots')) $('admin-metric-slots').textContent = `${usedSlots} / ${totalSlots}`;
  if ($('admin-metric-slots-sub')) $('admin-metric-slots-sub').textContent = `${slotPct}% slot occupati nel cluster`;
  if ($('admin-metric-slots-badge')) $('admin-metric-slots-badge').textContent = `${slotPct}%`;
  if ($('admin-bar-slots')) $('admin-bar-slots').style.width = `${slotPct}%`;

  const curBps = data.current_cluster_bps || 0;
  const globBps = data.global_bps || 1;
  const bandPct = Math.min(100, Math.round((curBps / globBps) * 100));
  if ($('admin-metric-bandwidth')) $('admin-metric-bandwidth').textContent = bytes(curBps) + '/s';
  if ($('admin-metric-bandwidth-sub')) $('admin-metric-bandwidth-sub').textContent = `Limite: ${Math.round(globBps / 1048576)} MiB/s`;
  if ($('admin-metric-bandwidth-badge')) $('admin-metric-bandwidth-badge').textContent = `${bandPct}%`;
  if ($('admin-bar-bandwidth')) $('admin-bar-bandwidth').style.width = `${bandPct}%`;

  const successRate = data.success_rate ?? 100;
  if ($('admin-metric-health')) $('admin-metric-health').textContent = `${successRate}%`;
  if ($('admin-metric-health-sub')) $('admin-metric-health-sub').textContent = `${data.completed_jobs || 0} completati · ${data.failed_jobs || 0} falliti`;
  if ($('admin-bar-health')) $('admin-bar-health').style.width = `${successRate}%`;

  // Render Nodes Grid
  const nodes = $('nodes');
  nodes.replaceChildren();
  for (const n of data.nodes) {
    const card = el('article', 'remote-card');
    const isOnline = n.online ?? (Date.now() / 1000 - n.last_seen < 25 && n.enabled);
    const lastSeenSec = Math.max(0, Math.round(Date.now() / 1000 - n.last_seen));
    const slotUsage = n.slots_used_pct ?? Math.round(((n.active_jobs || 0) / n.slots) * 100);

    card.append(
      el('div', 'remote-icon', '⌘'),
      el('h3', '', n.id),
      el('div', 'kpi-sub', isOnline ? `🟢 Online (${lastSeenSec}s fa)` : (!n.enabled ? '🟡 In Manutenzione' : '🔴 Non connesso'))
    );

    const meters = el('div', 'node-stats-bar');
    meters.append(
      el('span', '', `Slot: ${n.active_jobs || 0} / ${n.slots} occupati (${slotUsage}%)`),
      el('div', 'node-meter').appendChild(el('div', 'node-meter-fill', '')).parentNode,
      el('span', '', `Banda allocata: ${bytes(n.bandwidth_bps)}/s`)
    );
    meters.querySelector('.node-meter-fill').style.width = `${slotUsage}%`;
    card.append(meters);

    const btnToolbar = el('div', 'job-actions');
    btnToolbar.append(
      button('Modifica limiti', () => openEditNode(n), 'secondary'),
      button(n.enabled ? 'Pausa' : 'Riattiva', async () => {
        await api('/admin/nodes/' + n.id, {
          method: 'PATCH',
          body: JSON.stringify({ slots: n.slots, bandwidth_bps: n.bandwidth_bps, enabled: !n.enabled })
        });
        toast(n.enabled ? 'Nodo posto in manutenzione' : 'Nodo riattivato nel cluster');
        await loadAdmin();
      }, 'secondary'),
      button('Rigenera token', async () => {
        if (confirm(`Rigenerare il token di sicurezza per il nodo ${n.id}?`)) {
          const res = await post('/admin/nodes/' + n.id + '/token');
          alert(`Nuovo WORKER_TOKEN per ${n.id}:\n\n${res.token}\n\nAggiornalo nel file .env del worker.`);
          await loadAdmin();
        }
      }, 'quiet'),
      button('Revoca', async () => {
        if (confirm(`Revocare definitivamente il nodo ${n.id}? I lavori attivi saranno migrati su altri nodi.`)) {
          await api('/admin/nodes/' + n.id, { method: 'DELETE' });
          logEvent('warn', `Nodo revocato: '${n.id}'`);
          await loadAdmin();
        }
      }, 'quiet')
    );
    card.append(btnToolbar);
    nodes.append(card);
  }

  await loadAdminJobs();
  if (!full) return;

  // Render Users Stack
  const users = $('users');
  users.replaceChildren();
  for (const u of data.users) {
    const row = el('div', 'panel user-row');
    const uInfo = el('div', 'user-info');
    const uTitle = el('h3', '', u.username);
    if (u.admin) uTitle.append(el('span', 'badge running', 'ADMIN'));
    uInfo.append(uTitle);

    const uMeta = el('div', 'user-meta');
    uMeta.append(
      el('span', 'meta-pill', `Peso: ${u.weight}`),
      el('span', 'meta-pill', `Max: ${u.max_jobs} slot`),
      el('span', 'meta-pill', `Quota: ${u.bandwidth_bps ? bytes(u.bandwidth_bps) + '/s' : 'Illimitata'}`),
      el('span', 'badge ' + (u.enabled ? 'completed' : 'cancelled'), u.enabled ? 'Attivo' : 'Sospeso')
    );

    const uActions = el('div', 'user-actions');
    const editBtn = button('Modifica', () => openEditUser(u), 'secondary');
    uActions.append(editBtn);

    row.append(uInfo, uMeta, uActions);
    users.append(row);
  }

  renderEvents();
}

function openEditNode(node) {
  $('edit-node-id').value = node.id;
  $('edit-node-name').textContent = `(${node.id})`;
  $('edit-node-slots').value = node.slots;
  $('edit-node-bandwidth').value = Math.round(node.bandwidth_bps / 1048576);
  $('edit-node-enabled').checked = node.enabled;
  $('edit-node-dialog').showModal();
}

$('edit-node-form').onsubmit = e => {
  e.preventDefault();
  guard(async () => {
    const id = $('edit-node-id').value;
    const slots = Number($('edit-node-slots').value);
    const bandwidth_bps = Math.round(Number($('edit-node-bandwidth').value) * 1048576);
    const enabled = $('edit-node-enabled').checked;

    await api('/admin/nodes/' + id, {
      method: 'PATCH',
      body: JSON.stringify({ slots, bandwidth_bps, enabled })
    });
    $('edit-node-dialog').close();
    toast('Parametri del nodo aggiornati con successo.');
    logEvent('info', `Limiti aggiornati per il nodo '${id}'`);
    await loadAdmin();
  });
};

function openEditUser(u) {
  $('edit-user-id').value = u.id;
  $('edit-user-name').textContent = `(${u.username})`;
  $('edit-user-weight').value = u.weight;
  $('edit-user-slots').value = u.max_jobs;
  $('edit-user-band').value = Math.round((u.bandwidth_bps || 0) / 1048576);
  $('edit-user-password').value = '';
  $('edit-user-admin').checked = u.admin;
  $('edit-user-enabled').checked = u.enabled;
  $('edit-user-dialog').showModal();
}

$('edit-user-form').onsubmit = e => {
  e.preventDefault();
  guard(async () => {
    const id = $('edit-user-id').value;
    const weight = Number($('edit-user-weight').value);
    const max_jobs = Number($('edit-user-slots').value);
    const bandwidth_bps = Math.round(Number($('edit-user-band').value) * 1048576);
    const admin = $('edit-user-admin').checked;
    const enabled = $('edit-user-enabled').checked;
    const password = $('edit-user-password').value || null;

    const payload = { weight, max_jobs, bandwidth_bps, admin, enabled };
    if (password) payload.password = password;

    await api('/admin/users/' + id, {
      method: 'PATCH',
      body: JSON.stringify(payload)
    });
    $('edit-user-dialog').close();
    toast('Profilo utente e autorizzazioni aggiornati.');
    logEvent('info', `Policy utente aggiornata per ID ${id}`);
    await loadAdmin();
  });
};

$('cluster-form').onsubmit = e => {
  e.preventDefault();
  guard(async () => {
    await api('/admin/cluster', {
      method: 'PATCH',
      body: JSON.stringify({
        global_bps: Math.round(Number($('global-bandwidth').value) * 1048576),
        max_active_jobs: Number($('max-active').value)
      })
    });
    toast('Limiti globali del cluster aggiornati');
    logEvent('info', 'Limiti globali di rete aggiornati');
    await loadAdmin();
  });
};

$('add-node').onclick = () => {
  $('node-form').reset();
  $('node-token').hidden = true;
  document.querySelector('#node-token textarea').value = '';
  $('node-dialog').showModal();
};

$('node-form').onsubmit = e => {
  e.preventDefault();
  guard(async () => {
    const f = e.target.elements;
    const data = await post('/admin/nodes', {
      name: f.name.value,
      slots: Number(f.slots.value),
      bandwidth_bps: Math.round(Number(f.bandwidth.value) * 1048576)
    });
    $('node-token').hidden = false;
    document.querySelector('#node-token textarea').value = data.token;
    logEvent('success', `Nuovo nodo registrato: '${f.name.value}'`);
    await loadAdmin();
  });
};

async function loadAdminJobs() {
  const filter = $('admin-job-filter').value;
  const data = await api('/admin/jobs?offset=' + adminJobOffset + '&status=' + encodeURIComponent(filter));
  const root = $('admin-jobs');
  if (root.contains(document.activeElement)) return;
  root.replaceChildren();

  if (!data.items.length) {
    empty(root, 'Nessun processo trovato', 'Non ci sono trasferimenti per questo filtro.');
    return;
  }

  for (const j of data.items) {
    const row = el('article', 'job');
    const info = el('div');
    info.append(
      el('h3', '', `${j.username} · ${operationLabels[j.operation] || j.operation} · ID: ${j.id.slice(0, 8)}`),
      el('p', 'job-paths', `${j.source_path || '/'} ➔ ${j.destination_path || '/'}`),
      el('p', '', `${labels[j.status]} · Nodo: ${j.node_id || 'in attesa'}`),
      el('p', 'muted', `${bytes(j.stats?.bytes || 0)} trasferiti · Velocità: ${bytes(j.stats?.speed || 0)}/s · Quota: ${bytes(j.assigned_bps)}/s`)
    );
    if (j.error) info.append(el('p', 'error', j.error));

    const actions = el('div', 'job-actions');
    if (['queued', 'running'].includes(j.status)) {
      const select = el('select');
      select.setAttribute('aria-label', 'Priorità processo ' + j.id);
      ['Bassa', 'Normale', 'Alta'].forEach((l, i) => select.add(new Option(l, String(i))));
      select.value = String(j.priority);

      const nodeSelect = el('select');
      nodeSelect.setAttribute('aria-label', 'Sposta nodo per ' + j.id);
      nodeSelect.add(new Option('Auto (qualsiasi nodo)', ''));
      (state.clusterNodes || []).filter(n => n.enabled).forEach(n => {
        const isCur = j.node_id === n.id;
        nodeSelect.add(new Option('Nodo: ' + n.id + (isCur ? ' (in uso)' : ''), n.id));
      });
      if (j.node_id) nodeSelect.value = j.node_id;

      actions.append(
        select,
        button('Salva priorità', async () => {
          await api('/admin/jobs/' + j.id, { method: 'PATCH', body: JSON.stringify({ priority: Number(select.value) }) });
          toast('Priorità aggiornata.');
        }),
        nodeSelect,
        button('Sposta nodo ⇄', async () => {
          const target = nodeSelect.value || null;
          if (j.status === 'running' && target === j.node_id) {
            toast('Il lavoro è già in esecuzione sul nodo ' + target);
            return;
          }
          await post('/admin/jobs/' + j.id + '/reassign', { node_id: target });
          toast(`Lavoro migrato a ${target ? 'nodo ' + target : 'coda automatica'}. Il trasferimento riprende dal checkpoint senza ripartire da zero.`);
          logEvent('info', `Migrazione a caldo del lavoro ${j.id.slice(0, 8)} su ${target || 'coda'}`);
          await loadAdminJobs();
          await refresh();
        }, 'primary'),
        button('Interrompi', async () => {
          if (confirm('Interrompere questo processo di ' + j.username + '?')) {
            await post('/admin/jobs/' + j.id + '/cancel');
            toast('Processo interrotto.');
            logEvent('warn', `Processo ${j.id.slice(0, 8)} interrotto da admin`);
            await loadAdminJobs();
          }
        }, 'quiet')
      );
    }
    row.append(info, actions);
    root.append(row);
  }

  $('admin-jobs-prev').disabled = adminJobOffset === 0;
  $('admin-jobs-next').disabled = !data.has_more;
  $('admin-jobs-page').textContent = 'Pagina ' + (adminJobOffset / 100 + 1);
}

$('admin-job-filter').onchange = () => {
  adminJobOffset = 0;
  guard(loadAdminJobs);
};
$('admin-jobs-prev').onclick = () => {
  adminJobOffset = Math.max(0, adminJobOffset - 100);
  guard(loadAdminJobs);
};
$('admin-jobs-next').onclick = () => {
  adminJobOffset += 100;
  guard(loadAdminJobs);
};

// Apple iCloud 2FA Verification
async function startApple(remoteId) {
  const job = await post('/remotes/' + remoteId + '/connect');
  watchApple(job.id);
}

function watchApple(jobId) {
  appleJob = jobId;
  appleChallenge = null;
  clearInterval(appleTimer);
  $('apple-status').textContent = 'In attesa del worker. La connessione usa Apple ID e 2FA.';
  $('apple-answer-label').hidden = true;
  $('apple-submit').hidden = true;
  $('apple-dialog').showModal();
  guard(pollApple);
  appleTimer = setInterval(() => guard(pollApple), 2000);
}

async function pollApple() {
  if (!$('apple-dialog').open) {
    clearInterval(appleTimer);
    return;
  }
  const job = await api('/jobs/' + appleJob);
  if (['completed', 'failed', 'cancelled'].includes(job.status)) {
    clearInterval(appleTimer);
    $('apple-status').textContent = job.status === 'completed'
      ? 'iCloud Drive collegato con successo. Puoi esplorare e trasferire i tuoi file.'
      : (job.error || 'Connessione annullata');
    $('apple-answer-label').hidden = true;
    $('apple-submit').hidden = true;
    $('apple-examples').replaceChildren();
    await refresh();
    return;
  }
  const c = job.result?.challenge;
  if (c && c.id !== appleChallenge) {
    appleChallenge = c.id;
    $('apple-status').textContent = c.help;
    $('apple-answer').value = '';
    $('apple-answer-label').hidden = false;
    $('apple-submit').hidden = false;
    $('apple-submit').disabled = false;
    $('apple-examples').replaceChildren();
    for (const example of c.examples || []) {
      $('apple-examples').append(button(example.label || example.value, () => sendApple(example.value), 'secondary'));
    }
  } else if (!c) {
    appleChallenge = null;
    $('apple-status').textContent = job.status === 'queued'
      ? 'Connessione in coda: attendo un worker disponibile…'
      : 'Verifica Apple in corso…';
    $('apple-answer-label').hidden = true;
    $('apple-submit').hidden = true;
    $('apple-examples').replaceChildren();
  }
}

async function sendApple(answer) {
  $('apple-submit').disabled = true;
  try {
    await post('/jobs/' + appleJob + '/answer', { challenge_id: appleChallenge, answer });
    $('apple-answer').value = '';
    $('apple-status').textContent = 'Risposta inviata ad Apple. In attesa di convalida…';
  } catch (e) {
    $('apple-submit').disabled = false;
    throw e;
  }
}

$('apple-form').onsubmit = e => {
  e.preventDefault();
  guard(() => sendApple($('apple-answer').value));
};
$('apple-cancel').onclick = () => guard(async () => {
  if (appleJob) await post('/jobs/' + appleJob + '/cancel');
  $('apple-dialog').close();
  clearInterval(appleTimer);
});

// Initial boot
const googleResult = new URLSearchParams(location.search).get('google');
if (googleResult) {
  history.replaceState({}, '', location.pathname);
  toast({
    connected: 'Google Drive collegato con successo.',
    cancelled: 'Autorizzazione Google annullata.',
    expired: 'Richiesta Google scaduta. Riprova da Collegamenti.',
    failed: 'Google non ha completato l’accesso.'
  }[googleResult] || 'Accesso Google terminato.');
}

try {
  const options = await api('/auth/options');
  $('register').hidden = !options.registration;
  await enter();
  if (googleResult === 'connected') await page('remotes');
} catch {
  // Login is the initial screen when no session exists
}
