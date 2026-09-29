let groups = [];
let selectedGroup = null;

const $ = id => document.getElementById(id);

async function api(url, options = {}) {
  const res = await fetch(url, {
    headers: {"Content-Type": "application/json"},
    ...options
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

function showAlert(message, error=false) {
  $("alert").innerHTML = `<div class="alert ${error ? "error" : "success"}">${escapeHtml(message)}</div>`;
  setTimeout(() => $("alert").innerHTML = "", 5000);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, c =>
    ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#039;" }[c])
  );
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

async function loadGroups() {
  try {
    const svm = encodeURIComponent($("svm").value);
    const data = await api(`/api/groups?svm=${svm}`);
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
  `).join("");
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

function openCreate() {
  openModal("Create Local Group", `
    <form onsubmit="createGroup(event)">
      <label>Group name<input id="groupName" required maxlength="256"></label>
      <label>Description<textarea id="groupDescription" maxlength="1024"></textarea></label>
      <button class="primary" type="submit">Create</button>
    </form>
  `);
}

async function createGroup(event) {
  event.preventDefault();
  try {
    await api(`/api/groups?svm=${encodeURIComponent($("svm").value)}`, {
      method: "POST",
      body: JSON.stringify({
        name: $("groupName").value,
        description: $("groupDescription").value || null
      })
    });
    closeModal();
    showAlert("Group created");
    await loadGroups();
  } catch (e) { showAlert(e.message, true); }
}

async function viewGroup(sid) {
  try {
    const g = await api(`/api/groups/${encodeURIComponent(sid)}?svm=${encodeURIComponent($("svm").value)}`);
    selectedGroup = g;
    openModal(g.name, `
      <div class="details">
        <p><b>Description:</b> ${escapeHtml(g.description || "")}</p>
        <p><b>SID:</b> <code>${escapeHtml(g.sid)}</code></p>
      </div>
      <h3>Members</h3>
      <div id="members">${renderMembers(g.members || [])}</div>
      <form class="member-form" onsubmit="addMember(event, '${escapeHtml(g.sid)}')">
        <input id="memberName" placeholder="CAPE\\user or CAPE\\group" required>
        <button class="primary" type="submit">Add Member</button>
      </form>
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
    await api(`/api/groups/${encodeURIComponent(sid)}/members?svm=${encodeURIComponent($("svm").value)}`, {
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
    await api(`/api/groups/${encodeURIComponent(sid)}/members?svm=${encodeURIComponent($("svm").value)}&name=${encodeURIComponent(member)}`, {
      method: "DELETE"
    });
    showAlert("Member removed");
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
      <button class="primary" type="submit">Save</button>
    </form>
  `);
}

async function saveGroup(event, sid) {
  event.preventDefault();
  try {
    await api(`/api/groups/${encodeURIComponent(sid)}?svm=${encodeURIComponent($("svm").value)}`, {
      method: "PATCH",
      body: JSON.stringify({
        name: $("editName").value,
        description: $("editDescription").value
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
    await api(`/api/groups/${encodeURIComponent(sid)}?svm=${encodeURIComponent($("svm").value)}`, {
      method: "DELETE"
    });
    showAlert("Group deleted");
    await loadGroups();
  } catch (e) { showAlert(e.message, true); }
}

$("svm").addEventListener("change", loadGroups);

(async function init() {
  try {
    await loadSvms();
    await loadGroups();
  } catch (e) {
    showAlert(e.message, true);
  }
})();
