/**
 * CloudSync Frontend Application Logic
 * Minimalist, reactive SPA for Google Drive to iCloud Drive file moving & copying.
 */

// Application State
const state = {
  gdrivePath: "",
  icloudPath: "",
  gdriveItems: [],
  icloudItems: [],
  selectedItems: new Map(), // path -> { path, is_dir, size, name }
  remotes: { gdrive: false, icloud: false },
  deleteSource: true, // true = Sposta (Move), false = Copia (Copy)
  ws: null,
  sse: null,
  activeJobs: [],
  tunnelUrl: null,
};

// Utilities
function formatBytes(bytes, decimals = 1) {
  if (!bytes || bytes === 0) return "0 B";
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + " " + sizes[i];
}

function formatSpeed(bytesPerSec) {
  if (!bytesPerSec || bytesPerSec === 0) return "0.0";
  const mb = bytesPerSec / (1024 * 1024);
  return mb.toFixed(1);
}

function formatSeconds(sec) {
  if (!sec || isNaN(sec) || sec <= 0) return "--";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

function logMessage(msg, type = "info") {
  const consoleEl = document.getElementById("logs-console");
  if (!consoleEl) return;
  const time = new Date().toTimeString().split(" ")[0];
  const row = document.createElement("div");

  let colorClass = "text-slate-400";
  if (type === "success") colorClass = "text-emerald-400 font-medium";
  if (type === "error") colorClass = "text-rose-400 font-medium";
  if (type === "warn") colorClass = "text-amber-400";

  row.className = colorClass;
  row.innerHTML = `<span class="text-slate-600">[${time}]</span> ${msg}`;
  consoleEl.appendChild(row);
  consoleEl.scrollTop = consoleEl.scrollHeight;
}

// Icon Picker based on extension and mime type
function getFileIcon(item) {
  if (item.is_dir) {
    return `<svg class="w-4 h-4 text-amber-400 shrink-0" fill="currentColor" viewBox="0 0 20 20"><path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z"/></svg>`;
  }
  if (item.is_gdoc) {
    return `<svg class="w-4 h-4 text-blue-400 shrink-0" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4zm2 6a1 1 0 011-1h6a1 1 0 110 2H7a1 1 0 01-1-1zm1 3a1 1 0 100 2h6a1 1 0 100-2H7z" clip-rule="evenodd"/></svg>`;
  }
  const ext = item.name.split(".").pop().toLowerCase();
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) {
    return `<svg class="w-4 h-4 text-emerald-400 shrink-0" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M4 3a2 2 0 00-2 2v10a2 2 0 002 2h12a2 2 0 002-2V5a2 2 0 00-2-2H4zm12 12H4l4-8 3 6 2-4 3 6z" clip-rule="evenodd"/></svg>`;
  }
  if (["zip", "tar", "gz", "rar", "7z"].includes(ext)) {
    return `<svg class="w-4 h-4 text-purple-400 shrink-0" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M4 4a2 2 0 012-2h8a2 2 0 012 2v12a2 2 0 01-2 2H6a2 2 0 01-2-2V4zm3 1h2v2H7V5zm2 3H7v2h2V8zm-2 3h2v2H7v-2zm2 3H7v2h2v-2z" clip-rule="evenodd"/></svg>`;
  }
  return `<svg class="w-4 h-4 text-slate-400 shrink-0" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" clip-rule="evenodd"/></svg>`;
}

// ---------------------------------------------------------------------------
// Cloudflare Tunnel Detection
// ---------------------------------------------------------------------------

async function checkTunnelStatus() {
  try {
    const res = await fetch("/api/system/tunnel");
    const data = await res.json();
    const badge = document.getElementById("tunnel-badge");
    const link = document.getElementById("tunnel-link");

    if (data.active && data.url) {
      state.tunnelUrl = data.url;
      link.href = data.url;
      link.textContent = data.url.replace("https://", "");
      badge.classList.remove("hidden");
      badge.classList.add("flex");
      logMessage(`Link pubblico Cloudflare attivo: ${data.url}`, "success");
    } else {
      badge.classList.add("hidden");
      badge.classList.remove("flex");
    }
  } catch (err) {
    console.debug("Could not check tunnel:", err);
  }
}

// ---------------------------------------------------------------------------
// Remotes Verification & Unauthenticated State Handling
// ---------------------------------------------------------------------------

