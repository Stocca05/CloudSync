/**
 * CloudSync Frontend Application Logic
 * Minimalist, reactive SPA for Google Drive to iCloud Drive file moving.
 */

// Application State
const state = {
  gdrivePath: "",
  icloudPath: "",
  gdriveItems: [],
  icloudItems: [],
  selectedItems: new Map(), // path -> { path, is_dir, size, name }
  remotes: { gdrive: false, icloud: false },
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
// Remotes Verification
// ---------------------------------------------------------------------------

async function checkRemotesStatus() {
  try {
    const res = await fetch("/api/remotes/status");
    const data = await res.json();

    const dotGdrive = document.getElementById("dot-gdrive");
    const infoGdrive = document.getElementById("info-gdrive");

    const dotIcloud = document.getElementById("dot-icloud");
    const infoIcloud = document.getElementById("info-icloud");

    const warningBanner = document.getElementById("remotes-warning");

    state.remotes.gdrive = data.gdrive_configured;
    state.remotes.icloud = data.icloud_configured;

    // Update GDrive status pill
    if (data.gdrive_configured) {
      dotGdrive.className = "w-2 h-2 rounded-full bg-emerald-400";
      const used = data.gdrive?.used_bytes ? formatBytes(data.gdrive.used_bytes) : "Connesso";
      infoGdrive.textContent = used;
      infoGdrive.className = "text-emerald-400 font-medium text-[11px]";
    } else {
      dotGdrive.className = "w-2 h-2 rounded-full bg-rose-400";
      infoGdrive.textContent = "Non configurato";
      infoGdrive.className = "text-rose-400 font-medium text-[11px]";
    }

    // Update iCloud status pill
    if (data.icloud_configured) {
      dotIcloud.className = "w-2 h-2 rounded-full bg-emerald-400";
      const info = data.icloud?.used_bytes ? formatBytes(data.icloud.used_bytes) : "Connesso";
      infoIcloud.textContent = info;
      infoIcloud.className = "text-emerald-400 font-medium text-[11px]";
    } else {
      dotIcloud.className = "w-2 h-2 rounded-full bg-rose-400";
      infoIcloud.textContent = "Non configurato";
      infoIcloud.className = "text-rose-400 font-medium text-[11px]";
    }

    // Toggle missing remotes banner
    if (!data.gdrive_configured || !data.icloud_configured) {
      warningBanner.classList.remove("hidden");
    } else {
      warningBanner.classList.add("hidden");
    }

    logMessage(`Stato remoti: gdrive=${data.gdrive_configured ? "OK" : "MANCANTE"}, icloud=${data.icloud_configured ? "OK" : "MANCANTE"}`);
  } catch (err) {
    logMessage("Impossibile contattare l'API per lo stato dei remoti: " + err.message, "error");
  }
}

// ---------------------------------------------------------------------------
// File Explorers & Navigation
// ---------------------------------------------------------------------------

async function loadGDrive(path = "") {
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
      throw new Error(err.details || err.error || "Errore nella lettura dei file su iCloud");
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
    tr.className = `hover:bg-slate-800/40 transition select-none ${item.is_dir ? "cursor-pointer font-medium" : "text-slate-400"}`;

    tr.innerHTML = `
      <td class="py-2 px-3 flex items-center gap-2 truncate">
        ${getFileIcon(item)}
        <span class="truncate ${item.is_dir ? "text-cyan-300 hover:text-cyan-200" : "text-slate-300"}">${item.name}</span>
      </td>
      <td class="py-2 px-3 text-right text-slate-500 font-mono text-[11px]">
        ${item.is_dir ? "Cartella" : formatBytes(item.size)}
      </td>
    `;

    if (item.is_dir) {
      tr.addEventListener("click", () => {
        loadICloud(item.path);
      });
    }

    tbody.appendChild(tr);
  });
}

