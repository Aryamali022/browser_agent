/// <reference types="chrome"/>
// Background service worker: owns the backend WebSocket, routes tool
// execution (tab/navigation tools here, DOM tools to the content script)
// and relays everything else to the side panel.

// Set at build time via extension/.env* (VITE_WS_URL, VITE_AGENT_TOKEN).
// Defaults target a local backend so `npm run dev` works with no config.
const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8000/ws';
const AGENT_TOKEN = import.meta.env.VITE_AGENT_TOKEN || '';
const NAV_TIMEOUT_MS = 15000;
const SETTLE_MS = 600;

let websocket: WebSocket | null = null;
let connected = false;
let clientId = '';
let agentTabId: number | null = null; // tab the agent is working in

chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(console.error);

// ---------------------------------------------------------------- utilities

function send(message: object) {
  if (websocket && websocket.readyState === WebSocket.OPEN) {
    websocket.send(JSON.stringify(message));
  }
}

function toPanel(message: object) {
  chrome.runtime.sendMessage(message).catch(() => { /* panel closed */ });
}

async function getClientId(): Promise<string> {
  if (clientId) return clientId;
  const stored = await chrome.storage.local.get('client_id');
  if (typeof stored.client_id === 'string' && stored.client_id) {
    clientId = stored.client_id;
  } else {
    clientId = 'user-' + crypto.randomUUID().slice(0, 8);
    await chrome.storage.local.set({ client_id: clientId });
  }
  return clientId;
}

async function getAgentTab(): Promise<chrome.tabs.Tab> {
  if (agentTabId !== null) {
    try {
      return await chrome.tabs.get(agentTabId);
    } catch {
      agentTabId = null; // tab was closed
    }
  }
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error('No active tab available');
  agentTabId = tab.id;
  return tab;
}

function waitForLoad(tabId: number): Promise<void> {
  return new Promise(resolve => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      chrome.tabs.onUpdated.removeListener(listener);
      // Small settle delay so SPAs render before we snapshot.
      setTimeout(resolve, SETTLE_MS);
    };
    const listener = (id: number, info: { status?: string }) => {
      if (id === tabId && info.status === 'complete') finish();
    };
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId).then(tab => {
      if (tab.status === 'complete') finish();
    }).catch(finish);
    setTimeout(finish, NAV_TIMEOUT_MS);
  });
}

async function ensureContentScript(tabId: number): Promise<void> {
  const ping = () => chrome.tabs.sendMessage(tabId, { type: 'ping' });
  try {
    await ping();
    return;
  } catch {
    // Not injected (e.g. tab opened before the extension loaded) — inject now.
    await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] });
    await ping();
  }
}

// ------------------------------------------------------------ tool execution

type ToolOutcome = { ok: boolean; result?: unknown; error?: string };

const BACKGROUND_TOOLS = new Set([
  'open_url', 'go_back', 'go_forward', 'refresh_page',
  'new_tab', 'close_tab', 'switch_tab', 'list_tabs',
  'current_url', 'current_title', 'screenshot',
]);

