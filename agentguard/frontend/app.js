const API = "";  // same-origin
let killSwitchOn = false;
let proposedPoliciesToSave = [];
const runningDemos = new Set();

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#039;"}[ch]));
}

function addCustomerBubble(content, kind = "bot") {
  const log = document.getElementById("customerChatLog");
  if (!log) return;
  const bubble = document.createElement("div");
  bubble.className = `chat-bubble ${kind}`;
  bubble.innerHTML = kind === "user" ? escapeHtml(content) : escapeHtml(content).replace(/\n/g, "<br>");
  log.appendChild(bubble);
  log.scrollTop = log.scrollHeight;
}

async function loadCustomerProfile() {
  const id = document.getElementById("customerIdSelect")?.value;
  const card = document.getElementById("customerProfileCard");
  if (!id || !card) return;
  card.textContent = "Loading verified company profile...";
  try {
    const res = await fetch(`${API}/chat/customer-profile/${encodeURIComponent(id)}`);
    if (!res.ok) throw new Error("Profile unavailable");
    const profile = await res.json();
    const c = profile.customer;
    card.innerHTML = `<strong>${escapeHtml(c.name)}</strong><span>${escapeHtml(c.email)}</span><span>${escapeHtml(c.phone)}</span><span class="profile-balance">Wallet balance ₹${Number(c.wallet_balance || 0).toLocaleString("en-IN", {minimumFractionDigits: 2})}</span><small>${profile.orders.length} orders · ${profile.transactions.length} transactions</small>`;
  } catch (error) { card.textContent = error.message; }
}

function renderCustomerTrace(workflow) {
  const guard = workflow.agentguard || {};
  const db = workflow.company_database || {};
  const status = document.getElementById("customerTraceStatus");
  const trace = document.getElementById("customerTrace");
  if (!status || !trace) return;
  status.className = `badge ${guard.decision || "MODEL"}`;
  status.textContent = guard.status || guard.decision || "UNKNOWN";
  trace.innerHTML = `<div><span>AI planned</span><strong>${escapeHtml(workflow.ai_agent?.planned_tool || "-")}</strong></div><div><span>AgentGuard decision</span><strong>${escapeHtml(guard.decision || "-")} · risk ${escapeHtml(guard.risk_score ?? "-")}/100</strong></div><div><span>Company database</span><strong>${db.mutation_executed ? "Updated" : "Not changed"}</strong></div><small>${escapeHtml(guard.reason || "Request evaluated by deterministic policy and risk engines.")}</small>`;
}

const customerChatForm = document.getElementById("customerChatForm");
if (customerChatForm) {
  document.querySelectorAll(".customer-preset").forEach(button => button.addEventListener("click", () => { document.getElementById("customerChatInput").value = button.dataset.message; document.getElementById("customerChatInput").focus(); }));
  document.getElementById("customerIdSelect")?.addEventListener("change", loadCustomerProfile);
  customerChatForm.addEventListener("submit", async event => {
    event.preventDefault();
    const input = document.getElementById("customerChatInput");
    const submit = document.getElementById("customerChatSubmit");
    const message = input.value.trim();
    const customerId = document.getElementById("customerIdSelect").value;
    if (!message) return;
    addCustomerBubble(message, "user"); input.value = ""; submit.disabled = true; submit.textContent = "Checking with AgentGuard...";
    try {
      const response = await fetch(`${API}/chat/message`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({message, customer_id: customerId, conversation_id: "conv-customer-web", provider: "auto"})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "The assistant could not process that request.");
      addCustomerBubble(data.reply, "bot"); renderCustomerTrace(data.workflow);
      if (data.workflow.company_database.mutation_executed) loadCustomerProfile();
      refreshStatus();
    } catch (error) { addCustomerBubble(`I could not complete that request: ${error.message}`, "error"); }
    finally { submit.disabled = false; submit.textContent = "Send request"; }
  });
  loadCustomerProfile();
}

function cls(decisionOrType) {
  if (["ALLOW", "action_executed"].includes(decisionOrType)) return "allow";
  if (["DENY", "action_blocked", "execution_error"].includes(decisionOrType)) return "deny";
  if (["REQUIRE_APPROVAL", "approval_required", "approval_requested", "WAITING_APPROVAL", "PENDING_APPROVAL"].includes(decisionOrType)) return "approval";
  return "info";
}

function fmtTime(iso) {
  try {
    const value = typeof iso === "string" && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? `${iso}Z` : iso;
    return new Date(value).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  } catch { return iso; }
}

function customerIdFromArgs(args) {
  return args && (args.customer_id || args.customerId);
}