function renderBreadcrumbs(remote, currentPath) {
  const container = document.getElementById(`breadcrumbs-${remote}`);
  container.innerHTML = "";

  const segments = currentPath ? currentPath.split("/").filter(Boolean) : [];

  const rootBtn = document.createElement("button");
  rootBtn.className = "hover:text-white px-1.5 py-0.5 rounded hover:bg-slate-800 transition flex items-center gap-1 font-semibold";
  rootBtn.textContent = remote === "gdrive" ? "gdrive:/" : "icloud:/";
  rootBtn.onclick = () => {
    if (remote === "gdrive") loadGDrive("");
    else loadICloud("");
  };
  container.appendChild(rootBtn);

  let accumulated = "";
  segments.forEach((seg, idx) => {
    accumulated += (accumulated ? "/" : "") + seg;
    const targetPath = accumulated;

    const slash = document.createElement("span");
    slash.className = "text-slate-600";
    slash.textContent = "/";
    container.appendChild(slash);

    const segBtn = document.createElement("button");
    segBtn.className = idx === segments.length - 1 ? "text-slate-200 font-bold px-1" : "hover:text-white px-1 py-0.5 rounded hover:bg-slate-800 transition";
    segBtn.textContent = seg;
    segBtn.onclick = () => {
      if (remote === "gdrive") loadGDrive(targetPath);
      else loadICloud(targetPath);
    };
    container.appendChild(segBtn);
  });
}

// ---------------------------------------------------------------------------
// Selection Management
// ---------------------------------------------------------------------------

function toggleItemSelection(item, isSelected) {
  if (isSelected) {
    state.selectedItems.set(item.path, {
      path: item.path,
      is_dir: item.is_dir,
      size: item.size,
      name: item.name,
    });
  } else {
    state.selectedItems.delete(item.path);
  }
  updateSelectionUI();
}

function updateSelectionUI() {
  const count = state.selectedItems.size;
  let totalSize = 0;
  for (const item of state.selectedItems.values()) {
    totalSize += item.size || 0;
  }

  const countBadge = document.getElementById("selected-count-badge");
  const sizeBadge = document.getElementById("selected-size-badge");
  const btnTrigger = document.getElementById("btn-trigger-move");

  countBadge.textContent = `${count} element${count === 1 ? "o" : "i"}`;
  sizeBadge.textContent = `(${formatBytes(totalSize)})`;

  btnTrigger.disabled = count === 0;

  const masterChk = document.getElementById("checkbox-master-gdrive");
  if (state.gdriveItems.length > 0 && state.gdriveItems.every((i) => state.selectedItems.has(i.path))) {
    masterChk.checked = true;
    masterChk.indeterminate = false;
  } else if (state.gdriveItems.some((i) => state.selectedItems.has(i.path))) {
    masterChk.checked = false;
    masterChk.indeterminate = true;
  } else {
    masterChk.checked = false;
    masterChk.indeterminate = false;
  }
}

// ---------------------------------------------------------------------------
// Move Execution & Confirmation Modal
// ---------------------------------------------------------------------------

function openConfirmModal() {
  if (state.selectedItems.size === 0) return;
  const modal = document.getElementById("modal-confirm");
  const countEl = document.getElementById("modal-items-count");
  const pathEl = document.getElementById("modal-target-path");
  const dryRunWarning = document.getElementById("modal-dry-run-warning");
  const isDryRun = document.getElementById("toggle-dry-run").checked;

  countEl.textContent = state.selectedItems.size;
  pathEl.textContent = `icloud:${state.icloudPath || "/"}`;

  if (isDryRun) {
    dryRunWarning.classList.remove("hidden");
  } else {
    dryRunWarning.classList.add("hidden");
  }

  modal.classList.remove("hidden");
}

function closeConfirmModal() {
  document.getElementById("modal-confirm").classList.add("hidden");
}

