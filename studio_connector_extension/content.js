"use strict";

const CHANNEL_PATH = /^\/channel\/(UC[A-Za-z0-9_-]{22})(?:\/|$)/;

function studioId(value) {
  try {
    const url = new URL(value, "https://studio.youtube.com/");
    if (url.protocol !== "https:" || url.hostname !== "studio.youtube.com" || url.username || url.password) return "";
    const match = url.pathname.match(CHANNEL_PATH);
    return match ? match[1] : "";
  } catch (_) { return ""; }
}

function visible(element) {
  return !!element && element.getClientRects().length > 0 &&
    element.getAttribute("aria-hidden") !== "true";
}

function cleanName(value) {
  return typeof value === "string" ? value.trim().replace(/\s+/g, " ").slice(0, 160) : "";
}

function diagnosticSample(value, limit) {
  return cleanName(value).replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/ig, "[email hidden]")
    .replace(/\bBearer\s+\S+/ig, "Bearer [hidden]").slice(0, limit);
}

function identityRoots(doc) {
  const hosts = ["ytcp-app-sidebar", "ytcp-sidebar", "ytcp-header"]
    .flatMap(selector => [...doc.querySelectorAll(selector)]);
  const roots = [];
  const seen = new Set();
  while (hosts.length && seen.size < 80) {
    const host = hosts.shift();
    if (!host || seen.has(host)) continue;
    seen.add(host);
    roots.push(host);
    if (host.shadowRoot) roots.push(host.shadowRoot);
    for (const root of [host, host.shadowRoot].filter(Boolean)) {
      // Traverse web-component boundaries, but only read names under sidebar/header roots.
      hosts.push(...[...root.querySelectorAll("*")].filter(node => node.shadowRoot).slice(0, 80));
    }
  }
  return roots;
}

function activeChannelName(doc) {
  for (const root of identityRoots(doc)) {
    for (const node of root.querySelectorAll("#channel-name, .channel-name, [data-testid='channel-name']")) {
      if (visible(node)) {
        const name = cleanName(node.textContent || node.getAttribute("aria-label"));
        if (name) return name;
      }
    }
  }
  return "";
}

function identityCandidates(doc, pageUrl) {
  const candidates = [];
  const seen = new Set();
  for (const root of identityRoots(doc)) {
    // Explicit diagnostic: inspect only visible sidebar/header identity vicinity.
    const identityArea = root.matches?.("ytcp-channel-info, #channel-info") ||
      root.host?.matches?.("ytcp-channel-info, #channel-info");
    const selector = identityArea ? "*" :
      "ytcp-channel-info, #channel-info, #channel-name, .channel-name, [id*='avatar'], [class*='avatar'], [id*='channel'], [class*='channel']";
    for (const node of root.querySelectorAll(selector)) {
      if (candidates.length >= 24 || seen.has(node) || !visible(node)) continue;
      seen.add(node);
      if (["INPUT", "TEXTAREA", "VIDEO", "SCRIPT"].includes(node.tagName) || node.isContentEditable) continue;
      const rect = node.getBoundingClientRect?.();
      if (rect && (rect.top < 0 || rect.top > 380 || rect.left > 430)) continue;
      const identityLabel = /name/i.test(String(node.id || "") + " " + String(node.className || ""));
      const sample = identityLabel || identityArea && node.children?.length === 0 ?
        diagnosticSample(node.textContent, 80) : "";
      candidates.push({tag: String(node.tagName || "").toLowerCase(),
        id: diagnosticSample(node.id, 80),
        classes: diagnosticSample(typeof node.className === "string" ? node.className : "", 120),
        role: diagnosticSample(node.getAttribute("role"), 40),
        aria_label: diagnosticSample(node.getAttribute("aria-label"), 80),
        aria_labelledby: diagnosticSample(node.getAttribute("aria-labelledby"), 80),
        text_sample: sample});
    }
  }
  return {active_id_found: !!studioId(pageUrl), candidates};
}

function switcherRoots(doc) {
  const roots = [doc], seen = new Set();
  const hosts = "ytcp-app, ytcp-ve, ytcp-popup-container, ytcp-account-switcher, ytcp-account-switcher-dialog, ytcp-dialog, tp-yt-iron-dropdown, tp-yt-paper-dialog, [role='dialog']";
  for (let index = 0; index < roots.length && roots.length < 80; index++) {
    const root = roots[index];
    if (seen.has(root)) continue;
    seen.add(root);
    for (const node of root.querySelectorAll(hosts)) if (node.shadowRoot && !seen.has(node.shadowRoot)) roots.push(node.shadowRoot);
  }
  return roots;
}

function switcherContainers(doc) {
  const selector = "ytcp-account-switcher, ytcp-account-switcher-dialog, [id*='account-switch'], [class*='account-switch'], [role='dialog'][aria-label*='account' i], [role='menu'][aria-label*='account' i], tp-yt-iron-dropdown";
  const seen = new Set(), containers = [];
  for (const root of switcherRoots(doc)) for (const item of root.querySelectorAll(selector)) {
    const genericDropdown = item.matches?.("tp-yt-iron-dropdown");
    const hasChannelEvidence = !!(item.shadowRoot || item).querySelector?.(
      "ytcp-account-item, ytcp-account-picker-item, ytcp-channel-list-item, a[href*='/channel/']");
    if (!seen.has(item) && visible(item) && (!genericDropdown || hasChannelEvidence)) {
      seen.add(item); containers.push(item);
    }
  }
  return containers;
}