async function customerSummary(customerId) {
  if (!customerId) return "";
  try {
    const c = await fetch(`${API}/customers/${encodeURIComponent(customerId)}`).then(r => r.ok ? r.json() : null);
    if (!c) return "";
    const initials = c.name.split(" ").map(part => part[0]).join("").slice(0, 2);
    return `<div class="customer-summary"><div class="customer-avatar">${initials}</div><div><b>${c.name}</b><span>${c.customer_id} · ${c.plan} plan</span><span>${c.email} · ${c.phone}</span></div></div>`;
  } catch { return ""; }
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------
document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach(tc => tc.classList.remove("active"));
    btn.classList.add("active");
    const targetId = btn.dataset.tab;
    const targetEl = document.getElementById(targetId);
    if (targetEl) targetEl.classList.add("active");

    if (targetId === "tab-policies") refreshPolicies();
    if (targetId === "tab-approvals") refreshApprovals();
    if (targetId === "tab-audit") refreshAudit();
  });
});

// ---------------------------------------------------------------------------
// Live Feed & WebSocket
// ---------------------------------------------------------------------------
function addFeedEvent(evt) {
  const feed = document.getElementById("feed");
  if (!feed) return;
  const div = document.createElement("div");
  const decisionType = evt.decision || evt.event_type || "";
  div.className = "evt " + cls(decisionType);

  const title = {
    action_received: `${evt.agent_name || "Agent"} requested ${evt.tool}`,
    action_executed: `${evt.agent_name || "Agent"} → ${evt.tool} EXECUTED`,
    action_blocked: `${evt.agent_name || "Agent"} → ${evt.tool} BLOCKED`,
    approval_required: `⚠️ ${evt.agent_name || "Agent"} → ${evt.tool} needs human approval`,
    approval_granted: `✓ Approved: ${evt.tool || ""} by ${evt.approver || "admin"}`,
    approval_denied: `🛑 Denied: ${evt.tool || ""} — ${evt.reason || ""}`,
    execution_error: `Execution error on ${evt.tool}`,
    agent_status_changed: `Agent ${evt.agent_name} → ${evt.status}`,
    kill_switch_changed: `Emergency Kill Switch ${evt.enabled ? "ACTIVATED" : "Deactivated"}`,
    policy_created: `Policy created: ${evt.name || ""}`,
    policy_deleted: `Policy deleted: ${evt.name || ""}`,
  }[evt.event_type] || `${evt.event_type || decisionType}`;

  div.innerHTML = `
    <div class="top">
      <span>${fmtTime(evt.timestamp || new Date().toISOString())}</span>
      ${evt.risk_level ? `<span class="badge ${evt.risk_level}">${evt.risk_level}${evt.risk_score !== undefined ? " · " + evt.risk_score : ""}</span>` : ""}
    </div>
    <div class="main">${title}</div>`;

  if (evt.action_request_id) {
    div.onclick = () => openInspector(evt.action_request_id);
  }
  feed.appendChild(div);
  while (feed.children.length > 150) feed.removeChild(feed.firstChild);

  refreshStatus();
  if (["approval_required", "approval_granted", "approval_denied", "agent_status_changed"].includes(evt.event_type)) {
    refreshApprovals();
    refreshAgents();
  }
}

function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  const pillWs = document.getElementById("pillWs");

  ws.onopen = () => {
    if (pillWs) pillWs.querySelector(".dot").style.background = "#10b981";
  };
  ws.onclose = () => {
    if (pillWs) pillWs.querySelector(".dot").style.background = "#f43f5e";
    setTimeout(connectWs, 2500);
  };
  ws.onmessage = (m) => {
    try {
      addFeedEvent(JSON.parse(m.data));
    } catch (e) {
      console.error(e);
    }
  };
}

