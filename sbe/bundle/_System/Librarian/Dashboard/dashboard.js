const $ = (id) => document.getElementById(id);
const labels = {
  ready: "Librarian pronto",
  working: "Librarian in elaborazione",
  queued: "Librarian in coda",
  paused: "Librarian in pausa",
  attention: "Librarian richiede attenzione",
};
let lastStatus = null;

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("it-IT", { dateStyle: "short", timeStyle: "medium" }).format(date);
}

function providerLabel(value) {
  if (typeof value === "boolean") return value ? "Installato · stato non verificato" : "Non installato";
  if (!value) return "Stato non verificato";
  const activity = value.client_activity?.[0] || value.general_activity;
  return activity?.observed_at ? `Attività rilevata · ${formatTime(activity.observed_at)}` : "Nessuna attività client rilevata";
}

function renderProvider(id, value) {
  const node = $(id);
  node.className = "provider-signals";
  const lines = [];
  const activities = value?.client_activity || (value?.general_activity ? [value.general_activity] : []);
  for (const activity of activities) lines.push(`Attività ${activity.client || "client"} rilevata · ${formatTime(activity.observed_at)}`);
  if (!activities.length) lines.push(providerLabel(value));
  const call = value?.last_orchestrated_call || (value?.observed_at ? value : null);
  lines.push(call ? `Ultima chiamata ${systemLabel(call.source)}: ${statusLabel(call.status)} · ${formatTime(call.observed_at)}` : "Nessuna chiamata orchestrata registrata");
  const quota = value?.last_quota_check;
  lines.push(quota?.status === "usable" ? `Quota utilizzabile all’ultima chiamata · ${formatTime(quota.observed_at)}`
    : quota?.status === "exhausted" ? `Quota esaurita all’ultimo tentativo · ${formatTime(quota.observed_at)}` : "Quota non verificata");
  node.replaceChildren(...lines.map(text => { const line = document.createElement("span"); line.textContent = text; return line; }));
}

function statusLabel(status) {
  return ({ observed: "Attività rilevata", started: "Avviata", completed: "Completata", unavailable: "Tentativo non disponibile",
    exhausted: "Quota esaurita", unknown: "Non verificato", configured: "Configurato", invalid: "Non valido",
    disabled: "Disabilitato", historical: "Storico", working: "In esecuzione", builtin: "Integrato" })[status] || status || "Non verificato";
}

function resultBadge(status) {
  const badge = document.createElement("span");
  badge.className = `result ${String(status || "unknown").replace(/[^A-Za-z0-9_-]/g, "_").toUpperCase()}`;
  badge.textContent = statusLabel(status);
  return badge;
}

function appendCells(row, values) {
  for (const value of values) {
    const cell = document.createElement("td");
    cell.textContent = value ?? "—";
    row.appendChild(cell);
  }
}

function roleLabel(role) {
  return ({ coordinator: "Coordinatore", investigator: "Investigatore", executor: "Writer", reviewer: "Revisore" })[role] || role;
}

function systemLabel(value) {
  if (value === "ruflo") return "Ruflo";
  if (value === "librarian") return "Librarian";
  if (value === "general") return "Generale";
  return value || "—";
}

function openDisclosureKeys(container, selector, attribute) {
  return new Set([...container.querySelectorAll(`${selector}[open]`)].map((node) => node.dataset[attribute]).filter(Boolean));
}

function profileCard(item, openProfiles) {
  const card = document.createElement("details"); card.className = "profile-card";
  card.dataset.profileName = item.name;
  card.open = openProfiles.has(item.name);
  const summary = document.createElement("summary");
  const identity = document.createElement("div"); identity.className = "profile-identity";
  const name = document.createElement("strong"); name.textContent = item.name;
  const compact = document.createElement("small"); compact.textContent = `${item.system} · ${item.provider} · ${item.function || item.role}`;
  identity.append(name, compact);
  const badges = document.createElement("div"); badges.className = "profile-badges";
  badges.append(resultBadge(item.lifecycle === "current" ? "corrente" : item.lifecycle === "builtin" ? "builtin" : "storico"), resultBadge(item.status));
  summary.append(identity, badges);
  const body = document.createElement("div"); body.className = "profile-body";
  const description = document.createElement("p"); description.textContent = item.description || "Nessuna descrizione registrata.";
  const responsibility = document.createElement("p"); responsibility.textContent = item.responsibility;
  const metadata = document.createElement("dl");
  for (const [label, value] of [
    ["Sistema", item.system], ["Provider", item.provider], ["Funzione", item.function],
    ["Ruolo tecnico CAO", item.role], ["Stato", item.status], ["Ultima attività CAO", formatTime(item.last_active)],
  ]) {
    const term = document.createElement("dt"); term.textContent = label;
    const detail = document.createElement("dd"); detail.textContent = value || "—";
    metadata.append(term, detail);
  }
  body.append(description, responsibility, metadata); card.append(summary, body); return card;
}

