let groups = [];
let volumes = [];
let availablePrivileges = [];
let ntfsPermissions = [];
let aggregates = [];
let selectedGroup = null;
let currentSection = "groups";

const $ = id => document.getElementById(id);

async function api(url, options = {}) {
  const res = await fetch(url, {
    headers: {"Content-Type": "application/json"},
    ...options
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    const message = typeof detail === "string"
      ? detail
      : (detail ? JSON.stringify(detail) : `HTTP ${res.status}`);
    throw new Error(message);
  }
  return data;
}

function showAlert(message, error=false) {
  $("alert").innerHTML = `<div class="alert ${error ? "error" : "success"}">${escapeHtml(message)}</div>`;
  setTimeout(() => $("alert").innerHTML = "", 7000);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, c =>
    ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#039;" }[c])
  );
}

function svm() {
  return encodeURIComponent($("svm").value);
}

function showSection(name) {
  currentSection = name;
  document.querySelectorAll(".nav-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.section === name);
  });
  $("section-groups").classList.toggle("hidden", name !== "groups");
  $("section-volumes").classList.toggle("hidden", name !== "volumes");
  if (name === "groups") loadGroups();
  if (name === "volumes") loadVolumes();
}

async function loadSvms() {
  const data = await api("/api/svms");
  const select = $("svm");
  select.innerHTML = "";
  data.records.forEach(s => {
    const opt = document.createElement("option");
    opt.value = s.name;
    opt.textContent = `${s.name} (${s.uuid})`;
    select.appendChild(opt);
  });
  if (data.records.length) {
    const defaultSvm = await api("/api/health");
    if (defaultSvm.default_svm) select.value = defaultSvm.default_svm;
  }
}

async function loadMeta() {
  const [privs, perms] = await Promise.all([
    api("/api/groups/meta/privileges"),
    api("/api/groups/meta/permissions")
  ]);
  availablePrivileges = privs.records || [];
  ntfsPermissions = perms.records || [];
  await loadAggregates();
}

async function loadAggregates() {
  try {
    const data = await api(`/api/volumes/meta/aggregates?svm=${svm()}`);
    aggregates = data.records || [];
  } catch (_) {
    aggregates = [];
  }
}

function privilegeOptions(selected = []) {
  const selectedSet = new Set(selected);
  return availablePrivileges.map(p =>
    `<option value="${escapeHtml(p.value)}" ${selectedSet.has(p.value) ? "selected" : ""}>${escapeHtml(p.label)}</option>`
  ).join("");
}

function ntfsPermissionOptions(selected = "modify") {
  return ntfsPermissions.map(p =>
    `<option value="${escapeHtml(p.value)}" ${selected === p.value ? "selected" : ""}>${escapeHtml(p.label)}</option>`
  ).join("");
}

function volumeOptions(selected = "") {
  return (volumes.length ? volumes : []).map(v => {
    const path = (v.nas && v.nas.path) || "";
    const label = path ? `${v.name} (${path})` : v.name;
    return `<option value="${escapeHtml(v.uuid)}" ${selected === v.uuid ? "selected" : ""}>${escapeHtml(label)}</option>`;
  }).join("") || `<option value="">No volumes found</option>`;
}

