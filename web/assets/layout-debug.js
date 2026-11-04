/* Dočasná, pouze lokální diagnostika geometrie; žádná data se neodesílají. */
(() => {
  const header = document.querySelector(".mbar");
  if (!header) return;
  const panel = document.createElement("section");
  panel.setAttribute("aria-label", "Diagnostika hlavičky");
  panel.style.cssText = "position:fixed;z-index:100;bottom:calc(16px + env(safe-area-inset-bottom));left:12px;right:12px;max-width:480px;margin:auto;padding:16px;border-radius:18px;background:#fff;color:#15191f;box-shadow:0 4px 24px #14283c33;font:13px/1.5 -apple-system,sans-serif;max-height:45svh;overflow:auto";
  panel.innerHTML = '<div style="display:flex;align-items:center;justify-content:space-between;gap:12px"><b>Diagnostika hlavičky</b><button type="button" aria-label="Zavřít diagnostiku" style="border:0;border-radius:50%;width:44px;height:44px;background:#e9edf3;font-size:22px">×</button></div><p style="margin:4px 0 8px">Posuňte stránku, až se hlavička ořízne, a pořiďte screenshot i s těmito hodnotami.</p><pre style="margin:0;font:12px/1.5 ui-monospace,monospace;white-space:pre-wrap" aria-live="off"></pre>';
  document.body.appendChild(panel);
  const output = panel.querySelector("pre"), controller = new AbortController();
  let frame = 0, minOffset = Infinity, maxOffset = -Infinity;
  const number = value => Math.round(value * 10) / 10;
  const update = () => {
    frame = 0;
    const viewport = window.visualViewport, rect = header.getBoundingClientRect(), style = getComputedStyle(header);
    const offset = viewport?.offsetTop || 0;
    minOffset = Math.min(minOffset, offset); maxOffset = Math.max(maxOffset, offset);
    output.textContent = [
      `Safari: ${navigator.userAgent.match(/Version\/([\d.]+)/)?.[1] || "neuvedeno"}`,
      `Scroll stránky: ${number(window.scrollY)}`,
      `Visual viewport top: ${number(offset)}`,
      `Rozsah posunu: ${number(minOffset)} až ${number(maxOffset)}`,
      `Hlavička top / výška: ${number(rect.top)} / ${number(rect.height)}`,
      `Pozice hlavičky: ${style.position}`,
      `Hlavička padding top: ${style.paddingTop}`,
      `Okno / visual výška: ${innerHeight} / ${number(viewport?.height || innerHeight)}`,
      `Zoom: ${number(viewport?.scale || 1)} · DPR: ${devicePixelRatio}`
    ].join("\n");
  };
  const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
  const options = { passive: true, signal: controller.signal };
  window.addEventListener("scroll", schedule, options);
  window.addEventListener("resize", schedule, options);
  window.visualViewport?.addEventListener("scroll", schedule, options);
  window.visualViewport?.addEventListener("resize", schedule, options);
  panel.querySelector("button").addEventListener("click", () => {
    controller.abort();
    if (frame) cancelAnimationFrame(frame);
    panel.remove();
  }, { once: true });
  update();
})();