async function executeMove() {
  closeConfirmModal();
  const itemsToMove = Array.from(state.selectedItems.values());
  const exportDocs = document.getElementById("toggle-export-docs").checked;
  const deleteEmpty = document.getElementById("toggle-delete-empty").checked;
  const dryRun = document.getElementById("toggle-dry-run").checked;

  logMessage(`Avvio spostamento (dry_run=${dryRun}) di ${itemsToMove.length} elemento/i verso icloud:${state.icloudPath || "/"}...`, "warn");

  try {
    const res = await fetch("/api/transfer/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        src_remote: "gdrive",
        dst_remote: "icloud",
        dst_path: state.icloudPath,
        items: itemsToMove,
        export_docs: exportDocs,
        delete_empty_src_dirs: deleteEmpty,
        dry_run: dryRun,
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || err.error || "Errore durante l'avvio del trasferimento");
    }

    const result = await res.json();
    logMessage(`Operazione registrata! Batch ID: ${result.batch_id} (${result.jobs.length} job Rclone)`, "success");

    state.selectedItems.clear();
    updateSelectionUI();
    renderGDriveTable();

    setTimeout(() => {
      loadGDrive(state.gdrivePath);
      loadICloud(state.icloudPath);
    }, 2500);
  } catch (err) {
    logMessage(`Errore durante lo spostamento: ${err.message}`, "error");
  }
}

// ---------------------------------------------------------------------------
// Bandwidth Limit Control
// ---------------------------------------------------------------------------

async function handleBwlimitChange(e) {
  const rate = e.target.value;
  try {
    const res = await fetch("/api/system/bwlimit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rate }),
    });
    if (res.ok) {
      logMessage(`Limite di banda impostato a: ${rate === "off" ? "Illimitata" : rate}`, "info");
    }
  } catch (err) {
    console.error("Failed to set bwlimit:", err);
  }
}

// ---------------------------------------------------------------------------
// Demo Mode Activation
// ---------------------------------------------------------------------------

async function activateDemoMode() {
  logMessage("Attivazione modalità dimostrativa in corso...", "warn");
  try {
    const res = await fetch("/api/demo/activate", { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      logMessage("Modalità demo attivata con successo! File di test caricati.", "success");
      await checkRemotesStatus();
      await loadGDrive("");
      await loadICloud("");
    } else {
      throw new Error(data.message || "Errore attivazione demo");
    }
  } catch (err) {
    logMessage("Errore attivazione demo: " + err.message, "error");
  }
}

// ---------------------------------------------------------------------------
// Remote Config Wizard Modal
// ---------------------------------------------------------------------------

function openConfigModal() {
  document.getElementById("modal-config").classList.remove("hidden");
  document.getElementById("config-status-msg").classList.add("hidden");
}

function closeConfigModal() {
  document.getElementById("modal-config").classList.add("hidden");
}

function switchConfigTab(tab) {
  const btnGdrive = document.getElementById("tab-btn-gdrive");
  const btnIcloud = document.getElementById("tab-btn-icloud");
  const contentGdrive = document.getElementById("tab-content-gdrive");
  const contentIcloud = document.getElementById("tab-content-icloud");

  if (tab === "gdrive") {
    btnGdrive.className = "px-4 py-2 text-blue-400 border-b-2 border-blue-500 focus:outline-none";
    btnIcloud.className = "px-4 py-2 text-slate-400 hover:text-slate-200 focus:outline-none";
    contentGdrive.classList.remove("hidden");
    contentIcloud.classList.add("hidden");
  } else {
    btnIcloud.className = "px-4 py-2 text-cyan-400 border-b-2 border-cyan-500 focus:outline-none";
    btnGdrive.className = "px-4 py-2 text-slate-400 hover:text-slate-200 focus:outline-none";
    contentIcloud.classList.remove("hidden");
    contentGdrive.classList.add("hidden");
  }
}

async function testRemote(remoteName) {
  const msgEl = document.getElementById("config-status-msg");
  msgEl.className = "text-xs p-2.5 rounded-lg bg-slate-800 text-slate-300";
  msgEl.textContent = `Test connessione '${remoteName}' in corso...`;
  msgEl.classList.remove("hidden");

  try {
    const res = await fetch("/api/remotes/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ remote: remoteName }),
    });
    const data = await res.json();
    if (data.success) {
      msgEl.className = "text-xs p-2.5 rounded-lg bg-emerald-950/60 border border-emerald-500/30 text-emerald-300";
      msgEl.textContent = `✅ ${data.message}`;
    } else {
      msgEl.className = "text-xs p-2.5 rounded-lg bg-rose-950/60 border border-rose-500/30 text-rose-300";
      msgEl.textContent = `❌ ${data.message}`;
    }
  } catch (err) {
    msgEl.className = "text-xs p-2.5 rounded-lg bg-rose-950/60 text-rose-300";
    msgEl.textContent = `Errore di rete: ${err.message}`;
  }
}

