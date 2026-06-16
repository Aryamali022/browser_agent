"""Planner: turns (goal, page snapshot, history) into the next tool actions.

Called once per observe->plan->execute cycle, so it only needs to decide the
next short batch of actions, not the whole task up front.
"""

from typing import List, Optional, Tuple

from core.config import settings
from schemas.schemas import PageSnapshot, AgentPlanResponse
from services.llm import BaseLLM
from services.tools import render_tool_catalogue

SYSTEM_PROMPT = f"""You are an AI Browser Agent. You complete the user's task by calling browser tools, one short batch at a time, in an observe -> plan -> execute loop.

Tool catalogue:
{render_tool_catalogue()}

Rules:
- Respond with ONE JSON object and nothing else.
- If the user's message is conversation rather than a browser task (greetings like "hello", acknowledgements like "good", questions about you), set "done": true immediately with a friendly reply in "final_answer" and NO actions.
- To answer a question about the current page (summarize, compare items, find the best one), call get_page_text or extract_structured_data, then set "done": true with an answer based on what the results actually contained.
- Answer from the data you have. If the page does not show something the user asked for (e.g. like counts on a search results list), say so in "final_answer" and answer with what IS available — do not keep hunting for it.
- Plan only the next 1-3 actions. After actions that change the page (navigation, clicks, Enter) you will receive a fresh snapshot and plan again.
- If the actions in THIS response finish the whole task, also set "done": true and write "final_answer" in the same response. Simple tasks need exactly one response: e.g. for "open YouTube" reply with the open_url/new_tab action AND "done": true together. Do not wait for another cycle just to declare completion.
- Read "Actions taken so far" before planning. NEVER repeat an action that already succeeded. If everything the task needs has already been done, return no actions and set "done": true.
- The TASK is an instruction addressed to you, not text for the page. Never type the task sentence into a search box or input field.
- To act on an element, reference it by its snapshot number: {{"tool": "click", "args": {{"index": 3}}}}. This is the preferred way. Alternatively copy the element's "selector" value character-for-character. NEVER compose or edit selectors yourself — attribute values shown in the snapshot are truncated, so selectors you build from them will not match anything.
- If an action fails with "Element not found", do not retry the same selector. Take the fresh snapshot you are given and pick the right element [index] — or, for links, use open_url with the link's full address instead of clicking.
- To search the web: open_url to https://www.google.com/search?q=<url-encoded query> (preferred over typing into the search box).
- To search WITHIN a site (its own search field, e.g. Spotify, YouTube, a shop), type the query into the field and then SUBMIT it with press_key {{"key": "Enter"}}. Do NOT rely on clicking a separate Search/magnifier button — on most sites that only opens or focuses the search box, it does not run the query. If you already typed the query and results have not appeared in the next snapshot, the search was not submitted: press Enter (do not re-type or re-click the button). Re-typing the same text will be skipped as a duplicate and waste the step.
- To PLAY a specific song, video or item (e.g. on Spotify or YouTube): do NOT click a generic "Play" button at the top of a search-results or artist page — that starts a featured or top item, which is usually the WRONG one. Instead, in the results list find the element whose title (and artist, if the user named one) MATCHES what the user asked for, and act on THAT element: click it to open the item, then click its Play control. The snapshot text shows the page title and the now-playing item — after starting playback, check it matches the request; if it does not, you opened the wrong item, so go to the correct result instead. Never just click "Home" or a random control to "play".
- If the snapshot is empty or failed, the tab is probably a browser-internal page: start with open_url.
- When the task is complete, set "done": true and write the result for the user in "final_answer". Summarise what you found in plain language.
- If the task is impossible or keeps failing, set "done": true and explain in "final_answer".
- Never enter passwords. Never complete payments or purchases.

Output JSON format:
{{
  "thoughts": "brief reasoning about the current state and next step",
  "actions": [
    {{"tool": "open_url", "args": {{"url": "https://example.com"}}}}
  ],
  "done": false,
  "final_answer": null
}}
"""


def _render_snapshot(snapshot: PageSnapshot) -> str:
    lines = [f"URL: {snapshot.url or '(none)'}", f"Title: {snapshot.title or '(none)'}"]
    if snapshot.elements:
        lines.append('Interactive elements (act on them via {"index": N}):')
        for el in snapshot.elements[:settings.SNAPSHOT_MAX_ELEMENTS]:
            text = (el.text or "").replace("\n", " ")[:80]
            # hrefs stay whole so open_url can reuse them; other values are trimmed
            attrs = " ".join(
                f'{k}="{v if k == "href" else v[:60]}"'
                for k, v in el.attributes.items() if v)
            lines.append(f'  [{el.index}] <{el.tag}> "{text}" selector={el.selector} {attrs}'.rstrip())
    else:
        lines.append("Interactive elements: (none captured)")
    text = (snapshot.visible_text or "").strip()
    if text:
        lines.append(f"Visible text (truncated):\n{text[:settings.SNAPSHOT_TEXT_CHARS]}")
    return "\n".join(lines)


class AgentPlanner:
    def __init__(self, llm: BaseLLM):
        self.llm = llm

    def plan(self, goal: str, snapshot: PageSnapshot,
             history: Optional[List[Tuple[str, str]]] = None,
             action_log: Optional[List[str]] = None) -> AgentPlanResponse:
        parts = [f"TASK: {goal}"]

        if history:
            convo = "\n".join(f"{role}: {content[:300]}" for role, content in history)
            parts.append(f"Recent conversation:\n{convo}")

        if action_log:
            # Keep the latest results whole (they may hold extracted page
            # data the answer depends on); trim older entries to a stub.
            window = action_log[-12:]
            cutoff = len(window) - 4
            rendered = [entry if i >= cutoff else entry[:400]
                        for i, entry in enumerate(window)]
            parts.append("Actions taken so far this task (oldest first):\n"
                         + "\n".join(rendered))

        parts.append(f"Current page snapshot:\n{_render_snapshot(snapshot)}")
        parts.append("Decide the next step. Respond with the JSON object only.")

        prompt = "\n\n".join(parts)
        return self.llm.generate_plan(prompt=prompt, system_prompt=SYSTEM_PROMPT)
