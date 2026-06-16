/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Backend WebSocket URL, e.g. wss://your-app.onrender.com/ws */
  readonly VITE_WS_URL?: string;
  /** Shared token the backend requires (must match backend AGENT_TOKEN). */
  readonly VITE_AGENT_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
