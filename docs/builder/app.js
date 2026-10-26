/*
 * Lugic character builder: the page. The rules live in engine.js, the numbers in rules.js.
 * This file only builds the form, shows what the engine says, and hands over the code.
 */
(function () {
  'use strict';

  var E = window.LugicEngine;
  var R = E.rules;
  var SAVE_KEY = 'lugic-character-builder-v1';
  var ART_KEY = 'lugic-character-builder-pictures-v1';
  var MAX_COINS_SHOWN = 120;
  // How wide the portrait shows on the card, as a share of the card. The picture always keeps its own shape; this only scales it.
  // It belongs to this page, not to the character, so it is not in the code and the bot is never told.
  var PORTRAIT_KEY = 'lugic-character-builder-portrait-v1';
  var PORTRAIT_SIZES = [
    { id: 'small', label: 'Small', percent: 40 },
    { id: 'medium', label: 'Medium', percent: 60 },
    { id: 'large', label: 'Large', percent: 80 },
    { id: 'full', label: 'Full', percent: 100 }
  ];
  var portraitSize = loadPortraitSize();

  var state = loadSaved() || E.defaultState();
  var syncs = [];          // functions that repaint one thing when the review changes
  var codeSeq = 0;         // so a slow encode cannot overwrite a newer one
  var currentCode = '';
  var saveTimer = null;
  var resetArmed = false;

  // ---- icons -------------------------------------------------------------------------------------------------
  // A picture for a name such as "burn" can come from three places, tried in this order:
  //   1. wiki/index.json   the wiki's game icons saved beside the page (`python -m game.wiki_assets`), fast and works offline
  //   2. rules.js          the same icons linked straight from the Limbus Company wiki, so nothing has to be downloaded
  //   3. icons/index.json  the bot's own emoji (`python -m game.emoji_icons`)
  // If a picture does not load, the next one is tried; if the wiki refuses pictures from this page, linking is switched off for the
  // rest of the visit. With none of them the slot stays empty and the page shows text only.

  var botFiles = {};
  var wikiFiles = {};
  var wikiBroken = false;

  function icon(name) { return el('span', { class: 'ico', 'data-icon': name, 'aria-hidden': 'true' }); }

  function iconCandidates(names) {
    var out = [];
    names.forEach(function (n) { if (wikiFiles[n]) { out.push({ src: wikiFiles[n], remote: false }); } });
    if (!wikiBroken) { names.forEach(function (n) { if (R.wikiImages && R.wikiImages[n]) { out.push({ src: R.wikiImages[n], remote: true }); } }); }
    names.forEach(function (n) { if (botFiles[n]) { out.push({ src: botFiles[n], remote: false }); } });
    return out;
  }

  // A slot can name several icons, best first ("coin coin_heads").
  function fillIcons() {
    var slots = document.querySelectorAll('.ico[data-icon]');
    for (var i = 0; i < slots.length; i++) { fillSlot(slots[i]); }
  }

  function fillSlot(slot) {
    if (slot.firstChild || slot.getAttribute('data-done')) { return; }
    var names = slot.getAttribute('data-icon').split(' ');
    if (!iconCandidates(names).length) { return; }   // nothing to show (yet)
    var img = document.createElement('img');
    img.alt = '';
    img.referrerPolicy = 'no-referrer';   // the wiki never learns which page the picture is on
    var tried = {};
    // The list is read again at every step, because the lists of pictures may arrive after the first try.
    function next() {
      var list = iconCandidates(names);
      for (var k = 0; k < list.length; k++) {
        if (tried[list[k].src]) { continue; }
        tried[list[k].src] = true;
        img.setAttribute('data-remote', list[k].remote ? '1' : '');
        img.src = list[k].src;
        return;
      }
      slot.setAttribute('data-done', '1');
      if (img.parentNode) { img.parentNode.removeChild(img); }
    }
    img.addEventListener('error', function () {
      if (img.getAttribute('data-remote')) { wikiBroken = true; }
      next();
    });
    slot.appendChild(img);
    next();
  }

  function loadIcons() {
    if (!window.fetch) { fillIcons(); return; }
    function read(url) { return fetch(url).then(function (r) { return r.ok ? r.json() : {}; }).catch(function () { return {}; }); }
    Promise.all([read('icons/index.json'), read('wiki/index.json')]).then(function (maps) {
      botFiles = maps[0] && typeof maps[0] === 'object' ? maps[0] : {};
      wikiFiles = maps[1] && typeof maps[1] === 'object' ? maps[1] : {};
      var spent = document.querySelectorAll('.ico[data-done]');   // slots that ran out of pictures before these lists arrived
      for (var i = 0; i < spent.length; i++) { spent[i].removeAttribute('data-done'); }
      fillIcons();
    });
  }

  // ---- tiny DOM helpers ------------------------------------------------------------------------

  function el(tag, attrs, kids) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      var value = attrs[key];
      if (value === null || value === undefined || value === false) { return; }
      if (key === 'class') { node.className = value; }
      else if (key === 'text') { node.textContent = value; }
      else if (key.indexOf('on') === 0) { node.addEventListener(key.slice(2), value); }
      else if (value === true) { node.setAttribute(key, ''); }
      else { node.setAttribute(key, value); }
    });
    (kids || []).forEach(function (kid) {
      if (kid === null || kid === undefined) { return; }
      node.appendChild(typeof kid === 'string' ? document.createTextNode(kid) : kid);
    });
    return node;
  }

  var uid = 0;
  function nextId(prefix) { uid += 1; return prefix + '-' + uid; }

  // A label, its control and an optional note, as one grid cell.
  function field(labelText, control, note) {
    if (!control.id) { control.id = nextId('f'); }
    return el('div', { class: 'field' }, [
      el('label', { for: control.id, text: labelText }),
      control,
      note ? el('p', { class: 'note', text: note }) : null
    ]);
  }

  function numberInput(value, min, max, onChange) {
    var input = el('input', { type: 'number', inputmode: 'numeric', min: String(min), step: '1' });
    if (max !== null && max !== undefined) { input.max = String(max); }
    input.value = String(value);
    input.addEventListener('input', function () {
      var n = parseInt(input.value, 10);
      onChange(isFinite(n) ? n : 0);
      update();
    });
    return input;
  }

  function selectInput(options, value, onChange) {
    var select = el('select');
    options.forEach(function (o) {
      if (o.group) {
        var group = el('optgroup', { label: o.group });
        o.items.forEach(function (item) { group.appendChild(el('option', { value: item.value, text: item.label })); });
        select.appendChild(group);
      } else { select.appendChild(el('option', { value: o.value, text: o.label })); }
    });
    select.value = String(value);
    select.addEventListener('change', function () { onChange(select.value); update(); });
    return select;
  }

  function slider(min, max, step, value, onChange) {
    var input = el('input', { type: 'range', min: String(min), max: String(max), step: String(step) });
    input.value = String(value);
    input.addEventListener('input', function () { onChange(parseInt(input.value, 10)); update(); });
    return input;
  }

  function section(title, intro, kids) {
    return el('section', { class: 'part', id: 'sec-' + title.toLowerCase().replace(/[^a-z0-9]+/g, '-') }, [
      el('h2', { text: title }),
      intro ? el('p', { class: 'intro', text: intro }) : null,
      el('div', { class: 'part-body' }, kids)
    ]);
  }

  function issueList() {
    return el('ul', { class: 'issues', 'aria-live': 'polite' });
  }
  function paintIssues(list, items) {
    list.textContent = '';
    items.forEach(function (text) { list.appendChild(el('li', { text: text })); });
    list.hidden = !items.length;
  }

  function bp(n) { return n + ' BP'; }

  // ---- the form -----------------------------------------------------------------------------------

  function build() {
    syncs = [];
    var sheet = document.getElementById('sheet');
    sheet.textContent = '';
    // Skills come right after the things they depend on (ranks and defense types), before the rarer parts.
    sheet.appendChild(buildIdentity());
    sheet.appendChild(buildPoints());
    sheet.appendChild(buildStats());
    sheet.appendChild(buildResistances());
    sheet.appendChild(buildRanks());
    sheet.appendChild(buildDefense());
    sheet.appendChild(buildTags());
    sheet.appendChild(buildSkills());
    sheet.appendChild(buildPassives());
    sheet.appendChild(buildReference());
    buildTabs(sheet);
  }

  // One tab for each part of the form. Only the open part is shown; Back and Next at the bottom walk through them in order.
  var TAB_KEY = 'lugic-character-builder-tab-v1';
  var tabParts = [];
  var tabButtons = [];

  function savedTab() {
    try { return window.sessionStorage.getItem(TAB_KEY); } catch (e) { return null; }
  }
  // The address follows the tab only once the reader has picked one. Writing it while the page loads would make the browser
  // scroll to that part as if a link had been followed, hiding the top of the page.
  function rememberTab(id, userDriven) {
    try { window.sessionStorage.setItem(TAB_KEY, id); } catch (e) { /* private window: the tab is just not remembered */ }
    if (!userDriven) { return; }
    try { if (window.history && history.replaceState) { history.replaceState(null, '', '#' + id); } } catch (e) { /* ignore */ }
  }

  function selectTab(id, userDriven) {
    var known = tabParts.some(function (part) { return part.id === id; });
    if (!known) { return; }
    tabParts.forEach(function (part) { part.hidden = part.id !== id; });
    tabButtons.forEach(function (tab) {
      var on = tab.getAttribute('aria-controls') === id;
      tab.setAttribute('aria-selected', on ? 'true' : 'false');
      tab.tabIndex = on ? 0 : -1;
      if (on && userDriven && tab.scrollIntoView) { tab.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }
    });
    rememberTab(id, userDriven);
    if (userDriven) {
      var nav = document.getElementById('nav');
      var top = document.getElementById('sheet').getBoundingClientRect().top + window.pageYOffset - nav.offsetHeight - 12;
      if (window.pageYOffset > top) { window.scrollTo(0, Math.max(0, top)); }
    }
  }

  function buildTabs(sheet) {
    var nav = document.getElementById('nav');
    nav.textContent = '';
    nav.setAttribute('role', 'tablist');
    tabParts = [].slice.call(sheet.querySelectorAll('section.part'));
    tabButtons = [];
    tabParts.forEach(function (part, i) {
      var title = part.querySelector('h2').textContent;
      var tab = el('button', { type: 'button', class: 'tab', role: 'tab', id: 'tab-' + part.id, 'aria-controls': part.id, 'aria-selected': 'false', text: title });
      tab.addEventListener('click', function () { selectTab(part.id, true); });
      tab.addEventListener('keydown', function (event) {
        var to = null;
        if (event.key === 'ArrowRight') { to = (i + 1) % tabParts.length; }
        else if (event.key === 'ArrowLeft') { to = (i + tabParts.length - 1) % tabParts.length; }
        else if (event.key === 'Home') { to = 0; }
        else if (event.key === 'End') { to = tabParts.length - 1; }
        if (to === null) { return; }
        event.preventDefault();
        selectTab(tabParts[to].id, true);
        tabButtons[to].focus();
      });
      tabButtons.push(tab);
      nav.appendChild(tab);
      part.setAttribute('role', 'tabpanel');
      part.setAttribute('aria-labelledby', tab.id);
      var back = i > 0 ? el('button', { type: 'button', class: 'btn quiet', text: 'Back: ' + tabParts[i - 1].querySelector('h2').textContent, onclick: function () { selectTab(tabParts[i - 1].id, true); } }) : null;
      var next = i < tabParts.length - 1 ? el('button', { type: 'button', class: 'btn', text: 'Next: ' + tabParts[i + 1].querySelector('h2').textContent, onclick: function () { selectTab(tabParts[i + 1].id, true); } }) : null;
      part.appendChild(el('div', { class: 'tab-nav' }, [back, next]));
    });
    var wanted = [window.location.hash.slice(1), savedTab(), tabParts[0].id].filter(function (id) {
      return tabParts.some(function (part) { return part.id === id; });
    })[0];
    selectTab(wanted, false);
  }

  window.addEventListener('hashchange', function () { selectTab(window.location.hash.slice(1), true); });

  // A small dot on a tab whose part has something to fix.
  function markTabs(r) {
    var marks = {
      'sec-skills': (r.perSkill || []).some(function (list) { return list.length; }),
      'sec-passives': (r.perPassive || []).some(function (list) { return list.length; })
    };
    tabButtons.forEach(function (tab) {
      var id = tab.getAttribute('aria-controls');
      var on = !!marks[id];
      tab.classList.toggle('flagged', on);
      if (on) { tab.title = 'Something here needs fixing'; } else { tab.removeAttribute('title'); }
    });
  }

  function buildIdentity() {
    var name = el('input', { type: 'text', maxlength: String(R.limits.name), autocomplete: 'off', spellcheck: 'false' });
    name.value = state.name;
    name.addEventListener('input', function () { state.name = name.value; update(); });
    var avatar = el('input', { type: 'url', placeholder: 'https://', autocomplete: 'off', spellcheck: 'false' });
    avatar.value = state.avatar;
    avatar.addEventListener('input', function () { state.avatar = avatar.value; update(); });
    return section('Identity', null, [
      el('div', { class: 'grid two' }, [
        field('Name', name, 'A new name. A character that already exists cannot be imported over.'),
        field('Portrait link (optional)', avatar, 'A direct link to an image, starting with https://.')
      ]),
      portraitSizeControl()
    ]);
  }

  // Fixed steps, so the card looks the same for everyone: the picture keeps its own shape and only its width changes.
  function portraitSizeControl() {
    var group = el('div', { class: 'segments wide', role: 'radiogroup', 'aria-label': 'Portrait size' });
    PORTRAIT_SIZES.forEach(function (option) {
      var input = el('input', { type: 'radio', name: 'portrait-size', value: option.id, id: nextId('portrait') });
      input.checked = option.id === portraitSize;
      input.addEventListener('change', function () {
        portraitSize = option.id;
        try { window.localStorage.setItem(PORTRAIT_KEY, portraitSize); } catch (e) { /* not kept in this browser */ }
        paintPortraitSize();
      });
      group.appendChild(el('label', { class: 'segment', for: input.id, title: option.percent + '% of the card' }, [input, el('span', { text: option.label })]));
    });
    return el('div', { class: 'field portrait-size' }, [
      el('span', { class: 'label-like', text: 'Portrait size on the card' }),
      group,
      el('p', { class: 'note', text: 'The picture keeps its own shape, so a tall one stays tall. This only changes how wide it shows here; the bot is not told.' })
    ]);
  }

  function buildPoints() {
    var bpInput = numberInput(state.pools.bp, 1, null, function (n) { state.pools.bp = n; });
    var spInput = numberInput(state.pools.sp, 0, null, function (n) { state.pools.sp = n; });
    var ppInput = numberInput(state.pools.pp, 0, null, function (n) { state.pools.pp = n; });
    var level = el('p', { class: 'levelline' });
    syncs.push(function (r) {
      var text = 'Level ' + r.level;
      text += r.nextLevel ? '. The next level comes at ' + r.nextLevel + ' Build Points.' : '. That is the highest level.';
      level.textContent = text;
    });
    return section(
      'Your points',
      'What the bot says you have. A new character starts with ' + R.startingBp + ' Build Points and no Skill or Passive Points. Admins hand out more, so only raise these if the bot already shows them on your sheet.',
      [
        el('div', { class: 'grid three' }, [
          field('Build Points', bpInput, 'Pays for stats, ranks and defense types.'),
          field('Skill Points', spInput, 'Pays for extra trigger lines.'),
          field('Passive Points', ppInput, 'Pays for passives.')
        ]),
        level
      ]
    );
  }

  function costNote(text) { return el('output', { class: 'cost' }, [text || '']); }

  function sliderRow(labelText, min, max, step, get, set, describe) {
    var input = slider(min, max, step, get(), set);
    input.id = nextId('s');
    var out = el('output', { class: 'readout', for: input.id });
    var cost = el('output', { class: 'cost' });
    syncs.push(function () {
      var d = describe(get());
      out.textContent = d.value;
      cost.textContent = d.cost;
    });
    return el('div', { class: 'row-slider' }, [
      el('label', { for: input.id, text: labelText }),
      input,
      el('div', { class: 'readouts' }, [out, cost])
    ]);
  }

  function buildStats() {
    var s = state.stats;
    var hp = sliderRow('HP', R.hp.base, R.hp.max, R.hp.step, function () { return s.hp; }, function (n) { s.hp = n; },
      function (v) { return { value: v + ' HP', cost: bp(E.checkBuild(state).breakdown.HP) }; });
    var power = sliderRow('Power', R.power.base, R.power.max, 1, function () { return s.power; }, function (n) { s.power = n; },
      function (v) {
        return { value: 'Power ' + v, cost: bp(E.checkBuild(state).breakdown.Power) +
          '. Skills can have Base Power up to ' + E.maxBasePower(v) + ' and Coin Power up to ' + E.maxCoinPower(v) + '.' };
      });
    var off = sliderRow('Offense Level', 0, R.offdef.max, 1, function () { return s.offense; }, function (n) { s.offense = n; },
      function (v) { return { value: v ? '+' + v * R.offdef.percentPerStep + '% damage dealt' : 'None', cost: bp(v ? E.checkBuild(state).breakdown['Offense Level'] : 0) }; });
    var def = sliderRow('Defense Level', 0, R.offdef.max, 1, function () { return s.defense; }, function (n) { s.defense = n; },
      function (v) { return { value: v ? '-' + v * R.offdef.percentPerStep + '% damage taken' : 'None', cost: bp(v ? E.checkBuild(state).breakdown['Defense Level'] : 0) }; });

    var speedOptions = [];
    for (var i = R.speed.lowest; i <= R.speed.highest; i++) { speedOptions.push({ value: String(i), label: String(i) }); }
    var low = selectInput(speedOptions, s.speed[0], function (v) { s.speed[0] = parseInt(v, 10); });
    var high = selectInput(speedOptions, s.speed[1], function (v) { s.speed[1] = parseInt(v, 10); });
    var speedCost = costNote();
    syncs.push(function (r) {
      speedCost.textContent = bp(r.report.breakdown.Speed) + ' for a range averaging ' + ((s.speed[0] + s.speed[1]) / 2) + '. The range must span at least ' + R.speed.minSpread + '.';
      low.value = String(s.speed[0]); high.value = String(s.speed[1]);
    });

    var tiers = el('div', { class: 'checks' }, ['2', '3'].map(function (tier) {
      var box = el('input', { type: 'checkbox', id: nextId('tier') });
      box.checked = s.staggerOff.indexOf(parseInt(tier, 10)) >= 0;
      box.addEventListener('change', function () {
        var t = parseInt(tier, 10);
        s.staggerOff = s.staggerOff.filter(function (x) { return x !== t; });
        if (box.checked) { s.staggerOff.push(t); }
        update();
      });
      return el('label', { class: 'check', for: box.id }, [
        box,
        el('span', { text: 'Remove Stagger Tier ' + tier + ' (' + bp(R.stagger[tier]) + ')' })
      ]);
    }));

    return section('Stats', 'Each step costs more than the last, and no single stat can use more than ' + Math.round(R.caps.single * 100) + '% of your points.', [
      hp, power,
      el('div', { class: 'row-speed' }, [
        el('div', { class: 'grid two' }, [field('Lowest Speed', low), field('Highest Speed', high)]),
        speedCost
      ]),
      off, def,
      el('div', { class: 'field' }, [el('span', { class: 'label-like', text: 'Stagger' }), tiers])
    ]);
  }

  // The wiki's words for a resistance: Fatal, Weak, Normal, Endure and Ineffective, each in its own colour.
  function resistWord(value) {
    var v = Number(value);
    if (v >= 1.5) { return { word: 'Fatal', cls: 'res-fatal' }; }
    if (v > 1) { return { word: 'Weak', cls: 'res-weak' }; }
    if (v === 1) { return { word: 'Normal', cls: 'res-normal' }; }
    if (v >= 0.75) { return { word: 'Endure', cls: 'res-endure' }; }
    return { word: 'Ineffective', cls: 'res-ineffective' };
  }
  function resistTag(key) {
    var tag = el('span', { class: 'res-tag' });
    syncs.push(function () {
      var w = resistWord(state.stats.resist[key]);
      tag.className = 'res-tag ' + w.cls;
      tag.textContent = w.word + ' x' + Number(state.stats.resist[key]);
    });
    return tag;
  }

  function resistLabel(value) {
    var key = Number.isInteger(value) ? value.toFixed(1) : String(value);
    var price = R.resist.statusCosts[key];
    var name = key + 'x';
    if (price === undefined) { return name; }
    if (price > 0) { return name + ', resists (' + bp(price) + ')'; }
    if (price < 0) { return name + ', weak (gives back ' + bp(-price) + ')'; }
    return name + ', normal';
  }

  function buildResistances() {
    var statusValues = Object.keys(R.resist.statusCosts).map(Number).sort(function (a, b) { return a - b; });
    var rows = R.resist.statusTypes.map(function (key) {
      var select = selectInput(statusValues.map(function (v) { return { value: String(v), label: resistLabel(v) }; }),
        state.stats.resist[key], function (v) { state.stats.resist[key] = parseFloat(v); });
      syncs.push(function () { select.value = String(state.stats.resist[key]); });
      var f = field(E.capitalize(key), select);
      f.appendChild(resistTag(key));
      return f;
    });
    var physical = R.resist.damageTypes.map(function (key) {
      var select = selectInput([0.5, 1, 1.5].map(function (v) { return { value: String(v), label: v === 1 ? '1.0x, normal' : v + 'x' + (v < 1 ? ', resists' : ', weak') }; }),
        state.stats.resist[key], function (v) { state.stats.resist[key] = parseFloat(v); });
      syncs.push(function () { select.value = String(state.stats.resist[key]); });
      var f = field(E.capitalize(key), select);
      f.appendChild(resistTag(key));
      return f;
    });
    var cost = costNote();
    syncs.push(function (r) {
      var c = r.report.breakdown.Resistances || 0;
      cost.textContent = c ? 'Resistances cost ' + bp(c) + ' in total.' : 'Resistances cost nothing while everything is normal.';
    });
    return section('Resistances', 'Resisting costs points. Being weak gives up to ' + R.resist.refundCap + ' back. Slash, Blunt and Pierce are either all normal, or one resisted, one normal and one weak.', [
      el('h3', { text: 'Statuses' }),
      el('div', { class: 'grid three' }, rows),
      el('h3', { text: 'Damage types' }),
      el('div', { class: 'grid three' }, physical),
      cost
    ]);
  }

  function buildRanks() {
    var rows = R.ranks.statuses.map(function (status) {
      var name = 'rank-' + status;
      var group = el('div', { class: 'segments', role: 'radiogroup', 'aria-label': E.capitalize(status) + ' rank' });
      var inputs = [];
      for (var rank = 0; rank <= R.ranks.max; rank++) {
        (function (rank) {
          var input = el('input', { type: 'radio', name: name, id: nextId('r'), value: String(rank) });
          input.checked = (state.ranks[status] || 0) === rank;
          input.addEventListener('change', function () {
            if (input.checked) { if (rank) { state.ranks[status] = rank; } else { delete state.ranks[status]; } update(); }
          });
          inputs.push(input);
          group.appendChild(el('label', { class: 'segment', for: input.id }, [input, el('span', { text: String(rank) })]));
        })(rank);
      }
      var cost = el('output', { class: 'cost' });
      syncs.push(function () {
        var rank = state.ranks[status] || 0;
        inputs.forEach(function (input, i) { input.checked = i === rank; });
        cost.textContent = rank ? bp(R.ranks.costs[rank]) : '';
      });
      return el('div', { class: 'rank-row' }, [el('span', { class: 'rank-name chip-' + status }, [icon(status), E.capitalize(status)]), group, cost]);
    });
    var tail = el('p', { class: 'note' });
    syncs.push(function (r) {
      var cost = r.report.breakdown['Status ranks'] || 0;
      tail.textContent = cost ? 'Ranks cost ' + bp(cost) + ' in total.' : '';
    });
    return section('Status ranks', 'Own up to ' + R.ranks.maxStatuses + '. A skill can only hand out a status you have a rank in, and the rank sets how strong it can be.', [
      el('div', { class: 'ranks' }, rows), tail
    ]);
  }

  function buildDefense() {
    var items = Object.keys(R.defense.archetypes).sort(function (a, b) { return R.defense.archetypes[a] - R.defense.archetypes[b]; }).map(function (key) {
      var box = el('input', { type: 'checkbox', id: nextId('d') });
      box.checked = state.archetypes.indexOf(key) >= 0;
      box.addEventListener('change', function () {
        state.archetypes = state.archetypes.filter(function (k) { return k !== key; });
        if (box.checked) { state.archetypes.push(key); }
        update();
      });
      syncs.push(function () { box.checked = state.archetypes.indexOf(key) >= 0; });
      var needs = [];
      if (R.defense.needsSpeed.indexOf(key) >= 0) { needs.push('lowest Speed ' + R.defense.minSpeed + ' or more'); }
      if (R.defense.needsNoTier2.indexOf(key) >= 0) { needs.push('Stagger Tier 2 removed'); }
      return el('label', { class: 'check wide', for: box.id }, [
        box,
        el('span', {}, [
          el('strong', { text: E.title(key) + ' (' + bp(R.defense.archetypes[key]) + ')' }),
          needs.length ? el('span', { class: 'sub', text: 'Needs ' + needs.join(' and ') }) : null
        ])
      ]);
    });
    return section('Defense types', 'Own up to ' + R.defense.max + '. A skill tagged Guard, Evade or Counter needs the matching type.', [
      el('div', { class: 'checks' }, items)
    ]);
  }

  function buildTags() {
    var inputs = [];
    for (var i = 0; i < R.tags.max; i++) {
      (function (i) {
        var input = el('input', { type: 'text', maxlength: String(R.tags.maxLength), autocomplete: 'off', placeholder: i === 0 ? 'N Corp' : '' });
        input.value = state.tags[i] || '';
        input.addEventListener('input', function () {
          var list = inputs.map(function (x) { return x.value; });
          state.tags = list;
          update();
        });
        inputs.push(input);
      })(i);
    }
    state.tags = inputs.map(function (x) { return x.value; });
    return section('Group tags', 'Short labels for the groups your character belongs to. Passives can count them. Up to ' + R.tags.max + '.', [
      el('div', { class: 'grid three' }, inputs.map(function (input, i) { return field('Tag ' + (i + 1), input); }))
    ]);
  }


  // ---- the rule-line picker ----------------------------------------------------------------------------------
  // A rule line is picked from lists: when it fires, an optional condition, what it does, and a few extras. lines.js turns the
  // choices into the one line of text the bot reads and says why a combination does not work. The lines still live in the
  // same text the bot gets (a skill's trigger text, a passive's lines), so typing them stays possible.

  var LN = window.LugicLines;

  function plainSelect(options, value, onChange) {
    var select = el('select');
    options.forEach(function (o) {
      if (o.group) {
        var group = el('optgroup', { label: o.group });
        o.items.forEach(function (item) { group.appendChild(el('option', { value: item.value, text: item.label })); });
        select.appendChild(group);
      } else { select.appendChild(el('option', { value: o.value, text: o.label })); }
    });
    select.value = String(value);
    select.addEventListener('change', function () { onChange(select.value); });
    return select;
  }

  var DEFENSE_FLAGS = ['guard', 'evade', 'counter', 'clashable_guard', 'clashable_counter', 'supermove'];
  // The names of the skills a follow-up attack may use: the ones that are not defense skills or Supermoves.
  function attackNames() {
    var seen = {};
    return state.skills.filter(function (s) {
      var name = String(s.name || '').trim();
      if (!name || seen[name.toLowerCase()]) { return false; }
      seen[name.toLowerCase()] = true;
      return E.readTriggers(s.triggers).flags.every(function (f) { return DEFENSE_FLAGS.indexOf(f) < 0; });
    }).map(function (s) { return String(s.name).trim(); });
  }
  function groupTags() { return state.tags.map(function (t) { return String(t).trim(); }).filter(Boolean); }

  function lineTokens(line) {
    var code = el('code', { class: 'rule-text' });
    var m = /^:Coin\s*(\d+):\s*(.*)$/i.exec(line);
    if (m) { code.appendChild(el('span', { class: 'rule-coin', text: 'Coin ' + m[1] })); }
    window.LugicPreview.renderTokens(window.LugicPreview.tokenize(m ? m[2] : line), code);
    return code;
  }

  // opts: passive (bool), maxLines (number or null), read() -> the lines, write(lines), names() -> attack skill names, tags() -> group tags.
  function ruleEditor(opts) {
    var wrap = el('div', { class: 'rules' });
    var list = el('ol', { class: 'rule-list' });
    var panel = el('details', { class: 'rule-panel' });
    var summary = el('summary', { text: 'Add a rule line' });
    var body = el('div', { class: 'rule-body' });
    var pick = newPick();
    panel.addEventListener('toggle', function () { if (panel.open) { drawPanel(); } });
    panel.appendChild(summary);
    panel.appendChild(body);
    wrap.appendChild(list);
    wrap.appendChild(panel);

    function fresh(params) {
      var values = LN.defaults(params);
      params.forEach(function (p) {
        if (p.kind === 'skill') { values[p.name] = opts.names()[0] || ''; }
        if (p.kind === 'tag') { values[p.name] = opts.tags()[0] || ''; }
      });
      return values;
    }
    function newPick() {
      var p = LN.blank(opts.passive ? 'Combat Start' : 'On Use', opts.passive ? 'gain_bare' : 'coin_power', '');
      p.effValues = fresh(LN.effect(p.effect).params);
      return p;
    }

    function timingOptions() {
      var groups = LN.spec.groups.map(function (g) {
        var items = LN.spec.timings.filter(function (t) { return t.group === g.id && (opts.passive ? t.passive : t.skill); })
          .map(function (t) { return { value: t.key, label: t.key }; });
        return { group: g.label, items: items };
      });
      return groups.filter(function (g) { return g.items.length; });
    }
    function conditionOptions() {
      return [{ value: '', label: 'Always (no condition)' }].concat(LN.spec.conditions.map(function (c) { return { value: c.id, label: c.label }; }));
    }
    function effectOptions() {
      var names = [];
      var by = {};
      LN.spec.effects.forEach(function (e) {
        if (!by[e.group]) { by[e.group] = []; names.push(e.group); }
        by[e.group].push({ value: e.id, label: e.label });
      });
      return names.map(function (g) { return { group: g, items: by[g] }; });
    }

    // One input for one parameter. `values` is the object the pick keeps its numbers and words in.
    function paramControl(p, values, changed) {
      var control;
      if (p.kind === 'number') {
        control = el('input', { type: 'number', inputmode: 'numeric', min: String(p.min), max: String(p.max), step: '1' });
        control.value = String(values[p.name]);
        control.addEventListener('input', function () { values[p.name] = control.value === '' ? '' : Number(control.value); changed(); });
      } else if (p.kind === 'choice') {
        control = plainSelect(p.options, values[p.name], function (v) { values[p.name] = v; changed(); });
      } else if (p.kind === 'skill' && opts.names().length) {
        if (opts.names().indexOf(values[p.name]) < 0) { values[p.name] = opts.names()[0]; }
        control = plainSelect(opts.names().map(function (n) { return { value: n, label: n }; }), values[p.name], function (v) { values[p.name] = v; changed(); });
      } else {
        control = el('input', { type: 'text', autocomplete: 'off', maxlength: String(p.kind === 'tag' ? R.tags.maxLength : R.limits.skillName) });
        control.value = values[p.name] || '';
        if (p.kind === 'tag' && opts.tags().length) {
          var listId = nextId('tags');
          var datalist = el('datalist', { id: listId }, opts.tags().map(function (t) { return el('option', { value: t }); }));
          control.setAttribute('list', listId);
          control.addEventListener('input', function () { values[p.name] = control.value; changed(); });
          return el('label', { class: 'mini' }, [p.label, control, datalist]);
        }
        control.addEventListener('input', function () { values[p.name] = control.value; changed(); });
      }
      return el('label', { class: 'mini' }, [p.label, control]);
    }

    function step(number, title, kids, help) {
      return el('div', { class: 'rule-step' }, [
        el('div', { class: 'rule-step-head' }, [el('span', { class: 'rule-no', text: number }), el('span', { class: 'rule-title', text: title })]),
        el('div', { class: 'rule-fields' }, kids),
        help ? el('p', { class: 'note', text: help }) : null
      ]);
    }

    var previewCode = el('code', { class: 'rule-preview' });
    var problemText = el('p', { class: 'rule-problem', role: 'status' });
    var addButton = el('button', { type: 'button', class: 'btn primary', text: 'Add this line' });
    var fullNote = el('p', { class: 'note' });

    function paintResult() {
      var why = LN.problem(pick, opts.passive);
      var lines = opts.read();
      var full = opts.maxLines && lines.length >= opts.maxLines;
      previewCode.textContent = '';
      problemText.textContent = why || '';
      problemText.hidden = !why;
      if (!why) { previewCode.appendChild(lineTokens(LN.compose(pick))); fillIcons(); }
      else { previewCode.appendChild(el('span', { class: 'pv-none', text: 'The line shows here once the choices fit together.' })); }
      fullNote.textContent = full ? 'This passive already has ' + opts.maxLines + ' lines, which is the most it can hold. Remove one first.' : '';
      addButton.disabled = !!why || !!full;
    }

    function drawPanel() {
      body.textContent = '';
      var t = LN.timing(pick.timing);
      var c = LN.condition(pick.cond);
      var e = LN.effect(pick.effect);

      var when = [plainSelect(timingOptions(), pick.timing, function (v) { pick.timing = v; drawPanel(); })];
      if (t.perCoin) {
        var coinChoices = [];
        for (var n = 1; n <= R.skills.maxCoins; n++) { coinChoices.push({ value: String(n), label: 'Coin ' + n }); }
        when.push(plainSelect(coinChoices, String(pick.coin), function (v) { pick.coin = parseInt(v, 10); paintResult(); }));
      }
      body.appendChild(step('1', 'When does it fire?', when, t.help));

      var ifKids = [plainSelect(conditionOptions(), pick.cond, function (v) {
        pick.cond = v;
        pick.condValues = v ? fresh(LN.condition(v).params) : {};
        drawPanel();
      })];
      if (c) { c.params.forEach(function (p) { ifKids.push(paramControl(p, pick.condValues, paintResult)); }); }
      body.appendChild(step('2', 'Only if... (optional)', ifKids));

      var thenKids = [plainSelect(effectOptions(), pick.effect, function (v) {
        var next = LN.effect(v);
        pick.effect = v;
        pick.effValues = fresh(next.params);
        if (!next.ally) { pick.target = ''; }
        if (!(next.post && !next.followup && !next.assist)) { pick.next = false; }
        drawPanel();
      })];
      e.params.forEach(function (p) { thenKids.push(paramControl(p, pick.effValues, paintResult)); });
      if (e.ally) {
        thenKids.push(el('label', { class: 'mini' }, ['Aimed at', plainSelect(LN.spec.targets.map(function (x) { return { value: x.value, label: x.label }; }), pick.target, function (v) {
          pick.target = v; drawPanel();
        })]));
        if (pick.target.indexOf('{selector}') >= 0) {
          thenKids.push(el('label', { class: 'mini' }, ['Which ally', plainSelect(LN.spec.selectors.map(function (x) { return { value: x, label: x }; }), pick.selector, function (v) { pick.selector = v; paintResult(); })]));
        }
      }
      body.appendChild(step('3', 'What does it do?', thenKids));

      var extras = [];
      if (e.post && !e.followup && !e.assist) {
        var nextBox = el('input', { type: 'checkbox' });
        nextBox.checked = !!pick.next;
        nextBox.addEventListener('change', function () { pick.next = nextBox.checked; paintResult(); });
        extras.push(el('label', { class: 'check' }, [nextBox, ' Wait until next turn']));
      }
      extras.push(el('label', { class: 'mini' }, ['How often', plainSelect(LN.spec.limits, pick.limit, function (v) { pick.limit = v; paintResult(); })]));
      var chance = el('input', { type: 'number', inputmode: 'numeric', min: String(LN.spec.chance.min), max: String(LN.spec.chance.max), step: '1', placeholder: 'Always' });
      chance.value = pick.chance ? String(pick.chance) : '';
      chance.addEventListener('input', function () { pick.chance = chance.value === '' ? 0 : Number(chance.value); paintResult(); });
      extras.push(el('label', { class: 'mini' }, ['Chance (%)', chance]));
      body.appendChild(step('4', 'Extras (optional)', extras));

      body.appendChild(el('div', { class: 'rule-result' }, [
        el('span', { class: 'quick-label', text: 'The line' }), previewCode, problemText, fullNote,
        el('div', { class: 'row' }, [addButton])
      ]));
      paintResult();
    }

    addButton.addEventListener('click', function () {
      if (LN.problem(pick, opts.passive)) { return; }
      var lines = opts.read();
      if (opts.maxLines && lines.length >= opts.maxLines) { return; }
      lines.push(LN.compose(pick));
      opts.write(lines);
    });

    function refresh() {
      var lines = opts.read();
      list.textContent = '';
      if (!lines.length) { list.appendChild(el('li', { class: 'rule-empty', text: opts.passive ? 'No rule lines yet. A passive needs at least one.' : 'No rule lines yet. A skill works without any.' })); }
      lines.forEach(function (line, i) {
        var move = function (by) {
          var all = opts.read();
          var j = i + by;
          if (j < 0 || j >= all.length) { return; }
          var held = all[i]; all[i] = all[j]; all[j] = held;
          opts.write(all);
        };
        list.appendChild(el('li', { class: 'rule-row' }, [
          lineTokens(line),
          el('span', { class: 'rule-tools' }, [
            el('button', { type: 'button', class: 'tagbtn', text: 'Up', 'aria-label': 'Move line ' + (i + 1) + ' up', disabled: i === 0, onclick: function () { move(-1); } }),
            el('button', { type: 'button', class: 'tagbtn', text: 'Down', 'aria-label': 'Move line ' + (i + 1) + ' down', disabled: i === lines.length - 1, onclick: function () { move(1); } }),
            el('button', { type: 'button', class: 'tagbtn', text: 'Remove', 'aria-label': 'Remove line ' + (i + 1), onclick: function () {
              var all = opts.read(); all.splice(i, 1); opts.write(all);
            } })
          ])
        ]));
      });
      fillIcons();
      paintResult();
    }

    drawPanel();
    refresh();
    return { node: wrap, refresh: refresh };
  }

  // ---- passives ---------------------------------------------------------------------------------------

  function buildPassives() {
    var list = el('div', { class: 'stack' });
    var summary = el('p', { class: 'note' });
    var local = [];          // repainters for the cards that are on the page right now
    var add = el('button', { type: 'button', class: 'btn', text: 'Add a passive' });
    var example = selectInput(
      [{ value: '', label: 'Start from an example' }].concat(R.passives.templates.map(function (t, i) { return { value: String(i), label: t.name }; })),
      '', function (v) {
        if (v === '') { return; }
        var t = R.passives.templates[parseInt(v, 10)];
        if (state.passives.length < R.passives.max) {
          state.passives.push({ name: t.name, kind: t.kind, cost: t.cost, lines: t.lines.slice() });
          renderPassives();
        }
        example.value = '';
      });
    example.setAttribute('aria-label', 'Start from an example');
    add.addEventListener('click', function () {
      if (state.passives.length >= R.passives.max) { return; }
      state.passives.push({ name: '', kind: 'passive', cost: 1, lines: [''] });
      renderPassives();
    });

    function renderPassives() {
      list.textContent = '';
      local.length = 0;
      state.passives.forEach(function (p, index) {
        var issues = issueList();
        var name = el('input', { type: 'text', maxlength: String(R.limits.passiveName), autocomplete: 'off' });
        name.value = p.name;
        name.addEventListener('input', function () { p.name = name.value; update(); });
        var kind = selectInput([{ value: 'passive', label: 'Passive' }, { value: 'support', label: 'Support passive' }], p.kind, function (v) { p.kind = v; });
        var cost = numberInput(p.cost, 1, R.passives.costMax, function (n) { p.cost = n; });
        var rules = null;
        var lines = el('textarea', { rows: '3', spellcheck: 'false', placeholder: '[Combat Start] Gain 2 Protection' });
        lines.value = p.lines.join('\n');
        lines.addEventListener('input', function () { p.lines = lines.value.split('\n'); if (rules) { rules.refresh(); } update(); });
        rules = ruleEditor({
          passive: true, maxLines: R.passives.maxLines, names: attackNames, tags: groupTags,
          read: function () { return p.lines.map(function (l) { return l.trim(); }).filter(Boolean); },
          write: function (written) { p.lines = written.length ? written : ['']; lines.value = written.join('\n'); rules.refresh(); update(); }
        });
        var remove = el('button', { type: 'button', class: 'btn quiet', text: 'Remove', onclick: function () {
          state.passives.splice(index, 1); renderPassives(); update();
        } });
        local.push(function (r) { paintIssues(issues, r.perPassive[index] || []); });
        list.appendChild(el('article', { class: 'item' }, [
          el('div', { class: 'grid passive-head' }, [field('Name', name), field('Kind', kind), field('Price (PP)', cost, 'An admin can change it.')]),
          el('div', { class: 'field' }, [
            el('span', { class: 'label-like', text: 'Rule lines (up to ' + R.passives.maxLines + ')' }),
            rules.node,
            el('details', { class: 'rule-text-mode' }, [
              el('summary', { text: 'Type or paste the lines as text instead' }),
              field('Rule lines, one per line', lines, 'The bot checks the wording when you import.')
            ])
          ]),
          issues,
          el('div', { class: 'row' }, [remove])
        ]));
      });
      add.disabled = state.passives.length >= R.passives.max;
      example.disabled = add.disabled;
      update();
    }
    syncs.push(function (r) {
      local.forEach(function (fn) { fn(r); });
      var used = r.report.ppUsed;
      summary.textContent = state.passives.length
        ? 'Passives ask for ' + used + ' Passive Points; you have ' + state.pools.pp + '.' + (r.report.ppShort > 0 ? ' Admins give those out, so they add them before they approve the build.' : '')
        : '';
    });
    var node = section('Passives', 'Rules that are always on, like a start-of-fight buff. Up to ' + R.passives.max + '. They cost Passive Points, which only admins give out, so a build with passives waits for an admin to add the points.', [
      list,
      el('div', { class: 'row' }, [add, example]),
      summary
    ]);
    renderPassives();
    return node;
  }

  // ---- skills ------------------------------------------------------------------------------------------

  function statusOptions() {
    var ranked = R.ranks.statuses;
    var oneTurn = R.nonKeyword.oneTurn;
    function items(list) { return list.map(function (s) { return { value: s, label: E.title(s) }; }); }
    return [
      { value: '', label: 'No status' },
      { group: 'Needs a rank', items: items(ranked) },
      { group: 'Effects that cost Build Points', items: items(oneTurn) },
      { group: 'Other', items: items(R.otherStatuses) }
    ];
  }

  // ---- a picture inside the skill's frame -------------------------------------------------------------------
  // The picture is shrunk in the browser, kept on this device only, and is not part of the code.

  var PICTURE_SIDE = 640;       // longest side after shrinking
  var PICTURE_LIMIT = 20 * 1024 * 1024;

  function shrinkPicture(file) {
    return new Promise(function (resolve, reject) {
      if (!file || !/^image\//.test(file.type)) { reject(new Error('That is not a picture file.')); return; }
      if (file.size > PICTURE_LIMIT) { reject(new Error('That picture is too big. Pick one under 20 MB.')); return; }
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () {
        URL.revokeObjectURL(url);
        var w = img.naturalWidth, h = img.naturalHeight;
        if (!w || !h) { reject(new Error('The browser could not read that picture.')); return; }
        var scale = Math.min(1, PICTURE_SIDE / Math.max(w, h));
        var canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(w * scale));
        canvas.height = Math.max(1, Math.round(h * scale));
        var ctx = canvas.getContext('2d');
        ctx.fillStyle = '#050506';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        resolve({ src: canvas.toDataURL('image/jpeg', 0.86), a: canvas.width / canvas.height, x: 0, y: 0, zoom: 1 });
      };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error('The browser could not read that picture.')); };
      img.src = url;
    });
  }

  function pictureControls(skill, changed) {
    var P = window.LugicPreview;
    var input = el('input', { type: 'file', accept: 'image/*', id: nextId('pic') });
    var pick = el('label', { class: 'btn', for: input.id, text: skill.art ? 'Change picture' : 'Add a picture' });
    var adjust = el('button', { type: 'button', class: 'btn quiet', text: 'Adjust' });
    var remove = el('button', { type: 'button', class: 'btn quiet', text: 'Remove picture' });
    var msg = el('span', { class: 'msg bad', role: 'status' });
    var editor = el('div', { class: 'art-editor', hidden: true });
    var row = el('div', { class: 'art-row' }, [input, pick, adjust, remove, msg]);
    var note = el('p', { class: 'note', text: 'The picture shows inside the skill\'s frame. It stays on this device and is not part of the code, so the bot never sees it.' });

    function sync() {
      pick.textContent = skill.art ? 'Change picture' : 'Add a picture';
      adjust.hidden = remove.hidden = !skill.art;
      if (!skill.art) { editor.hidden = true; editor.textContent = ''; }
    }

    function openEditor() {
      var art = skill.art;
      if (!art) { return; }
      editor.textContent = '';
      editor.hidden = false;
      var model = P.model(skill);
      var stage = el('div', { class: 'art-stage', tabindex: '0', role: 'group', 'aria-label': 'Drag to move the picture. Arrow keys move it, plus and minus zoom.' });
      var big = P.emblem(model.frame, art, model.type);
      big.classList.add('big');
      stage.appendChild(big);
      var zoom = el('input', { type: 'range', min: '1', max: String(P.maxZoom), step: '0.01', 'aria-label': 'Zoom' });
      zoom.value = String(art.zoom);
      var done = el('button', { type: 'button', class: 'btn primary', text: 'Done' });
      var reset = el('button', { type: 'button', class: 'btn quiet', text: 'Reset' });

      function paint() {
        var c = P.clampArt(art);
        art.x = c.x; art.y = c.y; art.zoom = c.zoom;
        var img = big.querySelector('.pv-art img');
        if (img) { P.applyArt(img, art); }
        zoom.value = String(art.zoom);
        changed();
      }
      // dragging: the opening is 66% of the frame canvas, which is 132% of the emblem
      var drag = null;
      stage.addEventListener('pointerdown', function (e) {
        drag = { x: e.clientX, y: e.clientY };
        try { stage.setPointerCapture(e.pointerId); } catch (err) { /* older browsers */ }
        e.preventDefault();
      });
      stage.addEventListener('pointermove', function (e) {
        if (!drag) { return; }
        var open = big.getBoundingClientRect().width * 1.32 * 0.66;
        art.x += (e.clientX - drag.x) / open;
        art.y += (e.clientY - drag.y) / open;
        drag = { x: e.clientX, y: e.clientY };
        paint();
      });
      function stop() { drag = null; }
      stage.addEventListener('pointerup', stop);
      stage.addEventListener('pointercancel', stop);
      stage.addEventListener('wheel', function (e) {
        e.preventDefault();
        art.zoom += e.deltaY < 0 ? 0.08 : -0.08;
        paint();
      }, { passive: false });
      stage.addEventListener('keydown', function (e) {
        var step = 0.02, handled = true;
        if (e.key === 'ArrowLeft') { art.x -= step; } else if (e.key === 'ArrowRight') { art.x += step; }
        else if (e.key === 'ArrowUp') { art.y -= step; } else if (e.key === 'ArrowDown') { art.y += step; }
        else if (e.key === '+' || e.key === '=') { art.zoom += 0.1; } else if (e.key === '-') { art.zoom -= 0.1; }
        else { handled = false; }
        if (handled) { e.preventDefault(); paint(); }
      });
      zoom.addEventListener('input', function () { art.zoom = parseFloat(zoom.value); paint(); });
      reset.addEventListener('click', function () { art.x = 0; art.y = 0; art.zoom = 1; paint(); });
      done.addEventListener('click', function () { editor.hidden = true; editor.textContent = ''; });
      editor.appendChild(stage);
      editor.appendChild(el('div', { class: 'art-controls' }, [
        el('p', { class: 'note', text: 'Drag the picture to move it. Scroll or use the slider to zoom.' }),
        el('label', {}, ['Zoom', zoom]),
        el('div', { class: 'row' }, [done, reset])
      ]));
      fillIcons();
      stage.focus({ preventScroll: true });
    }

    input.addEventListener('change', function () {
      msg.textContent = '';
      var file = input.files && input.files[0];
      input.value = '';
      if (!file) { return; }
      shrinkPicture(file).then(function (art) {
        skill.art = art;
        sync();
        changed();
        openEditor();
      }).catch(function (err) { msg.textContent = err.message; });
    });
    adjust.addEventListener('click', function () { if (editor.hidden) { openEditor(); } else { editor.hidden = true; editor.textContent = ''; } });
    remove.addEventListener('click', function () { delete skill.art; sync(); changed(); });
    sync();
    return el('div', { class: 'stack tight' }, [row, editor, note]);
  }

  function buildSkills() {
    var list = el('div', { class: 'stack' });
    var add = el('button', { type: 'button', class: 'btn', text: 'Add a skill' });
    var summary = el('p', { class: 'note' });
    var local = [];          // repainters for the cards that are on the page right now
    add.addEventListener('click', function () {
      if (state.skills.length >= R.skills.max) { return; }
      state.skills.push(E.newSkill(state.skills.length));
      renderSkills();
    });

    function renderSkills() {
      list.textContent = '';
      local.length = 0;
      state.skills.forEach(function (skill, index) { list.appendChild(skillCard(skill, index)); });
      add.disabled = state.skills.length >= R.skills.max;
      update();
    }

    function skillCard(skill, index) {
      var issues = issueList();
      var name = el('input', { type: 'text', maxlength: String(R.limits.skillName), autocomplete: 'off' });
      name.value = skill.name;
      name.addEventListener('input', function () { skill.name = name.value; update(); });
      var base = numberInput(skill.base, 0, null, function (n) { skill.base = n; });
      var coinPower = numberInput(skill.coin, 0, null, function (n) { skill.coin = n; });
      var coinOptions = [];
      for (var c = 1; c <= R.skills.maxCoins; c++) { coinOptions.push({ value: String(c), label: String(c) }); }
      var coins = selectInput(coinOptions, skill.coins, function (v) {
        skill.coins = parseInt(v, 10);
        while (skill.statuses.length < skill.coins) { skill.statuses.push(E.emptyCoin()); }
        renderSkills();
      });
      var type = selectInput(R.skills.damageTypes.map(function (t) { return { value: t, label: E.capitalize(t) }; }), skill.type, function (v) { skill.type = v; });

      var coinRows = [];
      for (var i = 0; i < skill.coins; i++) {
        (function (i) {
          if (!skill.statuses[i]) { skill.statuses[i] = E.emptyCoin(); }
          var coin = skill.statuses[i];
          var pot = numberInput(coin.pot, 0, null, function (n) { coin.pot = n; });
          var cnt = numberInput(coin.cnt, 0, null, function (n) { coin.cnt = n; });
          pot.setAttribute('aria-label', 'Coin ' + (i + 1) + ' potency');
          cnt.setAttribute('aria-label', 'Coin ' + (i + 1) + ' count');
          var status = selectInput(statusOptions(), coin.name, function (v) {
            coin.name = v;
            if (v && !coin.pot && !coin.cnt) { coin.pot = 1; coin.cnt = 1; pot.value = '1'; cnt.value = '1'; }
            pot.disabled = cnt.disabled = !v;
          });
          status.setAttribute('aria-label', 'Coin ' + (i + 1) + ' status');
          pot.disabled = cnt.disabled = !coin.name;
          coinRows.push(el('div', { class: 'coin-row' }, [
            el('span', { class: 'coin-no', text: 'Coin ' + (i + 1) }),
            status,
            el('label', { class: 'mini' }, ['Potency', pot]),
            el('label', { class: 'mini' }, ['Count', cnt])
          ]));
        })(i);
      }

      var rules = null;
      var triggers = el('textarea', { rows: '3', spellcheck: 'false', placeholder: '[On Use] Coin Power +1' });
      triggers.value = skill.triggers;
      triggers.addEventListener('input', function () { skill.triggers = triggers.value; syncFlags(); update(); });

      function addLine(line) {
        var text = triggers.value.replace(/\s+$/, '');
        triggers.value = (text ? text + '\n' : '') + line;
        skill.triggers = triggers.value;
        syncFlags();
        update();
      }
      var flagButtons = R.skills.flagButtons.map(function (label) {
        var line = '[' + label + ']';
        var button = el('button', { type: 'button', class: 'tagbtn', 'aria-pressed': 'false', text: line });
        button.addEventListener('click', function () {
          var lines = triggers.value.split('\n');
          while (lines.length && !lines[lines.length - 1].trim()) { lines.pop(); }
          var at = lines.findIndex(function (l) { return l.trim().toLowerCase() === line.toLowerCase(); });
          if (at >= 0) { lines.splice(at, 1); } else { lines.push(line); }
          triggers.value = lines.join('\n');
          skill.triggers = triggers.value;
          syncFlags();
          update();
        });
        return { label: label, line: line, button: button };
      });
      function syncFlags() {
        var present = triggers.value.split('\n').map(function (l) { return l.trim().toLowerCase(); });
        flagButtons.forEach(function (f) { f.button.setAttribute('aria-pressed', present.indexOf(f.line.toLowerCase()) >= 0 ? 'true' : 'false'); });
        if (rules) { rules.refresh(); }
      }
      rules = ruleEditor({
        passive: false, maxLines: null, names: attackNames, tags: groupTags,
        read: function () { return triggers.value.split('\n').map(function (l) { return l.trim(); }).filter(Boolean); },
        write: function (lines) { triggers.value = lines.join('\n'); skill.triggers = triggers.value; syncFlags(); update(); }
      });
      syncFlags();
      var lineButtons = R.skills.lineTemplates.map(function (t) {
        return el('button', { type: 'button', class: 'tagbtn line', text: t.label, title: t.line, onclick: function () { addLine(t.line); } });
      });
      var spNote = el('p', { class: 'note' });
      var preview = el('div', { class: 'pv-wrap', 'aria-label': 'How the skill will look' });
      var picture = pictureControls(skill, function () { paintPreview(); queueSave(); });
      function paintPreview() {
        preview.textContent = '';
        preview.appendChild(window.LugicPreview.render(skill, index));
        fillIcons();
      }

      local.push(function (r) {
        paintPreview();
        paintIssues(issues, r.perSkill[index] || []);
        var read = E.readTriggers(skill.triggers);
        spNote.textContent = read.lines.length > 1
          ? read.lines.length + ' trigger lines: the first is free, the other ' + (read.lines.length - 1) + ' cost ' + (read.lines.length - 1) * R.sp.perExtraLine + ' Skill Point' + (read.lines.length - 1 > 1 ? 's' : '') + '.'
          : '';
      });

      return el('article', { class: 'item' }, [
        el('div', { class: 'item-head' }, [
          field('Skill name', name),
          el('div', { class: 'row' }, [
            el('button', { type: 'button', class: 'btn quiet', text: 'Copy', title: 'Make a copy of this skill', disabled: state.skills.length >= R.skills.max,
              onclick: function () {
                if (state.skills.length >= R.skills.max) { return; }
                var copy = JSON.parse(JSON.stringify(skill));
                copy.name = (skill.name + ' copy').slice(0, R.limits.skillName);
                state.skills.splice(index + 1, 0, copy);
                renderSkills();
              } }),
            el('button', { type: 'button', class: 'btn quiet', text: 'Remove', onclick: function () { state.skills.splice(index, 1); renderSkills(); } })
          ])
        ]),
        preview,
        picture,
        el('div', { class: 'grid four' }, [field('Base Power', base), field('Coin Power', coinPower), field('Coins', coins), field('Damage type', type)]),
        el('div', { class: 'coin-rows' }, coinRows),
        el('div', { class: 'field' }, [
          el('span', { class: 'label-like', text: 'Rule lines' }),
          rules.node,
          el('details', { class: 'rule-text-mode' }, [
            el('summary', { text: 'Type or paste the lines as text instead' }),
            field('Trigger lines', triggers, 'One per line. The bot checks the wording when you import.')
          ])
        ]),
        el('div', { class: 'quick' }, [
          el('span', { class: 'quick-label', text: 'Example lines' }), el('div', { class: 'quick-set' }, lineButtons),
          el('span', { class: 'quick-label', text: 'Skill tags' }), el('div', { class: 'quick-set' }, flagButtons.map(function (f) { return f.button; }))
        ]),
        spNote,
        issues
      ]);
    }

    syncs.push(function (r) {
      local.forEach(function (fn) { fn(r); });
      summary.textContent = state.skills.length ? state.skills.length + ' of ' + R.skills.max + ' skills.' : '';
    });
    var example = selectInput(
      [{ value: '', label: 'Add from an example' }].concat(R.skills.examples.map(function (t, i) { return { value: String(i), label: t.name + ' (' + t.blurb + ')' }; })),
      '', function (v) {
        if (v === '' || state.skills.length >= R.skills.max) { example.value = ''; return; }
        var t = R.skills.examples[parseInt(v, 10)];
        var s = E.newSkill(state.skills.length);
        s.name = t.name; s.base = t.base; s.coin = t.coin; s.coins = t.coins; s.type = t.type; s.triggers = t.triggers;
        s.statuses = [];
        for (var i = 0; i < t.coins; i++) { s.statuses.push(E.emptyCoin()); }
        state.skills.push(s);
        example.value = '';
        renderSkills();
      });
    example.setAttribute('aria-label', 'Add a skill from an example');
    var node = section('Skills', 'Up to ' + R.skills.max + '. Each skill shows a live preview the way it reads on the wiki. Skills are yours to add, so a build that only has skills needs no admin. Coin Power and Base Power are capped by your Power.', [
      list, el('div', { class: 'row' }, [add, example]), summary
    ]);
    renderSkills();
    return node;
  }

  // ---- the status list and colour key -------------------------------------------------------------------------------

  // **bold** and `code` only; every piece goes in as text, never as markup.
  function richText(text) {
    var frag = document.createDocumentFragment();
    String(text).split(/\n\n+/).forEach(function (para) {
      var p = el('p', { class: 'ref-p' });
      para.split(/(\*\*[^*]+\*\*|`[^`]+`)/).forEach(function (bit) {
        if (!bit) { return; }
        if (bit.slice(0, 2) === '**') { p.appendChild(el('strong', { text: bit.slice(2, -2) })); }
        else if (bit.charAt(0) === '`') { p.appendChild(el('code', { text: bit.slice(1, -1) })); }
        else { p.appendChild(document.createTextNode(bit)); }
      });
      frag.appendChild(p);
    });
    return frag;
  }

  // The status list reads the same way for every status: WHEN it acts, what it DOES, THEN what happens to it. Colour says the rest
  // (see colourKey). The bot's full wording sits in one fold-out at the end, so the page itself stays short.
  function buildReference() {
    var byKey = {};
    R.statusGuide.forEach(function (s) { byKey[s.key] = s; });
    var paired = {};
    R.statusPairs.forEach(function (p) { [p.up, p.down].concat(p.downExtra || []).forEach(function (k) { paired[k] = true; }); });
    var keywords = R.statusGuide.filter(function (s) { return s.group === 'keyword'; });
    var others = R.statusGuide.filter(function (s) { return s.group !== 'keyword' && !paired[s.key]; });

    var kids = [
      colourKey(),
      el('p', { class: 'st-rule', text: R.potencyCount }),
      el('h3', { text: 'Keyword statuses' }),
      statusTable(keywords),
      el('h3', { text: 'Up and down, this turn only' }),
      el('p', { class: 'note', text: 'Each row is one thing that can go up (yellow) or down (red). Everything here is gone at turn end.' }),
      pairTable(byKey),
      el('h3', { text: 'Other statuses' }),
      statusTable(others),
      fullRules(R.statusGuide.filter(function (s) { return !/_fragility$/.test(s.key); }))
    ];
    return section('Status effects', 'Every status is read the same way: when it acts, what it does, then what happens to it.', kids);
  }

  function colourKey() {
    var key = R.colorKey;
    var tones = key.tones.map(function (t) {
      return el('li', { class: 'ck-item tone-' + t.tone }, [
        el('i', { class: 'ck-swatch', 'aria-hidden': 'true' }),
        el('span', {}, [el('b', { text: t.color }), ' ' + t.means]),
        el('span', { class: 'ck-hint', text: t.hint })
      ]);
    });
    var sins = key.sins.map(function (s) {
      return el('li', { class: 'ck-item pv-sin-' + s.sin }, [
        el('i', { class: 'ck-swatch', 'aria-hidden': 'true' }),
        el('span', {}, [icon('sin_' + s.sin), el('b', { text: s.color }), ' ', icon(s.status), E.capitalize(s.status)]),
        el('span', { class: 'ck-hint', text: s.hint })
      ]);
    });
    return el('div', { class: 'legend' }, [
      el('h3', { text: 'What the colours say' }),
      el('p', { class: 'note', text: 'The name of a status is coloured by what it does to you.' }),
      el('ul', { class: 'ck-list' }, tones),
      el('p', { class: 'note', text: 'The small chip beside a keyword status is coloured by its Sin, and each colour suits the status.' }),
      el('ul', { class: 'ck-list' }, sins),
      el('details', { class: 'st-more' }, [
        el('summary', { text: 'Colours in the skill preview and resistances' }),
        el('ul', { class: 'legend-list' }, [
          legendItem('pv-tag pv-use', '[On Use]', 'when the skill is used'),
          legendItem('pv-tag pv-hit', '[On Hit]', 'every other timing'),
          legendItem('pv-tag pv-clashwin', '[Clash Win]', 'the clash was won'),
          legendItem('pv-tag pv-clashlose', '[Clash Lose]', 'the clash was lost'),
          legendItem('pv-tag pv-flag', '[Counter]', 'a skill type tag, such as Guard or Evade'),
          legendItem('res-tag res-fatal', 'Fatal x1.5 or more', 'takes far more damage'),
          legendItem('res-tag res-weak', 'Weak x1.25', 'takes more damage'),
          legendItem('res-tag res-normal', 'Normal x1', 'takes the usual damage'),
          legendItem('res-tag res-endure', 'Endure x0.75', 'takes less damage'),
          legendItem('res-tag res-ineffective', 'Ineffective x0.5', 'takes half damage')
        ])
      ])
    ]);
  }

  function legendItem(cls, sample, what) {
    return el('li', {}, [el('span', { class: cls, text: sample }), ' ' + what]);
  }

  function statusName(s) { return el('span', { class: 'st-name tone-' + s.tone }, [icon(s.key), s.title]); }

  // One row per status: name (and Sin chip), then the same three slots every time.
  function statusTable(list) {
    var head = el('tr', {}, ['Status', 'When', 'Does', 'Then'].map(function (h) { return el('th', { scope: 'col', text: h }); }));
    var rows = list.map(function (s) {
      var first = el('th', { scope: 'row', class: 'st-first' }, [statusName(s)]);
      if (s.sin) { first.appendChild(el('span', { class: 'st-sin' }, [icon('sin_' + s.sin), E.capitalize(s.sin)])); }
      return el('tr', { class: 'st-row' + (s.sin ? ' pv-sin-' + s.sin : '') }, [
        first,
        el('td', { 'data-label': 'When', text: s.quick.when }),
        el('td', { 'data-label': 'Does', text: s.quick.does }),
        el('td', { 'data-label': 'Then', text: s.quick.then })
      ]);
    });
    return el('div', { class: 'st-scroll' }, [el('table', { class: 'st-table' }, [el('thead', {}, [head]), el('tbody', {}, rows)])]);
  }

  // Opposites side by side: learn one, and you know the other.
  function pairTable(byKey) {
    var head = el('tr', {}, [
      el('th', { scope: 'col', text: 'Of' }),
      el('th', { scope: 'col', class: 'st-up', text: 'Up' }),
      el('th', { scope: 'col', class: 'st-down', text: 'Down' })
    ]);
    var rows = R.statusPairs.map(function (p) {
      return el('tr', { class: 'st-row' }, [
        el('th', { scope: 'row', class: 'st-first', text: p.axis }),
        pairCell(byKey[p.up], 'Up', []),
        pairCell(byKey[p.down], 'Down', (p.downExtra || []).map(function (k) { return byKey[k]; }))
      ]);
    });
    return el('div', { class: 'st-scroll' }, [el('table', { class: 'st-table st-pairs' }, [el('thead', {}, [head]), el('tbody', {}, rows)])]);
  }

  function pairCell(s, side, extra) {
    var cell = el('td', { 'data-label': side }, [statusName(s), el('span', { class: 'st-does', text: s.quick.does })]);
    if (extra.length) {
      var line = el('span', { class: 'st-extra' }, ['Same, one damage type: ']);
      extra.forEach(function (x, i) { line.appendChild(el('span', { class: 'st-name tone-' + x.tone }, [icon(x.key), x.title.replace(' Fragility', '')])); if (i < extra.length - 1) { line.appendChild(document.createTextNode(' ')); } });
      cell.appendChild(line);
    }
    return cell;
  }

  function fullRules(list) {
    var body = el('div', { class: 'st-full-body' }, list.map(function (s) {
      return el('div', { class: 'st-full-item' }, [el('h4', {}, [statusName(s)]), richText(s.body)]);
    }));
    return el('details', { class: 'st-full' }, [el('summary', { text: 'Full rules, in the same words as /help in Discord' }), body]);
  }

  // ---- the card -----------------------------------------------------------------------------------------

  var cardParts = {};

  function buildCard() {
    var card = document.getElementById('card');
    card.textContent = '';
    var portraitImg = el('img', { alt: '', referrerpolicy: 'no-referrer', hidden: true });
    portraitImg.addEventListener('error', function () { portraitImg.hidden = true; cardParts.initial.hidden = false; cardParts.portrait.classList.add('empty'); });
    portraitImg.addEventListener('load', function () { portraitImg.hidden = false; cardParts.initial.hidden = true; cardParts.portrait.classList.remove('empty'); });
    cardParts.img = portraitImg;
    cardParts.initial = el('span', { class: 'initial', 'aria-hidden': 'true', text: '?' });
    cardParts.level = el('span', { class: 'level' });
    cardParts.name = el('p', { class: 'cardname' });
    cardParts.sub = el('p', { class: 'cardsub' });
    cardParts.coins = el('div', { class: 'coins', 'aria-hidden': 'true' });
    cardParts.bpText = el('p', { class: 'bptext' });
    cardParts.stats = el('dl', { class: 'statlines' });
    cardParts.chips = el('div', { class: 'chips' });
    cardParts.breakdown = el('dl', { class: 'breakdown' });
    cardParts.problems = el('ul', { class: 'problems', 'aria-live': 'polite' });
    cardParts.notes = el('ul', { class: 'notes' });
    cardParts.status = el('p', { class: 'verdict', role: 'status' });
    cardParts.code = el('textarea', { class: 'code', readonly: true, rows: '3', spellcheck: 'false', 'aria-label': 'Your code' });
    cardParts.copy = el('button', { type: 'button', class: 'btn primary', text: 'Copy code' });
    cardParts.copyMsg = el('span', { class: 'msg', role: 'status' });
    cardParts.codeInfo = el('p', { class: 'note' });
    cardParts.steps = el('p', { class: 'steps' });
    cardParts.copy.addEventListener('click', copyCode);
    cardParts.jump = document.getElementById('jump');

    cardParts.portrait = el('div', { class: 'portrait empty' }, [cardParts.initial, portraitImg, cardParts.level]);
    card.appendChild(cardParts.portrait);
    card.appendChild(el('div', { class: 'plate' }, [cardParts.name, cardParts.sub]));
    card.appendChild(el('div', { class: 'budget' }, [cardParts.coins, cardParts.bpText]));
    card.appendChild(cardParts.stats);
    card.appendChild(cardParts.chips);
    card.appendChild(el('details', { class: 'costs' }, [el('summary', { text: 'Where the points go' }), cardParts.breakdown]));
    card.appendChild(el('div', { class: 'verdict-box' }, [cardParts.status, cardParts.problems, cardParts.notes]));
    card.appendChild(el('div', { class: 'export' }, [
      el('div', { class: 'export-head' }, [el('h2', { text: 'Your code' }), cardParts.copy, cardParts.copyMsg]),
      cardParts.code,
      cardParts.codeInfo,
      cardParts.steps
    ]));
    // "Load a code" and "Start over" live under the code, where a code is made and used.
    var tools = document.getElementById('tools');
    if (tools) { card.appendChild(tools); }
  }

  function statLine(label, value, iconName) { return [el('dt', {}, [iconName ? icon(iconName) : null, label]), el('dd', { text: value })]; }

  function paintCard(r) {
    var report = r.report;
    var name = (state.name || '').trim();
    cardParts.name.textContent = name || 'Unnamed';
    cardParts.name.classList.toggle('empty', !name);
    cardParts.initial.textContent = name ? name.charAt(0).toUpperCase() : '?';
    cardParts.level.textContent = 'Lv ' + report.level;
    var avatar = (state.avatar || '').trim();
    if (avatar && /^https:\/\/\S+$/.test(avatar)) {
      if (cardParts.img.getAttribute('src') !== avatar) { cardParts.img.hidden = true; cardParts.img.src = avatar; }
    } else {
      cardParts.img.removeAttribute('src');
      cardParts.img.hidden = true;
      cardParts.initial.hidden = false;
      cardParts.portrait.classList.add('empty');
    }
    var skills = state.skills.length, passives = state.passives.length;
    cardParts.sub.textContent = skills + (skills === 1 ? ' skill' : ' skills') + (passives ? ', ' + passives + (passives === 1 ? ' passive' : ' passives') : '');

    var shown = Math.min(Math.max(report.total, report.spent), MAX_COINS_SHOWN);
    cardParts.coins.textContent = '';
    for (var i = 0; i < shown; i++) {
      var cls = i < report.spent ? (i < report.total ? 'coin spent' : 'coin over') : 'coin';
      cardParts.coins.appendChild(el('i', { class: cls }));
    }
    var over = report.spent > report.total;
    cardParts.bpText.textContent = report.spent + ' of ' + report.total + ' Build Points' + (over ? ', ' + (report.spent - report.total) + ' too many' : ', ' + (report.total - report.spent) + ' left');
    cardParts.bpText.classList.toggle('bad', over);

    var s = state.stats;
    cardParts.stats.textContent = '';
    [].concat(
      statLine('HP', String(s.hp), 'hp'), statLine('Power', String(s.power)),
      statLine('Speed', Math.min(s.speed[0], s.speed[1]) + ' to ' + Math.max(s.speed[0], s.speed[1]), 'speed'),
      statLine('Offense / Defense', s.offense + ' / ' + s.defense)
    ).forEach(function (node) { cardParts.stats.appendChild(node); });

    cardParts.chips.textContent = '';
    Object.keys(state.ranks).forEach(function (k) {
      if (state.ranks[k]) { cardParts.chips.appendChild(el('span', { class: 'chip chip-' + k }, [icon(k), E.capitalize(k) + ' ' + state.ranks[k]])); }
    });
    state.archetypes.forEach(function (k) { cardParts.chips.appendChild(el('span', { class: 'chip', text: E.title(k) })); });
    state.tags.filter(function (t) { return t.trim(); }).forEach(function (t) { cardParts.chips.appendChild(el('span', { class: 'chip tag', text: t.trim() })); });
    cardParts.chips.hidden = !cardParts.chips.childNodes.length;

    cardParts.breakdown.textContent = '';
    var any = false;
    report.costs.forEach(function (pair) {
      if (pair[1] !== 0) {
        any = true;
        cardParts.breakdown.appendChild(el('dt', { text: pair[0] }));
        cardParts.breakdown.appendChild(el('dd', { text: String(pair[1]) }));
      }
    });
    report.effectLines.forEach(function (line) {
      cardParts.breakdown.appendChild(el('dt', { class: 'sub', text: line }));
      cardParts.breakdown.appendChild(el('dd', { class: 'sub', text: '' }));
    });
    if (!any) { cardParts.breakdown.appendChild(el('dt', { text: 'Nothing spent yet' })); }

    cardParts.problems.textContent = '';
    r.blocking.forEach(function (text) { cardParts.problems.appendChild(el('li', { text: text })); });
    cardParts.problems.hidden = !r.blocking.length;
    cardParts.notes.textContent = '';
    r.notes.forEach(function (text) { cardParts.notes.appendChild(el('li', { text: text })); });
    cardParts.notes.hidden = !r.notes.length;
    cardParts.status.textContent = r.ok
      ? 'This build fits the rules.'
      : r.blocking.length + (r.blocking.length === 1 ? ' thing to fix before you can copy a code.' : ' things to fix before you can copy a code.');
    cardParts.jump.textContent = report.spent + ' of ' + report.total + ' BP. ' +
      (r.ok ? 'Go to your code' : r.blocking.length + (r.blocking.length === 1 ? ' thing to fix' : ' things to fix'));
    cardParts.jump.classList.toggle('bad', !r.ok);
    cardParts.status.classList.toggle('ok', r.ok);
    cardParts.status.classList.toggle('bad', !r.ok);
    fillIcons();
  }

  // ---- the code -----------------------------------------------------------------------------------------------

  function paintCode(r) {
    var mine = ++codeSeq;
    if (!r.ok) {
      currentCode = '';
      cardParts.code.value = '';
      cardParts.code.placeholder = 'Fix the problems above and your code shows up here.';
      cardParts.copy.disabled = true;
      cardParts.codeInfo.textContent = '';
      cardParts.steps.textContent = '';
      return;
    }
    E.encode(E.toSpec(state)).then(function (code) {
      if (mine !== codeSeq) { return; }
      currentCode = code;
      cardParts.code.value = code;
      var tooLong = code.length > R.limits.discordBox;
      cardParts.copy.disabled = tooLong;
      cardParts.codeInfo.textContent = tooLong
        ? 'This code is ' + code.length + ' characters, and the box in Discord holds ' + R.limits.discordBox + '. Shorten the trigger lines or drop a skill.'
        : code.length + ' characters. The box in Discord holds ' + R.limits.discordBox + '.';
      var steps = 'In Discord, type /character import, paste the code and press Submit. ';
      steps += r.needsAdmin
        ? 'This build changes things that cost points, so the bot sends it to the admins to approve and DMs you their answer. Until then the character has the starting stats and no skills.'
        : 'Nothing here costs points, so the character is made right away.';
      cardParts.steps.textContent = steps;
    });
  }

  function copyCode() {
    if (!currentCode) { return; }
    function done(ok) {
      cardParts.copyMsg.textContent = ok ? 'Copied.' : 'Could not copy. Select the code and copy it by hand.';
      setTimeout(function () { cardParts.copyMsg.textContent = ''; }, 3000);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(currentCode).then(function () { done(true); }, function () { fallbackCopy(done); });
    } else { fallbackCopy(done); }
  }
  function fallbackCopy(done) {
    cardParts.code.focus();
    cardParts.code.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    done(ok);
  }

  // ---- saving -------------------------------------------------------------------------------------------------------

  function loadPortraitSize() {
    try {
      var id = window.localStorage.getItem(PORTRAIT_KEY);
      if (PORTRAIT_SIZES.some(function (o) { return o.id === id; })) { return id; }
    } catch (e) { /* no storage: the default is used */ }
    return 'full';
  }
  function paintPortraitSize() {
    var option = PORTRAIT_SIZES.filter(function (o) { return o.id === portraitSize; })[0] || PORTRAIT_SIZES[PORTRAIT_SIZES.length - 1];
    var card = document.getElementById('card');
    if (card) { card.style.setProperty('--pw', option.percent + '%'); }
  }

  function loadSaved() {
    try {
      var text = window.localStorage.getItem(SAVE_KEY);
      var loaded = text ? E.fromSpec(JSON.parse(text)) : null;
      if (loaded) {
        var pictures = JSON.parse(window.localStorage.getItem(ART_KEY) || '[]');
        loaded.skills.forEach(function (skill, i) { if (pictures[i] && pictures[i].src) { skill.art = pictures[i]; } });
      }
      return loaded;
    } catch (e) { return null; }
  }
  // Pictures are not part of the code, so they are kept beside it, one slot per skill.
  function queueSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      try { window.localStorage.setItem(SAVE_KEY, JSON.stringify(E.toSpec(state))); } catch (e) { /* private window: nothing to do */ }
      try { window.localStorage.setItem(ART_KEY, JSON.stringify(state.skills.map(function (s) { return s.art || null; }))); }
      catch (e) { /* the pictures did not fit in this browser's storage: they last until the page is closed */ }
    }, 400);
  }
  // A code carries no pictures, so loading one keeps the pictures of skills that have the same name.
  function carryPictures(from, to) {
    var byName = {};
    from.forEach(function (s) { if (s.art) { byName[s.name] = s.art; } });
    to.forEach(function (s) { if (byName[s.name] && !s.art) { s.art = byName[s.name]; } });
  }

  // ---- update loop ------------------------------------------------------------------------------------------------------

  function update() {
    var r = E.review(state);
    syncs.forEach(function (fn) { fn(r); });
    paintCard(r);
    paintCode(r);
    markTabs(r);
    queueSave();
    resetArmed = false;
    resetButton.textContent = 'Start over';
  }

  // ---- load and start over -----------------------------------------------------------------------------------------------------

  var resetButton = document.getElementById('reset');
  resetButton.addEventListener('click', function () {
    if (!resetArmed) {
      resetArmed = true;
      resetButton.textContent = 'Click again to erase everything';
      return;
    }
    state = E.defaultState();
    build();
    update();
  });

  document.getElementById('loader-go').addEventListener('click', function () {
    var msg = document.getElementById('loader-msg');
    var input = document.getElementById('loader-input');
    E.decode(input.value).then(function (res) {
      if (res.error) { msg.textContent = res.error; msg.classList.add('bad'); return; }
      var before = state.skills;
      state = E.fromSpec(res.spec);
      carryPictures(before, state.skills);
      build();
      update();
      msg.classList.remove('bad');
      msg.textContent = 'Loaded ' + (state.name || 'the character') + '.';
      input.value = '';
    });
  });

  buildCard();
  paintPortraitSize();
  build();
  update();
  loadIcons();
})();