// ---------------------------------------------------------------------------
// Status & KPI Metrics
// ---------------------------------------------------------------------------
async function refreshStatus() {
  try {
    const s = await fetch(`${API}/system/status`).then(r => r.json());
    killSwitchOn = s.kill_switch_active;

    const btn = document.getElementById("killBtn");
    if (btn) {
      btn.textContent = killSwitchOn ? "🔴 KILL SWITCH: ON — ACTIONS BLOCKED" : "🔴 KILL SWITCH: OFF";
      btn.classList.toggle("active", killSwitchOn);
    }

    // KPI Strip
    if (document.getElementById("kpiTotal")) document.getElementById("kpiTotal").textContent = s.actions_processed || 0;
    if (document.getElementById("kpiAllowed")) document.getElementById("kpiAllowed").textContent = s.total_allowed || 0;
    if (document.getElementById("kpiBlocked")) document.getElementById("kpiBlocked").textContent = s.total_blocked || 0;
    if (document.getElementById("kpiPending")) document.getElementById("kpiPending").textContent = s.total_pending || 0;
    if (document.getElementById("tabApprovalCount")) document.getElementById("tabApprovalCount").textContent = s.total_pending || 0;

    const chainEl = document.getElementById("kpiChain");
    if (chainEl) {
      chainEl.textContent = s.audit_chain_verified ? "VERIFIED" : "ATTENTION";
      chainEl.style.color = s.audit_chain_verified ? "var(--green)" : "var(--red)";
    }

    const modelName = s.featherless_model ? s.featherless_model.split("/").pop() : "Qwen 2.5 7B";
    const headerModel = document.getElementById("headerModelName");
    if (headerModel) headerModel.textContent = modelName;

    const sandboxPill = document.getElementById("sandboxActiveModelPill");
    if (sandboxPill) sandboxPill.textContent = s.featherless_model || "Qwen/Qwen2.5-7B-Instruct";
  } catch (err) {
    console.error("Status refresh error:", err);
  }
}

// ---------------------------------------------------------------------------
// Approvals
// ---------------------------------------------------------------------------
async function refreshApprovals() {
  try {
    const list = await fetch(`${API}/approvals?status=PENDING`).then(r => r.json());
    const count = list.length;
    const badge = document.getElementById("pendingApprovalsBadge");
    if (badge) badge.textContent = `${count} PENDING`;
    const tabCount = document.getElementById("tabApprovalCount");
    if (tabCount) tabCount.textContent = count;

    const renderCard = (a) => `
      <div class="approval-card" data-approval-id="${a.id}">
        <h3>🚨 ${a.action} — ${a.requested_by_agent}</h3>
        <div class="approval-customer" data-customer-id="${customerIdFromArgs(a.arguments) || ""}"></div>
        <div class="kv">
          <b>Arguments</b><span style="font-family:var(--font-mono);">${JSON.stringify(a.arguments)}</span>
          <b>Reason</b><span>${a.reason || "—"}</span>
          <b>Risk Score</b><span><b style="color:${a.risk_score > 60 ? 'var(--red)' : 'var(--amber)'};">${a.risk_score}/100</b> · Level ${a.risk_level}</span>
          <b>Requested</b><span>${fmtTime(a.requested_at)}</span>
        </div>
        <div class="btnrow">
          <button class="approve" onclick="decideApproval('${a.id}','approve')">✓ APPROVE ACTION</button>
          <button class="deny" onclick="decideApproval('${a.id}','deny')">🛑 DENY ACTION</button>
          <button class="action" onclick="openInspector('${a.action_request_id}')">🔍 Inspect Trace</button>
        </div>
      </div>
    `;

    const miniList = document.getElementById("approvalsList");
    if (miniList) {
      miniList.innerHTML = count === 0
        ? `<p style="color:var(--text-muted);font-size:12px;">No pending approvals.</p>`
        : list.map(renderCard).join("");
    }

    const fullList = document.getElementById("fullApprovalsList");
    if (fullList) {
      fullList.innerHTML = count === 0
        ? `<div class="panel" style="text-align:center;padding:30px;color:var(--text-muted);">No actions currently awaiting human authorization.</div>`
        : list.map(renderCard).join("");
    }
    document.querySelectorAll(".approval-customer[data-customer-id]").forEach(async el => {
      el.innerHTML = await customerSummary(el.dataset.customerId);
    });
  } catch (e) {
    console.error("Failed to refresh approvals:", e);
  }
}

