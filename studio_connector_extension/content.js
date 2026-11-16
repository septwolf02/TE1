"use strict";

const CHANNEL_PATH = /^\/channel\/(UC[A-Za-z0-9_-]{22})(?:\/|$)/;
const EMAIL_PATTERN = /[\p{L}\p{N}._%+-]+@[\p{L}\p{N}.-]+\.[\p{L}]{2,}/gu;

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

// Structural report only: no text, labels, hrefs, or attribute values that may contain identities.
function diagnoseChannelMenu(doc) {
  const structure = node => ({
    tag:String(node.tagName || "").toLowerCase(),
    id:structuralToken(node.id),
    classes:typeof node.className === "string" ? node.className.split(/\s+/)
      .map(structuralToken).filter(Boolean).slice(0, 6) : [],
    role:structuralToken(node.getAttribute?.("role")),
    aria_haspopup:structuralToken(node.getAttribute?.("aria-haspopup")),
    aria_expanded:structuralToken(node.getAttribute?.("aria-expanded")),
    aria_modal:structuralToken(node.getAttribute?.("aria-modal"))
  });
  const roots = [doc], boundaries = [], seen = new Set();
  for (let i = 0; i < roots.length && roots.length < 120; i++) {
    const root = roots[i];
    if (seen.has(root)) continue;
    seen.add(root);
    for (const node of root.querySelectorAll("*")) {
      if (node.shadowRoot && roots.length < 120) {
        roots.push(node.shadowRoot);
        if (boundaries.length < 24 && /account|switcher|popup|dropdown|dialog|menu/i.test(
          `${node.tagName} ${node.id} ${typeof node.className === "string" ? node.className : ""}`))
          boundaries.push({host:structure(node), visible:visible(node)});
      }
    }
  }
  const candidates = [], candidateSet = new Set();
  for (const root of roots) for (const node of root.querySelectorAll("*")) {
    if (candidates.length >= 16) break;
    if (!visible(node) || candidateSet.has(node) || inNavigationDrawer(node)) continue;
    const tag = String(node.tagName || "").toLowerCase();
    const role = node.getAttribute?.("role") || "";
    const structuralName = `${tag} ${node.id || ""} ${typeof node.className === "string" ? node.className : ""}`;
    if (!/account|switcher|popup|dropdown|dialog|menu|listbox/i.test(structuralName) &&
        !["dialog", "menu", "listbox"].includes(role)) continue;
    if (["script", "style", "input", "textarea"].includes(tag)) continue;
    candidateSet.add(node);
    const inside = node.shadowRoot || node;
    const descendants = [...inside.querySelectorAll("*")];
    const links = descendants.filter(child => String(child.tagName).toLowerCase() === "a" && child.href);
    const scrolls = [node, ...descendants].filter(child =>
      visible(child) && child.scrollHeight > child.clientHeight + 20 && child.clientHeight > 20)
      .slice(0, 5).map(child => ({element:structure(child),
        client_height:Math.round(child.clientHeight), scroll_height:Math.round(child.scrollHeight),
        scroll_top:Math.round(child.scrollTop)}));
    const possibleRows = descendants.filter(child => visible(child) &&
      (child.getAttribute?.("role") === "menuitem" || child.getAttribute?.("role") === "option" ||
       /account|channel|item/i.test(String(child.tagName || ""))));
    candidates.push({element:structure(node), shadow_root:!!node.shadowRoot,
      visible_direct_children:[...inside.children || []].filter(visible).length,
      direct_child_shapes:[...inside.children || []].filter(visible).slice(0, 10).map(structure),
      visible_row_candidates:possibleRows.length,
      row_shapes:possibleRows.slice(0, 12).map(structure),
      visible_links:links.filter(visible).length,
      visible_studio_channel_links:links.filter(link => visible(link) && studioId(link.href)).length,
      scroll_containers:scrolls});
  }
  return {schema:1, scope:"visible menu/account structural metadata only",
    active_url_has_channel_id:!!studioId(doc.location?.href || globalThis.location?.href || ""),
    shadow_roots_seen:roots.length - 1, shadow_boundaries:boundaries,
    candidate_count:candidates.length, candidates,
    virtualization:"undetermined from one snapshot; scroll while running diagnostics"};
}

