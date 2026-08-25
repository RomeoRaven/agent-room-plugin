const ROOM_TOKEN = /(^|[\s([{])(@[^\s,;:!?()[\]{}]*)/g;

export function tokensIn(text, knownTokens = []) {
  const known = new Map(Array.from(knownTokens, (token) => [token.toLocaleLowerCase(), token]));
  return Array.from(text.matchAll(ROOM_TOKEN), (match) => {
    let token = match[2];
    while (token.endsWith(".") && !known.has(token.toLocaleLowerCase())) token = token.slice(0, -1);
    return known.get(token.toLocaleLowerCase()) ?? token;
  });
}

export function insertExactMention(draft, mentionToken) {
  for (const match of draft.matchAll(ROOM_TOKEN)) {
    let candidate = match[2];
    let suffix = "";
    while (candidate.endsWith(".") && candidate.toLocaleLowerCase() !== mentionToken.toLocaleLowerCase()) {
      candidate = candidate.slice(0, -1);
      suffix = `.${suffix}`;
    }
    if (candidate.toLocaleLowerCase() === mentionToken.toLocaleLowerCase()) {
      const start = match.index + match[1].length;
      return `${draft.slice(0, start)}${mentionToken}${suffix}${draft.slice(start + match[2].length)}`;
    }
  }
  const prefix = draft.trimEnd();
  return `${prefix}${prefix ? " " : ""}${mentionToken} `;
}

export function recipientsForDraft(draft, members, allowAll) {
  const wakeable = members.filter((member) => member.kind === "agent" && member.mentionable);
  const byToken = new Map(wakeable.map((member) => [member.mention_token.toLocaleLowerCase(), member]));
  const tokens = tokensIn(draft, [...byToken.keys(), ...(allowAll ? ["@all"] : [])]);
  const recipients = [];
  const invalid = [];
  const seen = new Set();
  for (const token of tokens) {
    if (token.toLocaleLowerCase() === "@all" && allowAll) {
      for (const member of wakeable) if (!seen.has(member.principal)) {
        seen.add(member.principal);
        recipients.push(member);
      }
      continue;
    }
    const member = byToken.get(token.toLocaleLowerCase());
    if (!member) invalid.push(token);
    else if (!seen.has(member.principal)) {
      seen.add(member.principal);
      recipients.push(member);
    }
  }
  return { recipients, invalid };
}

export function sortMessages(messages) {
  return [...messages].sort((left, right) => left.sequence - right.sequence);
}

export function restoreActionFocus(root, action) {
  const target = action ? root.querySelector(`[data-action="${action}"]`) : null;
  target?.focus();
  return Boolean(target);
}

export function captureScrollPosition(scroller) {
  return scroller ? { scrollTop: scroller.scrollTop, scrollHeight: scroller.scrollHeight } : null;
}

export function restorePrependedScroll(scroller, previous) {
  if (!scroller || !previous) return false;
  scroller.scrollTop = previous.scrollTop + scroller.scrollHeight - previous.scrollHeight;
  return true;
}

export function groupMembers(members) {
  return {
    wakeable: members.filter((member) => member.kind === "agent" && member.mentionable),
    other: members.filter((member) => member.kind !== "agent" || !member.mentionable),
  };
}

function profileList(values, fallback) {
  const listed = Array.isArray(values) ? values.map((value) => String(value).trim()).filter(Boolean) : [];
  return listed.length ? listed : [fallback];
}

export function buildProfile(member) {
  const profile = member.profile ?? {};
  return {
    purpose: String(profile.summary ?? "").trim() || `${member.display_name} serves this room in the ${member.role} role.`,
    capabilities: profileList(profile.capabilities, "No capabilities listed."),
    bestFor: profileList(profile.best_for, "No best-fit work listed."),
    boundaries: profileList(profile.boundaries, "No boundaries listed."),
    fallback: String(profile.fallback ?? "").trim() || "No fallback guidance listed.",
    host: member.host,
    policy: [
      member.can_post ? "Can post to the room" : "Cannot post to the room",
      member.can_mention ? "Can mention room members" : "Cannot mention room members",
      member.kind === "agent" && member.mentionable
        ? `Wakeable as ${member.mention_token}`
        : "Not wakeable from this room",
    ],
  };
}

export function createApi(kit) {
  const root = "/api/plugins/agent-room";
  const request = async (path, init) => {
    const response = await kit.apiFetch(path, init);
    if (!response.ok) {
      let detail = `Room request failed (${response.status})`;
      try {
        const body = await response.json();
        if (body?.detail) detail = body.detail;
      } catch { /* retain status message */ }
      throw new Error(detail);
    }
    return response.json();
  };
  const json = (method, body) => ({
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const roomPath = (roomId) => `${root}/rooms/${encodeURIComponent(roomId)}`;
  const query = (values) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(values)) {
      if (value !== undefined && value !== null && value !== false && value !== "") params.set(key, String(value));
    }
    const encoded = params.toString();
    return encoded ? `?${encoded}` : "";
  };
  return {
    rooms: (status = "active") => request(`${root}/rooms${query({ status })}`),
    create: (name) => request(`${root}/rooms`, json("POST", { name })),
    rename: (roomId, name) => request(roomPath(roomId), json("PATCH", { name })),
    lifecycle: (roomId, action) => request(`${roomPath(roomId)}/${action}`, { method: "POST" }),
    search: ({ q, scope, roomId, history, limit = 50 }) =>
      request(`${root}/search${query({ q, scope, room_id: roomId, history, limit })}`),
    messages: (roomId, options = {}) => request(`${roomPath(roomId)}/messages${query(options)}`),
    post: (roomId, payload) => request(`${roomPath(roomId)}/post`, json("POST", payload)),
    ack: (roomId, sequence) => request(`${roomPath(roomId)}/ack`, json("POST", { sequence })),
    members: (roomId) => request(`${roomPath(roomId)}/members`),
  };
}

const escapeHtml = (value) => String(value ?? "").replace(/[&<>"]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
})[char]);
const resultOf = (payload) => payload?.result ?? payload ?? {};
const listOf = (payload, key) => resultOf(payload)[key] ?? payload?.[key] ?? [];
const messageId = () => globalThis.crypto?.randomUUID ? crypto.randomUUID() : `room-${Date.now()}-${Math.random().toString(16).slice(2)}`;

function memberState(member, room) {
  if (room.status === "archived") return "Room archived — wake-up unavailable";
  if (member.kind !== "agent" || !member.mentionable) return "Not configured for wake-up";
  return `Wake as ${escapeHtml(member.mention_token)}`;
}

function profileMarkup(member) {
  const profile = buildProfile(member);
  const list = (values) => `<ul>${values.map((value) => `<li>${escapeHtml(value)}</li>`).join("")}</ul>`;
  return `<div class="profile-body" id="profile-${escapeHtml(member.principal)}" hidden>
    <section class="profile-purpose"><h4>Purpose</h4><p>${escapeHtml(profile.purpose)}</p></section>
    <div class="profile-grid">
      <section><h4>Capabilities</h4>${list(profile.capabilities)}</section>
      <section><h4>Best for</h4>${list(profile.bestFor)}</section>
      <section><h4>Boundaries</h4>${list(profile.boundaries)}</section>
    </div>
    <section><h4>Fallback</h4><p>${escapeHtml(profile.fallback)}</p></section>
    <dl><div><dt>Host</dt><dd>${escapeHtml(profile.host)}</dd></div><div><dt>Policy</dt><dd>${escapeHtml(profile.policy.join(" · "))}</dd></div></dl>
  </div>`;
}

function memberMarkup(member, room) {
  const wakeable = room.status !== "archived" && member.kind === "agent" && member.mentionable;
  const identity = `<span class="member-identity"><strong>${escapeHtml(member.display_name)}</strong><code>${escapeHtml(member.mention_token)}</code><small>${escapeHtml(member.role)} · ${escapeHtml(member.host)}</small><span>${escapeHtml(memberState(member, room))}</span></span>`;
  return `<article class="member-card" data-principal="${escapeHtml(member.principal)}">
    <div class="member-summary">
      ${wakeable ? `<button type="button" class="member-name" data-mention="${escapeHtml(member.mention_token)}" aria-label="Mention ${escapeHtml(member.display_name)} as ${escapeHtml(member.mention_token)}">${identity}</button>` : `<div class="member-name is-static">${identity}</div>`}
      <button type="button" class="profile-button" data-profile="${escapeHtml(member.principal)}" aria-expanded="false" aria-controls="profile-${escapeHtml(member.principal)}">Profile</button>
    </div>${profileMarkup(member)}
  </article>`;
}

function rosterMarkup(members, room) {
  const groups = groupMembers(members);
  const group = (title, values, empty) => `<section class="member-group"><h3>${title}<span>${values.length}</span></h3>${values.length ? values.map((member) => memberMarkup(member, room)).join("") : `<p class="empty">${empty}</p>`}</section>`;
  return `${group("Wakeable agents", groups.wakeable, "No agents can be woken from this room.")}${group("Other members", groups.other, "No other members.")}`;
}

function deliveryMarkup(message, mentions, names) {
  const delivery = mentions.filter((mention) => mention.source_message_id === message.id);
  if (!delivery.length) return "";
  return `<ul class="delivery" aria-label="Mention delivery for message ${message.sequence}">${delivery.map((mention) => `<li class="is-${escapeHtml(mention.status)}">${escapeHtml(names.get(mention.target_principal) ?? mention.target_principal)} · ${escapeHtml(mention.status)}${mention.error ? ` · ${escapeHtml(mention.error)}` : ""}</li>`).join("")}</ul>`;
}

function messagesMarkup(state) {
  const names = new Map(state.members.map((member) => [member.principal, member.display_name]));
  const banners = [
    state.room.client_mode && state.ownerOnline === false ? '<div class="banner" role="status">Room owner offline — new posts remain pending.</div>' : "",
    state.contextSequence ? '<div class="banner"><span>Search result context</span><button type="button" data-action="latest">Return to latest</button></div>' : "",
    !state.contextSequence && state.room.history_available && !state.history ? '<button class="history-button" type="button" data-action="history">Show earlier history</button>' : "",
    !state.contextSequence && state.history ? '<button class="history-button" type="button" data-action="current">Show current messages</button>' : "",
    state.hasOlder ? '<button class="history-button" type="button" data-action="older">Load older messages</button>' : "",
  ].join("");
  if (state.loadingMessages) return `${banners}<p class="empty" aria-live="polite">Loading room…</p>`;
  if (state.messageError) return `${banners}<p class="empty error" role="alert">${escapeHtml(state.messageError)}</p>`;
  const messages = sortMessages(state.messages).map((message) => `<article class="message${state.contextSequence === message.sequence ? " is-target" : ""}" data-sequence="${message.sequence}" tabindex="-1">
    <header><strong>${escapeHtml(names.get(message.author_principal) ?? message.author_principal)}</strong><span>#${message.sequence}</span></header>
    <p>${escapeHtml(message.body)}</p>${deliveryMarkup(message, state.mentions, names)}
  </article>`).join("");
  const pending = state.pendingPosts.map((post) => `<article class="message is-pending"><header><strong>You</strong><span>pending</span></header><p>${escapeHtml(post.body)}</p><small>Pending — will send when the Room owner reconnects</small></article>`).join("");
  return `${banners}${messages || pending ? messages + pending : '<p class="empty">No messages yet. Post the first update below.</p>'}`;
}

function roomOptions(state) {
  const options = (rooms) => rooms.map((room) => `<option value="${escapeHtml(room.id)}"${room.id === state.room?.id ? " selected" : ""}>${escapeHtml(room.name)}${room.unread_mentions ? ` — ${room.unread_mentions} mentions` : room.unread_count ? ` — ${room.unread_count} unread` : ""}</option>`).join("");
  return `<optgroup label="Active rooms">${options(state.activeRooms)}</optgroup><optgroup label="Archived rooms">${options(state.archivedRooms)}</optgroup>`;
}

export function shellMarkup(state) {
  if (!state.room) return `<main class="no-rooms"><h1>Rooms</h1><p>No active rooms.</p><button type="button" data-action="create">New room</button></main>`;
  const archived = state.room.status === "archived";
  const ownerControls = state.room.client_mode ? "" : `<button type="button" data-action="create">New room</button><button type="button" data-action="search">Search rooms</button>
      ${archived ? '<button type="button" data-action="restore">Restore room</button>' : '<button type="button" data-action="rename">Rename room</button><button type="button" data-action="reset">Start fresh</button><button type="button" class="danger-button" data-action="archive">Archive room</button>'}`;
  return `<main class="room-shell">
    <header class="room-toolbar">
      <label class="sr-only" for="room-switcher">Switch room</label><select id="room-switcher" aria-label="Switch room, current: ${escapeHtml(state.room.name)}">${roomOptions(state)}</select>
      ${ownerControls}
    </header>
    <div class="room-grid">
      <aside class="roster" aria-label="Room members"><header><h2>Members</h2><span>${state.members.length}</span></header>${rosterMarkup(state.members, state.room)}</aside>
      <section class="conversation" aria-labelledby="room-title"><header><div><p class="eyebrow">Shared transcript</p><h1 id="room-title">${escapeHtml(state.room.name)}</h1></div><span class="sequence">#${state.room.latest_sequence ?? state.messages.at(-1)?.sequence ?? 0}</span></header>
        <div class="messages" id="room-messages">${messagesMarkup(state)}</div>
      </section>
    </div>
    ${archived ? `<div class="recipient-guide${state.postError ? " error" : ""}" role="${state.postError ? "alert" : "status"}">${escapeHtml(state.postError || "Archived room — restore to post")}</div>` : composerMarkup(state)}
    ${state.modal ? modalMarkup(state) : ""}
  </main>`;
}

export function composerMarkup(state) {
  const selection = recipientsForDraft(state.draft, state.members, state.allowAll);
  const guidance = selection.invalid.length
    ? `Unknown agent ${selection.invalid.join(", ")} — choose a suggested agent`
    : selection.recipients.length
      ? `Will notify ${selection.recipients.map((member) => member.display_name).join(", ")}`
      : "Post to room only — no agents notified";
  return `<div class="composer-wrap"><p class="recipient-guide${selection.invalid.length ? " error" : selection.recipients.length ? " is-notify" : ""}" role="${selection.invalid.length ? "alert" : "status"}">${escapeHtml(state.postError || guidance)}</p>
    <form id="composer-form" class="composer"><label class="sr-only" for="room-composer">Room message</label><textarea id="room-composer" rows="2" aria-label="Room message" aria-autocomplete="list" aria-expanded="false" placeholder="Post to the room, or type @ to notify an agent…">${escapeHtml(state.draft)}</textarea><button type="submit" aria-label="Post message"${!state.draft.trim() || selection.invalid.length ? " disabled" : ""}>Post message</button><div id="mention-picker" class="mention-picker" role="listbox" aria-label="Mention an agent" hidden></div></form></div>`;
}

function modalMarkup(state) {
  if (state.modal === "search") return `<div class="modal-backdrop"><section class="modal search-modal" role="dialog" aria-modal="true" aria-label="Search rooms"><header><h2>Search rooms</h2><button type="button" data-action="close-modal">Close</button></header><form id="search-form"><label>Search messages<input id="search-query" required value="${escapeHtml(state.searchQuery)}"></label><label>Search scope<select id="search-scope"><option value="current">Current room</option><option value="all">All active rooms</option><option value="archived">Archived rooms</option></select></label><label class="check"><input id="search-history" type="checkbox"> Include earlier history</label><button type="submit">Search</button></form><div class="search-results" aria-live="polite">${state.searchResults.map((item) => `<button type="button" data-result-room="${escapeHtml(item.room_id)}" data-result-sequence="${item.sequence}"><strong>${escapeHtml(item.room_name)}</strong><span>#${item.sequence}${item.earlier ? " · earlier history" : ""}</span><p>${escapeHtml(item.snippet ?? item.body)}</p></button>`).join("")}${state.searched && !state.searchResults.length ? "<p>No matching messages.</p>" : ""}</div><p class="modal-error" role="alert">${escapeHtml(state.modalError)}</p></section></div>`;
  const creating = state.modal === "create";
  return `<div class="modal-backdrop"><section class="modal" role="dialog" aria-modal="true" aria-label="${creating ? "Create room" : "Rename room"}"><h2>${creating ? "Create room" : "Rename room"}</h2><form id="name-form"><label>Room name<input id="room-name" maxlength="120" required value="${escapeHtml(creating ? "" : state.room.name)}"></label><p class="modal-error" role="alert">${escapeHtml(state.modalError)}</p><footer><button type="button" data-action="close-modal">Cancel</button><button type="submit">${creating ? "Create" : "Save"}</button></footer></form></section></div>`;
}

function mentionSuggestions(draft, members, allowAll) {
  const match = /(^|[\s([{])@([^\s,;:!?()[\]{}]*)$/.exec(draft);
  if (!match) return [];
  const query = match[2].toLocaleLowerCase();
  const wakeable = members.filter((member) => member.kind === "agent" && member.mentionable);
  const all = allowAll ? [{ principal: "__all__", display_name: "All wakeable agents", mention_token: "@all" }] : [];
  return [...wakeable, ...all].filter((member) => member.display_name.toLocaleLowerCase().includes(query) || member.mention_token.slice(1).toLocaleLowerCase().includes(query));
}

function createController(root, api) {
  const state = {
    activeRooms: [], archivedRooms: [], room: null, members: [], messages: [], mentions: [], pendingPosts: [],
    ownerOnline: true, hasOlder: false, history: false, contextSequence: null, loadingMessages: false,
    messageError: "", draft: "", postError: "", allowAll: false, modal: null, modalError: "",
    searchQuery: "", searchResults: [], searched: false, suggestionIndex: -1, returnFocusAction: null,
  };

  const render = () => {
    root.innerHTML = shellMarkup(state);
    root.setAttribute("aria-busy", "false");
    bind();
    if (state.contextSequence) requestAnimationFrame(() => root.querySelector(`[data-sequence="${state.contextSequence}"]`)?.focus());
  };
  const failFatal = (error) => {
    root.innerHTML = `<p class="state error" role="alert">Rooms unavailable: ${escapeHtml(error.message)}</p><button class="retry" type="button">Retry</button>`;
    root.querySelector(".retry")?.addEventListener("click", () => void loadRooms());
  };
  const loadRooms = async (preferred) => {
    root.setAttribute("aria-busy", "true");
    try {
      const [active, archived] = await Promise.all([api.rooms("active"), api.rooms("archived")]);
      state.activeRooms = listOf(active, "rooms");
      state.archivedRooms = listOf(archived, "rooms");
      const rooms = [...state.activeRooms, ...state.archivedRooms];
      state.room = rooms.find((room) => room.id === (preferred ?? state.room?.id)) ?? state.activeRooms[0] ?? state.archivedRooms[0] ?? null;
      if (!state.room) { render(); return; }
      await loadRoom();
    } catch (error) { failFatal(error); }
  };
  const loadRoom = async ({ appendOlder = false } = {}) => {
    const previousScroll = appendOlder ? captureScrollPosition(root.querySelector("#room-messages")) : null;
    const restoreScroll = () => {
      if (previousScroll) requestAnimationFrame(() => restorePrependedScroll(root.querySelector("#room-messages"), previousScroll));
    };
    state.loadingMessages = !appendOlder;
    state.messageError = "";
    if (!appendOlder) render();
    try {
      const options = state.contextSequence
        ? { around: state.contextSequence, limit: 21, history: true }
        : appendOlder
          ? { before: Math.min(...state.messages.map((message) => message.sequence)), limit: 50, history: state.history }
          : { limit: 50, history: state.history };
      const [syncPayload, memberPayload] = await Promise.all([api.messages(state.room.id, options), api.members(state.room.id)]);
      const sync = resultOf(syncPayload);
      const next = sync.messages ?? [];
      state.messages = appendOlder ? sortMessages([...next, ...state.messages].filter((message, index, all) => all.findIndex((item) => item.id === message.id) === index)) : sortMessages(next);
      state.mentions = appendOlder ? [...(sync.mentions ?? []), ...state.mentions] : (sync.mentions ?? []);
      state.pendingPosts = sync.pending_posts ?? [];
      state.ownerOnline = sync.owner_online ?? state.room.owner_online ?? true;
      state.hasOlder = Boolean(sync.has_older ?? sync.has_more);
      state.members = listOf(memberPayload, "members");
      state.allowAll = !state.room.client_mode && state.members.some((member) => (member.kind === "human" || member.kind === "host") && member.can_mention);
      state.loadingMessages = false;
      render();
      restoreScroll();
      const latest = state.messages.at(-1)?.sequence;
      if (!state.contextSequence && latest) {
        try { await api.ack(state.room.id, latest); } catch { /* acknowledgement is best effort */ }
      }
    } catch (error) {
      state.loadingMessages = false;
      state.messageError = error.message;
      render();
      restoreScroll();
    }
  };
  const lifecycle = async (action) => {
    const copy = action === "archive" ? "Archive this room? It remains searchable and can be restored." : action === "reset" ? "Start fresh? Earlier history remains searchable and viewable." : "Restore this room?";
    if (!window.confirm(copy)) return;
    try { await api.lifecycle(state.room.id, action); await loadRooms(state.room.id); }
    catch (error) { state.postError = error.message; render(); }
  };
  const openModal = (modal, trigger) => { state.modal = modal; state.modalError = ""; state.returnFocusAction = trigger.dataset.action; render(); requestAnimationFrame(() => root.querySelector(".modal input")?.focus()); };
  const closeModal = () => { const action = state.returnFocusAction; state.modal = null; render(); requestAnimationFrame(() => restoreActionFocus(root, action)); };
  const updatePicker = (composer) => {
    const picker = root.querySelector("#mention-picker");
    const suggestions = mentionSuggestions(composer.value, state.members, state.allowAll);
    state.suggestionIndex = Math.min(state.suggestionIndex, suggestions.length - 1);
    picker.hidden = !suggestions.length;
    composer.setAttribute("aria-expanded", String(Boolean(suggestions.length)));
    picker.innerHTML = suggestions.map((member, index) => `<button type="button" role="option" data-pick="${escapeHtml(member.mention_token)}" aria-selected="${index === state.suggestionIndex}"><strong>${escapeHtml(member.display_name)}</strong><span>${escapeHtml(member.mention_token)}</span></button>`).join("");
  };
  const pick = (token) => {
    const composer = root.querySelector("#room-composer");
    const match = /(^|[\s([{])@([^\s,;:!?()[\]{}]*)$/.exec(composer.value);
    state.draft = match ? `${composer.value.slice(0, match.index + match[1].length)}${token} ` : insertExactMention(composer.value, token);
    render();
    root.querySelector("#room-composer")?.focus();
  };
  const submitPost = async () => {
    const body = state.draft.trim();
    const selection = recipientsForDraft(body, state.members, state.allowAll);
    if (!body || selection.invalid.length) { render(); return; }
    state.postError = "";
    try {
      await api.post(state.room.id, { client_message_id: messageId(), body });
      state.draft = "";
      await loadRoom();
      root.querySelector("#room-composer")?.focus();
    } catch (error) { state.postError = error.message; render(); root.querySelector("#room-composer")?.focus(); }
  };

  const bind = () => {
    root.querySelector("#room-switcher")?.addEventListener("change", (event) => {
      state.room = [...state.activeRooms, ...state.archivedRooms].find((room) => room.id === event.target.value);
      state.history = false; state.contextSequence = null; state.draft = ""; void loadRoom();
    });
    root.querySelectorAll("[data-action]").forEach((button) => button.addEventListener("click", () => {
      const action = button.dataset.action;
      if (["create", "rename", "search"].includes(action)) openModal(action, button);
      else if (action === "close-modal") closeModal();
      else if (["archive", "restore", "reset"].includes(action)) void lifecycle(action);
      else if (action === "history") { state.history = true; void loadRoom(); }
      else if (action === "current") { state.history = false; void loadRoom(); }
      else if (action === "older") void loadRoom({ appendOlder: true });
      else if (action === "latest") { state.contextSequence = null; state.history = false; void loadRoom(); }
    }));
    root.querySelectorAll("[data-mention]").forEach((button) => button.addEventListener("click", () => {
      state.draft = insertExactMention(state.draft, button.dataset.mention);
      render();
      const composer = root.querySelector("#room-composer"); composer.focus();
    }));
    root.querySelectorAll("[data-profile]").forEach((button) => button.addEventListener("click", () => {
      const body = root.querySelector(`#profile-${CSS.escape(button.dataset.profile)}`);
      const open = button.getAttribute("aria-expanded") === "true";
      button.setAttribute("aria-expanded", String(!open)); body.hidden = open;
    }));
    root.querySelector("#composer-form")?.addEventListener("submit", (event) => { event.preventDefault(); void submitPost(); });
    const composer = root.querySelector("#room-composer");
    composer?.addEventListener("input", () => { state.draft = composer.value; state.postError = ""; updatePicker(composer); });
    composer?.addEventListener("keydown", (event) => {
      const suggestions = mentionSuggestions(composer.value, state.members, state.allowAll);
      if (suggestions.length && event.key === "ArrowDown") { event.preventDefault(); state.suggestionIndex = (state.suggestionIndex + 1) % suggestions.length; updatePicker(composer); }
      else if (suggestions.length && event.key === "ArrowUp") { event.preventDefault(); state.suggestionIndex = state.suggestionIndex <= 0 ? suggestions.length - 1 : state.suggestionIndex - 1; updatePicker(composer); }
      else if (suggestions.length && (event.key === "Enter" || event.key === "Tab")) { event.preventDefault(); pick(suggestions[state.suggestionIndex >= 0 ? state.suggestionIndex : 0].mention_token); }
      else if (event.key === "Escape") { root.querySelector("#mention-picker").hidden = true; composer.setAttribute("aria-expanded", "false"); }
      else if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submitPost(); }
    });
    root.querySelector("#mention-picker")?.addEventListener("click", (event) => { const option = event.target.closest("[data-pick]"); if (option) pick(option.dataset.pick); });
    root.querySelector("#name-form")?.addEventListener("submit", async (event) => {
      event.preventDefault(); const name = root.querySelector("#room-name").value.trim(); if (!name) return;
      try { const response = state.modal === "create" ? await api.create(name) : await api.rename(state.room.id, name); const id = resultOf(response).room?.id ?? state.room.id; state.modal = null; await loadRooms(id); }
      catch (error) { state.modalError = error.message; render(); }
    });
    root.querySelector("#search-form")?.addEventListener("submit", async (event) => {
      event.preventDefault(); state.searchQuery = root.querySelector("#search-query").value.trim();
      try { const payload = await api.search({ q: state.searchQuery, scope: root.querySelector("#search-scope").value, roomId: state.room.id, history: root.querySelector("#search-history").checked }); state.searchResults = listOf(payload, "results"); state.searched = true; state.modalError = ""; render(); }
      catch (error) { state.modalError = error.message; render(); }
    });
    root.querySelectorAll("[data-result-room]").forEach((button) => button.addEventListener("click", () => {
      state.modal = null; state.contextSequence = Number(button.dataset.resultSequence); state.room = [...state.activeRooms, ...state.archivedRooms].find((room) => room.id === button.dataset.resultRoom) ?? state.room; void loadRoom();
    }));
    root.querySelector(".modal")?.addEventListener("keydown", (event) => { if (event.key === "Escape") { event.stopPropagation(); closeModal(); } });
  };
  return { loadRooms };
}

let started = false;
export function start(kit) {
  if (started) return;
  started = true;
  const root = document.getElementById("room-app");
  document.getElementById("loading")?.remove();
  createController(root, createApi(kit)).loadRooms();
}