async function decideApproval(id, action) {
  const reason = action === "deny" ? (prompt("Reason for denial (optional):") || "") : undefined;
  const response = await fetch(`${API}/approvals/${id}/${action}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved_by: "admin", reason }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    alert(error.detail || "This approval has already been decided.");
  }
  refreshApprovals();
  refreshAgents();
  refreshAudit();
  refreshStatus();
}

// ---------------------------------------------------------------------------
// Agents List
// ---------------------------------------------------------------------------
async function refreshAgents() {
  try {
    const agents = await fetch(`${API}/agents`).then(r => r.json());
    const el = document.getElementById("agentsList");
    if (!el) return;

    el.innerHTML = agents.map(a => `
      <div class="agent-card">
        <div class="row1">
          <b style="font-size:13px;">${a.name}</b>
          <span class="status-tag ${a.status}">${a.status}</span>
        </div>
        <div style="color:var(--text-muted);font-size:11.5px;margin-top:2px;">
          ${a.description || a.owner} · Base Risk: <span class="badge ${a.risk_level}">${a.risk_level}</span>
        </div>
        <div style="font-size:11px;color:var(--text-dim);margin-top:4px;">
          <b>Permissions:</b> ${a.permissions.join(", ") || "none"}
        </div>
        <div class="stats">
          <span>Total: ${a.stats.requests}</span>
          <span style="color:var(--green);">Allowed: ${a.stats.allowed}</span>
          <span style="color:var(--red);">Blocked: ${a.stats.blocked}</span>
          <span style="color:var(--amber);">Approval: ${a.stats.approval_required}</span>
        </div>
        <div class="btnrow" style="margin-top:8px;">
          <button class="action" style="font-size:11px;padding:4px 10px;" onclick="toggleAgent('${a.id}','${a.status}')">
            ${a.status === 'ACTIVE' ? 'Pause / Disable' : 'Activate Agent'}
          </button>
        </div>
      </div>
    `).join("");

    // Populate Agent Selects
    ["simAgent", "sandboxAgentSelect"].forEach(selId => {
      const sel = document.getElementById(selId);
      if (sel && sel.dataset.loaded !== "1") {
        sel.innerHTML = agents.map(a => `<option value="${a.name}">${a.name} (${a.owner})</option>`).join("");
        sel.dataset.loaded = "1";
      }
    });
  } catch (e) {
    console.error("Agents fetch error:", e);
  }
}

async function toggleAgent(id, status) {
  const newStatus = status === "ACTIVE" ? "DISABLED" : "ACTIVE";
  await fetch(`${API}/agents/${id}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status: newStatus }),
  });
  refreshAgents();
  refreshStatus();
}

// ---------------------------------------------------------------------------
// Guided Demo Runner
// ---------------------------------------------------------------------------
document.querySelectorAll("[data-demo]").forEach(card => {
  card.addEventListener("click", async () => {
    if (runningDemos.has(card.dataset.demo)) return;
    runningDemos.add(card.dataset.demo);
    card.style.pointerEvents = "none";
    card.style.opacity = "0.5";
    try {
      await fetch(`${API}/demo/run/${card.dataset.demo}`, { method: "POST" });
      refreshApprovals();
      refreshAgents();
      refreshAudit();
      refreshStatus();
    } finally {
      runningDemos.delete(card.dataset.demo);
      card.style.pointerEvents = "";
      setTimeout(() => card.style.opacity = "1", 500);
    }
  });
});

// ---------------------------------------------------------------------------
// Featherless AI Interactive Sandbox
// ---------------------------------------------------------------------------
// Preset Chips
document.querySelectorAll(".preset-chip").forEach(chip => {
  chip.addEventListener("click", () => {
    const input = document.getElementById("sandboxPromptInput");
    if (input) {
      input.value = chip.dataset.prompt;
      input.focus();
    }
  });
});