function formatSize(bytes) {
  if (bytes == null || bytes === "") return "";
  const n = Number(bytes);
  if (Number.isNaN(n)) return String(bytes);
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let value = n;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(value >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

async function ensureVolumesLoaded() {
  if (!volumes.length) {
    try {
      const data = await api(`/api/volumes?svm=${svm()}`);
      volumes = data.records || [];
    } catch (_) {
      volumes = [];
    }
  }
}

async function loadGroups() {
  try {
    const data = await api(`/api/groups?svm=${svm()}`);
    groups = data.records;
    renderGroups();
  } catch (e) {
    showAlert(e.message, true);
  }
}

function renderGroups() {
  $("groups").innerHTML = groups.map(g => `
    <tr>
      <td><strong>${escapeHtml(g.name)}</strong></td>
      <td>${escapeHtml(g.description || "")}</td>
      <td><code>${escapeHtml(g.sid)}</code></td>
      <td>
        <button onclick="viewGroup('${escapeHtml(g.sid)}')">Manage</button>
        <button onclick="editGroup('${escapeHtml(g.sid)}')">Edit</button>
        <button class="danger" onclick="deleteGroup('${escapeHtml(g.sid)}', '${escapeHtml(g.name)}')">Delete</button>
      </td>
    </tr>
  `).join("") || `<tr><td colspan="4" class="muted">No local groups found.</td></tr>`;
}

function openModal(title, body) {
  $("modalTitle").textContent = title;
  $("modalBody").innerHTML = body;
  $("modal").classList.remove("hidden");
}
function closeModal() {
  $("modal").classList.add("hidden");
  selectedGroup = null;
}

async function openCreate() {
  await ensureVolumesLoaded();
  openModal("Create Local Group", `
    <form onsubmit="createGroup(event)">
      <label>Group name<input id="groupName" required maxlength="256" placeholder="FSX-Project1-RW"></label>
      <label>Description<textarea id="groupDescription" maxlength="1024" placeholder="Project1 Read Write"></textarea></label>
      <div class="grid-2">
        <label>Attach to volume (optional)
          <select id="createVolume">
            <option value="">— None —</option>
            ${volumeOptions()}
          </select>
        </label>
        <label>NTFS permission
          <select id="createPermission">${ntfsPermissionOptions("modify")}</select>
        </label>
      </div>
      <p class="hint">Uses file-security ACL on the volume junction path as <code>CIFS_SERVER\\group</code> (per FSx Permissions runbook).</p>
      <label>ONTAP privileges (optional)
        <span class="hint">Hold Ctrl/Cmd to select multiple</span>
        <select id="groupPrivileges" multiple>${privilegeOptions()}</select>
      </label>
      <button class="primary" type="submit">Create</button>
    </form>
  `);
}

function selectedPrivileges(id) {
  const el = $(id);
  if (!el) return [];
  return Array.from(el.selectedOptions).map(o => o.value);
}

async function createGroup(event) {
  event.preventDefault();
  try {
    const body = {
      name: $("groupName").value,
      description: $("groupDescription").value || null,
      privileges: selectedPrivileges("groupPrivileges")
    };
    if ($("createVolume").value) {
      body.volume_uuid = $("createVolume").value;
      body.permission = $("createPermission").value;
    }
    await api(`/api/groups?svm=${svm()}`, {
      method: "POST",
      body: JSON.stringify(body)
    });
    closeModal();
    showAlert("Group created");
    await loadGroups();
  } catch (e) { showAlert(e.message, true); }
}

async function viewGroup(sid) {
  try {
    await ensureVolumesLoaded();
    const g = await api(`/api/groups/${encodeURIComponent(sid)}?svm=${svm()}`);
    selectedGroup = g;
    const privChips = (g.privileges || []).length
      ? `<div class="chip-row">${g.privileges.map(p => `<span class="chip">${escapeHtml(p)}</span>`).join("")}</div>`
      : `<p class="muted">No Se* privileges assigned.</p>`;
    const vols = g.attached_volumes || [];
    const volRows = vols.length
      ? `<table><thead><tr><th>Volume</th><th>Path</th><th>Account</th><th>Permission</th><th></th></tr></thead><tbody>
          ${vols.map(v => `<tr>
            <td>${escapeHtml(v.volume || "—")}</td>
            <td><code>${escapeHtml(v.path || "")}</code></td>
            <td><code>${escapeHtml(v.user_or_group || "")}</code></td>
            <td>${escapeHtml(v.permission || "")}</td>
            <td>
              <button class="danger small" onclick="detachGroupVolume('${escapeHtml(g.sid)}', '${escapeHtml(v.volume_uuid)}', '${escapeHtml(v.user_or_group)}')">Remove</button>
            </td>
          </tr>`).join("")}
        </tbody></table>`
      : `<p class="muted">No volumes have this group in their NTFS ACL yet.</p>`;

    openModal(g.name, `
      <div class="details">
        <p><b>Description:</b> ${escapeHtml(g.description || "")}</p>
        <p><b>SID:</b> <code>${escapeHtml(g.sid)}</code></p>
        <p><b>CIFS local account:</b> <code>${escapeHtml(g.local_account || g.name)}</code></p>
      </div>

      <div class="section-block">
        <h3>Members / Users in group</h3>
        <div id="members">${renderMembers(g.members || [])}</div>
        <form class="member-form" onsubmit="addMember(event, '${escapeHtml(g.sid)}')">
          <input id="memberName" placeholder="CAPE\\user or CAPE\\group" required>
          <button class="primary" type="submit">Add Member</button>
        </form>
      </div>

      <div class="section-block">
        <h3>Attached volumes (NTFS permissions)</h3>
        ${volRows}
        <form class="inline-form" onsubmit="attachGroupVolume(event, '${escapeHtml(g.sid)}')">
          <label>Volume
            <select id="attachVolume" required>
              <option value="">Select volume…</option>
              ${volumeOptions()}
            </select>
          </label>
          <label>Permission
            <select id="attachPermission" required>${ntfsPermissionOptions("modify")}</select>
          </label>
          <button class="primary" type="submit">Attach to volume</button>
        </form>
        <p class="hint">Applies file-security ACL on the volume junction path using <code>${escapeHtml(g.local_account || "CIFS_SERVER\\\\group")}</code>.</p>
      </div>

      <div class="section-block">
        <h3>ONTAP privileges (optional)</h3>
        <div id="privDisplay">${privChips}</div>
        <form class="section-block" onsubmit="saveGroupPrivileges(event, '${escapeHtml(g.sid)}')">
          <label>Modify privileges
            <select id="editPrivileges" multiple>${privilegeOptions(g.privileges || [])}</select>
          </label>
          <button class="primary" type="submit">Save Privileges</button>
        </form>
      </div>
    `);
  } catch (e) { showAlert(e.message, true); }
}

function renderMembers(members) {
  if (!members.length) return "<p class='muted'>No members.</p>";
  return `<ul class="members">${members.map(m => `
    <li><span>${escapeHtml(m)}</span>
      <button class="danger small" onclick="removeMember('${escapeHtml(selectedGroup.sid)}', '${escapeHtml(m)}')">Remove</button>
    </li>`).join("")}</ul>`;
}

async function addMember(event, sid) {
  event.preventDefault();
  try {
    await api(`/api/groups/${encodeURIComponent(sid)}/members?svm=${svm()}`, {
      method: "POST",
      body: JSON.stringify({name: $("memberName").value})
    });
    showAlert("Member added");
    await viewGroup(sid);
  } catch (e) { showAlert(e.message, true); }
}

async function removeMember(sid, member) {
  if (!confirm(`Remove '${member}' from this group?`)) return;
  try {
    await api(`/api/groups/${encodeURIComponent(sid)}/members?svm=${svm()}&name=${encodeURIComponent(member)}`, {
      method: "DELETE"
    });
    showAlert("Member removed");
    await viewGroup(sid);
  } catch (e) { showAlert(e.message, true); }
}

async function saveGroupPrivileges(event, sid) {
  event.preventDefault();
  try {
    await api(`/api/groups/${encodeURIComponent(sid)}/privileges?svm=${svm()}`, {
      method: "PUT",
      body: JSON.stringify({privileges: selectedPrivileges("editPrivileges")})
    });
    showAlert("Privileges updated");
    await viewGroup(sid);
  } catch (e) { showAlert(e.message, true); }
}

async function attachGroupVolume(event, sid) {
  event.preventDefault();
  const volumeUuid = $("attachVolume").value;
  if (!volumeUuid) {
    showAlert("Select a volume", true);
    return;
  }
  try {
    await api(`/api/groups/${encodeURIComponent(sid)}/volume-permissions?svm=${svm()}`, {
      method: "POST",
      body: JSON.stringify({
        volume_uuid: volumeUuid,
        permission: $("attachPermission").value
      })
    });
    showAlert("NTFS permission attached to volume");
    await viewGroup(sid);
  } catch (e) { showAlert(e.message, true); }
}

async function detachGroupVolume(sid, volumeUuid, userOrGroup) {
  if (!confirm(`Remove '${userOrGroup}' from this volume ACL?`)) return;
  try {
    let url = `/api/groups/${encodeURIComponent(sid)}/volume-permissions?svm=${svm()}&volume_uuid=${encodeURIComponent(volumeUuid)}`;
    if (userOrGroup) url += `&user_or_group=${encodeURIComponent(userOrGroup)}`;
    await api(url, { method: "DELETE" });
    showAlert("Permission removed");
    await viewGroup(sid);
  } catch (e) { showAlert(e.message, true); }
}

function editGroup(sid) {
  const g = groups.find(x => x.sid === sid);
  if (!g) return;
  openModal("Edit Local Group", `
    <form onsubmit="saveGroup(event, '${escapeHtml(sid)}')">
      <label>Group name<input id="editName" value="${escapeHtml(g.name)}" required maxlength="256"></label>
      <label>Description<textarea id="editDescription" maxlength="1024">${escapeHtml(g.description || "")}</textarea></label>
      <label>ONTAP privileges
        <select id="editGroupPrivileges" multiple>${privilegeOptions()}</select>
      </label>
      <button class="primary" type="submit">Save</button>
    </form>
  `);
  api(`/api/groups/${encodeURIComponent(sid)}/privileges?svm=${svm()}`)
    .then(data => {
      const select = $("editGroupPrivileges");
      if (!select) return;
      const set = new Set(data.privileges || []);
      Array.from(select.options).forEach(o => { o.selected = set.has(o.value); });
    })
    .catch(() => {});
}

async function saveGroup(event, sid) {
  event.preventDefault();
  try {
    await api(`/api/groups/${encodeURIComponent(sid)}?svm=${svm()}`, {
      method: "PATCH",
      body: JSON.stringify({
        name: $("editName").value,
        description: $("editDescription").value,
        privileges: selectedPrivileges("editGroupPrivileges")
      })
    });
    closeModal();
    showAlert("Group updated");
    await loadGroups();
  } catch (e) { showAlert(e.message, true); }
}

async function deleteGroup(sid, name) {
  if (!confirm(`Delete local group '${name}'? This changes access control.`)) return;
  try {
    await api(`/api/groups/${encodeURIComponent(sid)}?svm=${svm()}`, {
      method: "DELETE"
    });
    showAlert("Group deleted");
    await loadGroups();
  } catch (e) { showAlert(e.message, true); }
}

/* -------- Volumes -------- */

async function loadVolumes() {
  try {
    const data = await api(`/api/volumes?svm=${svm()}`);
    volumes = data.records;
    renderVolumes();
    await loadAggregates();
  } catch (e) {
    showAlert(e.message, true);
  }
}

function renderVolumes() {
  $("volumes").innerHTML = volumes.map(v => `
    <tr>
      <td><strong>${escapeHtml(v.name)}</strong><div class="muted">${escapeHtml(v.comment || "")}</div></td>
      <td>${escapeHtml(formatSize(v.size ?? (v.space && v.space.size)))}</td>
      <td>${escapeHtml(v.state || "")}</td>
      <td><code>${escapeHtml((v.nas && v.nas.path) || "")}</code></td>
      <td>
        <button onclick="viewVolume('${escapeHtml(v.uuid)}')">Manage</button>
        <button onclick="editVolume('${escapeHtml(v.uuid)}')">Edit</button>
        <button class="danger" onclick="deleteVolume('${escapeHtml(v.uuid)}', '${escapeHtml(v.name)}')">Delete</button>
      </td>
    </tr>
  `).join("") || `<tr><td colspan="5" class="muted">No volumes found.</td></tr>`;
}

async function openCreateVolume() {
  await loadAggregates();
  const aggOptions = aggregates.map(a =>
    `<option value="${escapeHtml(a.name)}">${escapeHtml(a.name)}</option>`
  ).join("");

  openModal("Create Volume", `
    <form onsubmit="createVolume(event)">
      <div class="grid-2">
        <label>Volume name<input id="volName" required maxlength="203"></label>
        <label>Size<input id="volSize" required placeholder="10GB" value="10GB"></label>
      </div>
      <label>Aggregate
        <select id="volAggregate">
          ${aggOptions || ""}
          ${aggOptions ? "" : `<option value="">No aggregates discovered — type below</option>`}
        </select>
      </label>
      <label>Aggregate (manual override)
        <input id="volAggregateManual" placeholder="e.g. aggr1 — used if dropdown is empty">
        <span class="hint">On FSx, aggregates are often not listed by API; enter the aggregate used by existing volumes.</span>
      </label>
      <div class="grid-2">
        <label>Junction path<input id="volPath" placeholder="/Project1"></label>
        <label>Security style
          <select id="volSecurity">
            <option value="">Default</option>
            <option value="ntfs" selected>ntfs</option>
            <option value="unix">unix</option>
            <option value="mixed">mixed</option>
          </select>
        </label>
      </div>
      <label>Comment<textarea id="volComment" maxlength="1024"></textarea></label>
      <button class="primary" type="submit">Create</button>
    </form>
  `);
}

async function createVolume(event) {
  event.preventDefault();
  try {
    const aggregate = ($("volAggregateManual").value || $("volAggregate").value || "").trim();
    if (!aggregate) {
      showAlert("Select or enter an aggregate name", true);
      return;
    }
    await api(`/api/volumes?svm=${svm()}`, {
      method: "POST",
      body: JSON.stringify({
        name: $("volName").value,
        size: $("volSize").value,
        aggregate,
        comment: $("volComment").value || null,
        junction_path: $("volPath").value || null,
        security_style: $("volSecurity").value || null
      })
    });
    closeModal();
    showAlert("Volume create requested");
    await loadVolumes();
  } catch (e) { showAlert(e.message, true); }
}

async function viewVolume(uuid) {
  try {
    if (!groups.length) {
      try { await loadGroups(); } catch (_) {}
    }
    const v = await api(`/api/volumes/${encodeURIComponent(uuid)}?svm=${svm()}`);
    const groupsAttached = v.attached_groups || [];
    const groupRows = groupsAttached.length
      ? `<table><thead><tr><th>Group / User</th><th>Permission</th><th>Path</th><th></th></tr></thead><tbody>
          ${groupsAttached.map(g => `<tr>
            <td><code>${escapeHtml(g.user_or_group || "")}</code></td>
            <td>${escapeHtml(g.permission || "")}</td>
            <td><code>${escapeHtml(g.path || "")}</code></td>
            <td>
              <button class="danger small" onclick="removeVolumePermission('${escapeHtml(uuid)}', '${escapeHtml(g.user_or_group)}')">Remove</button>
            </td>
          </tr>`).join("")}
        </tbody></table>`
      : `<p class="muted">No NTFS ACL entries found on this volume path.</p>`;

    const groupOptions = groups.map(g =>
      `<option value="${escapeHtml(g.name)}">${escapeHtml(g.name)}</option>`
    ).join("") || `<option value="">No local groups</option>`;

    openModal(v.name, `
      <div class="details">
        <p><b>UUID:</b> <code>${escapeHtml(v.uuid)}</code></p>
        <p><b>Size:</b> ${escapeHtml(formatSize(v.size ?? (v.space && v.space.size)))}</p>
        <p><b>State:</b> ${escapeHtml(v.state || "")}</p>
        <p><b>Junction:</b> <code>${escapeHtml((v.nas && v.nas.path) || "—")}</code></p>
        <p><b>Security style:</b> ${escapeHtml((v.nas && v.nas.security_style) || v.security_style || "")}</p>
        <p><b>Comment:</b> ${escapeHtml(v.comment || "")}</p>
      </div>

      <div class="section-block">
        <h3>Attached groups (NTFS file-security ACL)</h3>
        ${groupRows}
        ${!(v.nas && v.nas.path) ? `<p class="hint">This volume has no junction path — set one before attaching group permissions.</p>` : `
        <form class="inline-form" onsubmit="attachVolumePermission(event, '${escapeHtml(uuid)}')">
          <label>Group<select id="volGroup" required>${groupOptions}</select></label>
          <label>Permission<select id="volPermission" required>${ntfsPermissionOptions("modify")}</select></label>
          <button class="primary" type="submit">Attach group</button>
        </form>
        <p class="hint">Applies <code>CIFS_SERVER\\group</code> with Modify or Read-and-Execute rights on the junction path.</p>`}
      </div>
    `);
  } catch (e) { showAlert(e.message, true); }
}

async function attachVolumePermission(event, uuid) {
  event.preventDefault();
  try {
    await api(`/api/volumes/${encodeURIComponent(uuid)}/permissions?svm=${svm()}`, {
      method: "POST",
      body: JSON.stringify({
        group_name: $("volGroup").value,
        permission: $("volPermission").value
      })
    });
    showAlert("Group permission attached");
    await viewVolume(uuid);
  } catch (e) { showAlert(e.message, true); }
}

async function removeVolumePermission(uuid, userOrGroup) {
  if (!confirm(`Remove '${userOrGroup}' from this volume ACL?`)) return;
  try {
    await api(
      `/api/volumes/${encodeURIComponent(uuid)}/permissions?svm=${svm()}&user_or_group=${encodeURIComponent(userOrGroup)}`,
      { method: "DELETE" }
    );
    showAlert("ACL entry removed");
    await viewVolume(uuid);
  } catch (e) { showAlert(e.message, true); }
}

function editVolume(uuid) {
  const v = volumes.find(x => x.uuid === uuid);
  if (!v) return;
  openModal("Edit Volume", `
    <form onsubmit="saveVolume(event, '${escapeHtml(uuid)}')">
      <label>Size<input id="editVolSize" value="${escapeHtml(v.size || "")}" placeholder="10GB or bytes"></label>
      <label>State
        <select id="editVolState">
          <option value="">Keep current (${escapeHtml(v.state || "")})</option>
          <option value="online">online</option>
          <option value="offline">offline</option>
          <option value="restricted">restricted</option>
        </select>
      </label>
      <label>Junction path<input id="editVolPath" value="${escapeHtml((v.nas && v.nas.path) || "")}"></label>
      <label>Security style
        <select id="editVolSecurity">
          <option value="">Keep current</option>
          <option value="ntfs">ntfs</option>
          <option value="unix">unix</option>
          <option value="mixed">mixed</option>
        </select>
      </label>
      <label>Comment<textarea id="editVolComment">${escapeHtml(v.comment || "")}</textarea></label>
      <button class="primary" type="submit">Save</button>
    </form>
  `);
}

async function saveVolume(event, uuid) {
  event.preventDefault();
  try {
    const body = {
      comment: $("editVolComment").value
    };
    if ($("editVolSize").value) body.size = $("editVolSize").value;
    if ($("editVolState").value) body.state = $("editVolState").value;
    if ($("editVolPath").value) body.junction_path = $("editVolPath").value;
    if ($("editVolSecurity").value) body.security_style = $("editVolSecurity").value;

    await api(`/api/volumes/${encodeURIComponent(uuid)}`, {
      method: "PATCH",
      body: JSON.stringify(body)
    });
    closeModal();
    showAlert("Volume updated");
    await loadVolumes();
  } catch (e) { showAlert(e.message, true); }
}

async function deleteVolume(uuid, name) {
  if (!confirm(`Delete volume '${name}'? This is destructive.`)) return;
  try {
    await api(`/api/volumes/${encodeURIComponent(uuid)}`, { method: "DELETE" });
    showAlert("Volume deleted");
    await loadVolumes();
  } catch (e) { showAlert(e.message, true); }
}

$("svm").addEventListener("change", async () => {
  volumes = [];
  aggregates = [];
  await loadAggregates();
  if (currentSection === "volumes") loadVolumes();
  else loadGroups();
});

(async function init() {
  try {
    await loadSvms();
    await loadMeta();
    await loadGroups();
    await ensureVolumesLoaded();
  } catch (e) {
    showAlert(e.message, true);
  }
})();
