import { useState, useEffect, useRef, useCallback } from 'react';
import {
  Send, Bot, Square, Trash2, Moon, Sun, Mic, History, Check, X, Wrench, Eye, Code2,
} from 'lucide-react';
import type { AgentStatus, ChatItem, AgentMode } from './types';

const API_BASE = 'http://localhost:8000';

interface Confirmation { id: string; reason: string; tool: string }
interface TaskRow { id: number; goal: string; status: string; created_at: string }

const STATUS_LABEL: Record<AgentStatus, string> = {
  idle: 'Ready',
  thinking: 'Thinking…',
  executing: 'Executing…',
  waiting_confirmation: 'Waiting for you',
};

function sendToBackground(message: object) {
  chrome.runtime.sendMessage(message).catch(() => undefined);
}

function App() {
  const [messages, setMessages] = useState<ChatItem[]>([]);
  const [input, setInput] = useState('');
  const [status, setStatus] = useState<AgentStatus>('idle');
  const [statusDetail, setStatusDetail] = useState('');
  const [connected, setConnected] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [dark, setDark] = useState(() => localStorage.getItem('theme') !== 'light');
  const [showHistory, setShowHistory] = useState(false);
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [clientId, setClientId] = useState('');
  const [listening, setListening] = useState(false);
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [mode, setMode] = useState<AgentMode>(
    () => (localStorage.getItem('agent_mode') === 'vision' ? 'vision' : 'dom'));
  const endRef = useRef<HTMLDivElement>(null);
  const restoredRef = useRef(false);

  const push = useCallback((item: Omit<ChatItem, 'ts'>) => {
    setMessages(prev => [...prev, { ...item, ts: Date.now() }]);
  }, []);

  // Theme
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark);
    localStorage.setItem('theme', dark ? 'dark' : 'light');
  }, [dark]);

  // Persist the chosen DOM/vision method across panel reloads.
  useEffect(() => {
    localStorage.setItem('agent_mode', mode);
  }, [mode]);

  // Restore chat from session storage (survives panel close, not browser restart)
  useEffect(() => {
    chrome.storage.session.get('chat').then(stored => {
      if (Array.isArray(stored.chat)) setMessages(stored.chat);
      restoredRef.current = true;
    });
    chrome.runtime.sendMessage({ type: 'get_state' })
      .then(state => {
        if (state) {
          setConnected(!!state.connected);
          setClientId(state.clientId || '');
        }
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (restoredRef.current) chrome.storage.session.set({ chat: messages.slice(-200) });
  }, [messages]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, status, confirmation]);

  // Messages from background worker
  useEffect(() => {
    const handler = (message: Record<string, unknown>) => {
      switch (message.type) {
        case 'connection_status':
          setConnected(!!message.connected);
          break;
        case 'session_started':
          setSessionId(Number(message.session_id));
          break;
        case 'agent_status':
          setStatus(message.status as AgentStatus);
          setStatusDetail(String(message.detail || ''));
          if (message.status !== 'waiting_confirmation') setConfirmation(null);
          break;
        case 'agent_message':
          push({ kind: 'agent', content: String(message.content) });
          break;
        case 'tool_log':
          push({
            kind: 'tool',
            content: String(message.summary || ''),
            tool: { name: String(message.tool), ok: !!message.ok },
          });
          break;
        case 'task_complete':
          push({ kind: 'final', content: String(message.content) });
          setStatus('idle');
          setConfirmation(null);
          break;
        case 'confirmation_required':
          setConfirmation({
            id: String(message.id),
            reason: String(message.reason),
            tool: String(message.tool),
          });
          break;
        case 'error':
          push({ kind: 'system', content: String(message.message) });
          setStatus('idle');
          break;
      }
    };
    chrome.runtime.onMessage.addListener(handler);
    return () => chrome.runtime.onMessage.removeListener(handler);
  }, [push]);

  const handleSend = () => {
    const content = input.trim();
    if (!content || status !== 'idle') return;
    push({ kind: 'user', content });
    setInput('');
    setStatus('thinking');
    sendToBackground({ type: 'user_request', content, mode });
  };

  const handleStop = () => sendToBackground({ type: 'stop' });

  const handleClear = () => {
    setMessages([]);
    chrome.storage.session.remove('chat');
  };

  const answerConfirmation = (approved: boolean) => {
    if (!confirmation) return;
    sendToBackground({ type: 'confirmation_response', id: confirmation.id, approved });
    setConfirmation(null);
  };

  const openMicPermissionPage = useCallback(() => {
    chrome.tabs.create({ url: chrome.runtime.getURL('permission.html') }).catch(() => undefined);
  }, []);

  const handleVoice = async () => {
    const SpeechRecognition = (window as unknown as Record<string, any>).webkitSpeechRecognition;
    if (!SpeechRecognition) {
      push({ kind: 'system', content: 'Voice input is not available in this browser yet — the full voice module arrives in Phase 4.' });
      return;
    }
    // Side panels cannot show the mic permission prompt; if the extension
    // doesn't have the grant yet, get it via a dedicated tab first.
    try {
      const perm = await navigator.permissions.query({ name: 'microphone' as PermissionName });
      if (perm.state !== 'granted') {
        openMicPermissionPage();
        push({ kind: 'system', content: 'Allow microphone access in the tab that just opened, then press the mic button again.' });
        return;
      }
    } catch { /* permissions API unavailable — try recognition directly */ }

    const recognition = new SpeechRecognition();
    recognition.lang = 'en-IN';
    recognition.interimResults = false;
    recognition.onresult = (event: any) => setInput(event.results[0][0].transcript);
    recognition.onend = () => setListening(false);
    recognition.onerror = (event: any) => {
      setListening(false);
      const code = event?.error || 'unknown';
      if (code === 'not-allowed' || code === 'service-not-allowed') {
        openMicPermissionPage();
        push({ kind: 'system', content: 'Microphone permission is blocked. Allow it in the tab that just opened, then try again.' });
      } else if (code === 'no-speech') {
        push({ kind: 'system', content: 'I didn’t hear anything — try again, a bit closer to the mic.' });
      } else if (code === 'network') {
        push({ kind: 'system', content: 'Speech service unreachable — Chrome’s voice recognition needs an internet connection.' });
      } else if (code !== 'aborted') {
        push({ kind: 'system', content: `Voice input failed (${code}).` });
      }
    };
    setListening(true);
    recognition.start();
  };

  const clearHistory = async () => {
    if (!confirmClear) {
      setConfirmClear(true);
      setTimeout(() => setConfirmClear(false), 4000);
      return;
    }
    setConfirmClear(false);
    if (!clientId) return;
    try {
      const keep = sessionId !== null ? `?keep_session=${sessionId}` : '';
      await fetch(`${API_BASE}/api/users/${clientId}/history${keep}`, { method: 'DELETE' });
      setTasks([]);
      push({ kind: 'system', content: 'Saved history cleared.' });
    } catch {
      push({ kind: 'system', content: 'Could not clear history — is the backend running?' });
    }
  };

  const toggleHistory = async () => {
    const next = !showHistory;
    setShowHistory(next);
    if (next && clientId) {
      try {
        const response = await fetch(`${API_BASE}/api/users/${clientId}/tasks`);
        const data = await response.json();
        setTasks(data.tasks || []);
      } catch {
        setTasks([]);
      }
    }
  };

  const busy = status !== 'idle';

  return (
    <div className="flex flex-col h-screen bg-gray-50 dark:bg-zinc-900 text-gray-900 dark:text-zinc-100">
      {/* Header */}
      <header className="bg-white dark:bg-zinc-800 shadow-sm px-3 py-2 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <Bot className="w-5 h-5 text-blue-600 dark:text-blue-400 shrink-0" />
          <h1 className="font-semibold text-sm truncate">Browser Agent</h1>
          <span
            className={`w-2 h-2 rounded-full shrink-0 ${connected ? 'bg-green-500' : 'bg-red-500'}`}
            title={connected ? 'Backend connected' : 'Backend offline — start the FastAPI server'}
          />
        </div>
        <div className="flex items-center gap-1">
          <button onClick={toggleHistory} title="Task history"
            className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-zinc-700">
            <History className="w-4 h-4" />
          </button>
          <button onClick={() => setDark(d => !d)} title="Toggle theme"
            className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-zinc-700">
            {dark ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
          </button>
          <button onClick={handleClear} title="Clear conversation"
            className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-zinc-700">
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </header>

      {/* Status bar */}
      <div className={`px-3 py-1.5 text-xs flex items-center gap-2 shrink-0 border-b
        ${busy ? 'bg-blue-50 dark:bg-blue-950/40 border-blue-100 dark:border-blue-900'
               : 'bg-white dark:bg-zinc-800 border-gray-100 dark:border-zinc-700'}`}>
        <span className={`w-1.5 h-1.5 rounded-full ${busy ? 'bg-blue-500 animate-pulse' : 'bg-gray-300 dark:bg-zinc-600'}`} />
        <span className="font-medium">{STATUS_LABEL[status]}</span>
        {statusDetail && <span className="truncate text-gray-500 dark:text-zinc-400">{statusDetail}</span>}
        {busy && (
          <button onClick={handleStop}
            className="ml-auto flex items-center gap-1 px-2 py-0.5 rounded-md bg-red-600 text-white hover:bg-red-700">
            <Square className="w-3 h-3" /> Stop
          </button>
        )}
      </div>

      {/* History drawer */}
      {showHistory && (
        <div className="border-b border-gray-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 max-h-48 overflow-y-auto shrink-0">
          <div className="px-3 pt-2 flex items-center justify-between">
            <p className="text-xs font-semibold text-gray-500 dark:text-zinc-400">Recent tasks</p>
            <button onClick={clearHistory}
              className={`text-xs font-medium ${confirmClear
                ? 'text-white bg-red-600 px-2 py-0.5 rounded-md'
                : 'text-red-500 hover:underline'}`}>
              {confirmClear ? 'Click again to confirm' : 'Clear history'}
            </button>
          </div>
          {tasks.length === 0 && <p className="px-3 py-2 text-xs text-gray-400">No tasks yet.</p>}
          {tasks.map(task => (
            <div key={task.id} className="px-3 py-1.5 text-xs flex items-center gap-2">
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                task.status === 'completed' ? 'bg-green-500'
                : task.status === 'in_progress' ? 'bg-blue-500' : 'bg-gray-400'}`} />
              <span className="truncate flex-1">{task.goal}</span>
              <span className="text-gray-400 shrink-0">{task.status}</span>
            </div>
          ))}
        </div>
      )}

      {/* Chat */}
      <main className="flex-1 overflow-y-auto p-3 space-y-2">
        {messages.length === 0 && (
          <div className="text-center text-sm text-gray-400 dark:text-zinc-500 mt-10 px-4 space-y-2">
            <Bot className="w-10 h-10 mx-auto opacity-40" />
            <p>Ask me to do something in your browser.</p>
            <p className="text-xs">e.g. “Search for the best AI laptops under ₹80,000” or “Summarize this page”.</p>
          </div>
        )}

        {messages.map((item, i) => {
          if (item.kind === 'tool') {
            return (
              <div key={i} className="flex items-start gap-2 text-xs px-1 text-gray-500 dark:text-zinc-400">
                <Wrench className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                <span className="font-mono">
                  <span className={item.tool?.ok ? 'text-green-600 dark:text-green-400' : 'text-red-500'}>
                    {item.tool?.name}
                  </span>
                  {' '}— {item.content}
                </span>
              </div>
            );
          }
          if (item.kind === 'system') {
            return (
              <p key={i} className="text-center text-xs text-amber-600 dark:text-amber-400 px-2">
                {item.content}
              </p>
            );
          }
          const isUser = item.kind === 'user';
          return (
            <div key={i} className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm whitespace-pre-wrap ${
                isUser
                  ? 'bg-blue-600 text-white rounded-br-sm'
                  : item.kind === 'final'
                    ? 'bg-green-50 dark:bg-green-950/40 border border-green-200 dark:border-green-900 rounded-bl-sm'
                    : 'bg-white dark:bg-zinc-800 border border-gray-200 dark:border-zinc-700 shadow-sm rounded-bl-sm'
              }`}>
                {item.content}
              </div>
            </div>
          );
        })}

        {(status === 'thinking' || status === 'executing') && (
          <div className="flex justify-start">
            <div className="bg-white dark:bg-zinc-800 border border-gray-200 dark:border-zinc-700 shadow-sm rounded-2xl rounded-bl-sm px-3 py-2 flex gap-1">
              <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" />
              <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:120ms]" />
              <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:240ms]" />
            </div>
          </div>
        )}

        {confirmation && (
          <div className="border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-950/40 rounded-xl p-3 text-sm space-y-2">
            <p className="font-medium">Confirmation needed</p>
            <p className="text-xs text-gray-600 dark:text-zinc-300">
              {confirmation.reason} <span className="font-mono">({confirmation.tool})</span>
            </p>
            <div className="flex gap-2">
              <button onClick={() => answerConfirmation(true)}
                className="flex items-center gap-1 px-3 py-1 rounded-lg bg-green-600 text-white text-xs hover:bg-green-700">
                <Check className="w-3 h-3" /> Allow
              </button>
              <button onClick={() => answerConfirmation(false)}
                className="flex items-center gap-1 px-3 py-1 rounded-lg bg-gray-200 dark:bg-zinc-700 text-xs hover:bg-gray-300 dark:hover:bg-zinc-600">
                <X className="w-3 h-3" /> Deny
              </button>
            </div>
          </div>
        )}

        <div ref={endRef} />
      </main>

      {/* Composer */}
      <footer className="p-3 bg-white dark:bg-zinc-800 border-t border-gray-200 dark:border-zinc-700 shrink-0">
        {/* Method picker: which path the agent tries first (the other is the fallback) */}
        <div className="flex items-center gap-2 mb-2">
          <span className="text-xs text-gray-400 dark:text-zinc-500">Try first:</span>
          <div className="inline-flex rounded-lg border border-gray-200 dark:border-zinc-700 p-0.5 text-xs">
            <button
              onClick={() => setMode('dom')}
              title="Use the page structure (DOM selectors) first; fall back to vision if it fails"
              className={`flex items-center gap-1 px-2 py-0.5 rounded-md transition-colors ${
                mode === 'dom'
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-500 dark:text-zinc-400 hover:bg-gray-100 dark:hover:bg-zinc-700'}`}>
              <Code2 className="w-3 h-3" /> DOM
            </button>
            <button
              onClick={() => setMode('vision')}
              title="Locate elements visually on a screenshot first; fall back to DOM if it fails"
              className={`flex items-center gap-1 px-2 py-0.5 rounded-md transition-colors ${
                mode === 'vision'
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-500 dark:text-zinc-400 hover:bg-gray-100 dark:hover:bg-zinc-700'}`}>
              <Eye className="w-3 h-3" /> Vision
            </button>
          </div>
        </div>
        <div className="flex gap-2 items-center">
          <button onClick={handleVoice} title={listening ? 'Listening…' : 'Voice input'}
            className={`p-2 rounded-full transition-colors ${
              listening
                ? 'bg-red-100 dark:bg-red-950 text-red-600 dark:text-red-400 animate-pulse'
                : 'hover:bg-gray-100 dark:hover:bg-zinc-700 text-gray-500 dark:text-zinc-400'}`}>
            <Mic className="w-4 h-4" />
          </button>
          <input
            type="text"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSend()}
            placeholder={busy ? 'Agent is working…' : 'Ask the agent to do something…'}
            disabled={busy}
            className="flex-1 rounded-full border border-gray-300 dark:border-zinc-600 bg-transparent px-4 py-2 text-sm
                       focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
          />
          <button
            onClick={handleSend}
            disabled={busy || !input.trim()}
            className="bg-blue-600 text-white p-2 rounded-full hover:bg-blue-700 transition-colors
                       flex items-center justify-center w-9 h-9 disabled:opacity-40"
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
      </footer>
    </div>
  );
}

export default App;