async function saveGDriveConfig() {
  const token = document.getElementById("cfg-gdrive-token").value.trim();
  const clientId = document.getElementById("cfg-gdrive-client-id").value.trim();
  const clientSecret = document.getElementById("cfg-gdrive-client-secret").value.trim();
  const msgEl = document.getElementById("config-status-msg");

  const params = { scope: "drive" };
  if (token) params.token = token;
  if (clientId) params.client_id = clientId;
  if (clientSecret) params.client_secret = clientSecret;

  try {
    const res = await fetch("/api/remotes/configure", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "gdrive", type: "drive", parameters: params }),
    });
    if (res.ok) {
      msgEl.className = "text-xs p-2.5 rounded-lg bg-emerald-950/60 text-emerald-300";
      msgEl.textContent = "✅ Configurazione 'gdrive' salvata con successo!";
      msgEl.classList.remove("hidden");
      checkRemotesStatus();
      loadGDrive("");
    }
  } catch (err) {
    msgEl.textContent = `Errore: ${err.message}`;
  }
}

async function saveICloudConfig() {
  const user = document.getElementById("cfg-icloud-user").value.trim();
  const pass = document.getElementById("cfg-icloud-pass").value.trim();
  const msgEl = document.getElementById("config-status-msg");

  if (!user || !pass) {
    msgEl.className = "text-xs p-2.5 rounded-lg bg-rose-950/60 text-rose-300";
    msgEl.textContent = "Inserisci sia l'email ID Apple sia la password specifica per l'app.";
    msgEl.classList.remove("hidden");
    return;
  }

  try {
    const res = await fetch("/api/remotes/configure", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "icloud",
        type: "webdav",
        parameters: {
          url: "https://p58-content.icloud.com",
          vendor: "other",
          user: user,
          pass: pass,
        },
        obscure: true,
      }),
    });
    if (res.ok) {
      msgEl.className = "text-xs p-2.5 rounded-lg bg-emerald-950/60 text-emerald-300";
      msgEl.textContent = "✅ Configurazione 'icloud' salvata con password protetta!";
      msgEl.classList.remove("hidden");
      checkRemotesStatus();
      loadICloud("");
    }
  } catch (err) {
    msgEl.textContent = `Errore: ${err.message}`;
  }
}

// ---------------------------------------------------------------------------
// History Drawer & Log View
// ---------------------------------------------------------------------------

