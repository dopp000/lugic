/*
 * Lugic character builder: the rule-line picker's logic. It turns choices ("When", "If", "Then") into the
 * one line of text the bot reads, and says whether a combination works. The choices themselves come from
 * rules.js (made from game/line_spec.py); this file has no list of its own.
 * tests/test_line_picker.py runs what it builds through the bot's own parser.
 */
(function (root) {
  'use strict';

  var S = (typeof module === 'object' && module.exports) ? require('./rules.js').lines : root.LUGIC_RULES.lines;

  function byKey(list, key, field) {
    for (var i = 0; i < list.length; i++) { if (list[i][field || 'id'] === key) { return list[i]; } }
    return null;
  }
  function timing(key) { return byKey(S.timings, key, 'key'); }
  function condition(id) { return id ? byKey(S.conditions, id) : null; }
  function effect(id) { return byKey(S.effects, id); }

  function fill(text, values) {
    return String(text).replace(/\{(\w+)\}/g, function (whole, name) {
      return values && values[name] !== undefined && values[name] !== null ? String(values[name]) : whole;
    });
  }

  // The value a parameter starts with.
  function defaults(params) {
    var out = {};
    (params || []).forEach(function (p) { out[p.name] = p.default; });
    return out;
  }

  // Why a parameter's value is not usable, or null.
  function paramProblem(p, value) {
    if (p.kind === 'number') {
      var n = Number(value);
      if (value === '' || value === null || value === undefined || !isFinite(n) || Math.floor(n) !== n) { return p.label + ' needs a whole number.'; }
      if (n < p.min || n > p.max) { return p.label + ' has to be from ' + p.min + ' to ' + p.max + '.'; }
      return null;
    }
    if (p.kind === 'tag' || p.kind === 'skill') {
      var text = String(value === undefined || value === null ? '' : value).trim();
      if (!text) { return p.kind === 'tag' ? 'Pick or type a tag.' : 'Pick a skill.'; }
      if (/[\[\](),:;]/.test(text)) { return p.label + " can't contain brackets, commas, colons or semicolons."; }
      return null;
    }
    return null;
  }

  // Whether a pick works. A pick is { timing, coin, cond, condValues, effect, effValues, target, selector, next, limit, chance }.
  // Returns null when it does, else a sentence saying what to change. `passive` limits the timings to the ones passives use.
  function problem(pick, passive) {
    var t = timing(pick.timing);
    var e = effect(pick.effect);
    if (!t) { return 'Pick when the line fires.'; }
    if (!e) { return 'Pick what the line does.'; }
    if (passive ? !t.passive : !t.skill) {
      return passive ? 'A passive never reacts to [' + t.key + '].' : '[' + t.key + '] only works on a passive.';
    }
    if (t.perCoin && !(pick.coin >= 1 && pick.coin <= 4)) { return 'Pick which coin.'; }
    if (e.power && !(t.power || (e.flat && t.coinStart))) {
      return 'This changes the hit before the coins are tossed, so at [' + t.key + '] it would do nothing. Use Before Use, On Use, Clash Start or Before Attack' +
        (e.flat ? ' (or Coin Start).' : '.');
    }
    if (t.assist && !e.post) { return 'At [Ally Before Getting Hit] only effects that land after the hit work, like Take the hit for the ally, Shield or Gain.'; }
    if (e.assist && !t.assist) { return "'Take the hit for the ally' only works at [Ally Before Getting Hit]."; }
    if (e.followup && !t.followup) { return 'A follow-up attack needs an enemy to hit: use it at an Ally, Self Is Hit, Self On Stagger or On Evade timing.'; }
    if (e.needsLimit && !pick.limit) { return 'A follow-up attack needs a limit: choose once per turn or once per Encounter.'; }
    if (pick.next && !(e.post && !e.followup && !e.assist)) { return "'Next turn' only works on effects that land after the hit (Gain, Inflict, Heal, Shield, Sanity, Level)."; }
    if (pick.target && !e.ally) { return 'This effect can only be aimed at this unit.'; }
    var c = condition(pick.cond);
    var first = null;
    [[c, pick.condValues], [e, pick.effValues]].forEach(function (pair) {
      if (!pair[0] || first) { return; }
      pair[0].params.forEach(function (p) {
        if (!first) { first = paramProblem(p, (pair[1] || {})[p.name]); }
      });
    });
    if (first) { return first; }
    if (pick.chance) {
      var ch = Number(pick.chance);
      if (!(ch >= S.chance.min && ch <= S.chance.max) || Math.floor(ch) !== ch) { return 'A chance has to be from ' + S.chance.min + '% to ' + S.chance.max + '%.'; }
    }
    return null;
  }

  // The line. Call problem() first: this does not check.
  function compose(pick) {
    var t = timing(pick.timing);
    var c = condition(pick.cond);
    var e = effect(pick.effect);
    var line = (t.perCoin ? ':Coin' + pick.coin + ': ' : '') + '[' + t.key + ']';
    if (c) { line += ' ' + fill(c.text, pick.condValues); }
    line += ' ' + fill(e.text, pick.effValues);
    if (pick.target) { line += fill(pick.target, { selector: (pick.selector || S.selectors[0]).toLowerCase() }); }
    if (pick.next) { line += ' next turn'; }
    if (pick.limit) { line += pick.limit; }
    if (pick.chance) { line += ' (' + Number(pick.chance) + '% chance)'; }
    return line;
  }

  // A starting pick for a timing and effect, with every parameter on its default.
  function blank(timingKey, effectId, condId) {
    var c = condition(condId);
    var e = effect(effectId);
    return {
      timing: timingKey, coin: 1, cond: condId || '', condValues: c ? defaults(c.params) : {},
      effect: effectId, effValues: e ? defaults(e.params) : {}, target: '', selector: S.selectors[0], next: false, limit: '', chance: 0
    };
  }

  var api = { spec: S, timing: timing, condition: condition, effect: effect, defaults: defaults, problem: problem, compose: compose, blank: blank, paramProblem: paramProblem, fill: fill };
  if (typeof module === 'object' && module.exports) { module.exports = api; } else { root.LugicLines = api; }
})(typeof self !== 'undefined' ? self : this);
