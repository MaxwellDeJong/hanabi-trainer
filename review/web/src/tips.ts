// Hover tooltips. Any element with a tip (see `tip`) shows it on hover or keyboard focus; one document-level
// listener handles them all, so tips survive the lobby and Label mode re-rendering their controls.

import { el } from "./table";

let tooltip: HTMLElement | null = null;
let anchor: HTMLElement | null = null;

/** Gives an element a hover tooltip. Returns the element, to use inline. */
export function tip<T extends HTMLElement>(target: T, text: string): T {
  target.dataset["tip"] = text;
  return target;
}

/** A small "?" that only carries a tooltip: the explanation that would otherwise be a paragraph. */
export function infoIcon(text: string): HTMLElement {
  const icon = tip(el("span", "info", "?"), text);
  icon.tabIndex = 0;
  icon.setAttribute("aria-label", text);
  return icon;
}

/** Shows `text` next to `target` (above it if there's room, else below). `wrap` for prose, else as is. */
export function showTooltip(target: HTMLElement, text: string, wrap = false): void {
  hideTooltip();
  anchor = target;
  tooltip = el("div", wrap ? "tooltip prose" : "tooltip", text);
  document.body.append(tooltip);
  const r = target.getBoundingClientRect();
  const t = tooltip.getBoundingClientRect();
  const left = Math.min(window.innerWidth - t.width - 8, Math.max(8, r.left + r.width / 2 - t.width / 2));
  const top = r.top - t.height - 8 > 0 ? r.top - t.height - 8 : r.bottom + 8;
  Object.assign(tooltip.style, { left: `${left}px`, top: `${top}px` });
}

export function hideTooltip(): void {
  tooltip?.remove();
  tooltip = null;
  anchor = null;
}

function tipTarget(e: Event): HTMLElement | null {
  return e.target instanceof Element ? e.target.closest<HTMLElement>("[data-tip]") : null;
}

function enter(e: Event): void {
  const target = tipTarget(e);
  if (target === anchor) return;
  if (target === null) {
    if (anchor?.dataset["tip"] !== undefined) hideTooltip();
    return;
  }
  showTooltip(target, target.dataset["tip"]!, true);
}

function leave(e: Event): void {
  const target = tipTarget(e);
  if (target === null || target !== anchor) return;
  // Still inside the same tipped element (moving between its children): keep it.
  const next = (e as MouseEvent | FocusEvent).relatedTarget;
  if (next instanceof Node && target.contains(next)) return;
  hideTooltip();
}

document.addEventListener("mouseover", enter);
document.addEventListener("mouseout", leave);
document.addEventListener("focusin", enter);
document.addEventListener("focusout", leave);
// A click usually re-renders or navigates: don't leave a tip behind for an element that's gone.
document.addEventListener("mousedown", () => { if (anchor?.dataset["tip"] !== undefined) hideTooltip(); }, true);

/** A table's heading row; headings with an entry in `tips` explain their column on hover. */
export function headRow(headers: string[], tips: Record<string, string>): HTMLTableRowElement {
  const row = el("tr");
  for (const h of headers) {
    const th = el("th", "", h);
    const help = tips[h];
    if (help !== undefined) tip(th, help).classList.add("has-tip");
    row.append(th);
  }
  return row;
}

/** A heading with a "?" whose tooltip explains the section. */
export function heading(tag: "h1" | "h2", text: string, help: string): HTMLElement {
  const h = el(tag, "with-info", text);
  h.append(infoIcon(help));
  return h;
}