async function checkRemotesStatus() {
  try {
    const res = await fetch("/api/remotes/status");
    const data = await res.json();

    const dotGdrive = document.getElementById("dot-gdrive");
    const infoGdrive = document.getElementById("info-gdrive");

    const dotIcloud = document.getElementById("dot-icloud");
    const infoIcloud = document.getElementById("info-icloud");

    const viewConnGdrive = document.getElementById("view-connected-gdrive");
    const viewUnauthGdrive = document.getElementById("view-unauth-gdrive");
    const btnSelectAllGdrive = document.getElementById("btn-select-all-gdrive");
    const btnDiscGdrive = document.getElementById("btn-disconnect-gdrive");

    const viewConnIcloud = document.getElementById("view-connected-icloud");
    const viewUnauthIcloud = document.getElementById("view-unauth-icloud");
    const btnNewFolderIcloud = document.getElementById("btn-new-folder-icloud");
    const btnDiscIcloud = document.getElementById("btn-disconnect-icloud");

    state.remotes.gdrive = Boolean(data.gdrive_configured && data.gdrive?.connected);
    state.remotes.icloud = Boolean(data.icloud_configured && data.icloud?.connected);

    // Update GDrive status pill & column view
    if (state.remotes.gdrive) {
      dotGdrive.className = "w-2 h-2 rounded-full bg-emerald-400";
      const used = data.gdrive?.used_bytes ? formatBytes(data.gdrive.used_bytes) : "Connesso";
      infoGdrive.textContent = used;
      infoGdrive.className = "text-emerald-400 font-medium text-[11px]";

      viewConnGdrive.classList.remove("hidden");
      viewUnauthGdrive.classList.add("hidden");
      btnSelectAllGdrive.classList.remove("hidden");
      btnDiscGdrive.classList.remove("hidden");

      loadGDrive(state.gdrivePath);
    } else {
      dotGdrive.className = "w-2 h-2 rounded-full bg-amber-400";
      infoGdrive.textContent = "Non connesso";
      infoGdrive.className = "text-amber-400 font-medium text-[11px]";

      viewConnGdrive.classList.add("hidden");
      viewUnauthGdrive.classList.remove("hidden");
      btnSelectAllGdrive.classList.add("hidden");
      btnDiscGdrive.classList.add("hidden");

      state.gdriveItems = [];
      state.selectedItems.clear();
      updateSelectionUI();
    }

    // Update iCloud status pill & column view
    if (state.remotes.icloud) {
      dotIcloud.className = "w-2 h-2 rounded-full bg-emerald-400";
      const info = data.icloud?.used_bytes ? formatBytes(data.icloud.used_bytes) : "Connesso";
      infoIcloud.textContent = info;
      infoIcloud.className = "text-emerald-400 font-medium text-[11px]";

      viewConnIcloud.classList.remove("hidden");
      viewUnauthIcloud.classList.add("hidden");
      btnNewFolderIcloud.classList.remove("hidden");
      btnDiscIcloud.classList.remove("hidden");

      loadICloud(state.icloudPath);
    } else {
      dotIcloud.className = "w-2 h-2 rounded-full bg-cyan-400";
      infoIcloud.textContent = "Non configurato";
      infoIcloud.className = "text-cyan-400 font-medium text-[11px]";

      viewConnIcloud.classList.add("hidden");
      viewUnauthIcloud.classList.remove("hidden");
      btnNewFolderIcloud.classList.add("hidden");
      btnDiscIcloud.classList.add("hidden");

      state.icloudItems = [];
    }

    logMessage(`Stato account: Google Drive=${state.remotes.gdrive ? "Connesso" : "Disconnesso"}, iCloud=${state.remotes.icloud ? "Connesso" : "Non configurato"}`);
  } catch (err) {
    logMessage("Impossibile verificare lo stato dei remoti: " + err.message, "error");
  }
}

// ---------------------------------------------------------------------------
// Transfer Mode Selector: Move vs Copy
// ---------------------------------------------------------------------------

function setTransferMode(isMove) {
  state.deleteSource = isMove;

  const btnMove = document.getElementById("btn-mode-move");
  const btnCopy = document.getElementById("btn-mode-copy");
  const containerDeleteEmpty = document.getElementById("container-delete-empty");
  const btnTrigger = document.getElementById("btn-trigger-move");
  const btnTriggerText = document.getElementById("btn-trigger-text");

  if (isMove) {
    // Mode MOVE
    btnMove.className = "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/30 transition shadow-sm";
    btnCopy.className = "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-slate-400 hover:text-slate-200 transition";

    containerDeleteEmpty.classList.remove("hidden");
    btnTriggerText.textContent = "Sposta su iCloud Drive (Elimina da Google)";
    btnTrigger.classList.remove("bg-blue-600", "hover:bg-blue-500", "shadow-blue-900/30");
    btnTrigger.classList.add("bg-amber-600", "hover:bg-amber-500", "shadow-amber-900/30");
    logMessage("Modalità impostata: SPOSTAMENTO (i file sorgente verranno cancellati dopo il completamento)");
  } else {
    // Mode COPY
    btnMove.className = "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-slate-400 hover:text-slate-200 transition";
    btnCopy.className = "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-blue-500/20 text-blue-300 border border-blue-500/30 transition shadow-sm";

    containerDeleteEmpty.classList.add("hidden");
    btnTriggerText.textContent = "Copia su iCloud Drive (Mantieni su Google)";
    btnTrigger.classList.remove("bg-amber-600", "hover:bg-amber-500", "shadow-amber-900/30");
    btnTrigger.classList.add("bg-blue-600", "hover:bg-blue-500", "shadow-blue-900/30");
    logMessage("Modalità impostata: COPIA (i file sorgente verranno CONSERVATI intatti)");
  }
}

// ---------------------------------------------------------------------------
// File Explorers & Navigation
// ---------------------------------------------------------------------------

async function loadGDrive(path = "") {
  if (!state.remotes.gdrive) return;
  state.gdrivePath = path;
  const loading = document.getElementById("loading-gdrive");
  const empty = document.getElementById("empty-gdrive");
  const tbody = document.getElementById("file-list-gdrive");

  loading.classList.remove("hidden");
  renderBreadcrumbs("gdrive", path);

  try {
    const res = await fetch("/api/fs/list", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ remote: "gdrive", path }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.details || err.error || "Errore nella lettura dei file");
    }

    const data = await res.json();
    state.gdriveItems = data.items || [];
    renderGDriveTable();
  } catch (err) {
    logMessage(`Errore caricamento Google Drive [${path}]: ${err.message}`, "error");
    tbody.innerHTML = `<tr><td colspan="3" class="p-6 text-center text-rose-400 text-xs">Errore nel caricamento: ${err.message}</td></tr>`;
  } finally {
    loading.classList.add("hidden");
  }
}

