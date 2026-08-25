import test from "node:test";
import assert from "node:assert/strict";

import {
  buildProfile,
  composerMarkup,
  captureScrollPosition,
  createApi,
  groupMembers,
  insertExactMention,
  recipientsForDraft,
  restoreActionFocus,
  restorePrependedScroll,
  shellMarkup,
  sortMessages,
  tokensIn,
} from "../web/room.js";

const members = [
  { principal: "hermes", kind: "agent", display_name: "Hermes", role: "member", mention_token: "@Hermes.S1", host: "s1", can_post: true, can_mention: false, mentionable: true },
  { principal: "dennis", kind: "human", display_name: "Dennis", role: "owner", mention_token: "@Dennis", host: "operator", can_post: true, can_mention: true, mentionable: false },
];

test("typed exact mentions select configured recipients and reject unknown tokens", () => {
  assert.deepEqual(tokensIn("please ask @hermes.s1, then @Missing", members.map((m) => m.mention_token)), ["@Hermes.S1", "@Missing"]);
  assert.deepEqual(recipientsForDraft("please ask @Hermes.S1", members, false), { recipients: [members[0]], invalid: [] });
  assert.deepEqual(recipientsForDraft("ask @Missing", members, false), { recipients: [], invalid: ["@Missing"] });
  assert.deepEqual(recipientsForDraft("ask @all", members, false), { recipients: [], invalid: ["@all"] });
  assert.deepEqual(recipientsForDraft("ask @all", members, true), { recipients: [members[0]], invalid: [] });
});

test("picker and roster insertion normalize an existing configured mention without posting or duplication", () => {
  assert.equal(insertExactMention("Status", "@Hermes.S1"), "Status @Hermes.S1 ");
  assert.equal(insertExactMention("Status @hermes.s1", "@Hermes.S1"), "Status @Hermes.S1");
});

test("messages are sequence ordered without mutating the response", () => {
  const input = [{ sequence: 9, id: "b" }, { sequence: 3, id: "a" }];
  assert.deepEqual(sortMessages(input).map((message) => message.id), ["a", "b"]);
  assert.deepEqual(input.map((message) => message.id), ["b", "a"]);
});

test("prepending older messages preserves the visible scroll position", () => {
  const scroller = { scrollTop: 120, scrollHeight: 600 };
  const previous = captureScrollPosition(scroller);
  scroller.scrollHeight = 820;
  scroller.scrollTop = 0;
  restorePrependedScroll(scroller, previous);
  assert.equal(scroller.scrollTop, 340);
});

test("roster grouping and bounded profile fallback use backend fields only", () => {
  assert.deepEqual(groupMembers(members), { wakeable: [members[0]], other: [members[1]] });
  assert.deepEqual(buildProfile(members[0]), {
    purpose: "Hermes serves this room in the member role.",
    capabilities: ["No capabilities listed."],
    bestFor: ["No best-fit work listed."],
    boundaries: ["No boundaries listed."],
    fallback: "No fallback guidance listed.",
    host: "s1",
    policy: ["Can post to the room", "Cannot mention room members", "Wakeable as @Hermes.S1"],
  });
});

test("slug-aware API client covers reading, posting, lifecycle, search, and acknowledgement", async () => {
  const calls = [];
  const api = createApi({
    apiFetch: async (path, init = {}) => {
      calls.push([path, init]);
      return { ok: true, json: async () => ({ result: { room: { id: "focus" } } }) };
    },
  });
  await api.rooms("archived");
  await api.create("Focus");
  await api.rename("focus", "Now");
  for (const action of ["archive", "restore", "reset"]) await api.lifecycle("focus", action);
  await api.search({ q: "needle", scope: "current", roomId: "focus", history: true });
  await api.messages("focus", { before: 18, limit: 50, history: true });
  await api.post("focus", { client_message_id: "m-1", body: "hello" });
  await api.ack("focus", 18);
  await api.members("focus");

  assert.deepEqual(calls.map(([path]) => path), [
    "/api/plugins/agent-room/rooms?status=archived",
    "/api/plugins/agent-room/rooms",
    "/api/plugins/agent-room/rooms/focus",
    "/api/plugins/agent-room/rooms/focus/archive",
    "/api/plugins/agent-room/rooms/focus/restore",
    "/api/plugins/agent-room/rooms/focus/reset",
    "/api/plugins/agent-room/search?q=needle&scope=current&room_id=focus&history=true&limit=50",
    "/api/plugins/agent-room/rooms/focus/messages?before=18&limit=50&history=true",
    "/api/plugins/agent-room/rooms/focus/post",
    "/api/plugins/agent-room/rooms/focus/ack",
    "/api/plugins/agent-room/rooms/focus/members",
  ]);
  assert.equal(calls[1][1].method, "POST");
  assert.equal(calls[2][1].method, "PATCH");
  assert.equal(calls[8][1].body, JSON.stringify({ client_message_id: "m-1", body: "hello" }));
});

