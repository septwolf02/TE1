"use strict";

const ID_PATTERN = /^UC[A-Za-z0-9_-]{22}$/;
const HANDLE_PATTERN = /^@[\p{L}\p{N}._-]{2,100}$/u;

function clean(value, limit = 160) {
  return typeof value === "string" ? value.trim().replace(/\s+/g, " ").slice(0, limit) : "";
}

function normalizeObservation(raw, keyFactory = () => crypto.randomUUID()) {
  if (!raw || typeof raw !== "object") throw new Error("Bản ghi kênh không hợp lệ.");
  const id = ID_PATTERN.test(raw.id || "") ? raw.id : "";
  const handle = HANDLE_PATTERN.test(raw.handle || "") ? raw.handle : "";
  const discoveredName = clean(raw.name || raw.discovered_name);
  if (!id && !handle && !discoveredName) throw new Error("Bản ghi thiếu tên, handle và ID.");
  const sourceUrl = typeof raw.source_url === "string" ? raw.source_url : "";
  if (!sourceUrl.startsWith("https://studio.youtube.com/")) throw new Error("Nguồn kênh không thuộc Studio.");
  return {key: id ? `id:${id}` : `candidate:${keyFactory()}`, id, handle,
    discovered_name: discoveredName, display_name: discoveredName,
    name_source: discoveredName ? (raw.name_source || "studio_switcher_dom") : "missing",
    handle_source: handle ? (raw.handle_source || "studio_switcher_dom") : "missing",
    id_source: id ? (raw.id_source || "studio_link") : "missing",
    status: id && discoveredName ? "identity_verified" : id ? "identity_incomplete" : "discovered",
    permission_status: "permission_unverified", upload_status: "upload_unverified",
    source_url: sourceUrl, evidence: clean(raw.evidence || "visible_switcher_item", 80),
    user_edited: false, last_seen_at: new Date().toISOString()};
}

function mergeCatalog(existing, observations, keyFactory) {
  const oldRows = Array.isArray(existing) ? existing.map(row => ({...row})) : [];
  const newRows = (Array.isArray(observations) ? observations : []).map(row =>
    normalizeObservation(row, keyFactory));
  const result = [...oldRows];
  const matched = new Set();
  for (const incoming of newRows) {
    let index = incoming.id ? result.findIndex(old => old.id === incoming.id) : -1;
    if (index < 0 && !incoming.id) {
      const exact = old => !old.id && old.handle === incoming.handle &&
        old.discovered_name === incoming.discovered_name && old.source_url === incoming.source_url &&
        old.evidence === incoming.evidence;
      const manualExact = old => old.id_source === "user_supplied" &&
        old.handle === incoming.handle &&
        old.discovered_name === incoming.discovered_name && old.source_url === incoming.source_url &&
        old.evidence === incoming.evidence;
      index = result.findIndex((old, position) => !matched.has(position) &&
        (exact(old) || manualExact(old)));
      if (index < 0 && incoming.handle) {
        const handleMatch = row => !row.id && row.handle === incoming.handle &&
          row.source_url === incoming.source_url;
        if (oldRows.filter(handleMatch).length === 1 && newRows.filter(handleMatch).length === 1)
          index = result.findIndex((old, position) => !matched.has(position) && handleMatch(old));
      }
    }
    if (index < 0) { matched.add(result.length); result.push(incoming); continue; }
    matched.add(index);
    const old = result[index];
    result[index] = {...old, ...incoming, key: old.key,
      id:old.id_source === "user_supplied" && !incoming.id ? old.id : incoming.id,
      status:old.id_source === "user_supplied" && !incoming.id ? old.status : incoming.status,
      source_url:old.id_source === "user_supplied" && !incoming.id ? old.source_url : incoming.source_url,
      display_name: old.user_edited ? old.display_name : incoming.discovered_name || old.display_name,
      discovered_name: incoming.discovered_name || old.discovered_name,
      name_source: old.user_edited ? "user_edited" : incoming.discovered_name ? incoming.name_source : old.name_source,
      user_edited: !!old.user_edited, id_source: incoming.id_source !== "missing" ? incoming.id_source : old.id_source};
  }
  return markDuplicates(result);
}

function mergeRemoteCatalog(local, remote) {
  const rows = Array.isArray(local) ? local.map(row=>({...row})) : [];
  for (const item of Array.isArray(remote) ? remote : []) {
    if (!item || typeof item.key !== "string") continue;
    const index = rows.findIndex(row=>row.key===item.key);
    if (index < 0) { rows.push({...item}); continue; }
    const old = rows[index];
    const oldVerified = ["studio_url", "studio_link", "studio_url_user_confirmed"].includes(old.id_source);
    if (oldVerified && item.id && item.id !== old.id)
      throw new Error("ID trong Chrome và TE xung đột; cần kiểm tra trước khi đồng bộ.");
    const keepVerified = oldVerified && old.id === item.id && item.id_source === "user_supplied";
    rows[index] = {...old, ...item,
      ...(keepVerified ? {id_source:old.id_source,status:old.status,source_url:old.source_url} : {}),
      display_name:old.user_edited ? old.display_name : item.display_name || item.name || old.display_name,
      name_source:old.user_edited ? "user_edited" : item.name_source,
      user_edited:!!old.user_edited || !!item.user_edited};
  }
  return markDuplicates(rows);
}

function markDuplicates(rows) {
  const names = new Map(), handles = new Map();
  for (const row of rows) {
    const name = clean(row.display_name).toLocaleLowerCase();
    const handle = clean(row.handle).toLocaleLowerCase();
    if (name) names.set(name, (names.get(name) || 0) + 1);
    if (handle) handles.set(handle, (handles.get(handle) || 0) + 1);
  }
  return rows.map(row => ({...row,
    duplicate_name: !!row.display_name && names.get(clean(row.display_name).toLocaleLowerCase()) > 1,
    duplicate_handle: !!row.handle && handles.get(clean(row.handle).toLocaleLowerCase()) > 1}));
}

function editName(rows, key, value) {
  const name = clean(value);
  if (!name) throw new Error("Tên kênh không được để trống.");
  return markDuplicates(rows.map(row => row.key === key ?
    {...row, display_name:name, name_source:"user_edited", user_edited:true} : row));
}

if (typeof module !== "undefined") module.exports = {normalizeObservation, mergeCatalog,
  mergeRemoteCatalog, markDuplicates, editName};
