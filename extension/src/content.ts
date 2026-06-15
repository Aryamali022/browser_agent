// Content script: observes the page and executes in-page tools.
// The background worker forwards `execute_tool` requests here and relays the
// response to the backend. Every handler returns {ok, result|error}.

type ToolResponse = { ok: boolean; result?: unknown; error?: string };

const INTERACTIVE_SELECTOR =
  'a[href], button, input, textarea, select, summary, ' +
  '[role="button"], [role="link"], [role="tab"], [role="searchbox"], ' +
  '[role="combobox"], [contenteditable="true"], [onclick]';

function isVisible(el: Element): boolean {
  const rect = el.getBoundingClientRect();
  if (rect.width < 2 || rect.height < 2) return false;
  const style = window.getComputedStyle(el);
  return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
}

// Build a selector that uniquely identifies the element, preferring stable
// attributes over positional paths.
function buildSelector(el: Element): string {
  if (el.id) {
    const sel = `#${CSS.escape(el.id)}`;
    if (document.querySelectorAll(sel).length === 1) return sel;
  }
  for (const attr of ['name', 'aria-label', 'placeholder', 'data-testid']) {
    const value = el.getAttribute(attr);
    if (value) {
      const sel = `${el.tagName.toLowerCase()}[${attr}="${CSS.escape(value)}"]`;
      try {
        if (document.querySelectorAll(sel).length === 1) return sel;
      } catch { /* invalid selector — fall through */ }
    }
  }
  // Positional fallback: tag path with nth-of-type, up to 5 ancestors.
  const path: string[] = [];
  let node: Element | null = el;
  while (node && node !== document.body && path.length < 5) {
    let part = node.tagName.toLowerCase();
    const parent: Element | null = node.parentElement;
    if (parent) {
      const siblings = Array.from(parent.children).filter(c => c.tagName === node!.tagName);
      if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
    }
    path.unshift(part);
    if (document.querySelectorAll(path.join(' > ')).length === 1) break;
    node = parent;
  }
  return path.join(' > ');
}

function elementText(el: Element): string {
  const input = el as HTMLInputElement;
  const text =
    (el as HTMLElement).innerText?.trim() ||
    input.value ||
    el.getAttribute('aria-label') ||
    el.getAttribute('placeholder') ||
    el.getAttribute('title') ||
    '';
  return text.replace(/\s+/g, ' ').slice(0, 120);
}

function collectElements(limit = 60) {
  const seen = new Set<Element>();
  const out: Array<Record<string, unknown>> = [];
  for (const el of Array.from(document.querySelectorAll(INTERACTIVE_SELECTOR))) {
    if (out.length >= limit) break;
    if (seen.has(el) || !isVisible(el)) continue;
    seen.add(el);
    const attributes: Record<string, string> = {};
    for (const attr of ['type', 'name', 'href', 'placeholder', 'aria-label', 'role', 'title']) {
      const value = el.getAttribute(attr);
      if (value) attributes[attr] = value.slice(0, 200);
    }
    // Never expose what is typed in password fields.
    if ((el as HTMLInputElement).type === 'password') attributes['type'] = 'password';
    out.push({
      index: out.length,
      tag: el.tagName.toLowerCase(),
      text: attributes['type'] === 'password' ? '(password field)' : elementText(el),
      selector: buildSelector(el),
      attributes,
    });
  }
  return out;
}

function getSnapshot() {
  const text = (document.body?.innerText || '').replace(/\n{3,}/g, '\n\n').slice(0, 5000);
  return {
    url: window.location.href,
    title: document.title,
    visible_text: text,
    elements: collectElements(),
    timestamp: new Date().toISOString(),
  };
}

function find(selector: string): HTMLElement | null {
  try {
    return document.querySelector(selector) as HTMLElement | null;
  } catch {
    return null;
  }
}

function requireElement(selector: string): HTMLElement {
  const el = find(selector);
  if (!el) throw new Error(`Element not found: ${selector}. Take a fresh snapshot.`);
  return el;
}