// Sandbox Form Run
const sandboxForm = document.getElementById("sandboxForm");
if (sandboxForm) {
  sandboxForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const promptInput = document.getElementById("sandboxPromptInput");
    const instruction = promptInput ? promptInput.value.trim() : "";
    if (!instruction) return;

    const agentName = document.getElementById("sandboxAgentSelect").value;
    const model = document.getElementById("sandboxModelSelect").value;
    const submitBtn = document.getElementById("sandboxSubmitBtn");
    submitBtn.disabled = true;
    submitBtn.textContent = "⚡ Running Model & Intercepting...";

    // Reset visual pipeline
    document.getElementById("tracePromptText").textContent = instruction;
    document.getElementById("traceModelTag").textContent = model.split("/").pop();
    document.getElementById("tracePlanDetails").innerHTML = `<span style="color:var(--accent);">Consulting Featherless AI inference engine...</span>`;
    document.getElementById("traceAuthDetails").textContent = "Awaiting planned tool...";
    document.getElementById("tracePolicyDetails").textContent = "Awaiting tool...";
    document.getElementById("traceVerdictDetails").textContent = "Evaluating...";

    const t0 = performance.now();
    try {
      const res = await fetch(`${API}/agent/run`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instruction, agent_name: agentName, model }),
      }).then(r => r.json());

      const elapsed = Math.round(performance.now() - t0);
      document.getElementById("traceLatencyBadge").textContent = `${elapsed} ms total roundtrip`;

      // Stage 2: Planned Tool
      document.getElementById("tracePlanDetails").innerHTML = `
        Planned tool: <b style="color:var(--text);">${res.planned_tool}</b><br>
        Arguments: <code style="font-family:var(--font-mono);">${JSON.stringify(res.planned_arguments)}</code><br>
        <span style="font-size:11px;color:var(--text-muted);">Provider: ${res.provider} · Model: ${res.model}</span>
      `;

      // Stage 3: Gateway
      const g = res.gateway_response || {};
      document.getElementById("traceAuthDetails").innerHTML = `
        Agent Identity: <b style="color:var(--text);">${res.agent}</b> · Authenticated<br>
        Request Trace ID: <code style="font-family:var(--font-mono);">${g.request_id || "—"}</code>
      `;

      // Stage 4: Policy & Risk
      const s4 = document.getElementById("traceStage4");
      document.getElementById("tracePolicyDetails").innerHTML = `
        Risk Score: <b style="color:${(g.risk_score || 0) > 60 ? 'var(--red)' : 'var(--amber)'};">${g.risk_score || 0}/100</b> (${g.risk_level || "LOW"})<br>
        Policy Reason: ${g.reason || "Evaluated by policy engine"}
      `;

      // Stage 5: Verdict
      const s5 = document.getElementById("traceStage5");
      const verdictTag = document.getElementById("traceVerdictTag");
      verdictTag.className = `badge ${g.decision}`;
      verdictTag.textContent = g.decision;

      s5.className = `trace-step ${g.decision === 'ALLOW' ? 'success' : (g.decision === 'DENY' ? 'blocked' : 'pending')}`;
      document.getElementById("traceVerdictDetails").innerHTML = `
        Status: <b class="badge ${g.decision}">${g.status || g.decision}</b><br>
        ${g.approval_id ? `⚠️ Created Approval Ticket: <code>${g.approval_id}</code> (Check Approvals Queue)` : ""}
        ${g.result ? `<div>Execution Output: <code style="font-family:var(--font-mono);">${JSON.stringify(g.result)}</code></div>` : ""}
      `;

      // Show Raw JSON
      const rawEl = document.getElementById("sandboxRawJson");
      rawEl.style.display = "block";
      rawEl.textContent = JSON.stringify(res, null, 2);

      refreshApprovals();
      refreshStatus();
      refreshAudit();
    } catch (err) {
      document.getElementById("traceVerdictDetails").textContent = `Error: ${err.message}`;
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "⚡ Execute Through AgentGuard";
    }
  });
}

// AI Threat Analysis button in Sandbox
const threatBtn = document.getElementById("analyzeThreatBtn");
if (threatBtn) {
  threatBtn.addEventListener("click", async () => {
    const promptInput = document.getElementById("sandboxPromptInput");
    const instruction = promptInput ? promptInput.value.trim() : "";
    if (!instruction) {
      alert("Please enter an instruction prompt first.");
      return;
    }
    const agentName = document.getElementById("sandboxAgentSelect").value;
    const model = "deepseek-ai/DeepSeek-R1-Distill-Qwen-8B";

    threatBtn.disabled = true;
    threatBtn.textContent = "Analyzing with Featherless DeepSeek...";
    const panel = document.getElementById("aiThreatPanel");
    const content = document.getElementById("aiThreatContent");
    panel.style.display = "block";
    content.innerHTML = `<span style="color:var(--text-muted);">Consulting Featherless AI security threat model...</span>`;

    try {
      const res = await fetch(`${API}/security/analyze-prompt`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          instruction,
          tool: "export_customers",
          arguments: {},
          agent_name: agentName,
          model,
        }),
      }).then(r => r.json());

      content.innerHTML = `
        <div style="margin-bottom:8px;">
          <b>Threat Detected:</b>
          <span class="badge ${res.threat_detected ? 'DENY' : 'ALLOW'}">${res.threat_detected ? 'YES — FLAGGED' : 'CLEAN'}</span>
          ${res.threat_category ? `<span class="badge MODEL" style="margin-left:6px;">${res.threat_category}</span>` : ""}
        </div>
        <div class="kv">
          <b>Assessment</b><span>${res.risk_assessment || "—"}</span>
          <b>Recommended</b><span>${res.recommendation || "—"}</span>
          <b>Confidence</b><span>${res.confidence !== undefined ? Math.round(res.confidence * 100) + "%" : "100%"}</span>
          <b>Model</b><span>${res.model || model} (${res.analyzed_by})</span>
        </div>
      `;
    } catch (e) {
      content.innerHTML = `<span style="color:var(--red);">Error running threat analyzer: ${e.message}</span>`;
    } finally {
      threatBtn.disabled = false;
      threatBtn.textContent = "🔍 Analyze Security Threat";
    }
  });
}

