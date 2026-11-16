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

function scanStudioDocument(doc, pageUrl) {
  const page = new URL(pageUrl);
  if (page.protocol !== "https:" || page.hostname !== "studio.youtube.com") throw new Error("Cần mở YouTube Studio.");
  const channels = [];
  const activeId = studioId(pageUrl);
  const activeName = activeChannelName(doc);
  if (activeId) channels.push({id: activeId, name: activeName, source_url: pageUrl});
  const containers = [...doc.querySelectorAll("ytcp-account-switcher, #account-switcher, ytcp-account-switcher-dialog")]
    .filter(visible);
  let switcherEntries = 0;
  let switcherNames = 0;
  for (const container of containers) {
    const entries = (container.shadowRoot || container).querySelectorAll("ytcp-account-item, [role='menuitem'], tp-yt-paper-item");
    for (const entry of entries) {
      if (!visible(entry)) continue;
      switcherEntries++;
      const name = cleanName(entry.querySelector("#channel-name, .channel-name, [aria-label]")?.getAttribute("aria-label") ||
        entry.querySelector("#channel-name, .channel-name")?.textContent || entry.textContent);
      if (!name) continue;
      switcherNames++;
      const link = entry.closest("a[href]") || entry.querySelector("a[href]");
      const href = link?.href || "";
      const id = studioId(href);
      channels.push({id, name, source_url: id ? href : "https://studio.youtube.com/"});
    }
  }
  const verified = new Set(channels.filter(item => item.id && item.name).map(item => item.id)).size;
  return {type: "scan_results", schema: 1, page_url: pageUrl, page_url_after: pageUrl,
    switcher_open: containers.length > 0, channels,
    diagnostics: {active_id_found: !!activeId, active_name_found: !!activeName,
      switcher_detected: containers.length > 0, switcher_entries: switcherEntries,
      switcher_names: switcherNames, verified_identities: verified}};
}

if (typeof chrome !== "undefined" && chrome.runtime?.onMessage) {
  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message?.type !== "scan_visible_studio" && message?.type !== "diagnose_channel_identity") return;
    try {
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

if (typeof module !== "undefined") module.exports = {studioId, activeChannelName, identityCandidates, scanStudioDocument};