function visibleSwitcherEntry(entry, index) {
  if (!visible(entry)) return null;
  const link = entry.closest?.("a[href]") || entry.querySelector("a[href]");
  const href = typeof link?.href === "string" ? link.href : "";
  const id = studioId(href);
  const rawText = cleanName(entry.textContent, 240);
  const handle = rawText.match(/@[\p{L}\p{N}._-]{2,100}/u)?.[0] || "";
  const nameNode = entry.querySelector("#channel-name, .channel-name, [id*='channel-name'], [class*='channel-name'], [id*='account-name'], [class*='account-name']");
  const name = cleanName(nameNode?.textContent || rawText.replace(handle, "").trim());
  if (!name && !handle && !id) return null;
  return {id, name, handle, source_url:id ? href : "https://studio.youtube.com/",
    id_source:id ? "studio_link" : "missing",
    name_source:name ? "studio_switcher_dom" : "missing",
    handle_source:handle ? "studio_switcher_dom" : "missing",
    evidence:"visible_switcher_item", entry_index:index};
}

function scanStudioDocument(doc, pageUrl) {
  const page = new URL(pageUrl);
  if (page.protocol !== "https:" || page.hostname !== "studio.youtube.com") throw new Error("Cần mở YouTube Studio.");
  const channels = [];
  const activeId = studioId(pageUrl);
  const activeName = activeChannelName(doc);
  if (activeId) channels.push({id: activeId, name: activeName, handle:"", source_url: pageUrl,
    id_source:"studio_url", name_source:activeName ? "studio_identity_dom" : "missing",
    handle_source:"missing", evidence:"active_studio_url"});
  const containers = switcherContainers(doc);
  let switcherEntries = 0;
  let switcherNames = 0;
  for (const container of containers) {
    const entries = (container.shadowRoot || container).querySelectorAll("ytcp-account-item, ytcp-account-picker-item, ytcp-channel-list-item, [role='menuitem'], tp-yt-paper-item, a[href*='/channel/']");
    for (const entry of entries) {
      if (!visible(entry)) continue;
      switcherEntries++;
      const candidate = visibleSwitcherEntry(entry, switcherEntries);
      if (!candidate) continue;
      if (candidate.name) switcherNames++;
      channels.push(candidate);
    }
  }
  const verified = new Set(channels.filter(item => item.id && item.name).map(item => item.id)).size;
  return {type: "scan_results", schema: 1, page_url: pageUrl, page_url_after: pageUrl,
    switcher_open: containers.length > 0, channels,
    diagnostics: {active_id_found: !!activeId, active_name_found: !!activeName,
      switcher_detected: containers.length > 0, switcher_entries: switcherEntries,
      switcher_names: switcherNames, verified_identities: verified}};
}

let bulkSession = null;
function stopBulkScan() {
  if (!bulkSession) return;
  bulkSession.stop();
  bulkSession = null;
}

function startBulkScan(doc, pageUrl, emit, durationMs = 20000, Observer = globalThis.MutationObserver) {
  stopBulkScan();
  const session = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  const observers = [], observed = new Set(), scrollRoots = new Set();
  let timer, expiry, stopped = false;
  const send = complete => {
    if (stopped && !complete) return;
    const result = scanStudioDocument(doc, pageUrl());
    emit({type:"bulk_scan_update", session, channels:result.channels,
      switcher_open:result.switcher_open, diagnostics:result.diagnostics, complete});
  };
  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(() => { observeRoots(); send(false); }, 180);
  };
  const observeRoots = () => {
    for (const root of switcherRoots(doc)) {
      if (observed.has(root)) continue;
      observed.add(root);
      if (Observer) {
        const observer = new Observer(schedule);
        observer.observe(root, {subtree:true, childList:true, characterData:true});
        observers.push(observer);
      }
      if (root.addEventListener) { root.addEventListener("scroll", schedule, true); scrollRoots.add(root); }
    }
  };
  observeRoots();
  const stop = () => {
    if (stopped) return;
    clearTimeout(timer); clearTimeout(expiry);
    observers.forEach(item => item.disconnect());
    scrollRoots.forEach(root => root.removeEventListener("scroll", schedule, true));
    stopped = true; send(true);
  };
  expiry = setTimeout(stop, Math.max(1000, Math.min(durationMs, 30000)));
  bulkSession = {session, stop};
  return {session, channels:scanStudioDocument(doc, pageUrl()).channels};
}

if (typeof chrome !== "undefined" && chrome.runtime?.onMessage) {
  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (!["scan_visible_studio", "diagnose_channel_identity", "start_bulk_scan", "stop_bulk_scan"].includes(message?.type)) return;
    try {
      if (message.type === "start_bulk_scan") {
        respond(startBulkScan(document, () => location.href,
          update => { chrome.runtime.sendMessage(update).catch(() => {}); }, message.duration_ms));
        return;
      }
      if (message.type === "stop_bulk_scan") { stopBulkScan(); respond({ok:true}); return; }
      if (message.type === "diagnose_channel_identity") {
        respond(identityCandidates(document, location.href));
        return;
      }
      const before = location.href;
      const result = scanStudioDocument(document, before);
      result.page_url_after = location.href;
      respond(result);
    } catch (error) { respond({error: String(error.message || error)}); }
  });
}

if (typeof module !== "undefined") module.exports = {studioId, activeChannelName, identityCandidates,
  visibleSwitcherEntry, scanStudioDocument, startBulkScan, stopBulkScan};
