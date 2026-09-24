// Modal panels (full log, checks, JSON), shared by Inspect and Label mode.

import { el } from "./table";

let overlay: HTMLElement | null = null;

export function openOverlay(title: string, body: HTMLElement): void {
  closeOverlay();
  overlay = el("div", "overlay");
  const panel = el("div", "panel");
  const close = el("button", "close", "×");
  close.addEventListener("click", closeOverlay);
  panel.append(close, el("h2", "", title), body);
  overlay.append(panel);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) closeOverlay(); });
  document.body.append(overlay);
}

export function closeOverlay(): void {
  overlay?.remove();
  overlay = null;
}

export function overlayOpen(): boolean {
  return overlay !== null;
}