async function openHistoryModal() {
  const modal = document.getElementById("modal-history");
  const container = document.getElementById("history-items-container");
  container.innerHTML = `<div class="p-6 text-center text-xs text-slate-500">Caricamento cronologia...</div>`;
  modal.classList.remove("hidden");

  try {
    const res = await fetch("/api/history?limit=50");
    const entries = await res.json();

    if (!entries || entries.length === 0) {
      container.innerHTML = `<div class="p-8 text-center text-slate-500 text-xs italic">Nessun trasferimento registrato finora.</div>`;
      return;
    }

    container.innerHTML = "";
    entries.forEach((e) => {
      const card = document.createElement("div");
      card.className = "p-3 rounded-xl bg-slate-950 border border-slate-800/80 flex flex-col gap-2 text-xs";

      const dateStr = new Date(e.timestamp).toLocaleString("it-IT");
      const isDry = e.dry_run ? `<span class="bg-amber-500/20 text-amber-300 px-1.5 py-0.5 rounded text-[10px]">SIMULAZIONE</span>` : "";

      let statusBadge = `<span class="text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded font-medium">${e.status}</span>`;
      if (e.status === "failed") {
        statusBadge = `<span class="text-rose-400 bg-rose-500/10 px-2 py-0.5 rounded font-medium">${e.status}</span>`;
      }

      card.innerHTML = `
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span class="font-bold text-white">Batch #${e.id}</span>
            ${isDry}
          </div>
          <span class="text-slate-500 text-[11px] font-mono">${dateStr}</span>
        </div>
        <div class="text-slate-300 text-[11px]">
          Da: <code class="text-blue-300">${e.src_remote}:</code> ➔ A: <code class="text-cyan-300">${e.dst_remote}:${e.dst_path || "/"}</code>
        </div>
        <div class="flex items-center justify-between text-slate-400 text-[11px] border-t border-slate-900 pt-1.5">
          <span>Elementi: <strong>${e.total_items}</strong></span>
          ${statusBadge}
        </div>
      `;
      container.appendChild(card);
    });
  } catch (err) {
    container.innerHTML = `<div class="p-4 text-center text-rose-400 text-xs">Errore caricamento: ${err.message}</div>`;
  }
}

function closeHistoryModal() {
  document.getElementById("modal-history").classList.add("hidden");
}

async function clearHistory() {
  if (!confirm("Sei sicuro di voler cancellare lo storico dei trasferimenti?")) return;
  await fetch("/api/history", { method: "DELETE" });
  openHistoryModal();
}

// ---------------------------------------------------------------------------
// Create New Folder Modal on iCloud
// ---------------------------------------------------------------------------

function openMkdirModal() {
  const modal = document.getElementById("modal-mkdir");
  const input = document.getElementById("input-mkdir-name");
  input.value = "";
  modal.classList.remove("hidden");
  input.focus();
}

function closeMkdirModal() {
  document.getElementById("modal-mkdir").classList.add("hidden");
}

async function createICloudDirectory() {
  const input = document.getElementById("input-mkdir-name");
  const folderName = (input.value || "").trim();
  if (!folderName) return;

  const targetPath = state.icloudPath ? `${state.icloudPath}/${folderName}` : folderName;

  try {
    const res = await fetch("/api/fs/mkdir", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ remote: "icloud", path: targetPath }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || "Impossibile creare la cartella");
    }

    logMessage(`Cartella '${targetPath}' creata con successo su iCloud.`, "success");
    closeMkdirModal();
    loadICloud(state.icloudPath);
  } catch (err) {
    logMessage(`Errore creazione cartella: ${err.message}`, "error");
  }
}

// ---------------------------------------------------------------------------
// Real-Time Stats & WebSocket Connection
// ---------------------------------------------------------------------------