// React/Vue-controlled inputs ignore plain .value writes; go through the
// native setter so framework state updates too.
function setNativeValue(el: HTMLElement, value: string) {
  const proto = el instanceof HTMLTextAreaElement
    ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
  if (setter && (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement)) {
    setter.call(el, value);
  } else if (el.isContentEditable) {
    el.textContent = value;
  } else {
    (el as HTMLInputElement).value = value;
  }
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

function fireMouse(el: HTMLElement, types: string[]) {
  const rect = el.getBoundingClientRect();
  const opts = {
    bubbles: true, cancelable: true, view: window,
    clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2,
  };
  for (const type of types) {
    if (type.startsWith('pointer')) el.dispatchEvent(new PointerEvent(type, opts));
    else el.dispatchEvent(new MouseEvent(type, opts));
  }
}

// The vision fallback works in viewport fractions (0..1): the screenshot and
// the page share the same visible viewport, so fractions are DPI-independent.
function elementAtFraction(fx: number, fy: number): HTMLElement {
  const cx = Math.max(0, Math.min(window.innerWidth - 1, fx * window.innerWidth));
  const cy = Math.max(0, Math.min(window.innerHeight - 1, fy * window.innerHeight));
  const el = document.elementFromPoint(cx, cy) as HTMLElement | null;
  if (!el) throw new Error(`No element at viewport point (${fx.toFixed(2)}, ${fy.toFixed(2)})`);
  return el;
}

const handlers: Record<string, (args: Record<string, string>) => unknown> = {
  get_page_snapshot: () => getSnapshot(),

  get_page_text: () => (document.body?.innerText || '').slice(0, 15000),

  get_buttons: () =>
    Array.from(document.querySelectorAll('button, [role="button"], input[type="submit"]'))
      .filter(isVisible).slice(0, 80)
      .map(el => ({ text: elementText(el), selector: buildSelector(el) })),

  get_links: () =>
    Array.from(document.querySelectorAll('a[href]'))
      .filter(isVisible).slice(0, 120)
      .map(el => ({
        text: elementText(el),
        href: (el as HTMLAnchorElement).href,
        selector: buildSelector(el),
      })),

  click: ({ selector }) => {
    const el = requireElement(selector);
    el.scrollIntoView({ block: 'center' });
    fireMouse(el, ['pointerdown', 'mousedown', 'pointerup', 'mouseup']);
    el.click();
    return { success: true, clicked: elementText(el) };
  },

  double_click: ({ selector }) => {
    const el = requireElement(selector);
    el.scrollIntoView({ block: 'center' });
    fireMouse(el, ['dblclick']);
    return { success: true };
  },

  right_click: ({ selector }) => {
    const el = requireElement(selector);
    fireMouse(el, ['contextmenu']);
    return { success: true };
  },

  hover: ({ selector }) => {
    const el = requireElement(selector);
    el.scrollIntoView({ block: 'center' });
    fireMouse(el, ['pointerover', 'mouseover', 'mousemove']);
    return { success: true };
  },

  type_text: ({ selector, text }) => {
    const el = requireElement(selector);
    if ((el as HTMLInputElement).type === 'password') {
      throw new Error('Refused: I never type into password fields.');
    }
    el.focus();
    setNativeValue(el, text);
    return { success: true, typed: text };
  },

  clear_input: ({ selector }) => {
    const el = requireElement(selector);
    if ((el as HTMLInputElement).type === 'password') {
      throw new Error('Refused: I never modify password fields.');
    }
    el.focus();
    setNativeValue(el, '');
    return { success: true };
  },

  press_key: ({ key, selector }) => {
    const target = selector ? requireElement(selector) : (document.activeElement as HTMLElement) || document.body;
    target.focus?.();
    const opts = { key, code: key === 'Enter' ? 'Enter' : key, bubbles: true, cancelable: true };
    target.dispatchEvent(new KeyboardEvent('keydown', opts));
    target.dispatchEvent(new KeyboardEvent('keypress', opts));
    target.dispatchEvent(new KeyboardEvent('keyup', opts));
    const form = (target as HTMLInputElement).form;
    if (key === 'Enter' && form) form.requestSubmit();
    return { success: true, key };
  },

  scroll_down: () => {
    window.scrollBy({ top: window.innerHeight * 0.85, behavior: 'instant' as ScrollBehavior });
    return { success: true, scrollY: window.scrollY };
  },

  scroll_up: () => {
    window.scrollBy({ top: -window.innerHeight * 0.85, behavior: 'instant' as ScrollBehavior });
    return { success: true, scrollY: window.scrollY };
  },

  scroll_to: ({ selector }) => {
    requireElement(selector).scrollIntoView({ block: 'center' });
    return { success: true };
  },

  // --- vision fallback tools (called by the backend, not the planner) ---

  click_at_point: ({ fx, fy, kind }) => {
    const el = elementAtFraction(Number(fx), Number(fy));
    if (kind === 'double_click') {
      fireMouse(el, ['dblclick']);
    } else if (kind === 'hover') {
      fireMouse(el, ['pointerover', 'mouseover', 'mousemove']);
    } else {
      fireMouse(el, ['pointerdown', 'mousedown', 'pointerup', 'mouseup']);
      el.click();
    }
    return { success: true, target: elementText(el) };
  },

  type_at_point: ({ fx, fy, text }) => {
    let el = elementAtFraction(Number(fx), Number(fy));
    // The point may land on a label/wrapper; prefer an input inside it.
    if (!(el instanceof HTMLInputElement) && !(el instanceof HTMLTextAreaElement)
        && !el.isContentEditable) {
      const inner = el.querySelector('input, textarea, [contenteditable="true"]');
      if (inner) el = inner as HTMLElement;
    }
    if ((el as HTMLInputElement).type === 'password') {
      throw new Error('Refused: I never type into password fields.');
    }
    el.focus();
    setNativeValue(el, text);
    return { success: true, typed: text, target: elementText(el) };
  },
};

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === 'ping') {
    sendResponse({ ok: true, result: 'pong' });
    return;
  }
  if (message?.type !== 'execute_tool') return;
  const { tool, args } = message.payload as { tool: string; args: Record<string, string> };
  let response: ToolResponse;
  try {
    const handler = handlers[tool];
    if (!handler) throw new Error(`Tool '${tool}' is not available on this page.`);
    response = { ok: true, result: handler(args || {}) };
  } catch (e) {
    response = { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
  sendResponse(response);
});