test("API errors expose server detail instead of becoming empty state", async () => {
  const api = createApi({
    apiFetch: async () => ({ ok: false, status: 403, json: async () => ({ detail: "Not allowed" }) }),
  });
  await assert.rejects(() => api.rooms("active"), /Not allowed/);
});

test("client-mode rooms hide owner lifecycle and search controls while keeping posting", () => {
  const state = {
    activeRooms: [], archivedRooms: [], room: { id: "ao", name: "Agent Organization", status: "active", client_mode: true },
    members, messages: [], mentions: [], pendingPosts: [], ownerOnline: false, hasOlder: false,
    history: false, contextSequence: null, loadingMessages: false, messageError: "", draft: "status",
    postError: "", allowAll: false, modal: null, modalError: "", searchQuery: "", searchResults: [], searched: false,
  };
  const html = shellMarkup(state);
  assert.doesNotMatch(html, /New room|Search rooms|Rename room|Start fresh|Archive room/);
  assert.match(html, /Room message/);
});

test("archived rooms expose restore as the only lifecycle mutation", () => {
  const state = {
    activeRooms: [], archivedRooms: [], room: { id: "old", name: "Old room", status: "archived" },
    members, messages: [], mentions: [], pendingPosts: [], ownerOnline: true, hasOlder: false,
    history: false, contextSequence: null, loadingMessages: false, messageError: "", draft: "",
    postError: "", allowAll: false, modal: null, modalError: "", searchQuery: "", searchResults: [], searched: false,
  };
  const html = shellMarkup(state);
  assert.match(html, /New room|Search rooms/);
  assert.match(html, /Restore room/);
  assert.doesNotMatch(html, /Rename room|Start fresh|Archive room/);
});

test("archived rooms render lifecycle failures as explicit alerts", () => {
  const state = {
    activeRooms: [], archivedRooms: [], room: { id: "old", name: "Old room", status: "archived" },
    members, messages: [], mentions: [], pendingPosts: [], ownerOnline: true, hasOlder: false,
    history: false, contextSequence: null, loadingMessages: false, messageError: "", draft: "",
    postError: "Restore refused", allowAll: false, modal: null, modalError: "", searchQuery: "", searchResults: [], searched: false,
  };
  const html = shellMarkup(state);
  assert.match(html, /role="alert"[^>]*>Restore refused/);
});

test("closing an overlay restores focus through the newly rendered opener", () => {
  let focused = false;
  const root = {
    querySelector: (selector) => selector === '[data-action="search"]' ? { focus: () => { focused = true; } } : null,
  };
  assert.equal(restoreActionFocus(root, "search"), true);
  assert.equal(focused, true);
});

test("composer disables posting for empty or unknown-recipient drafts", () => {
  const base = { members, allowAll: false, postError: "" };
  assert.match(composerMarkup({ ...base, draft: "" }), /type="submit"[^>]* disabled/);
  assert.match(composerMarkup({ ...base, draft: "ask @Missing" }), /type="submit"[^>]* disabled/);
  assert.doesNotMatch(composerMarkup({ ...base, draft: "ask @Hermes.S1" }), /type="submit"[^>]* disabled/);
});