function structuralToken(value) {
  // Only CSS-like identifiers; never copy arbitrary label, email, URL, or channel text.
  return typeof value === "string" && /^[a-z][a-z0-9_-]{0,63}$/i.test(value) ? value : "";
}

function inNavigationDrawer(node) {
  for (let current = node; current; current = current.parentNode || current.host) {
    if (current.matches?.("ytcp-navigation-drawer, ul#main-menu")) return true;
  }
  return false;
}

function rowSignature(node) {
  return `${String(node.tagName || "").toLowerCase()}|${node.getAttribute?.("role") || ""}|` +
    (typeof node.className === "string" ? node.className.split(/\s+/)
      .filter(value => value && !/^(active|selected|hover|focused|checked|highlight)(-|$)/i.test(value))
      .sort().join(".") : "");
}

function regionCandidates(path) {
  const candidates = [], seen = new Set();
  for (const row of path) {
    if (!row?.tagName || inNavigationDrawer(row)) continue;
    const parent = row.parentElement || (row.parentNode?.host ? row.parentNode : null);
    if (!parent || seen.has(parent) || inNavigationDrawer(parent)) continue;
    seen.add(parent);
    const siblings = [...parent.children || []].filter(node => visible(node) &&
      rowSignature(node) === rowSignature(row));
    if (siblings.length < 2) continue;
    const display = parent.host || parent;
    if (!display.getBoundingClientRect?.()) continue;
    candidates.push({container:parent, row_signature:rowSignature(row), example_row:row,
      summary:{container:elementStructure(display), repeated_row_tag:String(row.tagName).toLowerCase(),
        visible_matching_rows:siblings.length, shadow_root:!!parent.host}});
  }
  return candidates.slice(0, 8);
}

function elementStructure(node) {
  return {tag:String(node.tagName || "").toLowerCase(), id:structuralToken(node.id),
    classes:typeof node.className === "string" ? node.className.split(/\s+/)
      .map(structuralToken).filter(Boolean).slice(0, 6) : [],
    role:structuralToken(node.getAttribute?.("role"))};
}

let selectionMode = null, selectedRegion = null;
function cancelRegionTargeting() {
  if (!selectionMode) return;
  selectionMode.cleanup();
  selectionMode = null;
}

function beginRegionTargeting(doc, emit, timeoutMs = 30000) {
  cancelRegionTargeting();
  const shield = event => {
    event.preventDefault(); event.stopImmediatePropagation();
  };
  const rowLike = path => path.some(node =>
    /(?:account|channel).*(?:item|row)|(?:item|row).*(?:account|channel)/i
      .test(String(node.tagName || "")) || node.getAttribute?.("role") === "menuitem");
  const blockEarly = event => {
    const path = event.composedPath?.() || [event.target];
    if (path.some(inNavigationDrawer) || regionCandidates(path).length || rowLike(path))
      event.stopImmediatePropagation();
  };
  const overlay = doc.createElement("div");
  overlay.setAttribute("data-te-region-highlight", "");
  overlay.style.cssText = "position:fixed;z-index:2147483647;pointer-events:none;" +
    "border:3px solid #00a67d;background:rgba(0,166,125,.10);display:none";
  (doc.documentElement || doc.body).append(overlay);
  const highlight = candidate => {
    const rect = (candidate.container.host || candidate.container).getBoundingClientRect();
    Object.assign(overlay.style,{display:"block",left:`${rect.left}px`,top:`${rect.top}px`,
      width:`${rect.width}px`,height:`${rect.height}px`});
  };
  const onClick = event => {
    const path = event.composedPath?.() || [event.target];
    if (path.some(inNavigationDrawer)) {
      shield(event);
      emit({type:"target_region_proposed", error:"Đã chọn thanh điều hướng trái. Hãy nhấp vào một hàng kênh trong menu chuyển kênh."});
      return;
    }
    const candidates = regionCandidates(path);
    if (!candidates.length) {
      // Let the user open the switcher after Side Panel focus closes it.
      if (rowLike(path)) {
        shield(event);
        emit({type:"target_region_proposed", error:"Đã chặn cú nhấp vào hàng, nhưng không thấy các hàng cùng cấu trúc. Menu có thể dùng closed shadow root hoặc chỉ tải một hàng."});
      }
      return;
    }
    shield(event);
    selectionMode.candidates = candidates;
    highlight(candidates[0]);
    emit({type:"target_region_proposed", candidates:candidates.map(item => item.summary)});
  };
  const onKey = event => {
    if (event.key !== "Escape") return;
    shield(event); cancelRegionTargeting();
    emit({type:"target_region_cancelled"});
  };
  for (const type of ["pointerdown", "mousedown", "mouseup"])
    doc.addEventListener(type, blockEarly, true);
  doc.addEventListener("click", onClick, true);
  doc.addEventListener("keydown", onKey, true);
  const timer = setTimeout(() => {
    cancelRegionTargeting(); emit({type:"target_region_cancelled", reason:"Hết 30 giây chọn vùng."});
  }, timeoutMs);
  selectionMode = {candidates:[], highlight, cleanup:() => {
    clearTimeout(timer); overlay.remove();
    for (const type of ["pointerdown", "mousedown", "mouseup"])
      doc.removeEventListener(type, blockEarly, true);
    doc.removeEventListener("click", onClick, true);
    doc.removeEventListener("keydown", onKey, true);
  }};
  return {ok:true};
}