function renderGDriveTable() {
  const tbody = document.getElementById("file-list-gdrive");
  const empty = document.getElementById("empty-gdrive");
  const searchTerm = (document.getElementById("search-gdrive").value || "").toLowerCase();

  tbody.innerHTML = "";
  const filtered = state.gdriveItems.filter((i) => i.name.toLowerCase().includes(searchTerm));

  if (filtered.length === 0) {
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");

  filtered.forEach((item) => {
    const isSelected = state.selectedItems.has(item.path);
    const tr = document.createElement("tr");
    tr.className = `hover:bg-slate-800/40 transition cursor-pointer select-none ${isSelected ? "bg-blue-950/30" : ""}`;

    // Google Docs badge
    let gdocBadge = "";
    if (item.is_gdoc) {
      gdocBadge = `<span class="ml-2 text-[9px] font-mono bg-blue-500/20 text-blue-300 border border-blue-500/30 px-1.5 py-0.2 rounded" title="Verrà esportato automaticamente in formato Office compatibile">DOCX</span>`;
    }

    tr.innerHTML = `
      <td class="py-2 px-3 text-center">
        <input type="checkbox" data-path="${item.path}" class="rounded bg-slate-800 border-slate-700 text-blue-500 focus:ring-0 item-checkbox" ${isSelected ? "checked" : ""}>
      </td>
      <td class="py-2 px-2 flex items-center gap-2 truncate">
        ${getFileIcon(item)}
        <span class="truncate font-medium text-slate-200 hover:text-white item-name">${item.name}</span>
        ${gdocBadge}
      </td>
      <td class="py-2 px-3 text-right text-slate-400 font-mono text-[11px]">
        ${item.is_dir ? "—" : formatBytes(item.size)}
      </td>
    `;

    // Click checkbox toggles selection
    const chk = tr.querySelector(".item-checkbox");
    chk.addEventListener("change", (e) => {
      e.stopPropagation();
      toggleItemSelection(item, chk.checked);
    });

    // Double-click or click on directory row navigates inside
    tr.addEventListener("click", (e) => {
      if (e.target.tagName === "INPUT") return;
      if (item.is_dir) {
        loadGDrive(item.path);
      } else {
        const next = !state.selectedItems.has(item.path);
        chk.checked = next;
        toggleItemSelection(item, next);
      }
    });

    tbody.appendChild(tr);
  });

  updateSelectionUI();
}

async function loadICloud(path = "") {
  if (!state.remotes.icloud) return;
  state.icloudPath = path;
  const loading = document.getElementById("loading-icloud");
  const empty = document.getElementById("empty-icloud");
  const tbody = document.getElementById("file-list-icloud");
  const targetSummary = document.getElementById("target-summary");

  loading.classList.remove("hidden");
  renderBreadcrumbs("icloud", path);
  targetSummary.textContent = `Destinazione: /${path}`;

  try {
    const res = await fetch("/api/fs/list", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ remote: "icloud", path }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.details || err.error || "Errore nella lettura dei file");
    }

    const data = await res.json();
    state.icloudItems = data.items || [];
    renderICloudTable();
  } catch (err) {
    logMessage(`Errore caricamento iCloud Drive [${path}]: ${err.message}`, "error");
    tbody.innerHTML = `<tr><td colspan="2" class="p-6 text-center text-rose-400 text-xs">Errore nel caricamento: ${err.message}</td></tr>`;
  } finally {
    loading.classList.add("hidden");
  }
}