function renderRufloTeams(teams, providers = {}) {
  const container = $("ruflo-teams");
  const hadCards = container.children.length > 0;
  const openTeams = openDisclosureKeys(container, ".team-card", "teamId");
  const openRoles = openDisclosureKeys(container, ".role-card", "roleKey");
  const allAgents = teams.agents || [];
  const allTasks = teams.tasks || [];
  const cards = (teams.teams || []).map((team, index) => {
    const details = document.createElement("details");
    details.className = "team-card";
    details.dataset.teamId = team.team_id;
    details.open = hadCards ? openTeams.has(team.team_id) : team.status === "active" || index === 0;
    const summary = document.createElement("summary");
    const heading = document.createElement("div");
    heading.className = "team-heading";
    const title = document.createElement("strong"); title.textContent = team.objective;
    const meta = document.createElement("small");
    meta.textContent = `${team.project} · ${team.open_tasks}/${team.tasks} task aperti · aggiornato ${formatTime(team.updated_at)}`;
    heading.append(title, meta);
    const state = document.createElement("div"); state.className = "team-state";
    state.append(resultBadge(team.status), resultBadge(team.circuit));
    const excludedProviders = team.unavailable_providers || [];
    const owner = providers?.[team.writer_provider];
    const ownerQuotaExhausted = owner?.last_quota_check?.status === "exhausted"
      && owner?.last_orchestrated_call?.team_id === team.team_id
      && owner.last_quota_check.observed_at === owner.last_orchestrated_call.observed_at;
    if (team.degraded_failover) state.append(resultBadge("fallback degradato"));
    if (team.status === "active" && ownerQuotaExhausted) state.append(resultBadge("handoff richiesto"));
    for (const provider of excludedProviders) state.append(resultBadge(`${provider} escluso`));
    summary.append(heading, state);
    details.appendChild(summary);

    const providerPair = document.createElement("p");
    providerPair.className = `provider-pair${team.provider_separation ? " verified" : " warning"}`;
    providerPair.textContent = team.provider_separation
      ? `Sessione VS Code: ${team.coordinator_provider || team.writer_provider} · Coordinatore + writer: ${team.writer_provider} · Revisore preferito: ${team.reviewer_provider}`
      : "Team storico: separazione provider non registrata";
    details.appendChild(providerPair);
    if (team.status === "active" && ownerQuotaExhausted) {
      const required = document.createElement("p");
      required.className = "provider-pair warning";
      required.textContent = `${team.writer_provider} risulta senza quota nell'ultima chiamata orchestrata. Apri l'altro agente in VS Code: troverà questo team e potrà assumerne coordinazione e scrittura.`;
      details.appendChild(required);
    }
    if (team.last_handoff) {
      const handoff = document.createElement("p");
      handoff.className = "provider-pair warning";
      handoff.textContent = `Ultimo handoff: ${team.last_handoff.from_provider} → ${team.last_handoff.to_provider} · ${formatTime(team.last_handoff.at)} · ${team.last_handoff.reason || "quota esaurita"}. ${team.last_handoff.from_provider} non verrà più interrogato in questo team.`;
      details.appendChild(handoff);
    } else if (team.last_review_mode === "same_provider_fallback") {
      const degraded = document.createElement("p");
      degraded.className = "provider-pair warning";
      degraded.textContent = "Ultima revisione: stesso provider del writer, usato perché il revisore preferito non era disponibile.";
      details.appendChild(degraded);
    }

    const agents = allAgents.filter((agent) => agent.team_id === team.team_id);
    const agentGrid = document.createElement("div"); agentGrid.className = "role-grid";
    for (const agent of agents) {
      const card = document.createElement("details"); card.className = "role-card";
      const roleKey = `${team.team_id}:${agent.role}`;
      card.dataset.roleKey = roleKey;
      card.open = openRoles.has(roleKey);
      const top = document.createElement("summary"); top.className = "role-card-top";
      const name = document.createElement("strong"); name.textContent = roleLabel(agent.role);
      top.append(name, resultBadge(agent.status));
      const body = document.createElement("div"); body.className = "role-card-body";
      const responsibility = document.createElement("p"); responsibility.textContent = agent.responsibility || "Ruolo registrato nel team.";
      const id = document.createElement("small"); id.textContent = `ID: ${agent.agent_id || "—"}`;
      const task = document.createElement("p"); task.textContent = agent.task || "Nessuna task assegnata";
      const provider = document.createElement("small"); provider.textContent = `Provider: ${agent.provider || "in attesa"}`;
      const providerRule = document.createElement("small"); providerRule.textContent = `Regola: ${agent.provider_rule || "assegnazione del team"}`;
      body.append(responsibility, task, id, provider, providerRule);
      card.append(top, body); agentGrid.appendChild(card);
    }
    details.appendChild(agentGrid);

    const teamTasks = allTasks.filter((task) => task.team_id === team.team_id);
    if (teamTasks.length) {
      const wrap = document.createElement("div"); wrap.className = "table-wrap team-tasks";
      const table = document.createElement("table");
      table.innerHTML = "<thead><tr><th>Task</th><th>Agente</th><th>Stato</th><th>Avanzamento</th><th>Provider</th><th>Aggiornata</th></tr></thead>";
      const body = document.createElement("tbody");
      for (const task of teamTasks) {
        const row = document.createElement("tr");
        const description = document.createElement("td");
        const strong = document.createElement("strong"); strong.textContent = task.description;
        const result = document.createElement("small"); result.textContent = task.result_excerpt || task.task_id;
        description.append(strong, result);
        if (task.role === "reviewer" && task.reviewed_writer_provider) {
          const author = document.createElement("small"); author.textContent = `Codice scritto da: ${task.reviewed_writer_provider}`;
          description.appendChild(author);
        }
        if (task.review_mode === "same_provider_fallback") description.appendChild(resultBadge("fallback degradato"));
        else if (task.review_mode === "independent") description.appendChild(resultBadge("revisione indipendente"));
        else if (task.fallback_used) description.appendChild(resultBadge("fallback provider"));
        if (task.worker_verdict) description.appendChild(resultBadge(`verdict ${task.worker_verdict}`));
        row.appendChild(description);
        appendCells(row, [roleLabel(task.role)]);
        const status = document.createElement("td"); status.appendChild(resultBadge(task.status)); row.appendChild(status);
        appendCells(row, [`${task.progress}%`, task.provider || "Locale / VS Code", formatTime(task.updated_at)]);
        body.appendChild(row);
      }
      table.appendChild(body); wrap.appendChild(table); details.appendChild(wrap);
    }
    return details;
  });
  container.replaceChildren(...cards);
  $("ruflo-teams-empty").hidden = cards.length > 0;
}

