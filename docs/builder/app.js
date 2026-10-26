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

  var state = loadSaved() || E.defaultState();
  var syncs = [];          // functions that repaint one thing when the review changes
  var codeSeq = 0;         // so a slow encode cannot overwrite a newer one
  var currentCode = '';
  var saveTimer = null;
  var resetArmed = false;

  // ---- icons -------------------------------------------------------------------------------------------------
  // icons/index.json (made by `python -m game.emoji_icons`) maps a name such as "burn" to an image of the bot's emoji.
  // Without it every icon slot stays empty and the page shows text only.

  var iconFiles = {};

  function icon(name) { return el('span', { class: 'ico', 'data-icon': name, 'aria-hidden': 'true' }); }

  function fillIcons() {
    var slots = document.querySelectorAll('.ico[data-icon]');
    for (var i = 0; i < slots.length; i++) {
      var slot = slots[i];
      var file = iconFiles[slot.getAttribute('data-icon')];
      if (file && !slot.firstChild) {
        var img = document.createElement('img');
        img.alt = '';
        img.src = file;
        img.addEventListener('error', function () { this.parentNode.removeChild(this); });
        slot.appendChild(img);
      }
    }
  }

  function loadIcons() {
    if (!window.fetch) { return; }
    fetch('icons/index.json').then(function (r) { return r.ok ? r.json() : {}; }).then(function (map) {
      iconFiles = map && typeof map === 'object' ? map : {};
      fillIcons();
    }).catch(function () { /* no icons folder: text only */ });
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
    sheet.appendChild(buildRanks());
    sheet.appendChild(buildDefense());
    sheet.appendChild(buildSkills());
    sheet.appendChild(buildPassives());
    sheet.appendChild(buildResistances());
    sheet.appendChild(buildTags());
    sheet.appendChild(buildReference());
    buildNav(sheet);
  }

  // A row of links to every part, so Skills is one tap away.
  function buildNav(sheet) {
    var nav = document.getElementById('nav');
    nav.textContent = '';
    [].forEach.call(sheet.querySelectorAll('section.part'), function (part) {
      nav.appendChild(el('a', { href: '#' + part.id, text: part.querySelector('h2').textContent }));
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
      ])
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
      return field(E.capitalize(key), select);
    });
    var physical = R.resist.damageTypes.map(function (key) {
      var select = selectInput([0.5, 1, 1.5].map(function (v) { return { value: String(v), label: v === 1 ? '1.0x, normal' : v + 'x' + (v < 1 ? ', resists' : ', weak') }; }),
        state.stats.resist[key], function (v) { state.stats.resist[key] = parseFloat(v); });
      syncs.push(function () { select.value = String(state.stats.resist[key]); });
      return field(E.capitalize(key), select);
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
        var lines = el('textarea', { rows: '3', spellcheck: 'false', placeholder: '[Combat Start] Gain 2 Protection' });
        lines.value = p.lines.join('\n');
        lines.addEventListener('input', function () { p.lines = lines.value.split('\n'); update(); });
        var remove = el('button', { type: 'button', class: 'btn quiet', text: 'Remove', onclick: function () {
          state.passives.splice(index, 1); renderPassives(); update();
        } });
        local.push(function (r) { paintIssues(issues, r.perPassive[index] || []); });
        list.appendChild(el('article', { class: 'item' }, [
          el('div', { class: 'grid passive-head' }, [field('Name', name), field('Kind', kind), field('Price (PP)', cost, 'An admin can change it.')]),
          field('Rule lines (up to ' + R.passives.maxLines + ', one per line)', lines, 'The bot checks the wording when you import.'),
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
      }
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
        field('Trigger lines', triggers, 'One per line. The bot checks the wording when you import.'),
        el('div', { class: 'quick' }, [
          el('span', { class: 'quick-label', text: 'Add a line' }), el('div', { class: 'quick-set' }, lineButtons),
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

  var GROUP_TITLES = { keyword: 'Keyword statuses', buff: 'Buffs', debuff: 'Debuffs', other: 'Other' };
  var TONE_TITLES = { positive: 'Positive', negative: 'Negative', neutral: 'Neutral' };

  function buildReference() {
    var legend = el('div', { class: 'legend' }, [
      el('h3', { text: 'What the colours mean' }),
      el('p', { class: 'note', text: 'The skill preview colours the tag at the start of a line by when it happens, and any status or damage word by what it is.' }),
      el('ul', { class: 'legend-list' }, [
        legendItem('pv-tag pv-use', '[On Use]', 'when the skill is used'),
        legendItem('pv-tag pv-before', '[Before Use]', 'just before it is used'),
        legendItem('pv-tag pv-hit', '[On Hit]', 'a coin hits, a kill, a crit and similar'),
        legendItem('pv-tag pv-clash', '[Clash Win]', 'anything about a clash'),
        legendItem('pv-tag pv-turn', '[Turn Start]', 'turn and combat timings'),
        legendItem('pv-tag pv-ally', '[Ally Is Hit]', 'about an ally'),
        legendItem('pv-tag pv-flag', '[Counter]', 'a skill type tag, such as Guard or Evade'),
        legendItem('pv-word pv-w-damage pv-k-slash', 'Slash', 'a damage type'),
        legendItem('pv-word pv-k-burn', 'Burn', 'a status; the colour is the status\'s own')
      ]),
      el('p', { class: 'note', text: 'Statuses are grouped the way the wiki groups them: positive (yellow), negative (red) and neutral (brown).' })
    ]);
    var groups = {};
    R.statusGuide.forEach(function (s) { (groups[s.group] = groups[s.group] || []).push(s); });
    var kids = [legend, el('div', { class: 'ref-intro' }, [richText(R.stackingNote)])];
    Object.keys(GROUP_TITLES).forEach(function (g) {
      if (!groups[g]) { return; }
      kids.push(el('h3', { text: GROUP_TITLES[g] }));
      kids.push(el('div', { class: 'status-grid' }, groups[g].map(statusCard)));
    });
    return section('Status effects', 'Every status the bot knows, in the bot\'s own words. Skills hand them out on a coin or in a trigger line.', kids);
  }

  function legendItem(cls, sample, what) {
    return el('li', {}, [el('span', { class: cls, text: sample }), ' ' + what]);
  }

  function statusCard(s) {
    var name = el('span', { class: 'st-name ' + (s.sin ? 'pv-k-' + s.key : 'tone-' + s.tone) }, [icon(s.key), s.title]);
    var chips = el('span', { class: 'st-chips' }, [el('span', { class: 'st-tone tone-' + s.tone, text: TONE_TITLES[s.tone] })]);
    if (s.sin) { chips.appendChild(el('span', { class: 'st-sin pv-sin-' + s.sin, text: E.capitalize(s.sin) })); }
    return el('article', { class: 'status-card' + (s.sin ? ' pv-sin-' + s.sin : '') }, [
      el('div', { class: 'st-head' }, [name, chips]),
      el('p', { class: 'st-sum', text: s.summary }),
      el('details', { class: 'st-more' }, [el('summary', { text: 'How it works' }), el('div', {}, [richText(s.body)])])
    ]);
  }

  // ---- the card -----------------------------------------------------------------------------------------

  var cardParts = {};

  function buildCard() {
    var card = document.getElementById('card');
    card.textContent = '';
    var portraitImg = el('img', { alt: '', referrerpolicy: 'no-referrer', hidden: true });
    portraitImg.addEventListener('error', function () { portraitImg.hidden = true; cardParts.initial.hidden = false; });
    portraitImg.addEventListener('load', function () { portraitImg.hidden = false; cardParts.initial.hidden = true; });
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

    card.appendChild(el('div', { class: 'portrait' }, [cardParts.initial, portraitImg, cardParts.level]));
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
    queueSave();
    resetArmed = false;
    resetButton.textContent = 'Start over';
  }

  // ---- header buttons -----------------------------------------------------------------------------------------------------

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
  build();
  update();
  loadIcons();
})();