function renderICloudTable() {
  const tbody = document.getElementById("file-list-icloud");
  const empty = document.getElementById("empty-icloud");

  tbody.innerHTML = "";
  if (state.icloudItems.length === 0) {
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");

  state.icloudItems.forEach((item) => {
    const tr = document.createElement("tr");
    tr.className = "hover:bg-slate-800/40 transition cursor-pointer select-none";

    tr.innerHTML = `
      <td class="py-2 px-3 flex items-center gap-2 truncate">
        ${getFileIcon(item)}
        <span class="truncate font-medium text-slate-200 hover:text-white">${item.name}</span>
      </td>
      <td class="py-2 px-3 text-right text-slate-400 text-[11px]">
        ${item.is_dir ? "Cartella" : formatBytes(item.size)}
      </td>
    `;

    // Double-click to browse deeper into iCloud destination
    tr.addEventListener("click", () => {
      if (item.is_dir) {
        loadICloud(item.path);
      }
    });

    tbody.appendChild(tr);
  });
}

// Breadcrumb generator
function renderBreadcrumbs(remote, path) {
  const container = document.getElementById(`breadcrumbs-${remote}`);
  if (!container) return;
  container.innerHTML = "";

  const rootSpan = document.createElement("span");
  rootSpan.className = "hover:text-white cursor-pointer px-1 py-0.5 rounded hover:bg-slate-800 transition font-bold text-slate-300";
  rootSpan.textContent = remote === "gdrive" ? "gdrive:/" : "icloud:/";
  rootSpan.addEventListener("click", () => {
    if (remote === "gdrive") loadGDrive("");
    else loadICloud("");
  });
  container.appendChild(rootSpan);

  if (!path) return;

  const parts = path.split("/").filter(Boolean);
  let currentPath = "";

  parts.forEach((p, index) => {
    const sep = document.createElement("span");
    sep.className = "text-slate-600 select-none";
    sep.textContent = "/";
    container.appendChild(sep);

    currentPath += (currentPath ? "/" : "") + p;
    const targetPath = currentPath;

    const span = document.createElement("span");
    span.className = index === parts.length - 1 ? "text-slate-100 font-semibold px-1" : "hover:text-white cursor-pointer px-1 py-0.5 rounded hover:bg-slate-800 transition";
    span.textContent = p;
    if (index !== parts.length - 1) {
      span.addEventListener("click", () => {
        if (remote === "gdrive") loadGDrive(targetPath);
        else loadICloud(targetPath);
      });
    }
    container.appendChild(span);
  });
}

// ---------------------------------------------------------------------------
// Item Selection Management
// ---------------------------------------------------------------------------

function toggleItemSelection(item, isSelected) {
  if (isSelected) {
    state.selectedItems.set(item.path, item);
  } else {
    state.selectedItems.delete(item.path);
  }
  updateSelectionUI();
}

function updateSelectionUI() {
  const count = state.selectedItems.size;
  let totalBytes = 0;
  state.selectedItems.forEach((item) => {
    totalBytes += item.size || 0;
  });

  const countBadge = document.getElementById("selected-count-badge");
  const sizeBadge = document.getElementById("selected-size-badge");
  const btnTrigger = document.getElementById("btn-trigger-move");
  const masterCheckbox = document.getElementById("checkbox-master-gdrive");

  countBadge.textContent = `${count} element${count === 1 ? "o" : "i"}`;
  sizeBadge.textContent = `(${formatBytes(totalBytes)})`;
  btnTrigger.disabled = count === 0;

  // Sync individual checkboxes in view
  document.querySelectorAll("#file-list-gdrive .item-checkbox").forEach((chk) => {
    const path = chk.getAttribute("data-path");
    chk.checked = state.selectedItems.has(path);
    const row = chk.closest("tr");
    if (row) {
      if (chk.checked) row.classList.add("bg-blue-950/30");
      else row.classList.remove("bg-blue-950/30");
    }
  });

  // Sync master checkbox
  const visibleCheckboxes = document.querySelectorAll("#file-list-gdrive .item-checkbox");
  if (visibleCheckboxes.length > 0) {
    const allChecked = Array.from(visibleCheckboxes).every((c) => c.checked);
    masterCheckbox.checked = allChecked;
  } else {
    masterCheckbox.checked = false;
  }
}

// ---------------------------------------------------------------------------
// Transfer Operations (Move / Copy)
// ---------------------------------------------------------------------------

function openConfirmationModal() {
  if (state.selectedItems.size === 0) return;

  const count = state.selectedItems.size;
  const isMove = state.deleteSource;

  document.getElementById("modal-items-count").textContent = count;
  document.getElementById("modal-target-path").textContent = `icloud:/${state.icloudPath}`;

  const isDryRun = document.getElementById("toggle-dry-run").checked;
  const dryWarning = document.getElementById("modal-dry-run-warning");
  if (isDryRun) dryWarning.classList.remove("hidden");
  else dryWarning.classList.add("hidden");

  const titleEl = document.getElementById("modal-confirm-title");
  const subtitleEl = document.getElementById("modal-action-subtitle");
  const noticeEl = document.getElementById("modal-delete-notice");
  const btnProceed = document.getElementById("modal-btn-proceed");
  const iconBox = document.getElementById("modal-confirm-icon-box");

  if (isMove) {
    titleEl.textContent = "Conferma Spostamento File";
    subtitleEl.textContent = "Operazione atomica di spostamento (Move)";
    noticeEl.innerHTML = `ℹ️ I file sorgente su Google Drive verranno rimossi <strong>esclusivamente dopo</strong> il completamento e la verifica di integrità su iCloud Drive.`;
    btnProceed.textContent = "Conferma e Sposta (Elimina sorgente)";
    btnProceed.className = "px-5 py-2 rounded-xl bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold transition shadow-lg shadow-amber-900/30";
    iconBox.className = "w-10 h-10 rounded-xl bg-amber-500/10 text-amber-400 flex items-center justify-center shrink-0";
  } else {
    titleEl.textContent = "Conferma Copia File";
    subtitleEl.textContent = "Operazione di duplicazione (Copy - Conserva sorgente)";
    noticeEl.innerHTML = `ℹ️ I file originali su Google Drive <strong>non verranno toccati</strong> né eliminati. Verrà creata una copia esatta su iCloud Drive.`;
    btnProceed.textContent = "Conferma e Copia (Mantieni sorgente)";
    btnProceed.className = "px-5 py-2 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold transition shadow-lg shadow-blue-900/30";
    iconBox.className = "w-10 h-10 rounded-xl bg-blue-500/10 text-blue-400 flex items-center justify-center shrink-0";
  }

  document.getElementById("modal-confirm").classList.remove("hidden");
}

function closeConfirmationModal() {
  document.getElementById("modal-confirm").classList.add("hidden");
}

async function executeTransfer() {
  closeConfirmationModal();

  const items = Array.from(state.selectedItems.values()).map((i) => ({
    path: i.path,
    is_dir: i.is_dir,
  }));

  const payload = {
    src_remote: "gdrive",
    dst_remote: "icloud",
    dst_path: state.icloudPath,
    items: items,
    delete_source: state.deleteSource,
    export_docs: document.getElementById("toggle-export-docs").checked,
    delete_empty_src_dirs: document.getElementById("toggle-delete-empty").checked,
    dry_run: document.getElementById("toggle-dry-run").checked,
  };

  const actionVerb = state.deleteSource ? "spostamento" : "copia";
  logMessage(`Avvio job di ${actionVerb} per ${items.length} elemento/i verso icloud:/${state.icloudPath}...`);

  try {
    const res = await fetch("/api/transfer/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || err.error || "Errore durante l'avvio del trasferimento");
    }

    const data = await res.json();
    logMessage(`Operazione avviata con successo! ${data.message}`, "success");

    // Clear selection
    state.selectedItems.clear();
    updateSelectionUI();

    // Reload lists after 2s
    setTimeout(() => {
      loadGDrive(state.gdrivePath);
      loadICloud(state.icloudPath);
    }, 2000);
  } catch (err) {
    logMessage(`Errore durante il trasferimento: ${err.message}`, "error");
  }
}

// ---------------------------------------------------------------------------
// Real-Time Stats Stream (WebSocket & SSE fallback)
// ---------------------------------------------------------------------------

function initStatsStream() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws/stats`;
  const wsIndicator = document.getElementById("ws-indicator");
  const wsStatusText = document.getElementById("ws-status-text");

  try {
    state.ws = new WebSocket(wsUrl);

    state.ws.onopen = () => {
      wsIndicator.className = "w-2 h-2 rounded-full bg-emerald-400";
      wsStatusText.textContent = "Live WS";
      logMessage("Canale metriche real-time (WebSocket) collegato.", "success");
    };

    state.ws.onmessage = (event) => {
      try {
        const stats = JSON.parse(event.data);
        updateDashboardMetrics(stats);
      } catch (e) {
        console.error("Error parsing WS stats:", e);
      }
    };

    state.ws.onclose = () => {
      wsIndicator.className = "w-2 h-2 rounded-full bg-amber-400";
      wsStatusText.textContent = "Fallback SSE";
      fallbackToSSE();
    };

    state.ws.onerror = () => {
      state.ws.close();
    };
  } catch (e) {
    fallbackToSSE();
  }
}

function fallbackToSSE() {
  if (state.sse) return;
  const wsIndicator = document.getElementById("ws-indicator");
  const wsStatusText = document.getElementById("ws-status-text");

  state.sse = new EventSource("/api/stream/stats");
  state.sse.onmessage = (event) => {
    try {
      const stats = JSON.parse(event.data);
      updateDashboardMetrics(stats);
      wsIndicator.className = "w-2 h-2 rounded-full bg-emerald-400";
      wsStatusText.textContent = "Live SSE";
    } catch (e) {
      console.error("Error parsing SSE stats:", e);
    }
  };

  state.sse.onerror = () => {
    wsIndicator.className = "w-2 h-2 rounded-full bg-rose-400";
    wsStatusText.textContent = "Riconnessione...";
  };
}

function updateDashboardMetrics(stats) {
  // Speed
  document.getElementById("metric-speed").textContent = formatSpeed(stats.speed);

  // Bytes
  document.getElementById("metric-bytes").textContent = formatBytes(stats.bytes);
  document.getElementById("metric-total-bytes").textContent = `/ ${formatBytes(stats.total_bytes)}`;

  // Transfers
  document.getElementById("metric-transfers").textContent = stats.transfers || 0;
  document.getElementById("metric-total-transfers").textContent = `/ ${stats.total_transfers || 0}`;

  // Time & ETA
  document.getElementById("metric-time").textContent = formatSeconds(stats.elapsed_time);
  const etaText = stats.eta ? `(ETA: ${formatSeconds(stats.eta)})` : "(ETA: --)";
  document.getElementById("metric-eta").textContent = etaText;

  // Active jobs badge
  const activeCount = (stats.active_jobs || []).length;
  document.getElementById("active-jobs-badge").textContent = activeCount;

  // Global Progress bar
  let percent = 0;
  if (stats.total_bytes > 0) {
    percent = Math.min(100, Math.round((stats.bytes / stats.total_bytes) * 100));
  } else if (stats.total_transfers > 0) {
    percent = Math.min(100, Math.round((stats.transfers / stats.total_transfers) * 100));
  }
  document.getElementById("global-progress-percent").textContent = `${percent}%`;
  document.getElementById("global-progress-bar").style.width = `${percent}%`;

  // Transferring in-flight files list
  renderInFlightFiles(stats.transferring || []);
}

function renderInFlightFiles(files) {
  const container = document.getElementById("transferring-files-list");
  const countBadge = document.getElementById("in-flight-count");

  countBadge.textContent = `${files.length} file`;

  if (files.length === 0) {
    container.innerHTML = `<div class="p-4 text-center text-xs text-slate-500 italic">Nessun file attualmente in transito.</div>`;
    return;
  }

  container.innerHTML = "";
  files.forEach((f) => {
    const row = document.createElement("div");
    row.className = "px-3 py-2 text-xs flex flex-col gap-1 bg-slate-900/60";

    const p = f.percentage || 0;
    const speed = formatSpeed(f.speed);

    row.innerHTML = `
      <div class="flex items-center justify-between text-slate-200">
        <span class="font-medium truncate max-w-[60%]">${f.name}</span>
        <span class="font-mono text-emerald-400 text-[11px]">${speed} MB/s • ${p}%</span>
      </div>
      <div class="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
        <div class="bg-emerald-400 h-1.5 rounded-full transition-all duration-300" style="width: ${p}%"></div>
      </div>
    `;
    container.appendChild(row);
  });
}

// ---------------------------------------------------------------------------
// Google Auth Modal (Alternative Methods: Service Account, OAuth Code, Token)
// ---------------------------------------------------------------------------

function openGoogleAuthModal(defaultTab = "sa") {
  document.getElementById("modal-google-auth").classList.remove("hidden");
  switchGoogleAuthTab(defaultTab);
  const msg = document.getElementById("ga-status-msg");
  msg.className = "hidden text-xs p-3 rounded-xl";
}

function closeGoogleAuthModal() {
  document.getElementById("modal-google-auth").classList.add("hidden");
}

function switchGoogleAuthTab(tab) {
  const btnSa = document.getElementById("tab-ga-sa");
  const btnCode = document.getElementById("tab-ga-code");
  const btnToken = document.getElementById("tab-ga-token");

  const contentSa = document.getElementById("content-ga-sa");
  const contentCode = document.getElementById("content-ga-code");
  const contentToken = document.getElementById("content-ga-token");

  btnSa.className = "py-1.5 px-2 rounded-lg text-slate-400 hover:text-white transition text-center truncate";
  btnCode.className = "py-1.5 px-2 rounded-lg text-slate-400 hover:text-white transition text-center truncate";
  btnToken.className = "py-1.5 px-2 rounded-lg text-slate-400 hover:text-white transition text-center truncate";

  contentSa.classList.add("hidden");
  contentCode.classList.add("hidden");
  contentToken.classList.add("hidden");

  if (tab === "sa") {
    btnSa.className = "py-1.5 px-2 rounded-lg bg-blue-600 text-white font-semibold transition text-center truncate";
    contentSa.classList.remove("hidden");
  } else if (tab === "code") {
    btnCode.className = "py-1.5 px-2 rounded-lg bg-blue-600 text-white font-semibold transition text-center truncate";
    contentCode.classList.remove("hidden");
  } else if (tab === "token") {
    btnToken.className = "py-1.5 px-2 rounded-lg bg-blue-600 text-white font-semibold transition text-center truncate";
    contentToken.classList.remove("hidden");
  }
}

async function submitGoogleServiceAccount() {
  const saJson = document.getElementById("input-ga-sa-json").value.trim();
  const folderId = document.getElementById("input-ga-folder-id").value.trim();
  const msg = document.getElementById("ga-status-msg");

  if (!saJson) {
    msg.className = "text-xs p-3 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300";
    msg.textContent = "Inserisci o carica il contenuto JSON della Service Account Google.";
    return;
  }

  msg.className = "text-xs p-3 rounded-xl bg-blue-500/10 border border-blue-500/20 text-blue-300";
  msg.textContent = "Configurazione e verifica Service Account in corso...";

  try {
    const res = await fetch("/api/auth/google/service-account", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        service_account_json: saJson,
        folder_id: folderId || null,
      }),
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Errore configurazione Service Account");

    msg.className = "text-xs p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-300";
    msg.textContent = data.message;
    logMessage(data.message, "success");

    setTimeout(() => {
      closeGoogleAuthModal();
      checkRemotesStatus();
    }, 1200);
  } catch (err) {
    msg.className = "text-xs p-3 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300";
    msg.textContent = err.message;
    logMessage(`Errore login Google Service Account: ${err.message}`, "error");
  }
}

function openGoogleConsentWindow() {
  const clientId = document.getElementById("input-ga-code-client-id").value.trim();
  if (!clientId) {
    alert("Inserisci prima il tuo Client ID Google per generare l'autorizzazione.");
    return;
  }
  const scope = encodeURIComponent("https://www.googleapis.com/auth/drive");
  const redirectUri = encodeURIComponent("urn:ietf:wg:oauth:2.0:oob");
  const url = `https://accounts.google.com/o/oauth2/v2/auth?client_id=${encodeURIComponent(clientId)}&redirect_uri=${redirectUri}&response_type=code&scope=${scope}&access_type=offline`;
  window.open(url, "_blank");
}