function proposeRegion(index) {
  const candidate = selectionMode?.candidates[index];
  if (!candidate) throw new Error("Vùng đề xuất không còn; hãy chọn lại hàng kênh.");
  selectionMode.highlight(candidate);
  return {ok:true, proposal:candidate.summary};
}

function confirmRegion(index) {
  const candidate = selectionMode?.candidates[index];
  if (!candidate) throw new Error("Chưa chọn hàng kênh hợp lệ.");
  if (inNavigationDrawer(candidate.container)) throw new Error("Không được chọn thanh điều hướng trái.");
  selectedRegion = {container:candidate.container, row_signature:candidate.row_signature};
  cancelRegionTargeting();
  return {ok:true, region:candidate.summary, diagnostic:inspectSelectedRegion()};
}

function inspectSelectedRegion() {
  if (!selectedRegion || selectedRegion.container.isConnected === false)
    throw new Error("Vùng kênh đã đóng hoặc thay đổi; hãy chọn lại.");
  const {container, row_signature} = selectedRegion;
  const directChildren = [...container.children || []].filter(visible);
  const rows = directChildren.filter(node => rowSignature(node) === row_signature);
  const excluded = directChildren.filter(node => rowSignature(node) !== row_signature);
  const observations = rows.map((row, index) => visibleSwitcherEntry(row, index + 1)).filter(Boolean);
  const scroll = [container, ...container.querySelectorAll?.("*") || []].filter(node =>
    node.scrollHeight > node.clientHeight + 20 && node.clientHeight > 20).slice(0, 4)
    .map(node => ({element:elementStructure(node), scroll_top:Math.round(node.scrollTop),
      client_height:Math.round(node.clientHeight), scroll_height:Math.round(node.scrollHeight)}));
  return {region:elementStructure(container.host || container),
    visible_matching_rows:rows.length, extracted_rows:observations.length,
    excluded_nonmatching_children:excluded.length,
    excluded_structures:excluded.slice(0, 6).map(elementStructure),
    channel_ids_from_links:observations.filter(item => item.id).length,
    unresolved_rows:observations.filter(item => !item.id).length,
    row_structures:rows.slice(0, 8).map(elementStructure), scroll_containers:scroll,
    observations};
}

