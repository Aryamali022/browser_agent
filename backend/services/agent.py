"""The agentic loop: observe -> plan -> execute -> observe -> ... -> complete.

AgentRunner is transport-agnostic: it drives the browser through a
BrowserBridge (implemented by the websocket layer, faked in tests), so the
loop logic is testable without Chrome or a network.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import List, Optional, Protocol

from core.config import settings
from core.security import SecurityGuard, ALLOW, CONFIRM, DENY
from schemas.schemas import PageSnapshot, ToolCallRequest, ToolResult
from services.llm import BaseLLM, extract_json
from services.memory import MemoryService
from services.planner import AgentPlanner
from services.tools import TOOLS, NAVIGATION_TOOLS, validate_action
from services.vision import VisionLocator

logger = logging.getLogger("agent")


class BrowserBridge(Protocol):
    """What the agent needs from the outside world."""

    async def execute_tool(self, tool: str, args: dict) -> ToolResult: ...
    async def request_confirmation(self, reason: str, tool: str, args: dict) -> bool: ...
    async def emit(self, message: dict) -> None: ...


@dataclass
class ActionOutcome:
    executed: bool = False   # the tool actually ran
    ok: bool = False         # ran and succeeded (or was a harmless skip)
    navigated: bool = False  # page likely changed; re-observe
    skipped: bool = False    # not run: identical action already succeeded or kept failing


# Repeating these with identical args is almost always a planning loop, never
# progress (unlike e.g. click on a "next page" button or scroll_down).
DEDUP_TOOLS = {"new_tab", "open_url", "switch_tab", "close_tab",
               "type_text", "go_back", "go_forward"}

# Tools that target an element; they accept {"index": N} referring to the
# snapshot's element list, resolved server-side to that element's selector so
# the model never has to write CSS selectors itself.
SELECTOR_TOOLS = {"click", "double_click", "right_click", "hover",
                  "type_text", "clear_input", "press_key", "scroll_to"}

# Give up on an identical action after this many failed executions.
MAX_SAME_FAILURES = 2

# Stop repeating an identical action after this many executions even when each
# one "succeeds" — a click that keeps landing on the wrong element reports
# success but never advances the task (e.g. re-clicking a dead search result).
MAX_SAME_ACTION = 3

# DOM actions the vision model can take over when the selector path fails.
VISION_FALLBACK_TOOLS = {"click", "double_click", "hover", "type_text"}

# Results of these tools ARE the data the planner needs (page text, extracted
# fields, link lists) — keep them nearly whole in the planner's action log.
# Everything else (clicks, scrolls) only needs a short ok/error note.
EXTRACTION_TOOLS = {"get_page_text", "extract_structured_data",
                    "get_buttons", "get_links", "list_tabs"}
EXTRACTION_LOG_CHARS = 3000

VERIFIER_SYSTEM = """You judge whether a browser-automation task is already complete.
Given the task, the actions already performed and the current page, decide if the user's goal is fully achieved.
Respond with ONE JSON object only:
{"done": true/false, "final_answer": "if done, a short plain-language result for the user; else empty string"}"""


class AgentRunner:
    def __init__(self, llm: BaseLLM, memory: MemoryService,
                 bridge: BrowserBridge, session_id: int,
                 vision: Optional[VisionLocator] = None,
                 mode: str = "dom"):
        self.llm = llm
        self.memory = memory
        self.bridge = bridge
        self.session_id = session_id
        self.vision = vision
        # Which method the user chose to try FIRST for element actions:
        # "dom"    -> selector path first, vision rescues a DOM failure.
        # "vision" -> screenshot/coordinate path first, DOM rescues it.
        # Either way the other method is the fallback.
        self.mode = mode if mode in ("dom", "vision") else "dom"
        self.planner = AgentPlanner(llm)
        self.guard = SecurityGuard()
        self._succeeded: set = set()   # keys of successful DEDUP_TOOLS actions
        self._failed: dict = {}        # action key -> consecutive failure count
        self._executed: dict = {}      # action key -> total executions (ok or not)
        self._banned: set = set()      # selectors proven to be dead ends

    async def run(self, goal: str) -> None:
        task_id = self.memory.create_task(self.session_id, goal)
        action_log: List[str] = []
        final_answer: Optional[str] = None
        status = "failed"
        try:
            await self._status("thinking", goal)
            history = self.memory.get_recent_messages(
                self.session_id, settings.HISTORY_MESSAGES)
            snapshot = await self._observe(action_log)
            repeat_strikes = 0
            recovery_attempts = 0

            for cycle in range(settings.MAX_PLAN_CYCLES):
                await self._status("thinking", goal)
                try:
                    plan = await asyncio.to_thread(
                        self.planner.plan, goal, snapshot, history, action_log)
                except Exception as exc:
                    logger.exception("Planner failed")
                    final_answer = f"I hit an LLM error and had to stop: {exc}"
                    break

                if plan.thoughts:
                    await self.bridge.emit({"type": "agent_message", "content": plan.thoughts})

                batch: List[ActionOutcome] = []
                for action in plan.actions[:settings.MAX_ACTIONS_PER_PLAN]:
                    outcome = await self._run_action(task_id, action, snapshot, action_log)
                    batch.append(outcome)
                    if not outcome.ok or outcome.navigated:
                        break

                # The plan may carry both actions and done=true: run the
                # actions, then finish — one-step tasks need only one cycle.
                if plan.done and all(o.ok for o in batch):
                    final_answer = plan.final_answer or plan.thoughts or "Task complete."
                    status = "completed"
                    break

                if not plan.actions:
                    action_log.append("planner returned no actions; asked to decide again")

                # Two consecutive cycles in which every action was skipped
                # means the planner is looping — on a finished task (repeats
                # of successes) or on an action that will never work.
                if batch and all(o.skipped for o in batch):
                    repeat_strikes += 1
                    if repeat_strikes >= 2:
                        if all(o.ok for o in batch):
                            # Every skip was a success-repeat: the requested
                            # actions are genuinely already done.
                            status = "completed"
                            final_answer = await self._summarize_outcome(
                                goal, action_log, done=True)
                            break
                        # Stuck repeating something that keeps failing. Roll
                        # back this approach and re-think a different next step
                        # instead of giving up — but only a limited number of
                        # times, then stop for real.
                        if recovery_attempts < settings.MAX_RECOVERY_ATTEMPTS:
                            recovery_attempts += 1
                            repeat_strikes = 0
                            # Fall back to the last known-good state: the failing
                            # repeats changed nothing, so a fresh snapshot IS the
                            # page as it was right after the last successful step.
                            snapshot = await self._observe(action_log)
                            # Verify there before thrashing further — loops are
                            # often the agent not realising it has ALREADY met the
                            # goal. If so, stop and report success.
                            if settings.VERIFY_COMPLETION:
                                verified, answer = await self._verify_done(
                                    goal, action_log, snapshot)
                                if verified:
                                    final_answer = answer
                                    status = "completed"
                                    break
                            # Not done — abandon this approach and plan anew.
                            action_log.append(
                                "RECOVERY: you are stuck repeating actions that do not work. "
                                "Abandon that approach completely. Do NOT propose any action "
                                "you have already tried. Re-read the current page snapshot and "
                                "choose a fundamentally different next step — a different element "
                                "[index], open_url with a direct address, scroll to reveal more, "
                                "or set done=true with an explanation if the task is impossible.")
                            await self.bridge.emit({"type": "agent_message",
                                "content": "That approach isn't working — rethinking a different way."})
                            continue
                        final_answer = await self._summarize_outcome(
                            goal, action_log, done=False)
                        break
                else:
                    repeat_strikes = 0

                # Always re-observe before the next plan so the planner sees
                # the real page state, never a stale snapshot.
                snapshot = await self._observe(action_log)

                # Safety net for planners that forget done=true: after a
                # productive cycle, independently check if the goal is met
                # and stop automatically instead of planning further. Only
                # kicks in from VERIFY_AFTER_CYCLE onward — verifying after
                # every early step just doubles the LLM calls for a task that
                # is obviously nowhere near done yet.
                if (settings.VERIFY_COMPLETION
                        and cycle >= settings.VERIFY_AFTER_CYCLE
                        and any(o.executed and o.ok for o in batch)):
                    verified, answer = await self._verify_done(goal, action_log, snapshot)
                    if verified:
                        final_answer = answer
                        status = "completed"
                        break
            else:
                final_answer = await self._summarize_outcome(
                    goal, action_log, done=False)

            if final_answer is None:
                final_answer = "I stopped before completing the task."
        except asyncio.CancelledError:
            self.memory.set_task_status(task_id, "stopped")
            self.memory.add_message(self.session_id, "agent", "Task stopped by user.")
            try:
                await self.bridge.emit({"type": "agent_message",
                                        "content": "Stopped. Ready for a new task."})
                await self._status("idle")
            except Exception:
                pass  # socket may already be gone
            raise
        except Exception as exc:
            logger.exception("Agent loop crashed")
            final_answer = f"Something went wrong and I had to stop: {exc}"

        self.memory.set_task_status(task_id, status)
        self.memory.add_message(self.session_id, "agent", final_answer)
        await self.bridge.emit({"type": "task_complete", "content": final_answer})
        await self._status("idle")

    # --- helpers ------------------------------------------------------------

    async def _status(self, state: str, detail: str = "") -> None:
        await self.bridge.emit({"type": "agent_status", "status": state, "detail": detail})

    async def _observe(self, action_log: List[str]) -> PageSnapshot:
        result = await self.bridge.execute_tool("get_page_snapshot", {})
        if result.ok and isinstance(result.result, dict):
            try:
                snapshot = PageSnapshot(**result.result)
            except Exception as exc:
                action_log.append(f"observe -> malformed snapshot ({exc})")
                return PageSnapshot()
            if self._banned:
                # Hide elements the agent already proved are dead ends so the
                # planner can't keep re-selecting the same target every cycle —
                # this is what actually forces the recovery re-plan to diverge.
                snapshot.elements = [e for e in snapshot.elements
                                     if e.selector not in self._banned]
            return snapshot
        action_log.append(f"observe -> failed: {result.error or 'no data'} "
                          "(probably a browser-internal page; use open_url)")
        return PageSnapshot()

    def _resolve_index(self, action: ToolCallRequest, snapshot: PageSnapshot,
                       action_log: List[str]) -> bool:
        """Turn {"index": N} into the snapshot element's real selector."""
        if action.tool not in SELECTOR_TOOLS or "index" not in action.args:
            return True
        index = action.args.pop("index")
        if str(action.args.get("selector", "")).strip():
            return True  # explicit selector wins
        try:
            element = next(e for e in snapshot.elements if e.index == int(index))
        except (StopIteration, TypeError, ValueError):
            action_log.append(
                f"{action.tool} -> rejected: element index {index!r} is not in the "
                "current snapshot. Use an [index] from the latest snapshot.")
            return False
        action.args["selector"] = element.selector
        return True

    async def _run_action(self, task_id: int, action: ToolCallRequest,
                          snapshot: PageSnapshot,
                          action_log: List[str]) -> ActionOutcome:
        """Validate, guard and execute one planned action."""
        if not self._resolve_index(action, snapshot, action_log):
            return ActionOutcome()

        error = validate_action(action)
        if error:
            action_log.append(f"{action.tool} -> rejected: {error}")
            return ActionOutcome()

        dedup_key = f"{action.tool}|{json.dumps(action.args, sort_keys=True, default=str)}"
        if action.tool in DEDUP_TOOLS and dedup_key in self._succeeded:
            action_log.append(
                f"{action.tool}({action.args}) -> SKIPPED: this exact action already "
                "succeeded earlier. Do not repeat it. If the task is finished, "
                "set done=true with a final_answer.")
            return ActionOutcome(ok=True, skipped=True)

        if self._failed.get(dedup_key, 0) >= MAX_SAME_FAILURES:
            action_log.append(
                f"{action.tool}({action.args}) -> SKIPPED: this exact action already "
                f"failed {MAX_SAME_FAILURES} times. Do NOT try it again. Pick a "
                "different element [index] from the fresh snapshot, or a different "
                "approach (e.g. open_url with the link's address).")
            return ActionOutcome(skipped=True)

        # The same action can "succeed" repeatedly while getting nowhere (a
        # click that keeps hitting the wrong element). Treat that as a loop too
        # so it feeds the recovery path instead of burning every cycle.
        if self._executed.get(dedup_key, 0) >= MAX_SAME_ACTION:
            action_log.append(
                f"{action.tool}({action.args}) -> SKIPPED: you have already done this "
                f"exact action {MAX_SAME_ACTION} times and it is NOT advancing the task. "
                "Stop repeating it. Choose a DIFFERENT element [index] from the fresh "
                "snapshot, or a different approach entirely.")
            return ActionOutcome(skipped=True)

        decision = self.guard.check(action, snapshot)
        if decision.action == DENY:
            action_log.append(f"{action.tool}({action.args}) -> blocked by safety rules: {decision.reason}")
            await self.bridge.emit({"type": "agent_message",
                                    "content": f"I won't do that: {decision.reason}"})
            return ActionOutcome()
        if decision.action == CONFIRM:
            await self._status("waiting_confirmation", decision.reason)
            approved = await self.bridge.request_confirmation(
                decision.reason, action.tool, action.args)
            if not approved:
                action_log.append(f"{action.tool}({action.args}) -> user declined confirmation")
                await self.bridge.emit({"type": "agent_message",
                                        "content": "Okay, I skipped that step."})
                return ActionOutcome()

        await self._status("executing", f"{action.tool}")

        result = await self._execute_action(action, snapshot)

        self.memory.log_tool(task_id, action.tool, action.args,
                             result.ok, result.result, result.error)
        await self.bridge.emit({
            "type": "tool_log", "tool": action.tool, "args": action.args,
            "ok": result.ok, "summary": result.summary(200),
        })
        log_chars = EXTRACTION_LOG_CHARS if action.tool in EXTRACTION_TOOLS else 200
        action_log.append(f"{action.tool}({action.args}) -> {result.summary(log_chars)}")

        self._executed[dedup_key] = self._executed.get(dedup_key, 0) + 1
        if result.ok:
            if action.tool in DEDUP_TOOLS:
                self._succeeded.add(dedup_key)
            self._failed.pop(dedup_key, None)
        else:
            self._failed[dedup_key] = self._failed.get(dedup_key, 0) + 1

        # Once an element action has hit either cap, ban its selector so the
        # next snapshot drops it and the planner is forced onto a new target.
        selector = action.args.get("selector")
        if selector and (self._executed[dedup_key] >= MAX_SAME_ACTION
                         or self._failed.get(dedup_key, 0) >= MAX_SAME_FAILURES):
            self._banned.add(selector)
        return ActionOutcome(executed=True, ok=result.ok,
                             navigated=result.ok and action.tool in NAVIGATION_TOOLS)

    async def _execute_action(self, action: ToolCallRequest,
                              snapshot: PageSnapshot) -> ToolResult:
        """Run one action, trying the user's chosen method first and the other
        as a fallback. Only element actions (VISION_FALLBACK_TOOLS) have a
        vision path; everything else always goes through the DOM bridge."""
        if action.tool == "extract_structured_data":
            return await self._extract_structured(action.args.get("schema", ""))

        vision_capable = (self.vision is not None
                          and action.tool in VISION_FALLBACK_TOOLS)

        # Vision-first: locate on a screenshot and act at coordinates; if the
        # model can't find it, fall back to the DOM selector path.
        if vision_capable and self.mode == "vision":
            vision_result = await self._vision_fallback(action, snapshot)
            if vision_result is not None:
                return vision_result
            return await self.bridge.execute_tool(action.tool, action.args)

        # DOM-first (default): selector path, then let vision rescue a failure.
        result = await self.bridge.execute_tool(action.tool, action.args)
        if not result.ok and vision_capable:
            vision_result = await self._vision_fallback(action, snapshot)
            if vision_result is not None:
                result = vision_result
        return result

    def _describe_target(self, action: ToolCallRequest,
                         snapshot: PageSnapshot) -> str:
        """Natural-language description of the action's target for the VLM."""
        selector = str(action.args.get("selector", "") or "")
        for el in snapshot.elements:
            if el.selector == selector:
                hints = [f'a <{el.tag}> element']
                if el.text:
                    hints.append(f'with the text "{el.text[:80]}"')
                for key in ("aria-label", "placeholder", "title", "href"):
                    value = el.attributes.get(key)
                    if value:
                        hints.append(f'{key}="{value[:80]}"')
                return " ".join(hints)
        return f"the element matching the CSS selector {selector}"

    async def _vision_fallback(self, action: ToolCallRequest,
                               snapshot: PageSnapshot) -> Optional[ToolResult]:
        """Retry a failed DOM action by locating the target on a screenshot."""
        description = self._describe_target(action, snapshot)
        shot = await self.bridge.execute_tool("screenshot", {})
        if not shot.ok or not isinstance(shot.result, dict):
            return None
        data_url = shot.result.get("data_url")
        width, height = shot.result.get("width"), shot.result.get("height")
        if not data_url or not width or not height:
            return None
        try:
            point = await asyncio.to_thread(
                self.vision.locate, data_url, int(width), int(height), description)
        except Exception:
            logger.warning("Vision locate failed", exc_info=True)
            return None
        if point is None:
            return None
        fx, fy = point

        if action.tool == "type_text":
            retried = await self.bridge.execute_tool(
                "type_at_point", {"fx": fx, "fy": fy,
                                  "text": action.args.get("text", "")})
        else:
            retried = await self.bridge.execute_tool(
                "click_at_point", {"fx": fx, "fy": fy, "kind": action.tool})
        if not retried.ok:
            return None
        payload = retried.result if isinstance(retried.result, dict) else {"success": True}
        return ToolResult(ok=True, result={**payload, "via": "vision fallback"})

    async def _verify_done(self, goal: str, action_log: List[str],
                           snapshot: PageSnapshot) -> tuple[bool, str]:
        """Independent completion check. Errs on the side of 'not done'."""
        prompt = (f"TASK: {goal}\n\n"
                  "Actions performed so far:\n" + "\n".join(action_log[-10:]) +
                  f"\n\nCurrent page: {snapshot.url} — {snapshot.title}\n\n"
                  "Is the task fully complete? Respond with the JSON object only.")
        try:
            raw = await asyncio.to_thread(self.llm.complete, VERIFIER_SYSTEM, prompt)
            verdict = json.loads(extract_json(raw))
            if verdict.get("done"):
                return True, str(verdict.get("final_answer") or "Task complete.")
        except Exception:
            logger.warning("Completion check failed; continuing the loop", exc_info=True)
        return False, ""

    async def _summarize_outcome(self, goal: str, action_log: List[str],
                                 done: bool) -> str:
        """Write the user-facing closing message in plain language.

        The internal action_log is full of selectors, tool names and notes
        like "SKIPPED ...". The user must never see that — turn it into one
        short, friendly status instead. Falls back to a clean static message
        if the LLM call fails, so we never leak the raw log either way.
        """
        prompt = (
            f"You are a browser agent reporting back to the user who asked: \"{goal}\"\n\n"
            "This is your own internal log of what you did (NOT for the user):\n"
            + "\n".join(action_log[-12:]) +
            "\n\nWrite a SHORT message (1-2 sentences) to the user describing the "
            "result in plain language. "
            + ("Confirm what you accomplished."
               if done else
               "Explain honestly that you could not finish, what you did get done, "
               "and suggest what they could try (e.g. rephrasing the task).") +
            " Do NOT mention CSS selectors, element indexes, tool names, JSON, or "
            "internal words like 'SKIPPED', 'snapshot' or 'cycle'. Just talk to the user.")
        try:
            raw = await asyncio.to_thread(
                self.llm.complete,
                "You report browser-automation results to a non-technical user.",
                prompt)
            message = raw.strip()
            if message:
                return message
        except Exception:
            logger.warning("Outcome summary failed; using fallback", exc_info=True)
        return ("I finished what you asked." if done else
                "I wasn't able to finish that task. You could try rephrasing it or "
                "breaking it into smaller steps.")

    async def _extract_structured(self, schema: str) -> ToolResult:
        """Virtual tool: pull page text, then have the LLM extract fields."""
        page = await self.bridge.execute_tool("get_page_text", {})
        if not page.ok:
            return page
        prompt = (f"Extract the following from this page text as a JSON object. "
                  f"If the page lists multiple items, return an array covering ALL of "
                  f"them, not just the first (use null for missing fields):\n"
                  f"Fields: {schema}\n\n"
                  f"Page text:\n{str(page.result)[:12000]}\n\nRespond with JSON only.")
        try:
            raw = await asyncio.to_thread(
                self.llm.complete, "You extract structured data from web pages.", prompt)
            from services.llm import extract_json
            return ToolResult(ok=True, result=extract_json(raw))
        except Exception as exc:
            return ToolResult(ok=False, error=f"extraction failed: {exc}")