async function submitGoogleAuthCode() {
  const code = document.getElementById("input-ga-auth-code").value.trim();
  const clientId = document.getElementById("input-ga-code-client-id").value.trim();
  const clientSecret = document.getElementById("input-ga-code-client-secret").value.trim();
  const msg = document.getElementById("ga-status-msg");

  if (!code || !clientId) {
    msg.className = "text-xs p-3 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300";
    msg.textContent = "Client ID e Codice di Autorizzazione sono obbligatori.";
    return;
  }

  msg.className = "text-xs p-3 rounded-xl bg-blue-500/10 border border-blue-500/20 text-blue-300";
  msg.textContent = "Scambio codice con Google in corso...";

  try {
    const res = await fetch("/api/auth/google/exchange-code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        code: code,
        client_id: clientId,
        client_secret: clientSecret || "",
        redirect_uri: "urn:ietf:wg:oauth:2.0:oob",
      }),
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Errore nello scambio del codice");

    msg.className = "text-xs p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-300";
    msg.textContent = data.message;
    logMessage(data.message, "success");

    setTimeout(() => {
      closeGoogleAuthModal();
      checkRemotesStatus();
    }, 1200);
  } catch (err) {
    msg.className = "text-xs p-3 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300";
    msg.textContent = err.message;
    logMessage(`Errore autorizzazione Google: ${err.message}`, "error");
  }
}

