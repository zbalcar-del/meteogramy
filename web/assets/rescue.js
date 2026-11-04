/* Užitečné zprávy Horské služby (web/data/rescue.json z meteogram/rescue.py); bez zpráv se karta nezobrazí. */
(() => {
  'use strict';
  const card = document.getElementById('hs-card');
  const home = document.getElementById('home');
  const list = document.getElementById('hs-list');
  if (!card || !home || !list) return;
  const date = new Intl.DateTimeFormat('cs-CZ', {timeZone: 'Europe/Prague', day: 'numeric', month: 'numeric', year: 'numeric'});
  const checked = document.getElementById('hs-checked');
  const stamp = new Intl.DateTimeFormat('cs-CZ', {timeZone: 'Europe/Prague', day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit'});
  const MAX_AGE = 30 * 24 * 3600 * 1000;
  const TAGS = [[/pohřešov|pátrá|hledá|hledat/i, 'Pátrání']];
  let loadedAt = 0, pending = false;
  const node = (tag, className, text) => {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text) el.textContent = text;
    return el;
  };
  const valid = data => data?.version === 1 && data.ok === true && Array.isArray(data.items)
    && data.items.every(i => typeof i.title === 'string' && i.title && typeof i.summary === 'string'
      && typeof i.url === 'string' && i.url.startsWith('https://www.horskasluzba.cz/') && Number.isFinite(Date.parse(i.published)));
  function render(items, retrieved) {
    // stejně jako u výstrah ČHMÚ: kdy jsme zprávy naposledy stáhli
    if (checked) checked.textContent = Number.isFinite(Date.parse(retrieved)) ? `Ověřeno ${stamp.format(new Date(retrieved))}` : '';
    const fresh = items.filter(i => Date.now() - Date.parse(i.published) < MAX_AGE);
    card.hidden = !fresh.length;
    list.replaceChildren(...fresh.map(i => {
      const li = node('li', 'alerts-item');
      const a = node('a');
      a.href = i.url; a.target = '_blank'; a.rel = 'noopener';
      a.append(node('h3', '', i.title));
      // zvláštní typ zprávy (pátrání) se ukazuje malým červeným štítkem vždy za datem
      const meta = node('p', 'alerts-validity hs-meta', date.format(new Date(i.published)));
      const tag = TAGS.find(([re]) => re.test(i.title));
      if (tag) meta.append(node('span', 'hs-tag', tag[1]));
      li.append(a, node('p', 'alerts-areas', i.summary), meta);
      return li;
    }));
  }
  async function refresh() {
    if (pending || home.hidden || document.hidden || Date.now() - loadedAt < 10 * 60 * 1000) return;
    pending = true;
    try {
      const response = await fetch('/data/rescue.json', {cache: 'no-cache', signal: AbortSignal.timeout(15000)});
      if (!response.ok) throw new Error('Zprávy nejsou dostupné');
      const data = await response.json();
      if (!valid(data)) throw new Error('Neplatná data');
      loadedAt = Date.now();
      render(data.items, data.retrieved_at);
    } catch (error) {
      card.hidden = true;
    } finally {
      pending = false;
    }
  }
  new MutationObserver(() => refresh()).observe(home, {attributes: true, attributeFilter: ['hidden']});
  document.addEventListener('visibilitychange', refresh);
  setInterval(refresh, 60000);
  refresh();
})();