// ---------------------------------------------------------------------------
// Policy Studio & Natural Language Assistant
// ---------------------------------------------------------------------------
const policyAssistForm = document.getElementById("policyAssistForm");
if (policyAssistForm) {
  policyAssistForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = document.getElementById("assistInstruction");
    const instruction = input ? input.value.trim() : "";
    if (!instruction) return;

    const btn = document.getElementById("assistSubmitBtn");
    btn.disabled = true;
    btn.textContent = "Drafting with Featherless AI...";

    try {
      const res = await fetch(`${API}/policies/assist`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instruction }),
      }).then(r => r.json());

      proposedPoliciesToSave = res.proposed_policies || [];
      const box = document.getElementById("policyProposalBox");
      box.style.display = "block";
      document.getElementById("assistResultJson").textContent = JSON.stringify(res, null, 2);
    } catch (err) {
      alert("Policy assist error: " + err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "✨ Draft Policy Proposal with Featherless AI";
    }
  });
}

const activatePolicyBtn = document.getElementById("activatePolicyBtn");
if (activatePolicyBtn) {
  activatePolicyBtn.addEventListener("click", async () => {
    if (!proposedPoliciesToSave.length) return;
    activatePolicyBtn.disabled = true;
    activatePolicyBtn.textContent = "Saving...";

    try {
      for (const p of proposedPoliciesToSave) {
        await fetch(`${API}/policies`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(p),
        });
      }
      alert(`Successfully activated ${proposedPoliciesToSave.length} policy rules.`);
      document.getElementById("policyProposalBox").style.display = "none";
      proposedPoliciesToSave = [];
      refreshPolicies();
      refreshStatus();
    } catch (e) {
      alert("Failed to save policy: " + e.message);
    } finally {
      activatePolicyBtn.disabled = false;
      activatePolicyBtn.textContent = "🚀 Activate & Save Policy";
    }
  });
}

const cancelProposalBtn = document.getElementById("cancelProposalBtn");
if (cancelProposalBtn) {
  cancelProposalBtn.addEventListener("click", () => {
    document.getElementById("policyProposalBox").style.display = "none";
    proposedPoliciesToSave = [];
  });
}

async function refreshPolicies() {
  try {
    const list = await fetch(`${API}/policies`).then(r => r.json());
    const countEl = document.getElementById("policiesCount");
    if (countEl) countEl.textContent = `${list.length} Policies Active`;

    const tbody = document.querySelector("#policiesTable tbody");
    if (!tbody) return;

    tbody.innerHTML = list.map(p => {
      let cond = p.condition_operator === "always"
        ? "always"
        : `${p.condition_field} ${p.condition_operator} ${p.condition_value}`;
      return `
        <tr>
          <td>
            <b>${p.name}</b>
            <div style="color:var(--text-muted);font-size:11px;">${p.description || "—"}</div>
          </td>
          <td><code>${p.tool}</code></td>
          <td><span class="badge ${p.effect}">${p.effect}</span></td>
          <td><code style="font-size:11px;">${cond}</code></td>
          <td>
            <label class="switch">
              <input type="checkbox" ${p.enabled ? "checked" : ""} onchange="togglePolicy('${p.id}', this.checked)">
              <span class="slider"></span>
            </label>
          </td>
          <td>
            <button class="danger" style="font-size:11px;padding:3px 8px;" onclick="deletePolicy('${p.id}')">Delete</button>
          </td>
        </tr>
      `;
    }).join("");
  } catch (e) {
    console.error("Failed to load policies:", e);
  }
}

