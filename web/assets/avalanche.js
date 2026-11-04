/* Načtení stejného fragmentu jako v serverové stránce. */
(() => {
  'use strict';
  const root = document.getElementById('avalanche');
  if (!root) return;
  const body = document.getElementById('avalanche-body');
  const status = document.getElementById('avalanche-status');
  let pending = null, loadedAt = 0;
  const day = () => new Intl.DateTimeFormat('sv-SE', {timeZone: 'Europe/Prague', year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date());
  function expire() {
    const checked = Date.parse(body.querySelector('.ava-grid')?.dataset.avaChecked || '');
    const verified = Number.isFinite(checked) && Date.now() >= checked && Date.now() - checked < 7200000;
    for (const card of body.querySelectorAll('.ava-card[data-level]')) {
      if (!card.dataset.level || (verified && card.dataset.avaDay === day())) continue;
      card.dataset.level = '';
      card.querySelector('.ava-level').textContent = '—';
      card.querySelector('.ava-label').textContent = verified ? 'Starší bulletin' : 'Aktualizaci nelze ověřit';
      card.querySelector('.ava-rating-note').textContent = 'Aktuální situaci ověřte u Horské služby';
      const problems = card.querySelector('.ava-problems');
      if (problems) problems.hidden = true;
    }
    const info = body.querySelector('.ava-infobar');
    if (info) {
      const cards = [...body.querySelectorAll('.ava-card')];
      info.hidden = cards.length === 2 && cards.every(card => card.dataset.level);
    }
  }
  function open(force = false) {
    expire();
    if (pending) return pending;
    if (!force && loadedAt && Date.now() - loadedAt < 300000) return Promise.resolve();
    if (!body.children.length) {
      body.innerHTML = '<div class="ava-grid">' + Array.from({length: 2}, () => '<div class="snow-highlight skstack"><i class="sk" style="height:24px;width:55%"></i><i class="sk" style="height:88px;width:70%"></i><i class="sk" style="height:48px"></i></div>').join('') + '</div>';
    }
    body.setAttribute('aria-busy', 'true');
    status.hidden = true;
    pending = (async () => {
      try {
        const response = await fetch('/data/avalanche-fragment.html', {cache: 'no-cache', signal: AbortSignal.timeout(15000)});
        if (!response.ok) throw new Error('Nedostupný bulletin');
        const template = document.createElement('template');
        template.innerHTML = await response.text();
        if (template.content.querySelectorAll('.ava-card').length !== 2) throw new Error('Neplatný přehled');
        body.replaceChildren(template.content);
        loadedAt = Date.now();
      } catch (error) {
        // Při chybě neponecháváme číselný stupeň jako aktuálně ověřený.
        for (const card of body.querySelectorAll('.ava-card')) {
          card.dataset.level = '';
          card.querySelector('.ava-level').textContent = '—';
          card.querySelector('.ava-label').textContent = 'Aktualizaci nelze ověřit';
          card.querySelector('.ava-rating-note').textContent = 'Ověřte předpověď u Horské služby';
          const problems = card.querySelector('.ava-problems');
          if (problems) problems.hidden = true;
        }
        if (!body.querySelector('.ava-card')) body.replaceChildren();
        status.innerHTML = 'Předpověď se nepodařilo načíst. <button type="button" class="ckbtn" data-ava-retry>Načíst znovu</button>';
        status.hidden = false;
      } finally {
        expire();
        body.setAttribute('aria-busy', 'false');
        pending = null;
      }
    })();
    return pending;
  }
  root.addEventListener('click', event => { if (event.target.closest('[data-ava-retry]')) open(true); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden && !root.hidden) open(true); });
  setInterval(() => { if (!document.hidden && !root.hidden) open(true); }, 300000);
  window.MeteogramAvalanche = {open};
})();