async function submitGoogleTokenDirect() {
  const tokenJson = document.getElementById("input-ga-token-json").value.trim();
  const clientId = document.getElementById("input-ga-token-client-id").value.trim();
  const clientSecret = document.getElementById("input-ga-token-client-secret").value.trim();
  const msg = document.getElementById("ga-status-msg");

  if (!tokenJson) {
    msg.className = "text-xs p-3 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300";
    msg.textContent = "Incolla il token JSON.";
    return;
  }

  msg.className = "text-xs p-3 rounded-xl bg-blue-500/10 border border-blue-500/20 text-blue-300";
  msg.textContent = "Salvataggio token in corso...";

  try {
    const res = await fetch("/api/auth/google/token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token_json: tokenJson,
        client_id: clientId || null,
        client_secret: clientSecret || null,
      }),
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Errore configurazione token");

    msg.className = "text-xs p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-300";
    msg.textContent = data.message;
    logMessage(data.message, "success");

    setTimeout(() => {
      closeGoogleAuthModal();
      checkRemotesStatus();
    }, 1200);
  } catch (err) {
    msg.className = "text-xs p-3 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300";
    msg.textContent = err.message;
    logMessage(`Errore salvataggio token: ${err.message}`, "error");
  }
}

// ---------------------------------------------------------------------------
// Disconnect Remotes
// ---------------------------------------------------------------------------

async function disconnectRemote(remoteName) {
  const label = remoteName === "gdrive" ? "Google Drive" : "iCloud Drive";
  if (!confirm(`Sei sicuro di voler disconnettere ${label}?`)) return;

  try {
    const res = await fetch("/api/remotes/disconnect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ remote: remoteName }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Errore durante la disconnessione");
    }

    logMessage(`${label} disconnesso con successo.`, "warn");
    checkRemotesStatus();
  } catch (err) {
    logMessage(`Errore disconnessione ${label}: ${err.message}`, "error");
  }
}

// ---------------------------------------------------------------------------
// iCloud Configuration Wizard Modal
// ---------------------------------------------------------------------------

function openConfigModal() {
  document.getElementById("modal-config").classList.remove("hidden");
  document.getElementById("config-status-msg").classList.add("hidden");
}

function closeConfigModal() {
  document.getElementById("modal-config").classList.add("hidden");
}