function render(data) {
  lastStatus = data;
  $("overall-label").textContent = labels[data.overall] || data.overall;
  $("overall-dot").className = `status-dot ${data.overall}`;
  $("updated-at").textContent = `Aggiornato ${formatTime(data.generated_at)}`;
  $("source-total").textContent = data.sources.total;
  $("source-organized").textContent = `${data.sources.organized} organizzati`;
  $("source-linked").textContent = data.sources.linked;
  $("source-unlinked").textContent = `${data.sources.unlinked} senza nodo`;
  $("pending-count").textContent = data.sources.pending_ready.length;
  $("backoff-count").textContent = data.sources.backoff.length;
  $("knowledge-count").textContent = data.knowledge_count;
  $("area-count").textContent = data.area_count;
  $("active-agents").textContent = data.agents.active_workers;
  $("ecosystem-profile-count").textContent = data.agents.configured_count;
  $("configured-agents").textContent = `${data.agents.current_profile_count || 0} correnti · ${data.agents.legacy_profile_count || 0} storici`;
  const teams = data.ruflo.teams || { active_teams: 0, active_tasks: 0, paused_quota: 0, teams: [] };
  $("ruflo-team-count").textContent = teams.active_teams;
  $("ruflo-task-count").textContent = `${teams.active_tasks} task aperti`;
  renderProvider("antigravity-state", data.providers.antigravity);
  renderProvider("codex-state", data.providers.codex);
  $("ruflo-state").textContent = data.ruflo.installed && data.ruflo.memory_db
    ? (teams.available ? "Memoria + team pronti" : "Memoria pronta")
    : "Da verificare";
  $("ruflo-quota-state").textContent = teams.paused_quota ? `${teams.paused_quota} in pausa quota` : "Circuiti regolari";
  renderRufloTeams(teams, data.providers);
  $("cao-state").textContent = data.cao.health ? "Attivo" : "Non raggiungibile";
  $("service-state").textContent = data.service.installed ? (data.service.state === "running" ? "In esecuzione" : "In attesa") : "Non installato";
  $("failure-count").textContent = `${data.circuit.consecutive_dual_failures || 0} / ${data.circuit.threshold || 10}`;
  $("circuit-badge").textContent = data.circuit.state === "closed" ? "Circuito chiuso" : "Circuito aperto";
  $("control-copy").textContent = data.overall === "paused"
    ? "Il Librarian non chiamerà provider finché non premi Riprendi."
    : data.overall === "working"
      ? "Un cluster è in elaborazione. Il lock impedisce esecuzioni sovrapposte."
      : data.sources.pending_ready.length
        ? `${data.sources.pending_ready.length} Source pronti per il prossimo ciclo.`
        : "Il servizio monitora la coda e partirà automaticamente quando arrivano nuovi documenti.";
  $("pause-button").disabled = data.circuit.state !== "closed";
  $("resume-button").disabled = data.circuit.state === "closed";
  $("retry-all-button").disabled = data.sources.backoff.length === 0;

  const providerCalls = data.providers.calls || [];
  const providerBody = $("provider-calls-table");
  providerBody.replaceChildren(...providerCalls.map((item) => {
    const row = document.createElement("tr");
    appendCells(row, [formatTime(item.observed_at), item.provider, systemLabel(item.source), item.subject, item.profile || "—"]);
    const status = document.createElement("td"); status.appendChild(resultBadge(item.status)); row.appendChild(status);
    if (item.error) row.title = item.error;
    return row;
  }));
  $("provider-calls-empty").hidden = providerCalls.length > 0;

  const profiles = data.agents.profiles || [];
  const currentContainer = $("current-profiles-list");
  const historicalContainer = $("historical-profiles-list");
  const builtinContainer = $("builtin-profiles-list");
  const openProfiles = new Set([
    ...openDisclosureKeys(currentContainer, ".profile-card", "profileName"),
    ...openDisclosureKeys(historicalContainer, ".profile-card", "profileName"),
    ...openDisclosureKeys(builtinContainer, ".profile-card", "profileName"),
  ]);
  const currentProfiles = profiles.filter((item) => item.lifecycle === "current");
  const historicalProfiles = profiles.filter((item) => item.lifecycle !== "current");
  $("profile-count").textContent = `${profiles.length} configurati · ${currentProfiles.length} correnti`;
  $("historical-profile-count").textContent = `${historicalProfiles.length} storici`;
  currentContainer.replaceChildren(...currentProfiles.map((item) => profileCard(item, openProfiles)));
  historicalContainer.replaceChildren(...historicalProfiles.map((item) => profileCard(item, openProfiles)));
  $("historical-profiles").hidden = historicalProfiles.length === 0;
  const builtins = data.agents.builtin_profiles || [];
  builtinContainer.replaceChildren(...builtins.map(item => profileCard(item, openProfiles)));
  $("builtin-profile-count").textContent = `${builtins.length} aggiuntivi`;
  const inventory = data.agent_configuration || {};
  $("agent-configuration-table").replaceChildren(...(inventory.items || []).map(item => {
    const row = document.createElement("tr");
    appendCells(row, [item.provider, item.kind, item.name, item.origin, "Globale", statusLabel(item.status), (item.events || []).join(", ") || "—"]);
    return row;
  }));
  $("configuration-note").textContent = inventory.note || "Inventario non disponibile: aggiornare il runtime.";
  $("configuration-issues").textContent = (inventory.issues || []).map(item => `${item.provider} · ${item.origin}: ${statusLabel(item.status)}`).join("; ");
  $("hooks-note").textContent = (inventory.items || []).some(item => item.kind === "Hook")
    ? "Hook rilevati: gli eventi sono indicati nella tabella. I comandi non vengono mostrati."
    : "Nessun hook rilevato nelle configurazioni globali esaminate. Le procedure Ruflo e Second Brain funzionano tramite skill e MCP.";

  const terminals = data.agents.cao_terminals || [];
  $("cao-terminals-table").replaceChildren(...terminals.map((item) => {
    const row = document.createElement("tr");
    appendCells(row, [formatTime(item.last_active), item.profile, item.provider, item.session]);
    const status = document.createElement("td"); status.appendChild(resultBadge(item.status)); row.appendChild(status);
    appendCells(row, [item.workspace || "—"]);
    return row;
  }));
  $("cao-terminals-empty").hidden = terminals.length > 0;

  const reasons = data.sources.unlinked_breakdown || {};
  for (const key of ["technical_reference", "low_information", "sensitive_reference", "administrative", "pending", "other"]) {
    $(`reason-${key}`).textContent = reasons[key] || 0;
  }

  const backoffBody = $("backoff-table");
  backoffBody.replaceChildren(...data.sources.backoff.map((item) => {
    const row = document.createElement("tr");
    for (const value of [item.title, item.batch || "—", item.failures, formatTime(item.next_retry_at)]) {
      const cell = document.createElement("td"); cell.textContent = value; row.appendChild(cell);
    }
    const action = document.createElement("td");
    const button = document.createElement("button"); button.className = "quiet"; button.textContent = "Riprova";
    button.addEventListener("click", () => retryInvalid(item.source_note, item.title));
    action.appendChild(button); row.appendChild(action); return row;
  }));
  $("backoff-empty").hidden = data.sources.backoff.length > 0;

  const runBody = $("runs-table");
  runBody.replaceChildren(...data.runs.map((item) => {
    const row = document.createElement("tr");
    for (const value of [formatTime(item.time), item.cluster, item.sources || "—", item.provider || "Locale"]) {
      const cell = document.createElement("td"); cell.textContent = value; row.appendChild(cell);
    }
    const result = document.createElement("td");
    result.appendChild(resultBadge(item.status)); row.appendChild(result); return row;
  }));
}