async function runBackgroundTool(tool: string, args: Record<string, unknown>): Promise<ToolOutcome> {
  const tab = tool === 'new_tab' || tool === 'list_tabs' ? null : await getAgentTab();

  switch (tool) {
    case 'open_url':
      await chrome.tabs.update(tab!.id!, { url: String(args.url) });
      await waitForLoad(tab!.id!);
      return { ok: true, result: { success: true, url: String(args.url) } };

    case 'go_back':
      await chrome.tabs.goBack(tab!.id!);
      await waitForLoad(tab!.id!);
      return { ok: true, result: { success: true } };

    case 'go_forward':
      await chrome.tabs.goForward(tab!.id!);
      await waitForLoad(tab!.id!);
      return { ok: true, result: { success: true } };

    case 'refresh_page':
      await chrome.tabs.reload(tab!.id!);
      await waitForLoad(tab!.id!);
      return { ok: true, result: { success: true } };

    case 'new_tab': {
      const created = await chrome.tabs.create({ url: args.url ? String(args.url) : undefined });
      agentTabId = created.id ?? null;
      if (created.id && args.url) await waitForLoad(created.id);
      return { ok: true, result: { success: true, tab_id: created.id } };
    }

    case 'close_tab': {
      await chrome.tabs.remove(tab!.id!);
      agentTabId = null;
      return { ok: true, result: { success: true } };
    }

    case 'switch_tab': {
      const id = Number(args.tab_id);
      const target = await chrome.tabs.get(id);
      await chrome.tabs.update(id, { active: true });
      agentTabId = id;
      return { ok: true, result: { success: true, title: target.title } };
    }

    case 'list_tabs': {
      const tabs = await chrome.tabs.query({ currentWindow: true });
      return {
        ok: true,
        result: tabs.map(t => ({
          tab_id: t.id, title: t.title, url: t.url,
          active: t.active, is_working_tab: t.id === agentTabId,
        })),
      };
    }

    case 'screenshot': {
      // captureVisibleTab shoots the active tab, so bring the agent tab forward.
      if (!tab!.active) {
        await chrome.tabs.update(tab!.id!, { active: true });
        await new Promise(r => setTimeout(r, 300));
      }
      const dataUrl = await chrome.tabs.captureVisibleTab(tab!.windowId!, {
        format: 'jpeg', quality: 70,
      });
      const bitmap = await createImageBitmap(await (await fetch(dataUrl)).blob());
      const size = { width: bitmap.width, height: bitmap.height };
      bitmap.close();
      return { ok: true, result: { data_url: dataUrl, ...size } };
    }

    case 'current_url':
      return { ok: true, result: tab!.url };

    case 'current_title':
      return { ok: true, result: tab!.title };

    default:
      return { ok: false, error: `Unknown background tool: ${tool}` };
  }
}

async function runContentTool(tool: string, args: Record<string, unknown>): Promise<ToolOutcome> {
  const tab = await getAgentTab();
  const url = tab.url || '';
  if (/^(chrome|edge|about|devtools|chrome-extension):/.test(url)) {
    return { ok: false, error: `Cannot access browser-internal page (${url}). Use open_url first.` };
  }
  await ensureContentScript(tab.id!);
  try {
    const response = await chrome.tabs.sendMessage(tab.id!, {
      type: 'execute_tool',
      payload: { tool, args },
    });
    return response as ToolOutcome;
  } catch (e) {
    // Clicks that trigger navigation tear down the message channel before the
    // response arrives — treat that as success and wait for the new page.
    if (tool === 'click' || tool === 'press_key') {
      await waitForLoad(tab.id!);
      return { ok: true, result: { success: true, note: 'action triggered a page navigation' } };
    }
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

async function executeTool(id: string, tool: string, args: Record<string, unknown>) {
  let outcome: ToolOutcome;
  try {
    outcome = BACKGROUND_TOOLS.has(tool)
      ? await runBackgroundTool(tool, args)
      : await runContentTool(tool, args);
  } catch (e) {
    outcome = { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
  send({ type: 'tool_result', id, ...outcome });
}

// ----------------------------------------------------------------- websocket

function connectWebSocket() {
  getClientId().then(cid => {
    const params = new URLSearchParams({ client_id: cid });
    if (AGENT_TOKEN) params.set('token', AGENT_TOKEN);
    websocket = new WebSocket(`${WS_URL}?${params.toString()}`);

    websocket.onopen = () => {
      connected = true;
      toPanel({ type: 'connection_status', connected: true });
    };

    websocket.onmessage = event => {
      const data = JSON.parse(event.data);
      if (data.type === 'execute_tool') {
        executeTool(data.id, data.payload.tool, data.payload.args || {});
      } else if (data.type !== 'pong') {
        toPanel(data);
      }
    };

    websocket.onclose = () => {
      connected = false;
      toPanel({ type: 'connection_status', connected: false });
      setTimeout(connectWebSocket, 4000);
    };

    websocket.onerror = () => websocket?.close();
  });
}

connectWebSocket();

// Keepalive: an active WebSocket plus periodic traffic keeps the MV3 service
// worker alive while the backend is up.
setInterval(() => send({ type: 'ping' }), 20000);

// ------------------------------------------------------------- panel messages

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  switch (message?.type) {
    case 'user_request':
      // Bind the agent to the tab the user is looking at right now.
      chrome.tabs.query({ active: true, currentWindow: true }).then(([tab]) => {
        agentTabId = tab?.id ?? null;
        send({ type: 'user_request', content: message.content, mode: message.mode });
      });
      break;
    case 'stop':
    case 'confirmation_response':
      send(message);
      break;
    case 'get_state':
      getClientId().then(cid => sendResponse({ connected, clientId: cid }));
      return true; // async response
  }
});