async function saveICloudConfig() {
  const user = document.getElementById("cfg-icloud-user").value.trim();
  const pass = document.getElementById("cfg-icloud-pass").value.trim();
  const statusMsg = document.getElementById("config-status-msg");

  if (!user || !pass) {
    statusMsg.className = "text-xs p-2.5 rounded-lg bg-rose-500/10 text-rose-300 border border-rose-500/20";
    statusMsg.textContent = "Compila username Apple ID e password specifica per l'app.";
    statusMsg.classList.remove("hidden");
    return;
  }

  statusMsg.className = "text-xs p-2.5 rounded-lg bg-cyan-500/10 text-cyan-300 border border-cyan-500/20";
  statusMsg.textContent = "Cifratura password e configurazione remota...";
  statusMsg.classList.remove("hidden");

  try {
    const obsRes = await fetch("/api/remotes/obscure", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pass }),
    });
    const obsData = await obsRes.json();
    const obscuredPass = obsData.obscured;

    const confRes = await fetch("/api/remotes/configure", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "icloud",
        type: "webdav",
        parameters: {
          url: "https://p58-content.icloud.com",
          vendor: "other",
          user: user,
          pass: obscuredPass,
        },
      }),
    });

    if (!confRes.ok) throw new Error("Errore salvataggio parametri iCloud.");

    statusMsg.className = "text-xs p-2.5 rounded-lg bg-emerald-500/10 text-emerald-300 border border-emerald-500/20";
    statusMsg.textContent = "iCloud salvato con successo! Test connessione in corso...";

    setTimeout(() => {
      testRemote("icloud");
      checkRemotesStatus();
    }, 1000);
  } catch (err) {
    statusMsg.className = "text-xs p-2.5 rounded-lg bg-rose-500/10 text-rose-300 border border-rose-500/20";
    statusMsg.textContent = err.message;
  }
}

async function testRemote(remoteName) {
  const statusMsg = document.getElementById("config-status-msg");
  statusMsg.className = "text-xs p-2.5 rounded-lg bg-slate-800 text-slate-300";
  statusMsg.textContent = `Test connessione a ${remoteName}...`;
  statusMsg.classList.remove("hidden");

  try {
    const res = await fetch("/api/remotes/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ remote: remoteName }),
    });
    const data = await res.json();

    if (data.success) {
      statusMsg.className = "text-xs p-2.5 rounded-lg bg-emerald-500/10 text-emerald-300 border border-emerald-500/20";
      statusMsg.textContent = `✓ ${data.message}`;
      logMessage(`Test ${remoteName}: Raggiungibile!`, "success");
    } else {
      statusMsg.className = "text-xs p-2.5 rounded-lg bg-rose-500/10 text-rose-300 border border-rose-500/20";
      statusMsg.textContent = `✕ ${data.message}`;
      logMessage(`Test ${remoteName}: Fallito - ${data.message}`, "error");
    }
  } catch (err) {
    statusMsg.className = "text-xs p-2.5 rounded-lg bg-rose-500/10 text-rose-300 border border-rose-500/20";
    statusMsg.textContent = err.message;
  }
}

// ---------------------------------------------------------------------------
// New Folder Modal
// ---------------------------------------------------------------------------

function openMkdirModal() {
  document.getElementById("modal-mkdir").classList.remove("hidden");
  document.getElementById("input-mkdir-name").focus();
}

function closeMkdirModal() {
  document.getElementById("modal-mkdir").classList.add("hidden");
  document.getElementById("input-mkdir-name").value = "";
}

async function submitMkdir() {
  const name = document.getElementById("input-mkdir-name").value.trim();
  if (!name) return;

  const currentPath = state.icloudPath;
  const newPath = currentPath ? `${currentPath}/${name}` : name;

  try {
    const res = await fetch("/api/fs/mkdir", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ remote: "icloud", path: newPath }),
    });
    if (!res.ok) throw new Error("Errore creazione cartella");

    logMessage(`Cartella '${newPath}' creata su iCloud Drive.`, "success");
    closeMkdirModal();
    loadICloud(currentPath);
  } catch (err) {
    logMessage(`Impossibile creare la cartella: ${err.message}`, "error");
  }
}

// ---------------------------------------------------------------------------
// History Modal
// ---------------------------------------------------------------------------

async function openHistoryModal() {
  document.getElementById("modal-history").classList.remove("hidden");
  const container = document.getElementById("history-items-container");
  container.innerHTML = `<div class="p-4 text-center text-xs text-slate-400">Caricamento cronologia...</div>`;

  try {
    const res = await fetch("/api/history");
    const entries = await res.json();

    if (entries.length === 0) {
      container.innerHTML = `<div class="p-8 text-center text-xs text-slate-500 italic">Nessun trasferimento registrato finora.</div>`;
      return;
    }

    container.innerHTML = "";
    entries.forEach((e) => {
      const card = document.createElement("div");
      card.className = "p-3 rounded-xl bg-slate-950/70 border border-slate-800 text-xs flex flex-col gap-1.5";

      const time = new Date(e.timestamp).toLocaleString();
      const dryTag = e.dry_run ? `<span class="bg-amber-500/20 text-amber-300 px-1.5 py-0.2 rounded text-[10px] font-mono">DRY-RUN</span>` : "";
      const actionBadge = e.action === "copy" ? `<span class="bg-blue-500/20 text-blue-300 px-1.5 py-0.2 rounded text-[10px] font-semibold">COPIA</span>` : `<span class="bg-amber-500/20 text-amber-300 px-1.5 py-0.2 rounded text-[10px] font-semibold">SPOSTAMENTO</span>`;

      card.innerHTML = `
        <div class="flex items-center justify-between text-slate-400 text-[11px]">
          <span>${time} • Batch: <code class="text-slate-300 font-mono">${e.id}</code></span>
          <div class="flex items-center gap-1.5">${actionBadge} ${dryTag}</div>
        </div>
        <div class="text-slate-200 font-medium flex items-center justify-between">
          <span>${e.total_items} elemento/i ➔ icloud:/${e.dst_path || ""}</span>
          <span class="text-emerald-400 font-bold">${e.status}</span>
        </div>
        <div class="text-slate-400 text-[10px] truncate max-w-full font-mono">
          ${(e.items || []).map((i) => i.path).join(", ")}
        </div>
      `;
      container.appendChild(card);
    });
  } catch (err) {
    container.innerHTML = `<div class="p-4 text-center text-xs text-rose-400">Errore: ${err.message}</div>`;
  }
}

function closeHistoryModal() {
  document.getElementById("modal-history").classList.add("hidden");
}

async function clearHistoryDb() {
  if (!confirm("Sei sicuro di voler cancellare tutto lo storico dei trasferimenti?")) return;
  try {
    await fetch("/api/history", { method: "DELETE" });
    logMessage("Cronologia trasferimenti cancellata.", "warn");
    openHistoryModal();
  } catch (err) {
    logMessage(`Errore: ${err.message}`, "error");
  }
}