function visibleSwitcherEntry(entry, index) {
  if (!visible(entry) || inNavigationDrawer(entry)) return null;
  const link = entry.closest?.("a[href]") || entry.querySelector("a[href]");
  const href = typeof link?.href === "string" ? link.href : "";
  const id = studioId(href);
  const rawText = String(entry.innerText || entry.textContent || "")
    .replace(EMAIL_PATTERN, "");
  const handle = rawText.match(/@[\p{L}\p{N}._-]{2,100}/u)?.[0] || "";
  const nameNode = entry.querySelector("#channel-name, .channel-name, [id*='channel-name'], [class*='channel-name'], [id*='account-name'], [class*='account-name']");
  const nameText = String(nameNode?.innerText || "")
    .replace(EMAIL_PATTERN, "").trim();
  const name = cleanName(nameText || rawText.split(/\r?\n/)
    .map(line => line.replace(handle, "").trim()).find(line => line && !line.includes("@")) || "");
  if (!name && !handle && !id) return null;
  return {id, name, handle, source_url:id ? `https://studio.youtube.com/channel/${id}` : "https://studio.youtube.com/",
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
  // The navigation drawer also contains menu items and Studio links. Only a region
  // explicitly confirmed by the user may contribute switcher observations.
  const targeted = selectedRegion && selectedRegion.container.isConnected !== false ?
    inspectSelectedRegion() : null;
  if (targeted) channels.push(...targeted.observations);
  const switcherEntries = targeted?.visible_matching_rows || 0;
  const switcherNames = targeted?.observations.filter(item => item.name).length || 0;
  const verified = new Set(channels.filter(item => item.id && item.name).map(item => item.id)).size;
  return {type: "scan_results", schema: 1, page_url: pageUrl, page_url_after: pageUrl,
    switcher_open: !!targeted, channels,
    diagnostics: {active_id_found: !!activeId, active_name_found: !!activeName,
      switcher_detected: !!targeted, switcher_entries: switcherEntries,
      switcher_names: switcherNames, verified_identities: verified}};
}

let bulkSession = null;
function stopBulkScan() {
  if (!bulkSession) return;
  bulkSession.stop();
  bulkSession = null;
}

function startBulkScan(doc, pageUrl, emit, durationMs = 20000, Observer = globalThis.MutationObserver) {
  if (!selectedRegion || selectedRegion.container.isConnected === false)
    throw new Error("Hãy bấm Chọn vùng danh sách kênh và xác nhận trước khi quét.");
  stopBulkScan();
  const session = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  const observers = [], observed = new Set(), scrollRoots = new Set();
  let timer, expiry, stopped = false;
  const send = complete => {
    if (stopped && !complete) return;
    try {
      const report = inspectSelectedRegion();
      emit({type:"bulk_scan_update", session, channels:report.observations,
        switcher_open:true, diagnostics:report, complete});
    } catch (error) {
      emit({type:"bulk_scan_update", session, channels:[], switcher_open:false,
        error:error.message, complete:true});
      stop();
    }
  };
  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(() => { observeRoots(); send(false); }, 180);
  };
  const observeRoots = () => {
    const roots = [selectedRegion.container];
    for (const node of selectedRegion.container.querySelectorAll?.("*") || [])
      if (node.shadowRoot) roots.push(node.shadowRoot);
    for (const root of roots) {
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
  return {session, channels:inspectSelectedRegion().observations};
}

if (typeof chrome !== "undefined" && chrome.runtime?.onMessage) {
  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (!["scan_visible_studio", "diagnose_channel_identity", "diagnose_channel_menu",
          "start_bulk_scan", "stop_bulk_scan", "begin_region_targeting", "cancel_region_targeting",
          "propose_region", "confirm_region"].includes(message?.type)) return;
    try {
      if (message.type === "begin_region_targeting") {
        respond(beginRegionTargeting(document, update => {
          chrome.runtime.sendMessage(update).catch(() => {});
        }));
        return;
      }
      if (message.type === "cancel_region_targeting") {
        cancelRegionTargeting(); respond({ok:true}); return;
      }
      if (message.type === "propose_region") { respond(proposeRegion(message.index)); return; }
      if (message.type === "confirm_region") { respond(confirmRegion(message.index)); return; }
      if (message.type === "diagnose_channel_menu") {
        const first = diagnoseChannelMenu(document);
        let mutations = 0, scrolls = 0;
        const observer = new MutationObserver(records => { mutations += records.length; });
        observer.observe(document, {subtree:true, childList:true});
        const onScroll = () => { scrolls++; };
        document.addEventListener("scroll", onScroll, true);
        setTimeout(() => {
          observer.disconnect(); document.removeEventListener("scroll", onScroll, true);
          const last = diagnoseChannelMenu(document);
          respond({first, last, dynamics:{mutations:Math.min(mutations, 9999),
            scroll_events:Math.min(scrolls, 9999),
            lazy_or_virtualized_evidence:scrolls > 0 && mutations > 0 ? "possible" : "not_observed"}});
        }, 3000);
        return true;
      }
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
  visibleSwitcherEntry, scanStudioDocument, diagnoseChannelMenu, inNavigationDrawer,
  regionCandidates, beginRegionTargeting, cancelRegionTargeting, proposeRegion, confirmRegion,
  inspectSelectedRegion, startBulkScan, stopBulkScan};
