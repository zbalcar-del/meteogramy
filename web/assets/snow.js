/* Sněhové zpravodajství: stejný serverový fragment jako ve statické stránce. */
(() => {
  'use strict';
  const root = document.getElementById('snow');
  if (!root) return;
  const body = document.getElementById('snow-body');
  const status = document.getElementById('snow-fetch-status');
  const search = document.getElementById('snow-search');
  const clearSearch = document.getElementById('snow-search-clear');
  const range = document.getElementById('snow-range');
  const sort = document.getElementById('snow-sort');
  const snowOnly = document.getElementById('snow-only');
  const fold = text => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('cs');
  const value = (row, key) => row.dataset[key] === '' ? null : Number(row.dataset[key]);
  let loadedAt = 0, pending = null;

  // Stejné ovladače a seznamy jako výběr běhu modelu.
  const dropdowns = [range, sort].map(select => {
    const wrap = document.createElement('div');
    wrap.className = 'dd snow-dd';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'dd-btn';
    button.setAttribute('aria-haspopup', 'listbox');
    button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-label', select.getAttribute('aria-label'));
    button.innerHTML = '<span class="lab"></span><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M8 9l4-4 4 4M8 15l4 4 4-4"/></svg>';
    const list = document.createElement('ul');
    list.className = 'dd-list';
    list.id = select.id + '-options';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', select.getAttribute('aria-label'));
    list.tabIndex = -1;
    list.hidden = true;
    button.setAttribute('aria-controls', list.id);
    select.before(wrap);
    wrap.append(button, list, select);
    select.hidden = true;
    let active = 0;
    const close = focus => {
      list.hidden = true;
      button.setAttribute('aria-expanded', 'false');
      if (focus) button.focus();
    };
    const highlight = index => {
      active = Math.max(0, Math.min(select.options.length - 1, index));
      [...list.children].forEach((item, i) => item.classList.toggle('act', i === active));
      if (list.children[active]) {
        list.setAttribute('aria-activedescendant', list.children[active].id);
        list.children[active].scrollIntoView({block: 'nearest'});
      }
    };
    const sync = () => {
      button.querySelector('.lab').textContent = select.selectedOptions[0]?.textContent || '';
      list.replaceChildren(...[...select.options].map((option, i) => {
        const item = document.createElement('li');
        item.id = list.id + '-' + i;
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', String(option.selected));
        item.textContent = option.textContent;
        item.addEventListener('click', () => pick(i));
        item.addEventListener('mousemove', () => highlight(i));
        return item;
      }));
    };
    const pick = index => {
      select.selectedIndex = index;
      sync(); close(true);
      select.dispatchEvent(new Event('change', {bubbles: true}));
    };
    const open = () => {
      dropdowns.forEach(dropdown => dropdown.close(false));
      sync(); list.hidden = false;
      button.setAttribute('aria-expanded', 'true');
      list.focus(); highlight(select.selectedIndex);
    };
    button.addEventListener('click', () => list.hidden ? open() : close(false));
    button.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); open(); }
    });
    list.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault(); highlight(active + (event.key === 'ArrowDown' ? 1 : -1));
      } else if (event.key === 'Home' || event.key === 'End') {
        event.preventDefault(); highlight(event.key === 'Home' ? 0 : select.options.length - 1);
      } else if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault(); pick(active);
      } else if (event.key === 'Escape') { event.preventDefault(); close(true); }
      else if (event.key === 'Tab') close(false);
      else if (event.key.length === 1) {
        const index = [...select.options].findIndex(option => fold(option.textContent).startsWith(fold(event.key)));
        if (index >= 0) highlight(index);
      }
    });
    document.addEventListener('click', event => { if (!wrap.contains(event.target)) close(false); });
    sync();
    return {sync, close};
  });


  function filter() {
    clearSearch.hidden = search.value.length === 0;
    const query = fold(search.value.trim());
    const only = snowOnly.getAttribute('aria-pressed') === 'true';
    let total = 0;
    for (const group of body.querySelectorAll('.snow-group')) {
      const rows = [...group.querySelectorAll('.snow-row')];
      const key = sort.value;
      rows.sort((a, b) => {
        if (key === 'name') return a.dataset.name.localeCompare(b.dataset.name, 'cs');
        const av = value(a, key), bv = value(b, key);
        return (av === null) - (bv === null) || (bv ?? 0) - (av ?? 0)
          || a.dataset.name.localeCompare(b.dataset.name, 'cs');
      });
      let visible = 0;
      for (const row of rows) {
        const depth = value(row, 'depth');
        row.hidden = (!!range.value && range.value !== group.dataset.range)
          || (!!query && !fold(row.dataset.name + ' ' + group.dataset.range).includes(query))
          || (only && (depth === null || depth <= 0));
        if (!row.hidden) visible++;
        group.querySelector('.snow-rows').appendChild(row);
      }
      group.hidden = visible === 0;
      total += visible;
    }
    const count = document.getElementById('snow-count');
    if (count) count.textContent = `${total} ${total === 1 ? 'stanice' : total < 5 && total > 0 ? 'stanice' : 'stanic'}`;
    const empty = document.getElementById('snow-no-results');
    if (empty) empty.hidden = total !== 0;
  }

  function enhance() {
    const selected = range.value;
    range.replaceChildren(new Option('Všechna pohoří', ''));
    for (const group of body.querySelectorAll('.snow-group')) {
      range.add(new Option(group.dataset.range, group.dataset.range));
    }
    if ([...range.options].some(option => option.value === selected)) range.value = selected;
    dropdowns.forEach(dropdown => dropdown.sync());
    filter();
  }

  function skeleton() {
    body.innerHTML = '<div class="snow-highlights">' + Array.from({length: 3}, () =>
      '<div class="snow-highlight skstack"><i class="sk" style="height:14px;width:65%"></i><i class="sk" style="height:56px;width:45%"></i><i class="sk" style="height:14px;width:75%"></i></div>').join('')
      + '</div><div class="snow-group skstack" style="padding:24px">' + Array.from({length: 6}, () => '<i class="sk" style="height:54px"></i>').join('') + '</div>';
  }

  function open(force = false) {
    if (pending) return pending;
    if (!force && loadedAt && Date.now() - loadedAt < 300000) { filter(); return Promise.resolve(); }
    const hasMeasurements = !!body.querySelector('.snow-row');
    if (!body.children.length) skeleton();
    body.setAttribute('aria-busy', 'true');
    status.hidden = true;
    pending = (async () => {
      try {
        const response = await fetch('/data/snow-fragment.html', {signal: AbortSignal.timeout(20000), cache: 'no-cache'});
        if (!response.ok) throw new Error('Sněhová data nejsou dostupná.');
        const html = await response.text();
        const fragment = document.createElement('template');
        fragment.innerHTML = html;
        if (!fragment.content.querySelector('.snow-group, .snow-empty')) throw new Error('Neplatná sněhová data.');
        body.replaceChildren(fragment.content);
        loadedAt = Date.now();
        enhance();
      } catch (error) {
        if (!hasMeasurements) {
          body.innerHTML = '<div class="snow-empty"><h2>Měření se nepodařilo načíst</h2><p>Zkuste načtení znovu. Chybějící údaje neznamenají nulovou výšku sněhu.</p></div>';
        }
        status.innerHTML = (hasMeasurements ? 'Aktualizaci se nepodařilo ověřit. Zobrazená měření zůstávají dostupná. ' : 'Měření se nepodařilo načíst. ')
          + '<button type="button" class="ckbtn" data-snow-retry>Načíst znovu</button>';
        status.hidden = false;
      } finally {
        body.setAttribute('aria-busy', 'false');
        pending = null;
      }
    })();
    return pending;
  }

  search.addEventListener('input', filter);
  clearSearch.addEventListener('click', () => {
    search.value = '';
    filter();
    search.focus({preventScroll: true});
  });
  range.addEventListener('change', filter);
  sort.addEventListener('change', filter);
  snowOnly.addEventListener('click', () => {
    snowOnly.setAttribute('aria-pressed', String(snowOnly.getAttribute('aria-pressed') !== 'true'));
    filter();
  });
  root.addEventListener('click', event => { if (event.target.closest('[data-snow-retry]')) open(true); });
  if (body.querySelector('.snow-group')) enhance();
  window.MeteogramSnow = {open};
})();
