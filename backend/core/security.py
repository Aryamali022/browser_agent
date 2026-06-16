"""Guardrails evaluated on every planned action before it executes.

Decision levels:
  allow   - run the tool
  confirm - pause and ask the user in the side panel first
  deny    - refuse and tell the planner why

Hard rules (spec): never enter passwords, never complete a payment or
purchase, never drive browser-internal pages. Other sensitive actions
(checkout flows, card/OTP fields, destructive buttons) require explicit
user confirmation.
"""

import re
from dataclasses import dataclass
from typing import Optional

from schemas.schemas import PageSnapshot, PageElement, ToolCallRequest

ALLOW = "allow"
CONFIRM = "confirm"
DENY = "deny"

_BLOCKED_URL = re.compile(r"^\s*(chrome|edge|brave|opera|vivaldi|about|devtools|chrome-extension)://", re.I)
_PASSWORD_FIELD = re.compile(r"(password|passwd|pwd)", re.I)
_PAYMENT_FIELD = re.compile(r"(cvv|cvc|card[\s_-]?number|credit[\s_-]?card|expiry|expiration|otp\b|\bpin\b)", re.I)
_PURCHASE_TEXT = re.compile(r"\b(pay now|place (your )?order|buy now|purchase|confirm (order|payment)|complete (order|purchase|payment))\b", re.I)
_CONFIRM_TEXT = re.compile(r"\b(checkout|delete|remove|sign out|log out|unsubscribe|deactivate)\b", re.I)


@dataclass
class Decision:
    action: str          # allow | confirm | deny
    reason: str = ""


def _find_element(snapshot: Optional[PageSnapshot], selector: str) -> Optional[PageElement]:
    if snapshot is None:
        return None
    for el in snapshot.elements:
        if el.selector == selector:
            return el
    return None


def _element_descriptor(el: PageElement) -> str:
    """All the text we can use to judge what an element is."""
    parts = [el.text or "", el.selector or ""]
    parts.extend(f"{k}={v}" for k, v in el.attributes.items())
    return " ".join(parts)


class SecurityGuard:
    def check(self, action: ToolCallRequest,
              snapshot: Optional[PageSnapshot] = None) -> Decision:
        tool = action.tool
        args = action.args

        if tool in ("open_url", "new_tab"):
            url = str(args.get("url", "") or "")
            if _BLOCKED_URL.match(url):
                return Decision(DENY, "Browser-internal pages are off limits.")
            return Decision(ALLOW)

        if tool in ("type_text", "clear_input"):
            selector = str(args.get("selector", "") or "")
            el = _find_element(snapshot, selector)
            descriptor = _element_descriptor(el) if el else selector
            if el is not None and el.attributes.get("type", "").lower() == "password":
                return Decision(DENY, "I never enter or modify passwords. Please fill password fields yourself.")
            if _PASSWORD_FIELD.search(descriptor):
                return Decision(DENY, "I never enter or modify passwords. Please fill password fields yourself.")
            if tool == "type_text" and _PAYMENT_FIELD.search(descriptor):
                return Decision(CONFIRM, "This looks like a payment or one-time-code field.")
            return Decision(ALLOW)

        if tool in ("click", "double_click"):
            selector = str(args.get("selector", "") or "")
            el = _find_element(snapshot, selector)
            descriptor = _element_descriptor(el) if el else selector
            if _PURCHASE_TEXT.search(descriptor):
                return Decision(DENY, "I never complete payments or purchases. Please finish this step yourself.")
            if _CONFIRM_TEXT.search(descriptor):
                return Decision(CONFIRM, f"This click looks sensitive: \"{(el.text if el else selector)[:80]}\".")
            return Decision(ALLOW)

        return Decision(ALLOW)
