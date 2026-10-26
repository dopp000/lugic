/*
 * Lugic character builder: the skill preview, laid out like a skill on the Limbus wiki.
 * model() and tokenize() are plain functions (tests/preview_tokens.js runs them in Node);
 * render() turns the model into DOM and leaves icon slots that app.js fills from icons/index.json.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) { module.exports = factory(require('./engine.js')); }
  else { root.LugicPreview = factory(root.LugicEngine); }
})(typeof self !== 'undefined' ? self : this, function (E) {
  'use strict';

  var R = E.rules;
  var BUFFS = ['power_up', 'haste', 'damage_up', 'protection'];
  var STAT_WORDS = { 'hp': 'hp', 'speed': 'speed', 'sanity': 'sanity', 'offense level': 'offense_level', 'defense level': 'defense_level' };

  // ---- the frame round a skill ---------------------------------------------------------------------------------
  // Each Sin has a frame with three tiers, and defense skills have their own. A frame that is not drawn yet falls
  // back to a plain coloured ring. When a new image is added to borders/, add its tier here (a test checks the list).
  var SIN_OF_STATUS = { burn: 'wrath', bleed: 'lust', tremor: 'sloth', rupture: 'gluttony', sinking: 'gloom', poise: 'pride', charge: 'envy' };
  var DEFENSE_FLAGS = ['guard', 'evade', 'counter', 'clashable_guard', 'clashable_counter'];
  var FRAME_TIERS = { def: [1], wrath: [1, 2, 3], lust: [1, 2, 3], sloth: [1, 2], gluttony: [1, 2, 3], gloom: [1, 2, 3], pride: [1, 2, 3], envy: [1, 2, 3] };

  // 1 to 3: more coins, a fancier frame.
  function tierFor(coins) { return coins >= 4 ? 3 : coins === 3 ? 2 : 1; }

  // Which frame a skill gets: defense if it carries a defense tag, else the Sin of the status it leans on most.
  function frameOf(skill, coins, flagKeys) {
    var tier = tierFor(coins);
    var sin = null;
    if (DEFENSE_FLAGS.some(function (k) { return flagKeys.indexOf(k) >= 0; })) { sin = 'def'; tier = 1; }
    else {
      var score = {};
      var order = [];
      function add(status, amount) {
        var s = SIN_OF_STATUS[String(status).toLowerCase()];
        if (!s) { return; }
        if (!(s in score)) { score[s] = 0; order.push(s); }
        score[s] += amount;
      }
      (skill.statuses || []).slice(0, coins).forEach(function (c) { if (c && c.name) { add(c.name, Math.max(1, (Number(c.pot) || 0) + (Number(c.cnt) || 0))); } });
      String(skill.triggers || '').split('\n').forEach(function (line) {
        tokenize(line).forEach(function (t) { if (t.kind === 'status') { add(t.key, 1); } });
      });
      order.forEach(function (s) { if (sin === null || score[s] > score[sin]) { sin = s; } });
    }
    if (sin === null) { return { sin: null, tier: tier, file: null }; }
    var have = FRAME_TIERS[sin] || [];
    var best = null;
    have.forEach(function (n) { if (n <= tier && (best === null || n > best)) { best = n; } });
    return { sin: sin, tier: tier, file: best === null ? null : 'borders/' + sin + best + '.png' };
  }

  // ---- words worth colouring ---------------------------------------------------------------------------------

  var vocab = null;
  function getVocab() {
    if (vocab) { return vocab; }
    var words = {};   // lower-case phrase -> { kind, key }
    var statuses = [].concat(R.ranks.statuses, R.nonKeyword.oneTurn, R.nonKeyword.potencyTurn, R.nonKeyword.percentCount, R.otherStatuses);
    statuses.forEach(function (key) { words[E.title(key).toLowerCase()] = { kind: 'status', key: key }; });
    R.skills.damageTypes.forEach(function (key) { words[key] = { kind: 'damage', key: key }; });
    Object.keys(STAT_WORDS).forEach(function (word) { words[word] = { kind: 'stat', key: STAT_WORDS[word] }; });
    var phrases = Object.keys(words).sort(function (a, b) { return b.length - a.length; });
    var pattern = new RegExp('\\b(' + phrases.map(function (p) { return p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }).join('|') + ')\\b', 'gi');
    vocab = { words: words, pattern: pattern };
    return vocab;
  }

  // Which colour a [Timing] tag gets, the way the wiki does it: On Use is blue, Clash Win orange, Clash Lose red, every other
  // timing green. Skill-type tags such as [Counter] are neutral (brown), like Unfocused Volley on the wiki.
  function timingClass(label) {
    var key = String(label).trim().toLowerCase();
    if (Object.prototype.hasOwnProperty.call(R.skills.flagTags, key)) { return 'flag'; }
    if (key === 'on use') { return 'use'; }
    if (key === 'clash win') { return 'clashwin'; }
    if (key === 'clash lose') { return 'clashlose'; }
    return 'hit';
  }

  // Positive, negative or neutral, the way the wiki sorts effects (the status list on the page has the same data).
  var tones = null;
  function toneOf(key) {
    if (!tones) {
      tones = {};
      (R.statusGuide || []).forEach(function (s) { tones[s.key] = s.tone; });
    }
    return tones[key] || 'negative';
  }

  // Splits a line into coloured pieces: { kind: 'tag'|'status'|'damage'|'stat'|'text', text, key?, cls? }.
  function tokenize(text) {
    var out = [];
    var rest = String(text || '');
    var lead = /^\s*\[([^\]]+)\]\s*/.exec(rest);
    if (lead) {
      out.push({ kind: 'tag', text: '[' + lead[1].trim() + ']', cls: timingClass(lead[1]) });
      rest = rest.slice(lead[0].length);
    }
    var v = getVocab();
    var last = 0;
    var m;
    v.pattern.lastIndex = 0;
    while ((m = v.pattern.exec(rest)) !== null) {
      if (m.index > last) { out.push({ kind: 'text', text: rest.slice(last, m.index) }); }
      var hit = v.words[m[1].toLowerCase()];
      out.push({ kind: hit.kind, key: hit.key, text: m[1] });
      last = m.index + m[1].length;
    }
    if (last < rest.length) { out.push({ kind: 'text', text: rest.slice(last) }); }
    return out;
  }

  // ---- what to draw ------------------------------------------------------------------------------------------

  function statusSentence(coin) {
    var name = E.title(coin.name);
    var verb = BUFFS.indexOf(String(coin.name).toLowerCase()) >= 0 ? 'Gain' : 'Inflict';
    var pot = Math.floor(Number(coin.pot) || 0), cnt = Math.floor(Number(coin.cnt) || 0);
    if (pot && cnt) { return verb + ' ' + name + ' Potency ' + pot + ', Count ' + cnt; }
    if (pot) { return verb + ' ' + pot + ' ' + name; }
    if (cnt) { return verb + ' +' + cnt + ' ' + name + ' Count'; }
    return verb + ' ' + name;
  }

  // The skill as data: header numbers, tag chips, lines for the whole skill, lines for each coin.
  function model(skill) {
    var read = E.readTriggers(skill.triggers);
    var coins = Math.max(1, Math.min(R.skills.maxCoins, parseInt(skill.coins, 10) || 1));
    var rows = [];
    for (var i = 0; i < coins; i++) { rows.push({ n: i + 1, lines: [] }); }
    var general = [];
    read.lines.forEach(function (line) {
      var m = /^:Coin\s*([\d,\s]+):\s*(.*)$/i.exec(line);
      if (!m) { general.push(tokenize(line)); return; }
      var numbers = m[1].split(',').map(function (s) { return parseInt(s, 10); }).filter(function (n) { return n >= 1 && n <= coins; });
      if (!numbers.length) { general.push(tokenize(line)); return; }
      numbers.forEach(function (n) { rows[n - 1].lines.push(tokenize(m[2])); });
    });
    (skill.statuses || []).slice(0, coins).forEach(function (coin, i) {
      if (coin && coin.name) { rows[i].lines.unshift(tokenize('[On Hit] ' + statusSentence(coin))); }
    });
    var flags = [];
    String(skill.triggers || '').split('\n').forEach(function (raw) {
      var m = /^\s*\[([^\]]+)\]\s*$/.exec(raw);
      if (m && Object.prototype.hasOwnProperty.call(R.skills.flagTags, m[1].trim().toLowerCase())) {
        flags.push({ kind: 'tag', text: '[' + m[1].trim() + ']', cls: 'flag' });
      }
    });
    var flagKeys = [];
    flags.forEach(function (f) { flagKeys.push(R.skills.flagTags[f.text.slice(1, -1).trim().toLowerCase()]); });
    return {
      frame: frameOf(skill, coins, flagKeys),
      name: String(skill.name || '').trim() || 'Unnamed skill',
      type: skill.type, base: Math.floor(Number(skill.base) || 0), coin: Math.floor(Number(skill.coin) || 0), coins: coins,
      flags: flags, general: general, rows: rows
    };
  }

  // ---- the picture inside the frame ----------------------------------------------------------------------------
  // art = { src, a (width / height), x, y, zoom }. At zoom 1 the picture just covers the frame's opening; x and y move it,
  // as a fraction of the opening's size. Everything is relative, so the same numbers work at any size.
  function clampArt(art) {
    var a = art.a > 0 ? art.a : 1;
    var zoom = Math.min(ART_MAX_ZOOM, Math.max(1, Number(art.zoom) || 1));
    var w = Math.max(1, a) * zoom, h = Math.max(1, 1 / a) * zoom;
    var maxX = Math.max(0, (w - 1) / 2), maxY = Math.max(0, (h - 1) / 2);
    return {
      src: art.src, a: a, zoom: zoom,
      x: Math.min(maxX, Math.max(-maxX, Number(art.x) || 0)),
      y: Math.min(maxY, Math.max(-maxY, Number(art.y) || 0)),
      w: w, h: h
    };
  }
  // CSS percentages for the picture inside its opening.
  function artStyle(art) {
    var c = clampArt(art);
    return { width: c.w * 100, height: c.h * 100, left: (0.5 - c.w / 2 + c.x) * 100, top: (0.5 - c.h / 2 + c.y) * 100 };
  }
  var ART_MAX_ZOOM = 4;

  // ---- DOM ---------------------------------------------------------------------------------------------------

  function node(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text !== undefined) { n.textContent = text; }
    return n;
  }
  function icon(name) {
    var n = node('span', 'ico');
    n.setAttribute('data-icon', name);
    n.setAttribute('aria-hidden', 'true');
    return n;
  }
  function roman(n) { return ['I', 'II', 'III', 'IV', 'V', 'VI'][n - 1] || String(n); }

  function applyArt(img, art) {
    var s = artStyle(art);
    img.style.width = s.width + '%';
    img.style.height = s.height + '%';
    img.style.left = s.left + '%';
    img.style.top = s.top + '%';
  }

  // The round emblem: the skill's picture (if any) under the frame, and the damage-type icon when there is no picture.
  function emblem(frame, art, type) {
    var box = node('div', 'pv-emblem' + (art && art.src ? ' has-image' : ''));
    var inner = node('div', 'pv-framebox');
    if (art && art.src) {
      var hole = node('div', 'pv-art');
      var img = document.createElement('img');
      img.alt = '';
      img.draggable = false;
      img.src = art.src;
      applyArt(img, art);
      hole.appendChild(img);
      inner.appendChild(hole);
    } else {
      box.appendChild(icon(type));
      box.appendChild(node('span', 'pv-glyph', type.charAt(0).toUpperCase()));
    }
    if (frame.file) {
      var f = document.createElement('img');
      f.className = 'pv-frame';
      f.alt = '';
      f.src = frame.file;
      f.addEventListener('error', function () { f.remove(); inner.appendChild(ring()); });
      inner.appendChild(f);
    } else { inner.appendChild(ring()); }
    box.insertBefore(inner, box.firstChild);
    return box;
  }
  // A plain ring for a skill with no drawn frame (no status, no defense tag).
  function ring() {
    var svgNS = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('viewBox', '0 0 396 396');
    svg.setAttribute('class', 'pv-ring');
    var poly = document.createElementNS(svgNS, 'polygon');
    poly.setAttribute('points', RING_POINTS);
    svg.appendChild(poly);
    return svg;
  }
  var RING_POINTS = '51,178 132,75 255,75 343,178 314,314 189,369 77,312';

  function renderTokens(tokens, into) {
    tokens.forEach(function (t) {
      if (t.kind === 'tag') { into.appendChild(node('span', 'pv-tag pv-' + t.cls, t.text)); into.appendChild(document.createTextNode(' ')); }
      else if (t.kind === 'text') { into.appendChild(document.createTextNode(t.text)); }
      else {
        var span = node('span', 'pv-word pv-w-' + t.kind + ' pv-k-' + t.key + (t.kind === 'status' ? ' pv-tone-' + toneOf(t.key) : ''));
        span.appendChild(icon(t.key));
        span.appendChild(document.createTextNode(t.text));
        into.appendChild(span);
      }
    });
  }

  // index is the skill's place in the list (0 for the first); it only decides the title on the bar.
  function render(skill, index) {
    var m = model(skill);
    var box = node('div', 'pv pv-type-' + m.type + ' pv-sin-' + (m.frame.sin || 'none'));
    box.appendChild(node('div', 'pv-bar', m.frame.sin === 'def' ? 'Defense' : 'Skill ' + ((index || 0) + 1)));
    var head = node('div', 'pv-head');
    var coins = node('div', 'pv-coins');
    coins.setAttribute('aria-label', m.coins + (m.coins === 1 ? ' coin' : ' coins'));
    for (var i = 0; i < m.coins; i++) {
      var c = node('span', 'pv-coin');
      c.appendChild(icon('coin_heads'));
      coins.appendChild(c);
    }
    var banner = node('div', 'pv-banner', m.name);
    var side = node('div', 'pv-side');
    side.appendChild(coins);
    side.appendChild(banner);
    head.appendChild(emblem(m.frame, skill.art, m.type));
    head.appendChild(side);
    box.appendChild(head);

    var numbers = node('p', 'pv-numbers');
    var type = node('span', 'pv-damage pv-damage-' + m.type);
    type.appendChild(icon(m.type));
    type.appendChild(document.createTextNode(E.capitalize(m.type)));
    numbers.appendChild(type);
    numbers.appendChild(node('span', 'pv-base', String(m.base)));
    numbers.appendChild(node('span', 'pv-coinpower', (m.coin >= 0 ? '+' : '') + m.coin + ' per coin'));
    m.flags.forEach(function (t) { numbers.appendChild(node('span', 'pv-tag pv-' + t.cls, t.text)); });
    box.appendChild(numbers);

    m.general.forEach(function (tokens) {
      var line = node('p', 'pv-line');
      renderTokens(tokens, line);
      box.appendChild(line);
    });

    var rowsBox = node('div', 'pv-rows');
    m.rows.forEach(function (row) {
      var r = node('div', 'pv-row');
      r.appendChild(node('span', 'pv-num', roman(row.n)));
      var body = node('div', 'pv-rowbody');
      if (!row.lines.length) { body.appendChild(node('p', 'pv-line pv-none', 'No effect')); }
      row.lines.forEach(function (tokens) {
        var line = node('p', 'pv-line');
        renderTokens(tokens, line);
        body.appendChild(line);
      });
      r.appendChild(body);
      rowsBox.appendChild(r);
    });
    box.appendChild(rowsBox);
    return box;
  }

  return { tokenize: tokenize, renderTokens: renderTokens, model: model, render: render, timingClass: timingClass, statusSentence: statusSentence, toneOf: toneOf, frameTiers: FRAME_TIERS, emblem: emblem, applyArt: applyArt, clampArt: clampArt, artStyle: artStyle, maxZoom: ART_MAX_ZOOM };
});
