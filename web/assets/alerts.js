/* Jedna celostátní karta; aktuálnost dat je nezávislá na běhu ALADINu. */
(() => {
  'use strict';
  const card = document.getElementById('alerts-card');
  const home = document.getElementById('home');
  if (!card || !home) return;
  const content = document.getElementById('alerts-content');
  const badge = document.getElementById('alerts-badge');
  const checked = document.getElementById('alerts-checked');
  const MAX_AGE = 2 * 60 * 60 * 1000;
  const labels = ['Předběžné varování', 'Nízký stupeň nebezpečí', 'Vysoký stupeň nebezpečí', 'Extrémní stupeň nebezpečí'];
  const date = new Intl.DateTimeFormat('cs-CZ', {timeZone: 'Europe/Prague', day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit'});
  let snapshot = null, pending = false, loadedAt = 0;
  function node(tag, className, text) {
    const el = document.createElement(tag);
    el.className = className;
    el.textContent = text;
    return el;
  }
  function message(title, note) {
    content.replaceChildren(node('p', 'alerts-summary', title), node('p', 'alerts-note', note));
  }
  function unavailable() {
    card.dataset.state = 'unavailable';
    delete card.dataset.level;
    badge.textContent = 'Nelze ověřit';
    message('Výstrahy se nepodařilo ověřit.', 'Aktuální situaci si prosím ověřte přímo na webu ČHMÚ.');
    checked.textContent = 'Aktualizace není dostupná';
  }
  function validate(data) {
    if (data?.version !== 1 || data.ok !== true || !Array.isArray(data.warnings)
        || !Number.isFinite(Date.parse(data.retrieved_at)) || !Number.isFinite(Date.parse(data.issued_at))) return false;
    return data.warnings.every(w => typeof w.event === 'string' && w.event.length
      && Number.isInteger(w.level) && w.level >= 0 && w.level <= 3
      && ['Observed', 'Likely', 'Possible', 'Unknown'].includes(w.certainty)
      && Array.isArray(w.areas) && w.areas.length && w.areas.every(a => typeof a === 'string')
      && Number.isFinite(Date.parse(w.onset)) && (w.expires === null || Number.isFinite(Date.parse(w.expires))));
  }
  function render() {
    if (!snapshot) return;
    const now = Date.now(), age = now - Date.parse(snapshot.retrieved_at);
    if (age < -300000 || age >= MAX_AGE) { unavailable(); return; }
    const warnings = snapshot.warnings.filter(w => w.expires === null || Date.parse(w.expires) > now);
    checked.textContent = `Ověřeno ${date.format(new Date(snapshot.retrieved_at))}`;
    if (!warnings.length) {
      card.dataset.state = 'clear';
      delete card.dataset.level;
      badge.textContent = 'Bez výstrah';
      message('Aktuálně bez výstrah.', 'ČHMÚ nyní v celostátním přehledu neuvádí žádnou platnou ani nadcházející výstrahu.');
      return;
    }
    card.dataset.state = 'warning';
    card.dataset.level = String(Math.max(...warnings.map(w => w.level)));
    badge.textContent = 'Vydané výstrahy';
    const events = [...new Set(warnings.map(w => w.event))];
    message(events.length === 1 ? events[0] : 'Počasí vyžaduje pozornost.', 'Platné a nadcházející výstrahy pro území ČR. Podrobnou územní platnost a doporučení najdete u ČHMÚ.');
    const list = node('ul', 'alerts-list', '');
    for (const warning of warnings.slice(0, 3)) {
      const item = node('li', 'alerts-item', '');
      item.dataset.level = String(warning.level);
      const head = node('div', 'alerts-item-head', '');
      head.append(node('h3', '', warning.event), node('span', 'alerts-level', labels[warning.level]));
      const areas = warning.areas.slice(0, 3).join(' · ');
      const tail = warning.areas.length > 3 ? ` · a další (${warning.areas.length - 3})` : '';
      const validity = `${Date.parse(warning.onset) > now ? 'Od' : 'Platnost od'} ${date.format(new Date(warning.onset))}`
        + (warning.expires ? ` do ${date.format(new Date(warning.expires))}` : ' · do odvolání')
        + (warning.certainty === 'Possible' ? ' · možný výskyt' : warning.certainty === 'Observed' ? ' · pozorovaný jev' : '');
      item.append(head, node('p', 'alerts-areas', areas + tail), node('p', 'alerts-validity', validity));
      list.append(item);
    }
    content.append(list);
    if (warnings.length > 3) content.append(node('p', 'alerts-note', `Další výstražné informace (${warnings.length - 3}) najdete na webu ČHMÚ.`));
  }
  async function refresh() {
    if (pending || home.hidden || document.hidden) return;
    render();
    if (Date.now() - loadedAt < 5 * 60 * 1000) return;
    pending = true;
    card.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch('/data/alerts.json', {cache: 'no-cache', signal: AbortSignal.timeout(15000)});
      if (!response.ok) throw new Error('Výstrahy nejsou dostupné');
      const data = await response.json();
      if (!validate(data)) throw new Error('Neplatný přehled výstrah');
      snapshot = data;
      loadedAt = Date.now();
      render();
    } catch (error) {
      snapshot = null;
      unavailable();
    } finally {
      pending = false;
      card.removeAttribute('aria-busy');
    }
  }
  new MutationObserver(() => refresh()).observe(home, {attributes: true, attributeFilter: ['hidden']});
  document.addEventListener('visibilitychange', refresh);
  setInterval(refresh, 60000);
  refresh();
})();