function toast(message, error = false) {
  const node = $("toast"); node.textContent = message; node.className = `show${error ? " error" : ""}`;
  window.clearTimeout(toast.timer); toast.timer = window.setTimeout(() => node.className = "", 3500);
}

async function load() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  } catch (error) { toast(`Dashboard non raggiungibile: ${error.message}`, true); }
}

async function action(path, body = {}, message = "Operazione completata") {
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-SecondBrain-Dashboard": "1" },
      body: JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    toast(message); window.setTimeout(load, 700);
  } catch (error) { toast(error.message, true); }
}

function retryInvalid(sourceNote, title) {
  if (window.confirm(`Riprova ora “${title}”? Questo può usare quota AI.`)) action("/api/retry-invalid", { source_note: sourceNote }, "Documento rimesso in coda");
}

$("run-button").addEventListener("click", () => action("/api/run-now", {}, "Ciclo Librarian avviato"));
$("pause-button").addEventListener("click", () => { if (window.confirm("Mettere in pausa tutte le chiamate AI del Librarian?")) action("/api/pause", {}, "Librarian in pausa"); });
$("resume-button").addEventListener("click", () => action("/api/resume", {}, "Librarian riattivato"));
$("refresh-button").addEventListener("click", load);
$("retry-all-button").addEventListener("click", () => { if (window.confirm("Rimettere subito in coda tutti i documenti in backoff? Questo può usare quota AI.")) action("/api/retry-invalid", {}, "Documenti rimessi in coda"); });
load();
window.setInterval(load, 5000);