function updateDashboard(stats) {
  const speed = stats.speed || 0;
  document.getElementById("metric-speed").textContent = formatSpeed(speed);

  const bytes = stats.bytes || 0;
  const totalBytes = stats.total_bytes || 0;
  document.getElementById("metric-bytes").textContent = formatBytes(bytes);
  document.getElementById("metric-total-bytes").textContent = `/ ${formatBytes(totalBytes)}`;

  const transfers = stats.transfers || 0;
  const totalTransfers = stats.total_transfers || 0;
  document.getElementById("metric-transfers").textContent = transfers;
  document.getElementById("metric-total-transfers").textContent = `/ ${totalTransfers}`;

  const elapsedTime = stats.elapsed_time || 0;
  const eta = stats.eta;
  document.getElementById("metric-time").textContent = formatSeconds(elapsedTime);
  document.getElementById("metric-eta").textContent = eta ? `(ETA: ${formatSeconds(eta)})` : "(ETA: --)";

  let pct = 0;
  if (totalBytes > 0) {
    pct = Math.min(100, Math.round((bytes / totalBytes) * 100));
  } else if (totalTransfers > 0) {
    pct = Math.min(100, Math.round((transfers / totalTransfers) * 100));
  }
  document.getElementById("global-progress-percent").textContent = `${pct}%`;
  document.getElementById("global-progress-bar").style.width = `${pct}%`;

  const activeJobs = stats.active_jobs || [];
  document.getElementById("active-jobs-badge").textContent = activeJobs.length;

  const transferringList = stats.transferring || [];
  const inFlightCount = document.getElementById("in-flight-count");
  const container = document.getElementById("transferring-files-list");

  inFlightCount.textContent = `${transferringList.length} file`;

  if (transferringList.length === 0) {
    container.innerHTML = `<div class="p-4 text-center text-xs text-slate-500 italic">Nessun file attualmente in transito.</div>`;
  } else {
    container.innerHTML = "";
    transferringList.forEach((t) => {
      const row = document.createElement("div");
      row.className = "p-2.5 flex items-center justify-between text-xs hover:bg-slate-900/60";
      row.innerHTML = `
        <div class="flex items-center gap-2 truncate max-w-[50%]">
          <svg class="w-3.5 h-3.5 text-emerald-400 shrink-0 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path></svg>
          <span class="truncate font-mono text-slate-200">${t.name}</span>
        </div>
        <div class="flex items-center gap-4 text-[11px] font-mono">
          <span class="text-slate-400">${formatSpeed(t.speed)} MB/s</span>
          <span class="text-emerald-400 font-bold w-12 text-right">${t.percentage}%</span>
          <div class="w-20 bg-slate-800 rounded-full h-1.5 overflow-hidden">
            <div class="bg-emerald-500 h-1.5 rounded-full" style="width: ${t.percentage}%"></div>
          </div>
        </div>
      `;
      container.appendChild(row);
    });
  }
}

