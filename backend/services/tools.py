"""Tool registry: the single source of truth for what the planner may call.

The LLM never touches the browser directly — it emits tool calls and the
extension executes them. `where` says which extension layer handles a tool:
"background" for tab/navigation work, "content" for in-page DOM work, and
"backend" for virtual tools the agent loop resolves itself.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from schemas.schemas import ToolCallRequest


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args: Dict[str, str] = field(default_factory=dict)   # arg name -> description
    required: Tuple[str, ...] = ()
    where: str = "content"                               # content | background | backend


_ALL_TOOLS = [
    # Navigation
    ToolSpec("open_url", "Navigate the working tab to a URL.",
             {"url": "absolute http(s) URL"}, ("url",), "background"),
    ToolSpec("go_back", "Go back in the working tab's history.", {}, (), "background"),
    ToolSpec("go_forward", "Go forward in the working tab's history.", {}, (), "background"),
    ToolSpec("refresh_page", "Reload the working tab.", {}, (), "background"),

    # Tab management
    ToolSpec("new_tab", "Open a new tab (optionally at a URL) and make it the working tab.",
             {"url": "optional URL to open"}, (), "background"),
    ToolSpec("close_tab", "Close the working tab.", {}, (), "background"),
    ToolSpec("switch_tab", "Make another tab the working tab.",
             {"tab_id": "numeric id from list_tabs"}, ("tab_id",), "background"),
    ToolSpec("list_tabs", "List open tabs with their ids, titles and URLs.", {}, (), "background"),

    # Interaction
    ToolSpec("click", "Click an element.",
             {"selector": "CSS selector taken from the page snapshot"}, ("selector",)),
    ToolSpec("double_click", "Double-click an element.",
             {"selector": "CSS selector"}, ("selector",)),
    ToolSpec("right_click", "Right-click (context menu) an element.",
             {"selector": "CSS selector"}, ("selector",)),
    ToolSpec("hover", "Hover the mouse over an element.",
             {"selector": "CSS selector"}, ("selector",)),

    # Text
    ToolSpec("type_text", "Type text into an input, textarea or contenteditable element.",
             {"selector": "CSS selector", "text": "text to type"}, ("selector", "text")),
    ToolSpec("clear_input", "Clear the value of an input or textarea.",
             {"selector": "CSS selector"}, ("selector",)),
    ToolSpec("press_key", "Press a keyboard key (e.g. Enter, Tab, Escape) on an element or the focused element.",
             {"key": "key name, e.g. Enter", "selector": "optional CSS selector of the target"}, ("key",)),

    # Scrolling
    ToolSpec("scroll_up", "Scroll up one screen.", {}, ()),
    ToolSpec("scroll_down", "Scroll down one screen.", {}, ()),
    ToolSpec("scroll_to", "Scroll an element into view.",
             {"selector": "CSS selector"}, ("selector",)),

    # Extraction
    ToolSpec("get_page_text", "Get the visible text of the page.", {}, ()),
    ToolSpec("get_buttons", "List the visible buttons on the page.", {}, ()),
    ToolSpec("get_links", "List the visible links on the page.", {}, ()),
    ToolSpec("extract_structured_data",
             "Extract structured data from the current page matching a description of the wanted fields.",
             {"schema": "plain-text description of the fields to extract"}, ("schema",), "backend"),

    # Browser state
    ToolSpec("current_url", "Get the URL of the working tab.", {}, (), "background"),
    ToolSpec("current_title", "Get the title of the working tab.", {}, (), "background"),
    ToolSpec("get_page_snapshot", "Get a fresh structured snapshot of the page.", {}, ()),
]

TOOLS: Dict[str, ToolSpec] = {t.name: t for t in _ALL_TOOLS}

# Tools after which the page likely changed, so the agent must re-observe
# before planning further actions.
NAVIGATION_TOOLS = {
    "open_url", "go_back", "go_forward", "refresh_page",
    "new_tab", "close_tab", "switch_tab", "click", "press_key",
}


def validate_action(action: ToolCallRequest) -> Optional[str]:
    """Return an error string if the action is malformed, else None."""
    spec = TOOLS.get(action.tool)
    if spec is None:
        return f"Unknown tool '{action.tool}'. Use only the tools listed in the catalogue."
    missing = [a for a in spec.required if not str(action.args.get(a, "")).strip()]
    if missing:
        return f"Tool '{action.tool}' is missing required argument(s): {', '.join(missing)}."
    return None


def render_tool_catalogue() -> str:
    """Human/LLM-readable tool list for the planner system prompt."""
    lines = []
    for spec in TOOLS.values():
        if spec.args:
            args = ", ".join(f"{k}: {v}" for k, v in spec.args.items())
            lines.append(f"- {spec.name}({args}) — {spec.description}")
        else:
            lines.append(f"- {spec.name}() — {spec.description}")
    return "\n".join(lines)
