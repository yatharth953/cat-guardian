const BRIDGE_EVENT = "http://127.0.0.1:8765/event";
const BRIDGE_CLOSED = "http://127.0.0.1:8765/closed";
const BRIDGE_MUSIC = "http://127.0.0.1:8765/music";
const MUSIC_ALARM = "cat-guardian-music-beacon";
// The app answers with the close action immediately; this only bounds a slow
// or sleeping desktop app.
const EVENT_TIMEOUT_MS = 12000;
const REPORT_TIMEOUT_MS = 1500;
const inFlight = new Set();
const recent = new Map();
let lastMusicKey = null;

function isDuplicate(tabId, url) {
  const now = Date.now();
  const last = recent.get(tabId);
  recent.set(tabId, { url, at: now });
  return Boolean(last && last.url === url && now - last.at < 1500);
}

function hostOf(url) {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch (_) {
    return "";
  }
}

async function sendMusic(hosts) {
  try {
    await fetch(BRIDGE_MUSIC, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hosts }),
      signal: AbortSignal.timeout(REPORT_TIMEOUT_MS)
    });
  } catch (_) {
    // The app may not be running; the beacon is best-effort.
  }
}

// Only tabs that are producing sound are named, including background music
// tabs. The app matches these hosts against its own list and never stores or
// logs the others, so ordinary browsing is never reported.
async function beaconMusic() {
  let hosts = [];
  try {
    const tabs = await chrome.tabs.query({ audible: true });
    hosts = [...new Set(tabs
      .filter((tab) => tab.url && /^https?:/.test(tab.url))
      .map((tab) => hostOf(tab.url))
      .filter(Boolean))].sort().slice(0, 16);
  } catch (_) {
    return;
  }
  const key = hosts.join("|");
  if (key === lastMusicKey) return;
  lastMusicKey = key;
  await sendMusic(hosts);
}

async function tabContext(tabId) {
  try {
    const tab = await chrome.tabs.get(tabId);
    let count = null;
    try {
      const tabs = await chrome.tabs.query({ windowId: tab.windowId });
      count = tabs.length;
    } catch (_) {
      count = null;
    }
    return { tab, index: typeof tab.index === "number" ? tab.index : null, count };
  } catch (_) {
    return null;
  }
}

async function reportClosed(requestId, ok, reason) {
  if (!requestId) return;
  try {
    await fetch(BRIDGE_CLOSED, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ requestId, ok, reason }),
      signal: AbortSignal.timeout(REPORT_TIMEOUT_MS)
    });
  } catch (_) {
    // The app may have quit while the cat was mid-swipe.
  }
}

// The desktop app answers at once and lets its cat animation run separately,
// so the tab is removed by its stored id here, never by a screen click and
// never by assuming whichever tab happens to be active right now.
async function closeIntendedTab(tabId, eventUrl, requestId) {
  let tab;
  try {
    tab = await chrome.tabs.get(tabId);
  } catch (_) {
    await reportClosed(requestId, false, "gone");
    return;
  }
  if (!tab || !tab.url) {
    await reportClosed(requestId, false, "gone");
    return;
  }
  if (hostOf(tab.url) !== hostOf(eventUrl)) {
    await reportClosed(requestId, false, "navigated");
    return;
  }
  try {
    await chrome.tabs.remove(tabId);
    await reportClosed(requestId, true, "closed");
  } catch (_) {
    await reportClosed(requestId, false, "close_failed");
  }
}

async function handleNavigation(details, active = true) {
  if (details.frameId !== 0 || !/^https?:/.test(details.url)) return;
  if (isDuplicate(details.tabId, details.url)) return;
  if (inFlight.has(details.tabId)) return;
  inFlight.add(details.tabId);

  // All decisions are made by the local desktop app's blocklist.
  // If the app is offline, fail open and leave the tab alone.
  try {
    const context = await tabContext(details.tabId);
    if (!context) return;
    const response = await fetch(BRIDGE_EVENT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tabId: details.tabId,
        url: details.url,
        title: details.title || context.tab.title || "",
        active: Boolean(active),
        tabIndex: context.index,
        tabCount: context.count
      }),
      signal: AbortSignal.timeout(EVENT_TIMEOUT_MS)
    });
    if (!response.ok) return;
    const result = await response.json();
    if (result.action !== "close") return;
    const requestId = typeof result.requestId === "string" ? result.requestId : "";
    // Legacy bridges may still ask for a client-side delay.
    const delay = Math.min(Math.max(Number(result.delayMs) || 0, 0), 5000);
    if (delay > 0) await new Promise((resolve) => setTimeout(resolve, delay));
    await closeIntendedTab(details.tabId, details.url, requestId);
  } catch (_) {
    // App isn't running or local bridge isn't reachable: leave the tab alone.
  } finally {
    inFlight.delete(details.tabId);
  }
}

async function activeTab(tabId) {
  try {
    const tab = await chrome.tabs.get(tabId);
    return Boolean(tab && tab.active);
  } catch (_) {
    return false;
  }
}

chrome.webNavigation.onCommitted.addListener(async (details) => {
  if (details.frameId !== 0 || !/^https?:/.test(details.url)) return;
  handleNavigation(details, await activeTab(details.tabId));
});
chrome.webNavigation.onHistoryStateUpdated.addListener(async (details) => {
  if (details.frameId !== 0 || !/^https?:/.test(details.url)) return;
  handleNavigation(details, await activeTab(details.tabId));
});

// Catch tabs that finish loading (including restores) and tabs the user
// switches back to, so an already-open blocked tab is handled too.
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === "complete" && tab.url) {
    handleNavigation({ tabId, frameId: 0, url: tab.url, title: tab.title }, Boolean(tab.active));
  }
  if (changeInfo.audible !== undefined || changeInfo.url
      || changeInfo.status === "complete") {
    beaconMusic();
  }
});

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  try {
    const tab = await chrome.tabs.get(tabId);
    if (tab.url) {
      handleNavigation({ tabId, frameId: 0, url: tab.url, title: tab.title }, true);
    }
    await beaconMusic();
  } catch (_) {
    // Tab vanished before we could read it.
  }
});

chrome.tabs.onRemoved.addListener(() => {
  beaconMusic();
});

chrome.windows.onFocusChanged.addListener((windowId) => {
  if (windowId === chrome.windows.WINDOW_ID_NONE) return;
  beaconMusic();
});

async function scanOpenTabs() {
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (tab.id != null && tab.url) {
      handleNavigation({ tabId: tab.id, frameId: 0, url: tab.url, title: tab.title }, Boolean(tab.active));
    }
  }
  try {
    await beaconMusic();
  } catch (_) {
    // Nothing audible to report.
  }
}

chrome.runtime.onStartup.addListener(scanOpenTabs);
chrome.runtime.onInstalled.addListener(scanOpenTabs);

// The app may restart while music is already playing in a background tab.
// A periodic local refresh makes it relearn the audible set even when no tab
// event fires. The dedupe key is cleared first so the same set is re-sent.
chrome.alarms.create(MUSIC_ALARM, { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== MUSIC_ALARM) return;
  lastMusicKey = null;
  beaconMusic();
});