function initRealtimeConnection() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws/stats`;

  const indicator = document.getElementById("ws-indicator");
  const statusText = document.getElementById("ws-status-text");

  try {
    state.ws = new WebSocket(wsUrl);

    state.ws.onopen = () => {
      indicator.className = "w-2 h-2 rounded-full bg-emerald-400";
      statusText.textContent = "Live (WS)";
    };

    state.ws.onmessage = (event) => {
      try {
        const stats = JSON.parse(event.data);
        updateDashboard(stats);
      } catch (e) {
        console.error("Error parsing WS stats:", e);
      }
    };

    state.ws.onclose = () => {
      indicator.className = "w-2 h-2 rounded-full bg-amber-400";
      statusText.textContent = "Fallback SSE...";
      initSSEFallback();
    };

    state.ws.onerror = () => {
      state.ws.close();
    };
  } catch (err) {
    initSSEFallback();
  }
}

function initSSEFallback() {
  if (state.sse) return;

  const indicator = document.getElementById("ws-indicator");
  const statusText = document.getElementById("ws-status-text");

  try {
    state.sse = new EventSource("/api/stream/stats");
    state.sse.onopen = () => {
      indicator.className = "w-2 h-2 rounded-full bg-emerald-400";
      statusText.textContent = "Live (SSE)";
    };
    state.sse.onmessage = (event) => {
      try {
        const stats = JSON.parse(event.data);
        updateDashboard(stats);
      } catch (e) {
        console.error("Error parsing SSE stats:", e);
      }
    };
    state.sse.onerror = () => {
      indicator.className = "w-2 h-2 rounded-full bg-rose-400";
      statusText.textContent = "Disconnesso";
    };
  } catch (e) {
    console.error("SSE fallback failed:", e);
  }
}

// ---------------------------------------------------------------------------
// Event Listeners & Bootstrapping
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  // Remotes & Navigation
  document.getElementById("btn-refresh-remotes").addEventListener("click", checkRemotesStatus);
  document.getElementById("btn-refresh-gdrive").addEventListener("click", () => loadGDrive(state.gdrivePath));
  document.getElementById("btn-refresh-icloud").addEventListener("click", () => loadICloud(state.icloudPath));

  // Search filter
  document.getElementById("search-gdrive").addEventListener("input", renderGDriveTable);

  // Master Checkbox
  document.getElementById("checkbox-master-gdrive").addEventListener("change", (e) => {
    const checked = e.target.checked;
    state.gdriveItems.forEach((item) => {
      toggleItemSelection(item, checked);
    });
    renderGDriveTable();
  });

  // Select all button
  document.getElementById("btn-select-all-gdrive").addEventListener("click", () => {
    state.gdriveItems.forEach((item) => {
      toggleItemSelection(item, true);
    });
    renderGDriveTable();
  });

  // Bandwidth limit
  document.getElementById("select-bwlimit").addEventListener("change", handleBwlimitChange);

  // Trigger move & Confirmation Modal
  document.getElementById("btn-trigger-move").addEventListener("click", openConfirmModal);
  document.getElementById("modal-btn-cancel").addEventListener("click", closeConfirmModal);
  document.getElementById("modal-btn-proceed").addEventListener("click", executeMove);

  // Mkdir Modal
  document.getElementById("btn-new-folder-icloud").addEventListener("click", openMkdirModal);
  document.getElementById("modal-mkdir-cancel").addEventListener("click", closeMkdirModal);
  document.getElementById("modal-mkdir-confirm").addEventListener("click", createICloudDirectory);

  // Demo Mode Activation
  document.getElementById("btn-activate-demo").addEventListener("click", activateDemoMode);

  // Config Wizard Modal
  document.getElementById("btn-open-config").addEventListener("click", openConfigModal);
  document.getElementById("btn-wizard-banner").addEventListener("click", openConfigModal);
  document.getElementById("modal-config-close").addEventListener("click", closeConfigModal);
  document.getElementById("tab-btn-gdrive").addEventListener("click", () => switchConfigTab("gdrive"));
  document.getElementById("tab-btn-icloud").addEventListener("click", () => switchConfigTab("icloud"));
  document.getElementById("btn-test-gdrive").addEventListener("click", () => testRemote("gdrive"));
  document.getElementById("btn-test-icloud").addEventListener("click", () => testRemote("icloud"));
  document.getElementById("btn-save-gdrive").addEventListener("click", saveGDriveConfig);
  document.getElementById("btn-save-icloud").addEventListener("click", saveICloudConfig);

  // History Modal
  document.getElementById("btn-open-history").addEventListener("click", openHistoryModal);
  document.getElementById("modal-history-close").addEventListener("click", closeHistoryModal);
  document.getElementById("btn-clear-history-db").addEventListener("click", clearHistory);

  // Copy Tunnel URL
  document.getElementById("btn-copy-tunnel").addEventListener("click", () => {
    if (state.tunnelUrl) {
      navigator.clipboard.writeText(state.tunnelUrl);
      logMessage("Link Cloudflare copiato negli appunti!", "success");
    }
  });

  // Clear logs
  document.getElementById("btn-clear-logs").addEventListener("click", () => {
    document.getElementById("logs-console").innerHTML = "";
    logMessage("Log puliti.");
  });

  // Initial Data Loads
  checkTunnelStatus();
  checkRemotesStatus();
  loadGDrive("");
  loadICloud("");
  initRealtimeConnection();

  // Poll tunnel status every 5 seconds until active
  const tunnelPoll = setInterval(() => {
    if (state.tunnelUrl) {
      clearInterval(tunnelPoll);
    } else {
      checkTunnelStatus();
    }
  }, 5000);
});
