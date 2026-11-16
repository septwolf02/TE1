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

function activeChannelName(doc) {
  // Inspect channel identity components only. Studio may render their children in shadow roots.
  const hosts = ["ytcp-app-sidebar", "ytcp-sidebar", "ytcp-header"]
    .flatMap(selector => [...doc.querySelectorAll(selector)]);
  const seen = new Set();
  while (hosts.length && seen.size < 50) {
    const host = hosts.shift();
    if (seen.has(host)) continue;
    seen.add(host);
    for (const root of [host, host.shadowRoot].filter(Boolean)) {
      for (const node of root.querySelectorAll("#channel-name, .channel-name, [data-testid='channel-name']")) {
        if (visible(node)) {
          const name = cleanName(node.textContent || node.getAttribute("aria-label"));
          if (name) return name;
        }
      }
      if (root !== host) hosts.push(...root.querySelectorAll("ytcp-channel-info, #channel-info"));
    }
  }
  return "";
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
    if (message?.type !== "scan_visible_studio") return;
    try {
      const before = location.href;
      const result = scanStudioDocument(document, before);
      result.page_url_after = location.href;
      respond(result);
    } catch (error) { respond({error: String(error.message || error)}); }
  });
}

if (typeof module !== "undefined") module.exports = {studioId, activeChannelName, scanStudioDocument};
