// A small tilt on tiles, steps and KPI cards, following the mouse. Only with a mouse and without reduced motion; touch and keyboard see nothing.
"use strict";
(() => {
  const mouse = matchMedia("(hover: hover) and (pointer: fine)"), calm = matchMedia("(prefers-reduced-motion: reduce)");
  if (!mouse.matches || calm.matches) return;
  const SEL = ".tile, .step3, .kpi, .card", MAX = 5;      // degrees at the edge of a card
  let current = null;

  function release(el) {
    el.style.setProperty("--rx", "0deg");
    el.style.setProperty("--ry", "0deg");
    el._settle = setTimeout(() => el.classList.remove("is-tilt"), 520);      // after it has turned back
  }

  document.addEventListener("pointermove", (e) => {
    if (e.pointerType && e.pointerType !== "mouse") return;
    const el = e.target.closest ? e.target.closest(SEL) : null;
    if (el !== current) {
      if (current) release(current);
      current = el;
    }
    if (!el) return;
    clearTimeout(el._settle);
    const r = el.getBoundingClientRect(), px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
    const max = r.width > 900 ? 1.6 : r.width > 520 ? 2.6 : MAX;     // the bigger the card, the less it turns
    el.classList.add("is-tilt");
    el.style.setProperty("--ry", `${((px - .5) * 2 * max).toFixed(2)}deg`);
    el.style.setProperty("--rx", `${(-(py - .5) * 2 * max).toFixed(2)}deg`);
    el.style.setProperty("--mx", `${(px * 100).toFixed(1)}%`);
    el.style.setProperty("--my", `${(py * 100).toFixed(1)}%`);
  }, { passive: true });

  // the glow and spheres behind the page move a little against the pointer
  let frame = 0;
  document.addEventListener("pointermove", (e) => {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      const root = document.documentElement.style;
      root.setProperty("--px", ((e.clientX / innerWidth - .5) * 2).toFixed(3));
      root.setProperty("--py", ((e.clientY / innerHeight - .5) * 2).toFixed(3));
    });
  }, { passive: true });

  document.documentElement.addEventListener("mouseleave", () => { if (current) { release(current); current = null; } });
})();
