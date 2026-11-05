/* Zimy na horách: jedna karta na pohoří, data z /data/historie/<pohoří>.json (meteogram/winters.py). */
(() => {
  'use strict';
  const DAYS = 243;                       // 1. 10. – 31. 5. bez přestupného 29. 2.
  const FEB29 = 151;                      // index 29. 2. v přestupné zimě
  const MONTHS = [['říj', 0], ['lis', 31], ['pro', 61], ['led', 92], ['úno', 123], ['bře', 151], ['dub', 182], ['kvě', 212]];
  const W = 1000, H = 300, L = 44, R = 10, T = 12, B = 28;
  const fmtDay = new Intl.DateTimeFormat('cs-CZ', {day: 'numeric', month: 'numeric', timeZone: 'UTC'});
  const fmtFull = new Intl.DateTimeFormat('cs-CZ', {day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC'});
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'})[c]);
  const leap = y => (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
  const start = key => +key.slice(0, 4);
  const dateOf = (key, i) => new Date(Date.UTC(start(key), 9, 1) + (i + (leap(start(key) + 1) && i >= FEB29 ? 1 : 0)) * 864e5);
  const plural = (n, one, few, many) => n === 1 ? one : n >= 2 && n <= 4 ? few : many;
  const today = new Date();
  const currentKey = (() => {
    const y = today.getUTCMonth() >= 9 ? today.getUTCFullYear() : today.getUTCFullYear() - 1;
    return `${y}/${String(y + 1).slice(2)}`;
  })();

  function normalize(key, values) {
    const v = values.slice();
    if (leap(start(key) + 1) && v.length > FEB29) v.splice(FEB29, 1);
    return v;
  }

  function summary(key, v) {
    let max = null, maxAt = -1, days = 0, first = -1, last = -1, n = 0;
    v.forEach((x, i) => {
      if (x == null) return;
      n++;
      if (max == null || x > max) { max = x; maxAt = i; }
      if (x >= 1) { days++; if (first < 0) first = i; last = i; }
    });
    return {key, v, max, maxAt, days, first, last, n, current: key === currentKey, complete: key !== currentKey && n >= 120};
  }

  function percentile(sorted, p) {
    const i = (sorted.length - 1) * p, lo = Math.floor(i), hi = Math.ceil(i);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo);
  }

  function climate(seasons) {
    const full = seasons.filter(s => s.complete);
    const out = [];
    for (let i = 0; i < DAYS; i++) {
      const xs = full.map(s => s.v[i]).filter(x => x != null).sort((a, b) => a - b);
      out.push(xs.length >= 5 ? [percentile(xs, .1), percentile(xs, .5), percentile(xs, .9)] : null);
    }
    return out;
  }

  function niceMax(v) {
    const steps = [20, 40, 60, 80, 100, 120, 160, 200, 240, 280, 320, 400, 480, 600];
    return steps.find(s => s >= v) || Math.ceil(v / 100) * 100;
  }

  function prepare(doc) {
    return doc.stations.filter(st => Object.values(st.seasons).some(v => v.some(x => x != null))).map(st => {
      const seasons = Object.entries(st.seasons).map(([k, v]) => summary(k, normalize(k, v))).filter(s => s.n > 0);
      const clim = climate(seasons);
      const ranked = seasons.filter(s => s.complete && s.max != null).sort((a, b) => b.max - a.max);
      return {...st, seasons, clim, ranked, first: seasons[0].key, last: seasons[seasons.length - 1].key};
    });
  }

  const x = i => L + (W - L - R) * i / (DAYS - 1);

  function path(values, y, xAt = x) {
    let d = '', open = false;
    values.forEach((v, i) => {
      if (v == null) { open = false; return; }
      d += `${open ? 'L' : 'M'}${xAt(i).toFixed(1)} ${y(v).toFixed(1)}`;
      open = true;
    });
    return d;
  }

  function areaPath(values, y, xAt = x) {
    let d = '', run = [];
    const flush = () => {
      if (run.length > 1) d += `M${xAt(run[0][0]).toFixed(1)} ${y(0)}` + run.map(([i, v]) => `L${xAt(i).toFixed(1)} ${y(v).toFixed(1)}`).join('') + `L${xAt(run[run.length - 1][0]).toFixed(1)} ${y(0)}Z`;
      run = [];
    };
    values.forEach((v, i) => v == null ? flush() : run.push([i, v]));
    flush();
    return d;
  }

  function bandPath(clim, y, xAt = x) {
    let d = '', run = [];
    const flush = () => {
      if (run.length > 1) d += 'M' + run.map(([i, c]) => `${xAt(i).toFixed(1)} ${y(c[2]).toFixed(1)}`).join('L')
        + 'L' + run.slice().reverse().map(([i, c]) => `${xAt(i).toFixed(1)} ${y(c[0]).toFixed(1)}`).join('L') + 'Z';
      run = [];
    };
    clim.forEach((c, i) => c ? run.push([i, c]) : flush());
    flush();
    return d;
  }

  function chart(st, s, width = W, height = H) {
    const W = width, H = height;
    const x = i => L + (W - L - R) * i / (DAYS - 1);
    const top = niceMax(Math.max(s.max || 0, ...st.clim.map(c => c ? c[2] : 0), 10));
    const y = v => T + (H - T - B) * (1 - v / top);
    const ticks = [0, .25, .5, .75, 1].map(f => Math.round(top * f));
    const grid = ticks.map(t => `<line x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/>`).join('');
    const yl = ticks.map(t => `<text x="${L - 8}" y="${y(t) + 4}" text-anchor="end">${t}</text>`).join('');
    const xl = MONTHS.map(([m, i]) => `<text x="${x(i) + 4}" y="${H - 8}">${m}</text>`).join('');
    const vgrid = MONTHS.slice(1).map(([, i]) => `<line x1="${x(i)}" x2="${x(i)}" y1="${T}" y2="${H - B}"/>`).join('');
    const median = path(st.clim.map(c => c && c[1]), y, x);
    return {y, x, width, svg: `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Výška sněhu v zimě ${esc(s.key)}, stanice ${esc(st.name)}">
      <g class="grid">${grid}${vgrid}</g>
      <path class="band" d="${bandPath(st.clim, y, x)}"/>
      <path class="median" d="${median}"/>
      <path class="area" d="${areaPath(s.v, y, x)}"/>
      <path class="line" d="${path(s.v, y, x)}"/>
      <g class="ax">${yl}${xl}<text x="${L + 6}" y="${y(top) + 14}">cm</text></g>
      <line class="cursor" y1="${T}" y2="${H - B}" visibility="hidden"/><circle class="dot" r="3.5" visibility="hidden"/>
    </svg><div class="wtip" hidden></div>`};
  }

  function stat(label, value, unit, note, main) {
    return `<div class="wstat${main ? ' wstat-main' : ''}"><span>${label}</span><strong>${value}${unit ? ` <small>${unit}</small>` : ''}</strong><em>${note}</em></div>`;
  }

  function stats(st, s) {
    const rank = st.ranked.findIndex(r => r.key === s.key);
    const maxNote = s.max == null ? 'Bez měření'
      : `${fmtDay.format(dateOf(s.key, s.maxAt))} · `
        + (s.current ? 'zima probíhá' : rank < 0 ? 'neúplná zima' : `${rank + 1}. místo z ${st.ranked.length} zim`);
    const typical = st.ranked.length ? st.ranked.map(r => r.days).sort((a, b) => a - b)[Math.floor(st.ranked.length / 2)] : null;
    return stat('Nejvyšší pokrývka', s.max ?? '–', s.max == null ? '' : 'cm', maxNote, true)
      + stat('Dní se sněhem', s.days, plural(s.days, 'den', 'dny', 'dní'), typical == null ? `z ${s.n} měření` : `obvykle ${typical} ${plural(typical, 'den', 'dny', 'dní')}`)
      + stat('První sníh', s.first < 0 ? '–' : fmtDay.format(dateOf(s.key, s.first)), '', 'souvislá pokrývka od 1 cm')
      + stat(s.current ? 'Zatím poslední sníh' : 'Poslední sníh', s.last < 0 ? '–' : fmtDay.format(dateOf(s.key, s.last)), '', s.current ? 'zima ještě běží' : 'poslední den s pokrývkou');
  }

  function bars(st, s) {
    const by = new Map(st.seasons.map(x => [start(x.key), x]));
    const a = start(st.first), b = start(st.last);
    const top = Math.max(...st.seasons.map(x => x.max || 0), 1);
    let html = '';
    for (let yr = a; yr <= b; yr++) {
      const x = by.get(yr);
      if (!x || x.max == null) {
        const season = `${yr}/${String(yr + 1).slice(2)}`;
        html += `<button type="button" class="gap" data-season="${season}" aria-disabled="true" aria-label="Zima ${season} · data nejsou k dispozici"></button>`;
        continue;
      }
      html += `<button type="button" data-key="${x.key}" style="--h:${Math.max(2, x.max / top * 100).toFixed(1)}%" aria-pressed="${x.key === s.key}" title="Zima ${x.key}: nejvíc ${x.max} cm" aria-label="Zima ${x.key}, nejvíc ${x.max} cm"></button>`;
    }
    const mid = Math.round((a + b) / 2);
    return `<div class="wbars">${html}<div class="wtip wgap-tip" hidden></div></div><div class="wyears"><span>${a}/${String(a + 1).slice(2)}</span><span>${mid}</span><span>${b}/${String(b + 1).slice(2)}</span></div>`;
  }

  function mobileBars(st, s) {
    const top = Math.max(...st.seasons.map(x => x.max || 0), 1);
    const buttons = st.seasons.filter(x => x.max != null).map(x =>
      `<button type="button" data-preview="${x.key}" aria-pressed="${x.key === s.key}" aria-label="Zobrazit zimu ${x.key}, nejvýše ${x.max} cm"><span class="wmobile-column" aria-hidden="true"><i style="height:${Math.max(2, x.max / top * 100).toFixed(1)}%"></i></span><span class="wmobile-year">${x.key}</span></button>`).join('');
    return `<div class="wmobile-history"><p class="wmobile-help">Pouze zimy s měřením · klepnutím zobrazíte zimu</p><div class="wmobile-frame"><div class="wmobile-scroll" role="group" aria-label="Zimy s dostupným měřením">${buttons}</div></div></div>`;
  }

  function ranking(st) {
    const li = r => `<li><button type="button" data-key="${r.key}">${r.key}</button><span>${r.max} cm · ${r.days} ${plural(r.days, 'den', 'dny', 'dní')}</span></li>`;
    const most = st.ranked.slice(0, 5), least = st.ranked.slice(-5).reverse();
    return `<div class="wrank"><div><h4>Nejbohatší zimy</h4><ol>${most.map(li).join('')}</ol></div><div><h4>Nejchudší zimy</h4><ol>${least.map(li).join('')}</ol></div></div>`;
  }

  function mount(card, stations) {
    const body = card.querySelector('.wbody');
    const name = card.querySelector('h2').textContent;
    const id = card.dataset.range;
    card.style.setProperty('--c', card.dataset.color);
    const oldest = Math.min(...stations.map(s => start(s.first)));
    let si = 0, key = null;

    function render() {
      const st = stations[si];
      if (!st.seasons.some(s => s.key === key)) key = (st.ranked.length ? st.seasons.filter(s => s.complete).pop() : st.seasons[st.seasons.length - 1]).key;
      const idx = st.seasons.findIndex(s => s.key === key);
      const s = st.seasons[idx];
      const compact = window.matchMedia('(max-width: 1024px)').matches;
      const padding = getComputedStyle(body);
      const plotWidth = body.clientWidth - parseFloat(padding.paddingLeft) - parseFloat(padding.paddingRight);
      const c = compact ? chart(st, s, Math.max(240, plotWidth), 260) : chart(st, s);
      const oldScroll = body.querySelector('.wmobile-scroll')?.scrollLeft;
      const dropdown = (act, label, options, value) => {
        const uid = `w-${id}-${act}`;
        return `<div class="dd snow-dd wsel" data-act="${act}"><button type="button" class="dd-btn" aria-label="${label}: ${esc(options.find(o => o.value === value).label)}" aria-haspopup="listbox" aria-expanded="false" aria-controls="${uid}"><span class="lab">${esc(options.find(o => o.value === value).label)}</span><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M8 9l4-4 4 4M8 15l4 4 4-4"/></svg></button><ul class="dd-list" id="${uid}" role="listbox" aria-label="${label}" tabindex="-1" hidden>${options.map((o, i) => `<li role="option" id="${uid}-${i}" data-value="${o.value}" aria-selected="${o.value === value}">${esc(o.label)}</li>`).join('')}</ul></div>`;
      };
      body.innerHTML = `<header class="whead"><div><h2 id="w-${id}">${esc(name)}</h2>
          <p class="wsub">${stations.length} ${plural(stations.length, 'stanice', 'stanice', 'stanic')} · měření od zimy ${oldest}/${String(oldest + 1).slice(2)}</p></div>
        <div class="wctl">
          ${dropdown('station', 'Stanice', stations.map((x, i) => ({ value: String(i), label: `${x.name} · ${x.alt} m` })), String(si))}
          ${dropdown('season', 'Zima', st.seasons.slice().reverse().map(x => ({ value: x.key, label: `Zima ${x.key}${x.current ? ' (probíhá)' : ''}` })), key)}
          <span class="wstep"><button type="button" class="dd-btn" data-step="-1" aria-label="Předchozí zima"${idx === 0 ? ' disabled' : ''}><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3 5 8l5 5"/></svg></button><button type="button" class="dd-btn" data-step="1" aria-label="Následující zima"${idx === st.seasons.length - 1 ? ' disabled' : ''}><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m6 3 5 5-5 5"/></svg></button></span>
        </div></header>
        <div class="wstats">${stats(st, s)}</div>
        <div class="wchart">${c.svg}</div>
        <ul class="wlegend"><li><i class="k-line"></i>Zima ${s.key}</li><li><i class="k-med"></i>Medián všech zim</li><li><i class="k-band"></i>Obvyklé rozpětí (10–90 %)</li></ul>
        <div class="wall"><div class="wall-head"><h3>Všechny zimy</h3><span>Nejvyšší pokrývka každé zimy · ${st.seasons.length} zim od ${st.first}</span></div>
          ${bars(st, s)}${mobileBars(st, s)}${ranking(st)}</div>`;
      hover(body.querySelector('.wchart'), st, s, c);
      const mobileScroll = body.querySelector('.wmobile-scroll');
      if (oldScroll != null) mobileScroll.scrollLeft = oldScroll;
      else if (compact) {
        const selected = mobileScroll.querySelector('[aria-pressed="true"]');
        mobileScroll.scrollLeft = selected.offsetLeft - mobileScroll.clientWidth / 2 + selected.offsetWidth / 2;
      }
      const syncEdges = () => {
        const overflow = mobileScroll.scrollWidth - mobileScroll.clientWidth;
        const frame = mobileScroll.parentElement;
        frame.classList.toggle('can-scroll-left', overflow > 2 && mobileScroll.scrollLeft > 2);
        frame.classList.toggle('can-scroll-right', overflow > 2 && mobileScroll.scrollLeft < overflow - 2);
      };
      mobileScroll.addEventListener('scroll', syncEdges, {passive: true});
      syncEdges();
      body.querySelectorAll('[data-preview]').forEach(button => {
        button.onclick = () => {
          if (key === button.dataset.preview) return;
          key = button.dataset.preview;
          render();
          body.querySelector(`[data-preview="${key}"]`).focus({preventScroll: true});
        };
      });
      const barsBox = body.querySelector('.wbars'), gapTip = barsBox.querySelector('.wgap-tip');
      barsBox.querySelectorAll('.gap').forEach(button => {
        const show = () => {
          gapTip.innerHTML = `<b>Zima ${button.dataset.season}</b><span>Data nejsou k dispozici</span>`;
          gapTip.hidden = false;
          const boxRect = barsBox.getBoundingClientRect(), rect = button.getBoundingClientRect();
          const half = gapTip.offsetWidth / 2;
          gapTip.style.left = Math.max(half, Math.min(rect.left - boxRect.left + rect.width / 2, boxRect.width - half)) + 'px';
        };
        const hide = () => { gapTip.hidden = true; };
        button.addEventListener('pointerenter', show);
        button.addEventListener('focus', show);
        button.addEventListener('click', show);
        button.addEventListener('pointerleave', hide);
        button.addEventListener('blur', hide);
      });
      body.querySelectorAll('.wsel').forEach(box => {
        const btn = box.querySelector('button'), list = box.querySelector('ul'), items = [...list.children];
        let active = items.findIndex(li => li.getAttribute('aria-selected') === 'true');
        const move = i => {
          active = Math.max(0, Math.min(items.length - 1, i));
          items.forEach((li, j) => li.classList.toggle('act', j === active));
          list.setAttribute('aria-activedescendant', items[active].id);
          items[active].scrollIntoView({ block: 'nearest' });
        };
        const open = () => {
          document.querySelectorAll('#winters .wsel').forEach(other => {
            other.querySelector('ul').hidden = true;
            other.querySelector('button').setAttribute('aria-expanded', 'false');
          });
          list.hidden = false; btn.setAttribute('aria-expanded', 'true'); list.focus(); move(active);
        };
        const close = focus => { list.hidden = true; btn.setAttribute('aria-expanded', 'false'); if (focus) btn.focus(); };
        const pick = li => {
          if (box.dataset.act === 'station') si = +li.dataset.value;
          else key = li.dataset.value;
          render();
          body.querySelector(`.wsel[data-act="${box.dataset.act}"] button`).focus();
        };
        btn.onclick = () => list.hidden ? open() : close(false);
        btn.onkeydown = e => { if (['ArrowDown', 'ArrowUp'].includes(e.key)) { e.preventDefault(); open(); } };
        list.onclick = e => { const li = e.target.closest('[role="option"]'); if (li) pick(li); };
        list.onpointermove = e => { const li = e.target.closest('[role="option"]'); if (li) move(items.indexOf(li)); };
        list.onkeydown = e => {
          if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) {
            e.preventDefault(); move(e.key === 'Home' ? 0 : e.key === 'End' ? items.length - 1 : active + (e.key === 'ArrowDown' ? 1 : -1));
          } else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(items[active]); }
          else if (e.key === 'Escape') { e.preventDefault(); close(true); }
          else if (e.key === 'Tab') close(true);
        };
      });
    }

    function hover(box, st, s, geometry) {
      const {y, x, width: W} = geometry;
      const svg = box.querySelector('svg'), tip = box.querySelector('.wtip');
      const cursor = svg.querySelector('.cursor'), dot = svg.querySelector('.dot');
      const hide = () => {
        tip.hidden = true;
        cursor.setAttribute('visibility', 'hidden');
        dot.setAttribute('visibility', 'hidden');
      };
      const move = e => {
        const r = svg.getBoundingClientRect();
        const vx = (e.clientX - r.left) / r.width * W;
        const i = Math.round((vx - L) / (W - L - R) * (DAYS - 1));
        if (i < 0 || i >= DAYS) { hide(); return; }
        const v = s.v[i], c = st.clim[i];
        cursor.setAttribute('visibility', 'visible');
        cursor.setAttribute('x1', x(i)); cursor.setAttribute('x2', x(i));
        dot.setAttribute('visibility', v == null ? 'hidden' : 'visible');
        if (v != null) { dot.setAttribute('cx', x(i)); dot.setAttribute('cy', y(v)); }
        tip.hidden = false;
        tip.innerHTML = `<b>${v == null ? 'bez měření' : v + ' cm'}</b><span>${fmtFull.format(dateOf(s.key, i))}</span>`
          + (c ? `<br><span>obvykle ${Math.round(c[1])} cm</span>` : '');
        const px = x(i) / W * r.width;
        const pointerX = e.clientX - r.left;
        const right = Math.max(px, pointerX) + 14;
        const left = Math.min(px, pointerX) - tip.offsetWidth - 14;
        const maxLeft = r.width - tip.offsetWidth - 8;
        tip.style.left = Math.max(8, Math.min(right <= maxLeft ? right : left, maxLeft)) + 'px';
        tip.style.top = Math.max(0, Math.min(r.height - tip.offsetHeight, e.clientY - r.top - tip.offsetHeight / 2)) + 'px';
      };
      let touch = null, direction = null;
      svg.addEventListener('pointerdown', e => {
        if (e.pointerType === 'mouse') return;
        touch = {x: e.clientX, y: e.clientY}; direction = null;
        move(e);
      });
      svg.addEventListener('pointermove', e => {
        if (e.pointerType === 'mouse') { move(e); return; }
        if (!touch) return;
        const dx = Math.abs(e.clientX - touch.x), dy = Math.abs(e.clientY - touch.y);
        if (!direction && Math.max(dx, dy) > 8) direction = dx > dy ? 'horizontal' : 'vertical';
        if (direction === 'vertical') hide();
        else move(e);
      });
      svg.addEventListener('pointerup', () => { touch = null; });
      svg.addEventListener('pointercancel', () => { touch = null; hide(); });
      svg.addEventListener('pointerleave', e => { if (e.pointerType === 'mouse') hide(); });
      box.hideTip = hide;
    }

    document.addEventListener('click', e => {
      const chartBox = body.querySelector('.wchart');
      if (chartBox && !chartBox.contains(e.target)) chartBox.hideTip?.();
      body.querySelectorAll('.wsel').forEach(box => {
        if (!box.contains(e.target)) {
          box.querySelector('ul').hidden = true;
          box.querySelector('button').setAttribute('aria-expanded', 'false');
        }
      });
    });
    body.addEventListener('click', e => {
      const b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.key) { key = b.dataset.key; render(); }
      if (b.dataset.step) {
        const list = stations[si].seasons, idx = list.findIndex(s => s.key === key) + +b.dataset.step;
        if (list[idx]) {
          key = list[idx].key; render();
          body.querySelector(`[data-step="${b.dataset.step}"]`).focus({ preventScroll: true });
        }
      }
    });
    // výchozí stanice: nejvýš položená s probíhající řadou; výchozí zima je poslední úplná
    si = Math.max(0, stations.findIndex(s => s.last === currentKey));
    render();
    let lastWidth = body.clientWidth, lastCompact = window.matchMedia('(max-width: 1024px)').matches;
    new ResizeObserver(() => {
      const width = body.clientWidth, compact = window.matchMedia('(max-width: 1024px)').matches;
      if (width !== lastWidth || compact !== lastCompact) {
        lastWidth = width; lastCompact = compact; render();
      }
    }).observe(body);
  }

  // Vrstevnice pohoří bez velkého štítku z SVG; menší štítek se kreslí nad bodem stanice v HTML.
  async function relief(band) {
    const text = await (await request(band.dataset.relief)).text();
    const doc = new DOMParser().parseFromString(text, 'image/svg+xml');
    const svg = doc.documentElement;
    if (svg.localName !== 'svg' || doc.querySelector('parsererror')) throw new Error('Neplatná mapa');
    const lines = [...svg.querySelectorAll('text tspan')].map(t => t.textContent);
    svg.querySelector('g[filter]')?.remove();
    svg.querySelector('text')?.remove();
    const circles = [...svg.querySelectorAll('circle')];
    const pin = circles.find(c => c.getAttribute('r') === '12.5');
    // bod stanice i štítek jsou v HTML ve stejném místě, aby na sebe vždy přesně seděly
    circles.filter(c => ['12.5', '27'].includes(c.getAttribute('r'))).forEach(c => c.remove());
    const [vw, vh] = svg.getAttribute('viewBox').split(' ').slice(2).map(Number);
    const px = +pin.getAttribute('cx'), py = +pin.getAttribute('cy');
    svg.removeAttribute('width'); svg.removeAttribute('height');
    svg.setAttribute('aria-hidden', 'true');
    // značka = bod nulové velikosti; bod, šipka i bublina se vystředí na stejnou souřadnici
    const mark = document.createElement('div');
    mark.className = 'wmark';
    mark.innerHTML = `<i class="wdot"></i><div class="wtag"><div class="wpin"><b>${esc(lines.slice(0, -1).join(' '))}</b><span>${esc(lines[lines.length - 1] || '')}</span></div></div>`;
    band.replaceChildren(document.importNode(svg, true), mark);
    const el = band.querySelector('svg');
    // body kreslíme ve stálé velikosti na obrazovce, ne zvětšené s mapou (poloměr, tloušťka okraje v px)
    const dots = [...el.querySelectorAll('circle')].map(c => [c, ({'5.5': [3.5, 1.6]})[c.getAttribute('r')]]).filter(([, v]) => v);
    // výřez vyplní pás a bod stanice drží ve spodní třetině, aby nad ním byl štítek celý
    const place = () => {
      const w = band.clientWidth, h = band.clientHeight, k = Math.max(w / vw, h / vh);
      const ox = Math.min(0, Math.max(w - vw * k, w / 2 - px * k));
      const oy = Math.min(0, Math.max(h - vh * k, h * .7 - py * k));
      Object.assign(el.style, {width: `${vw * k}px`, height: `${vh * k}px`, left: `${ox}px`, top: `${oy}px`});
      for (const [c, [r, sw]] of dots) { c.setAttribute('r', r / k); if (sw) c.setAttribute('stroke-width', sw / k); }
      Object.assign(mark.style, {left: `${Math.round(ox + px * k)}px`, top: `${Math.round(oy + py * k)}px`});
    };
    place();
    new ResizeObserver(place).observe(band);
  }

  // Data (stovky kB) se načtou až při prvním otevření stránky, ne s každou stránkou webu.
  async function request(url) {
    const response = await fetch(url, {cache: 'no-cache', signal: AbortSignal.timeout(20000)});
    if (!response.ok) throw new Error(response.status);
    return response;
  }

  function skeleton(card) {
    const body = card.querySelector('.wbody');
    body.setAttribute('aria-busy', 'true');
    body.innerHTML = `<p class="sr-only" role="status">Načítám zimy na horách…</p><div aria-hidden="true">
      <header class="whead"><div class="skstack"><h2 id="w-${card.dataset.range}">${esc(card.dataset.name)}</h2><i class="sk" style="width:200px;height:14px"></i></div>
      <div class="wctl"><i class="sk" style="width:220px;height:48px"></i><i class="sk" style="width:160px;height:48px"></i></div></header>
      <div class="wstats">${Array.from({length: 4}, () => '<div class="wstat skstack"><i class="sk" style="width:65%;height:12px"></i><i class="sk" style="width:50%;height:36px"></i><i class="sk" style="height:12px"></i></div>').join('')}</div>
      <div class="wchart"><div class="sk wskeleton-chart"></div></div>
      <div class="wall skstack"><i class="sk" style="width:140px;height:20px"></i><i class="sk" style="height:96px"></i></div>
      <div class="wrank">${Array.from({length: 2}, () => '<div class="skstack"><i class="sk" style="width:45%;height:14px"></i><i class="sk" style="height:110px"></i></div>').join('')}</div></div>`;
  }

  const dataRequests = new WeakMap(), mapRequests = new WeakMap();
  function loadData(card) {
    if (dataRequests.has(card)) return dataRequests.get(card);
    skeleton(card);
    const body = card.querySelector('.wbody');
    const pending = request(`/data/historie/${card.dataset.range}.json`)
      .then(r => r.json())
      .then(doc => {
        const stations = prepare(doc);
        if (stations.length) mount(card, stations);
        else showState('Pro toto pohoří zatím nejsou dostupná měření.');
      })
      .catch(() => {
        dataRequests.delete(card);
        showState('Historii se nepodařilo načíst.');
      })
      .finally(() => body.setAttribute('aria-busy', 'false'));
    function showState(message) {
      body.innerHTML = `<header class="whead"><h2 id="w-${card.dataset.range}">${esc(card.dataset.name)}</h2></header><div class="snow-empty" role="status"><p>${message}</p><button type="button" class="ckbtn">Načíst znovu</button></div>`;
      body.querySelector('.ckbtn').onclick = () => { dataRequests.delete(card); loadData(card); };
    }
    dataRequests.set(card, pending);
    return pending;
  }

  function loadMap(band) {
    if (mapRequests.has(band)) return mapRequests.get(band);
    band.classList.add('sk');
    band.setAttribute('aria-busy', 'true');
    band.replaceChildren();
    const pending = relief(band).catch(() => {
      mapRequests.delete(band);
      band.innerHTML = '<div class="wmap-empty" role="status"><p>Mapu pohoří se nepodařilo načíst.</p><button type="button" class="ckbtn">Načíst znovu</button></div>';
      band.querySelector('button').onclick = () => loadMap(band);
    }).finally(() => {
      band.classList.remove('sk');
      band.setAttribute('aria-busy', 'false');
    });
    mapRequests.set(band, pending);
    return pending;
  }

  function open() {
    return Promise.all([...document.querySelectorAll('#winters .wcard')].flatMap(card => {
      card.dataset.name ||= card.querySelector('h2').textContent;
      return [loadData(card), loadMap(card.querySelector('.wband'))];
    }));
  }
  window.MeteogramWinters = {open};
})();