async function togglePolicy(id, enabled) {
  await fetch(`${API}/policies/${id}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  refreshPolicies();
}

async function deletePolicy(id) {
  if (!confirm("Are you sure you want to delete this policy?")) return;
  await fetch(`${API}/policies/${id}`, { method: "DELETE" });
  refreshPolicies();
  refreshStatus();
}

// Simulator Form
const simForm = document.getElementById("simForm");
if (simForm) {
  simForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const agent = document.getElementById("simAgent").value;
    const tool = document.getElementById("simTool").value;
    const amount = document.getElementById("simAmount").value;
    const args = { customer_id: "cust-4821" };
    if (amount) args.amount = Number(amount);

    const res = await fetch(`${API}/policies/simulate`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_id: agent, tool, arguments: args }),
    }).then(r => r.json());

    const out = document.getElementById("simResult");
    out.style.display = "block";
    out.textContent = JSON.stringify(res, null, 2);
  });
}

// ---------------------------------------------------------------------------
// Cryptographic Audit Trail
// ---------------------------------------------------------------------------
async function refreshAudit() {
  try {
    const rows = await fetch(`${API}/audit-events?limit=50`).then(r => r.json());
    const tbody = document.querySelector("#auditTable tbody");
    if (!tbody) return;

    tbody.innerHTML = rows.map(r => `
      <tr style="cursor:pointer;" onclick="${r.request_id ? `openInspectorByRequestId('${r.request_id}')` : ''}">
        <td style="font-family:var(--font-mono);font-size:11px;">${fmtTime(r.created_at)}</td>
        <td><b>${r.agent_name || "—"}</b></td>
        <td>${r.event_type}</td>
        <td><code>${r.tool || "—"}</code></td>
        <td>${r.decision ? `<span class="badge ${r.decision}">${r.decision}</span>` : "—"}</td>
        <td>${r.risk_score ? `<span class="badge ${r.risk_level}">${r.risk_score}</span>` : "—"}</td>
        <td><code style="font-size:10px;color:var(--accent);">${r.current_event_hash ? r.current_event_hash.substring(0, 16) + '...' : "—"}</code></td>
      </tr>
    `).join("");
  } catch (e) {
    console.error("Audit log error:", e);
  }
}

const verifyBtn = document.getElementById("verifyChainBtn");
if (verifyBtn) {
  verifyBtn.addEventListener("click", async () => {
    verifyBtn.disabled = true;
    verifyBtn.textContent = "Verifying cryptographic SHA-256 chain...";
    try {
      const r = await fetch(`${API}/audit-events/verify`).then(res => res.json());
      const badge = document.getElementById("verifyStatusBadge");
      const msg = document.getElementById("verifyMsg");
      badge.style.display = "inline-block";
      if (r.verified) {
        badge.className = "badge ALLOW";
        badge.textContent = "CRYPTOGRAPHICALLY VERIFIED";
        msg.innerHTML = `<span style="color:var(--green);">✅ ${r.message}</span>`;
      } else {
        badge.className = "badge DENY";
        badge.textContent = "CHAIN TAMPERED";
        msg.innerHTML = `<span style="color:var(--red);">❌ ${r.message}</span>`;
      }
    } finally {
      verifyBtn.disabled = false;
      verifyBtn.textContent = "🔒 Verify Cryptographic Chain";
    }
  });
}

// Kill Switch
const killBtn = document.getElementById("killBtn");
if (killBtn) {
  killBtn.addEventListener("click", async () => {
    if (!killSwitchOn && !confirm("ENGAGE KILL SWITCH? All agent actions will be immediately blocked.")) return;
    await fetch(`${API}/system/kill-switch?enabled=${!killSwitchOn}`, { method: "POST" });
    refreshStatus();
  });
}

// ---------------------------------------------------------------------------
// Action Inspector Modal
// ---------------------------------------------------------------------------
async function openInspector(actionRequestId) {
  try {
    const [action, allEvents] = await Promise.all([
      fetch(`${API}/actions/${actionRequestId}`).then(r => r.ok ? r.json() : null),
      fetch(`${API}/audit-events?limit=200`).then(r => r.json()),
    ]);
    if (!action) return;
    const related = allEvents.filter(e => e.request_id === action.request_id);
    const body = document.getElementById("modalBody");
    body.innerHTML = `
      <div class="section-title">Request Context</div>
      ${await customerSummary(customerIdFromArgs(action.arguments))}
      <div class="kv">
        <b>Agent</b><span>${action.agent_id}</span>
        <b>Tool</b><span><code>${action.tool}</code></span>
        <b>Arguments</b><span style="font-family:var(--font-mono);">${JSON.stringify(action.arguments)}</span>
        <b>Final Status</b><span class="badge ${action.decision}">${action.status} (${action.decision})</span>
      </div>

      <div class="section-title">Enforcement Decision Trail</div>
      ${related.map(e => `
        <div style="margin-bottom:8px;border-left:3px solid var(--accent);padding-left:10px;background:var(--bg-subtle);border-radius:4px;padding:8px 10px;">
          <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
            <b style="color:var(--text);">${e.event_type}</b>
            <span class="badge ${e.decision}">${e.decision || "LOGGED"}</span>
          </div>
          <div style="font-size:11.5px;color:var(--text-dim);">${e.message || "—"}</div>
          <div style="font-size:10px;color:var(--text-muted);margin-top:4px;font-family:var(--font-mono);">
            Hash: ${e.current_event_hash || "—"}
          </div>
        </div>
      `).join("")}

      <div class="section-title">Execution Result</div>
      <pre class="result">${JSON.stringify(action.result, null, 2)}</pre>
      <div class="inspector-ai" id="inspectorAiResult"><button class="action" id="analyzeActionBtn">🔍 Analyze with Featherless AI</button></div>
    `;
    document.getElementById("modalBg").classList.add("open");
    document.getElementById("analyzeActionBtn").onclick = async () => {
      const button = document.getElementById("analyzeActionBtn");
      const output = document.getElementById("inspectorAiResult");
      button.disabled = true;
      button.textContent = "Analyzing with Featherless AI...";
      try {
        const analysis = await fetch(`${API}/actions/${action.id}/analyze`, { method: "POST" }).then(r => r.json());
        output.innerHTML = `<div class="section-title">Featherless AI Security Analysis</div><div class="ai-analysis">${analysis.analysis?.risk_assessment || analysis.analysis?.summary || JSON.stringify(analysis.analysis)}</div>`;
      } catch (error) {
        output.innerHTML = `<span style="color:var(--red);">Analysis unavailable: ${error.message}</span>`;
      }
    };
  } catch (e) {
    console.error("Inspector error:", e);
  }
}

async function openInspectorByRequestId(requestId) {
  try {
    const allActions = await fetch(`${API}/actions?limit=100`).then(r => r.json());
    const matched = allActions.find(a => a.request_id === requestId);
    if (matched) openInspector(matched.id);
  } catch (e) {
    console.error(e);
  }
}

const modalClose = document.getElementById("modalClose");
if (modalClose) {
  modalClose.onclick = () => document.getElementById("modalBg").classList.remove("open");
}
const modalBg = document.getElementById("modalBg");
if (modalBg) {
  modalBg.onclick = (e) => { if (e.target === modalBg) modalBg.classList.remove("open"); };
}

// ---------------------------------------------------------------------------
// Featherless AI Settings Modal
// ---------------------------------------------------------------------------
const pillFeatherless = document.getElementById("pillFeatherless");
const featherlessModal = document.getElementById("featherlessModalBg");
const featherlessClose = document.getElementById("featherlessModalClose");

if (pillFeatherless) {
  pillFeatherless.addEventListener("click", async () => {
    try {
      const cfg = await fetch(`${API}/llm/config`).then(r => r.json());
      if (document.getElementById("cfgModel") && cfg.featherless_model) {
        document.getElementById("cfgModel").value = cfg.featherless_model;
      }
      if (document.getElementById("cfgBaseUrl") && cfg.featherless_base_url) {
        document.getElementById("cfgBaseUrl").value = cfg.featherless_base_url;
      }
      featherlessModal.classList.add("open");
    } catch (e) {
      console.error(e);
    }
  });
}

if (featherlessClose) {
  featherlessClose.onclick = () => featherlessModal.classList.remove("open");
}
if (featherlessModal) {
  featherlessModal.onclick = (e) => { if (e.target === featherlessModal) featherlessModal.classList.remove("open"); };
}

const featherlessForm = document.getElementById("featherlessSettingsForm");
if (featherlessForm) {
  featherlessForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const apiKey = document.getElementById("cfgApiKey").value.trim();
    const model = document.getElementById("cfgModel").value;
    const baseUrl = document.getElementById("cfgBaseUrl").value.trim();

    const payload = { model, base_url: baseUrl };
    if (apiKey) payload.api_key = apiKey;

    await fetch(`${API}/llm/config`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    alert("Featherless AI settings updated successfully.");
    featherlessModal.classList.remove("open");
    refreshStatus();
  });
}

const testConnBtn = document.getElementById("testFeatherlessConnBtn");
if (testConnBtn) {
  testConnBtn.addEventListener("click", async () => {
    testConnBtn.disabled = true;
    testConnBtn.textContent = "Pinging...";
    const resBox = document.getElementById("cfgTestResult");
    resBox.textContent = "Connecting to Featherless AI...";

    const apiKey = document.getElementById("cfgApiKey").value.trim() || undefined;
    const baseUrl = document.getElementById("cfgBaseUrl").value.trim() || undefined;

    try {
      const res = await fetch(`${API}/llm/test`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: apiKey, base_url: baseUrl }),
      }).then(r => r.json());

      if (res.status === "connected") {
        resBox.innerHTML = `<span style="color:var(--green);">✅ ${res.message}</span>`;
      } else {
        resBox.innerHTML = `<span style="color:var(--amber);">⚠️ ${res.message}</span>`;
      }
    } catch (e) {
      resBox.innerHTML = `<span style="color:var(--red);">❌ Connection failed: ${e.message}</span>`;
    } finally {
      testConnBtn.disabled = false;
      testConnBtn.textContent = "Ping Connection";
    }
  });
}

// ---------------------------------------------------------------------------
// Initialization
// ---------------------------------------------------------------------------
connectWs();
refreshStatus();
refreshApprovals();
refreshAgents();
refreshPolicies();
refreshAudit();
setInterval(refreshStatus, 6000);
setInterval(refreshAudit, 6000);
