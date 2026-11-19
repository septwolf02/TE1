"use strict";
importScripts("upload.js", "sidepanel.js");

const UPLOAD_ALARM = "te-studio-upload";
let uploadBusy = false;
async function checkUpload() {
  if (uploadBusy) return;
  uploadBusy = true;
  try {
    const saved = await chrome.storage.local.get("studioActiveUpload");
    if (saved.studioActiveUpload) {
      const active = saved.studioActiveUpload;
      const state = await nativeRequest(chrome,{type:"upload_state",schema:1,
        request_id:active.request_id});
      if (state.safe_to_clear === true) {
        await chrome.storage.local.remove("studioActiveUpload");
      } else {
        let tab;
        try { tab = await chrome.tabs.get(active.tab_id); } catch (_) { /* Chrome closed the tab. */ }
        if (tab && channelFromStudioUrl(tab.url) === active.channel_id) return;
        // Preserve the marker if TE is offline; retry the failure report later.
        await markDisconnected("chrome_disconnected");
      }
    }
    const reply = await nativeRequest(chrome,{type:"upload_get",schema:1});
    if (reply.request?.pending) {
      // Keep the request ID across a service-worker or host failure, including a
      // failure after CDP started but before its result reached TE.
      await chrome.storage.local.set({studioActiveUpload:{request_id:reply.request.request_id,
        channel_id:reply.request.channel_id,tab_id:null}});
      const result = await processUploadRequest(chrome,reply.request,() => {});
      await chrome.storage.local.set({studioActiveUpload:{request_id:reply.request.request_id,
        channel_id:reply.request.channel_id,tab_id:result.tabId}});
    }
  } catch (_) { /* TE records expiry or uncertain attempts; no automatic retry. */ }
  finally { uploadBusy = false; }
}

async function markDisconnected(reason, tabId = null) {
  const saved = await chrome.storage.local.get("studioActiveUpload");
  const active = saved.studioActiveUpload;
  if (!active || tabId !== null && active.tab_id !== tabId) return true;
  await nativeRequest(chrome,{type:"upload_failed",schema:1,
    request_id:active.request_id,reason});
  await chrome.storage.local.remove("studioActiveUpload");
  return true;
}

function scheduleUploadChecks() {
  chrome.alarms.create(UPLOAD_ALARM,{periodInMinutes:0.5});
  checkUpload();
}
chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({openPanelOnActionClick: true});
  scheduleUploadChecks();
});
chrome.runtime.onStartup.addListener(() => {
  chrome.sidePanel.setPanelBehavior({openPanelOnActionClick: true});
  scheduleUploadChecks();
});
chrome.tabs.onRemoved.addListener(tabId => markDisconnected("tab_closed",tabId).catch(() => {}));
chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === UPLOAD_ALARM) checkUpload();
});
// MV3 workers can be restarted independently of onInstalled/onStartup.
scheduleUploadChecks();