// ---------------------------------------------------------------------------
// Event Listeners Initialization
// ---------------------------------------------------------------------------

function setupEventListeners() {
  // Mode selector: Move vs Copy
  document.getElementById("btn-mode-move").addEventListener("click", () => setTransferMode(true));
  document.getElementById("btn-mode-copy").addEventListener("click", () => setTransferMode(false));

  // Top Nav Actions
  document.getElementById("btn-nav-google-auth").addEventListener("click", () => openGoogleAuthModal("sa"));
  document.getElementById("btn-open-config").addEventListener("click", openConfigModal);
  document.getElementById("btn-open-history").addEventListener("click", openHistoryModal);
  document.getElementById("btn-refresh-remotes").addEventListener("click", checkRemotesStatus);

  // Unauth View Login Buttons
  document.getElementById("btn-login-gdrive").addEventListener("click", () => openGoogleAuthModal("sa"));
  document.getElementById("btn-login-gdrive-sa").addEventListener("click", () => openGoogleAuthModal("sa"));
  document.getElementById("btn-login-icloud").addEventListener("click", openConfigModal);

  // Disconnect Buttons
  document.getElementById("btn-disconnect-gdrive").addEventListener("click", () => disconnectRemote("gdrive"));
  document.getElementById("btn-disconnect-icloud").addEventListener("click", () => disconnectRemote("icloud"));

  // Google Auth Modal Actions
  document.getElementById("modal-google-auth-close").addEventListener("click", closeGoogleAuthModal);
  document.getElementById("tab-ga-sa").addEventListener("click", () => switchGoogleAuthTab("sa"));
  document.getElementById("tab-ga-code").addEventListener("click", () => switchGoogleAuthTab("code"));
  document.getElementById("tab-ga-token").addEventListener("click", () => switchGoogleAuthTab("token"));

  // File upload for Service Account JSON
  document.getElementById("input-ga-file").addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (file) {
      document.getElementById("ga-file-name").textContent = file.name;
      const reader = new FileReader();
      reader.onload = (event) => {
        document.getElementById("input-ga-sa-json").value = event.target.result;
      };
      reader.readAsText(file);
    }
  });

  document.getElementById("btn-submit-ga-sa").addEventListener("click", submitGoogleServiceAccount);
  document.getElementById("btn-open-google-consent").addEventListener("click", openGoogleConsentWindow);
  document.getElementById("btn-submit-ga-code").addEventListener("click", submitGoogleAuthCode);
  document.getElementById("btn-submit-ga-token").addEventListener("click", submitGoogleTokenDirect);

  // Cloudflare copy link
  document.getElementById("btn-copy-tunnel").addEventListener("click", () => {
    if (state.tunnelUrl) {
      navigator.clipboard.writeText(state.tunnelUrl);
      logMessage(`Link Cloudflare copiato negli appunti: ${state.tunnelUrl}`, "success");
    }
  });

  // GDrive Refresh & Search & Select All
  document.getElementById("btn-refresh-gdrive").addEventListener("click", () => loadGDrive(state.gdrivePath));
  document.getElementById("search-gdrive").addEventListener("input", renderGDriveTable);
  document.getElementById("btn-select-all-gdrive").addEventListener("click", () => {
    const allSelected = state.selectedItems.size === state.gdriveItems.length;
    if (allSelected) {
      state.selectedItems.clear();
    } else {
      state.gdriveItems.forEach((item) => state.selectedItems.set(item.path, item));
    }
    updateSelectionUI();
  });
  document.getElementById("checkbox-master-gdrive").addEventListener("change", (e) => {
    if (e.target.checked) {
      state.gdriveItems.forEach((item) => state.selectedItems.set(item.path, item));
    } else {
      state.selectedItems.clear();
    }
    updateSelectionUI();
  });

  // iCloud Refresh & New Folder
  document.getElementById("btn-refresh-icloud").addEventListener("click", () => loadICloud(state.icloudPath));
  document.getElementById("btn-new-folder-icloud").addEventListener("click", openMkdirModal);
  document.getElementById("modal-mkdir-cancel").addEventListener("click", closeMkdirModal);
  document.getElementById("modal-mkdir-confirm").addEventListener("click", submitMkdir);

  // Bandwidth limit change
  document.getElementById("select-bwlimit").addEventListener("change", async (e) => {
    const rate = e.target.value;
    try {
      await fetch("/api/system/bwlimit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rate }),
      });
      logMessage(`Limite di banda impostato a: ${rate === "off" ? "Illimitato" : rate}`, "warn");
    } catch (err) {
      logMessage(`Impossibile impostare il limite di banda: ${err.message}`, "error");
    }
  });

  // Transfer Trigger Button & Modals
  document.getElementById("btn-trigger-move").addEventListener("click", openConfirmationModal);
  document.getElementById("modal-btn-cancel").addEventListener("click", closeConfirmationModal);
  document.getElementById("modal-btn-proceed").addEventListener("click", executeTransfer);

  // iCloud Config Modal
  document.getElementById("modal-config-close").addEventListener("click", closeConfigModal);
  document.getElementById("btn-test-icloud").addEventListener("click", () => testRemote("icloud"));
  document.getElementById("btn-save-icloud").addEventListener("click", saveICloudConfig);

  // History Modal
  document.getElementById("modal-history-close").addEventListener("click", closeHistoryModal);
  document.getElementById("btn-clear-history-db").addEventListener("click", clearHistoryDb);

  // Logs Clear
  document.getElementById("btn-clear-logs").addEventListener("click", () => {
    document.getElementById("logs-console").innerHTML = "";
  });
}

// ---------------------------------------------------------------------------
// App Startup
// ---------------------------------------------------------------------------

window.addEventListener("DOMContentLoaded", () => {
  setupEventListeners();
  checkTunnelStatus();
  checkRemotesStatus();
  initStatsStream();
});
