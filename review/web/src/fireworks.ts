// The reward for submitting a seat: fireworks (花火, hanabi) over the table in the game's suit colours,
// with a thank-you line. Resolves when the show ends, or early on a click or key press.

import { el } from "./table";

const DURATION_MS = 4500;
/** No new rockets in the last part, so the show fades out rather than stopping mid-burst. */
const LAUNCH_UNTIL_MS = 3000;
const GRAVITY = 0.00018; // px per ms²

interface Rocket {
  x: number;
  y: number;
  vx: number;
  vy: number;
  burstY: number;
  color: string;
}

interface Spark {
  x: number;
  y: number;
  vx: number;
  vy: number;
  born: number;
  life: number;
  color: string;
}

export function celebrate(title: string, subtitle: string, colors: string[]): Promise<void> {
  const layer = el("div", "fireworks");
  const canvas = el("canvas");
  const text = el("div", "fireworks-text");
  text.append(el("div", "fireworks-title", title), el("div", "fireworks-sub", subtitle));
  layer.append(canvas, text);
  document.body.append(layer);

  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const palette = colors.length > 0 ? colors : ["#ff5b5b", "#ffe14d", "#5bdc6a", "#5b9bff", "#c77dff"];
  const pick = (): string => palette[Math.floor(Math.random() * palette.length)]!;
  const ctx = canvas.getContext("2d")!;
  const dpr = window.devicePixelRatio || 1;
  let w = 0;
  let h = 0;
  const size = (): void => {
    w = window.innerWidth;
    h = window.innerHeight;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };
  size();

  const rockets: Rocket[] = [];
  const sparks: Spark[] = [];
  const start = performance.now();
  let last = start;
  let nextLaunch = start;

  const launch = (now: number): void => {
    const x = w * (0.15 + 0.7 * Math.random());
    const burstY = h * (0.15 + 0.3 * Math.random());
    // Fast enough to reach burstY just as gravity slows it.
    const vy = -Math.sqrt(2 * GRAVITY * (h - burstY)) * 1.05;
    rockets.push({ x, y: h, vx: (Math.random() - 0.5) * 0.08, vy, burstY, color: pick() });
    nextLaunch = now + 180 + Math.random() * 320;
  };

  const burst = (r: Rocket, now: number): void => {
    const n = 60 + Math.floor(Math.random() * 40);
    const speed = 0.18 + Math.random() * 0.12;
    const second = Math.random() < 0.35 ? pick() : r.color;
    for (let i = 0; i < n; i++) {
      const a = (2 * Math.PI * i) / n + Math.random() * 0.1;
      const s = speed * (0.6 + 0.4 * Math.random());
      sparks.push({ x: r.x, y: r.y, vx: Math.cos(a) * s, vy: Math.sin(a) * s, born: now, life: 900 + Math.random() * 700,
        color: i % 2 === 0 ? r.color : second });
    }
  };

  return new Promise((resolve) => {
    let frame = 0;
    let timer = 0;
    const finish = (): void => {
      cancelAnimationFrame(frame);
      window.clearTimeout(timer);
      window.removeEventListener("keydown", skip, true);
      window.removeEventListener("resize", size);
      layer.remove();
      resolve();
    };
    // Any key or click ends the show early. Keys stop here, so they don't also act on the game.
    const skip = (e: Event): void => {
      e.preventDefault();
      e.stopPropagation();
      finish();
    };
    window.addEventListener("keydown", skip, true);
    window.addEventListener("resize", size);
    layer.addEventListener("pointerdown", skip);

    if (still) {
      // Reduced motion: just the message, briefly.
      layer.classList.add("still");
      timer = window.setTimeout(finish, 2500);
      return;
    }

    const tick = (now: number): void => {
      const dt = Math.min(50, now - last);
      last = now;
      const t = now - start;
      if (t < LAUNCH_UNTIL_MS && now >= nextLaunch) launch(now);

      // Fade the previous frame for trails.
      ctx.globalCompositeOperation = "destination-out";
      ctx.fillStyle = "rgba(0, 0, 0, 0.22)";
      ctx.fillRect(0, 0, w, h);
      ctx.globalCompositeOperation = "lighter";

      for (let i = rockets.length - 1; i >= 0; i--) {
        const r = rockets[i]!;
        r.vy += GRAVITY * dt;
        r.x += r.vx * dt;
        r.y += r.vy * dt;
        ctx.fillStyle = "#fff6d8";
        ctx.beginPath();
        ctx.arc(r.x, r.y, 2.2, 0, 2 * Math.PI);
        ctx.fill();
        if (r.y <= r.burstY || r.vy >= 0) {
          burst(r, now);
          rockets.splice(i, 1);
        }
      }
      for (let i = sparks.length - 1; i >= 0; i--) {
        const s = sparks[i]!;
        const age = (now - s.born) / s.life;
        if (age >= 1) {
          sparks.splice(i, 1);
          continue;
        }
        s.vx *= 0.985;
        s.vy = s.vy * 0.985 + GRAVITY * 0.6 * dt;
        s.x += s.vx * dt;
        s.y += s.vy * dt;
        ctx.globalAlpha = 1 - age * age;
        ctx.fillStyle = s.color;
        ctx.beginPath();
        ctx.arc(s.x, s.y, 2.4 * (1 - age * 0.6), 0, 2 * Math.PI);
        ctx.fill();
      }
      ctx.globalAlpha = 1;

      if (t >= DURATION_MS) finish();
      else frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
  });
}
