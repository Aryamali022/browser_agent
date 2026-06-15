// Message protocol shared between side panel, background worker and backend.
// Mirrors shared/protocol.md — keep the two in sync.

export interface ToolPayload {
  tool: string;
  args: Record<string, unknown>;
}

// Backend -> extension
export type ServerMessage =
  | { type: 'session_started'; session_id: number; user_id: number }
  | { type: 'agent_status'; status: AgentStatus; detail?: string }
  | { type: 'agent_message'; content: string }
  | { type: 'tool_log'; tool: string; args: Record<string, unknown>; ok: boolean; summary: string }
  | { type: 'execute_tool'; id: string; payload: ToolPayload }
  | { type: 'confirmation_required'; id: string; reason: string; tool: string; args: Record<string, unknown> }
  | { type: 'task_complete'; content: string }
  | { type: 'error'; message: string }
  | { type: 'pong' };

export type AgentStatus = 'idle' | 'thinking' | 'executing' | 'waiting_confirmation';

// Which method the agent tries first for element actions; the other is the
// fallback. 'dom' = selector path first, 'vision' = screenshot/coordinate first.
export type AgentMode = 'dom' | 'vision';

// Extension -> backend
export type ClientMessage =
  | { type: 'user_request'; content: string; mode?: AgentMode }
  | { type: 'tool_result'; id: string; ok: boolean; result?: unknown; error?: string }
  | { type: 'confirmation_response'; id: string; approved: boolean }
  | { type: 'stop' }
  | { type: 'ping' };

// Panel <-> background (extra runtime messages)
export type PanelMessage =
  | ClientMessage
  | { type: 'get_state' };

export interface BackgroundState {
  connected: boolean;
  clientId: string;
}

// Chat rendering model used by the side panel
export interface ChatItem {
  kind: 'user' | 'agent' | 'tool' | 'system' | 'final';
  content: string;
  tool?: { name: string; ok: boolean };
  ts: number;
}
