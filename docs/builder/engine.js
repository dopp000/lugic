/*
 * Lugic character builder: the rules and the code format. No page code in here.
 *
 * This is a port of the stat side of game/build_points.py (check_build) and of the skill form's
 * rules for Power, status ranks and defense types. The prices come from rules.js, which is
 * generated from the bot's own constants. tests/test_builder_site.py runs this file and the
 * Python checks on the same builds and fails when they disagree, so keep the wording and the
 * order of the problems the same as in Python.
 *
 * The bot checks everything again when a code is imported, and it has the last word. What this
 * file cannot know: whether a trigger line parses, and what a trigger line costs.
 *
 * Runs in the browser (window.LugicEngine) and in node (require).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) { module.exports = factory(require('./rules.js')); }
  else { root.LugicEngine = factory(root.LUGIC_RULES); }
})(typeof self !== 'undefined' ? self : this, function (R) {
  'use strict';

  var PREFIX_COMPRESSED = 'LUGIC1Z.';
  var PREFIX_PLAIN = 'LUGIC1.';

  // ---- small helpers ----------------------------------------------------------

  function capitalize(text) { text = String(text); return text.charAt(0).toUpperCase() + text.slice(1).toLowerCase(); }
  function title(text) { return String(text).split(/[\s_]+/).filter(Boolean).map(capitalize).join(' '); }
  function slug(text) { return String(text).toLowerCase().split(/\s+/).filter(Boolean).join('_'); }
  function pyFloat(value) { return Number.isInteger(value) ? value.toFixed(1) : String(value); }
  function round2(value) { return Math.round(value * 100) / 100; }
  function isInt(value) { return typeof value === 'number' && isFinite(value) && Math.floor(value) === value; }
  function intOr(value, fallback) {
    var n = typeof value === 'number' ? value : parseInt(String(value).trim(), 10);
    return isInt(n) ? n : fallback;
  }
  function clamp(value, low, high) { return Math.min(Math.max(value, low), high); }
  // The flavour text of a passive as it is kept in the code: line breaks made plain, each line trimmed, empty lines dropped, at most
  // R.limits.passiveFlavour letters. '' for nothing. game/passives.py has the same function (clean_flavour) and a test feeds both the same texts.
  function cleanFlavour(text) {
    if (typeof text !== 'string') { return ''; }
    var lines = text.replace(/\r\n?/g, '\n').trim().split('\n').map(function (l) { return l.trim(); }).filter(Boolean);
    return lines.join('\n').slice(0, R.limits.passiveFlavour);
  }
  function unique(list) { return list.filter(function (item, index) { return list.indexOf(item) === index; }); }
  function sum(list) { return list.reduce(function (a, b) { return a + b; }, 0); }

  // ---- state ----------------------------------------------------------------------

  function allOnes() {
    var out = {};
    R.resist.statusTypes.concat(R.resist.damageTypes).forEach(function (key) { out[key] = 1; });
    return out;
  }

  function newSkill(index) {
    return {
      name: 'Skill ' + (index + 1), base: 4, coin: 2, coins: 2, type: 'blunt',
      statuses: [emptyCoin(), emptyCoin()], triggers: ''
    };
  }
  function emptyCoin() { return { name: '', pot: 0, cnt: 0 }; }

  function defaultState() {
    return {
      v: R.codeVersion, name: '', avatar: '',
      pools: { bp: R.startingBp, sp: 0, pp: 0 },
      stats: {
        hp: R.hp.base, power: R.power.base, speed: R.speed.start.slice(), staggerOff: [],
        offense: 0, defense: 0, resist: allOnes()
      },
      ranks: {}, archetypes: [], tags: [], passives: [], skills: []
    };
  }

  // ---- prices (same names as game/build_points.py) ------------------------------------

  function levelFor(total) {
    var level = 1;
    R.levels.forEach(function (pair) { if (total >= pair[1]) { level = pair[0]; } });
    return level;
  }
  function nextLevelAt(total) {
    for (var i = 0; i < R.levels.length; i++) { if (total < R.levels[i][1]) { return R.levels[i][1]; } }
    return null;
  }
  function hpCost(hp) {
    var steps = Math.max(0, Math.ceil((hp - R.hp.base) / R.hp.step));
    return R.hp.costs[Math.min(steps, R.hp.costs.length - 1)];
  }
  function powerCost(power) {
    var steps = Math.max(0, power - R.power.base);
    return R.power.costs[Math.min(steps, R.power.costs.length - 1)];
  }
  function speedCost(low, high) {
    var avg = (low + high) / 2;
    avg = Math.min(Math.max(avg, R.speed.lowest), R.speed.highest);
    var floor = Math.trunc(avg);
    if (floor >= R.speed.highest) { return R.speed.avgCosts[String(R.speed.highest)]; }
    var frac = avg - floor;
    var base = R.speed.avgCosts[String(floor)];
    var next = R.speed.avgCosts[String(floor + 1)];
    return Math.ceil(base + frac * (next - base));
  }
  function staggerCost(tiers) {
    var cost = 0;
    if (tiers.length > 1 && !tiers[1]) { cost += R.stagger['2']; }
    if (tiers.length > 2 && !tiers[2]) { cost += R.stagger['3']; }
    return cost;
  }
  function offdefCost(steps) { return R.offdef.costs[Math.max(0, Math.min(steps, R.offdef.max))]; }
  function maxBasePower(power) { return Math.floor((20 + 4 * power) / 10); }
  function maxCoinPower(power) { return Math.floor((5 + power) / 5); }
  function defenseLabel(key) { return title(key); }

  function nonkeywordCost(status, size) {
    var cap = null;
    if (Object.prototype.hasOwnProperty.call(R.nonKeyword.sizeCaps, status)) { cap = R.nonKeyword.sizeCaps[status]; }
    else if (R.nonKeyword.percentCount.indexOf(status) >= 0) { cap = R.nonKeyword.percentCap; }
    if (cap !== null) { size = Math.min(size, cap); }
    return Math.ceil(size / 2);
  }

  // ---- reading a skill ------------------------------------------------------------------

  // The trigger box: lines that are only a skill tag ([Guard], [Duality] ...) are flags, the rest are trigger lines.
  function readTriggers(text) {
    var flags = [];
    var lines = [];
    String(text || '').split('\n').forEach(function (raw) {
      var line = raw.trim();
      if (!line) { return; }
      var m = /^\[([^\]]+)\]$/.exec(line);
      var key = m ? m[1].trim().toLowerCase() : null;
      if (key !== null && Object.prototype.hasOwnProperty.call(R.skills.flagTags, key)) {
        var flag = R.skills.flagTags[key];
        if (flags.indexOf(flag) < 0) { flags.push(flag); }
      } else {
        lines.push(line);
      }
    });
    return { flags: flags, lines: lines };
  }

  // Every (status, potency, count) the coins hand out, with the status written the way the bot keys it.
  function coinGrants(skill) {
    var out = [];
    (skill.statuses || []).slice(0, skill.coins).forEach(function (coin) {
      if (coin && coin.name) { out.push([slug(coin.name), intOr(coin.pot, 0), intOr(coin.cnt, 0)]); }
    });
    return out;
  }

  function skillArchetype(flags) {
    for (var i = 0; i < R.defense.order.length; i++) {
      if (flags.indexOf(R.defense.order[i]) >= 0) { return R.defense.order[i]; }
    }
    return null;
  }

  function skillRankProblems(ranks, grants) {
    var problems = [];
    var used = {};
    grants.forEach(function (g) {
      var status = g[0], pot = g[1], cnt = g[2];
      if (R.ranks.statuses.indexOf(status) < 0) { return; }
      var rank = intOr(ranks[status], 0);
      var label = capitalize(status);
      if (rank <= 0) {
        var msg = label + ' needs a ' + label + ' rank, and this character has none.';
        if (problems.indexOf(msg) < 0) { problems.push(msg); }
      } else if (Object.prototype.hasOwnProperty.call(R.ranks.potencyCaps, status)) {
        var cap = R.ranks.potencyCaps[status][Math.min(rank, R.ranks.max)];
        if (pot > cap) { problems.push(label + ' Potency ' + pot + ' is over the Rank ' + rank + ' limit of ' + cap + '.'); }
      } else {
        used[status] = (used[status] || 0) + pot * cnt;
      }
    });
    Object.keys(used).forEach(function (status) {
      var rank = Math.min(intOr(ranks[status], 0), R.ranks.max);
      var budget = R.ranks.budgets[status][rank];
      if (used[status] > budget) {
        problems.push(capitalize(status) + ' Potency x Count adds up to ' + used[status] +
          ', over the Rank ' + rank + ' budget of ' + budget + '.');
      }
    });
    return problems;
  }

  function skillPowerProblem(power, base, coin) {
    var maxBase = maxBasePower(power), maxCoin = maxCoinPower(power);
    var parts = [];
    if (base > maxBase) { parts.push('Base Power ' + base + ' is above the limit of ' + maxBase); }
    if (coin > maxCoin) { parts.push('Coin Power ' + coin + ' is above the limit of ' + maxCoin); }
    if (!parts.length) { return null; }
    return parts.join(' and ') + ' for Power ' + power + '.';
  }

  // ---- the check (port of check_build) -------------------------------------------------------

  function speedRange(stats) {
    var lo = stats.speed[0], hi = stats.speed[1];
    return lo <= hi ? [lo, hi] : [hi, lo];
  }

  /*
   * opts.deferPassivePoints: a new character has no Passive Points yet and an admin hands them out
   * when the build is approved, so the page passes true and gets the shortfall back as `ppShort`.
   */
  function checkBuild(state, opts) {
    opts = opts || {};
    var total = state.pools.bp;
    var stats = state.stats;
    var hp = stats.hp, power = stats.power;
    var range = speedRange(stats), lo = range[0], hi = range[1];
    var tiers = [true, stats.staggerOff.indexOf(2) < 0, stats.staggerOff.indexOf(3) < 0];

    var costs = [];
    function setCost(label, cost) {
      for (var i = 0; i < costs.length; i++) { if (costs[i][0] === label) { costs[i][1] = cost; return; } }
      costs.push([label, cost]);
    }
    setCost('HP', hpCost(hp));
    setCost('Power', powerCost(power));
    setCost('Speed', speedCost(lo, hi));
    setCost('Stagger removal', staggerCost(tiers));

    // Resistances
    var res = stats.resist || {};
    var resProblems = [];
    var resPaid = 0, resRefunds = 0;
    R.resist.statusTypes.forEach(function (key) {
      var value = round2(res[key] === undefined ? 1 : Number(res[key]));
      var price = R.resist.statusCosts[pyFloat(value)];
      if (price === undefined) {
        resProblems.push(capitalize(key) + ' resistance ' + pyFloat(value) + 'x is outside the allowed 0.5x to 2.0x.');
      } else if (price > 0) { resPaid += price; }
      else { resRefunds += -price; }
    });
    if (resRefunds > R.resist.refundCap) {
      resProblems.push('Vulnerabilities give back ' + resRefunds + ' BP, but at most ' + R.resist.refundCap + ' BP can be banked.');
    }

    // Offense / Defense Level
    var offdefProblems = [];
    [['Offense Level', stats.offense], ['Defense Level', stats.defense]].forEach(function (pair) {
      if (!(pair[1] >= 0 && pair[1] <= R.offdef.max)) {
        offdefProblems.push(pair[0] + ' ' + pair[1] + ' is outside the allowed 0 to ' + R.offdef.max + '.');
      }
      if (pair[1]) { setCost(pair[0], offdefCost(pair[1])); }
    });

    // Status ranks
    var ranks = state.ranks || {};
    var rankProblems = [];
    var owned = [];
    Object.keys(ranks).forEach(function (key) { if (ranks[key]) { owned.push([key, ranks[key]]); } });
    owned.forEach(function (pair) {
      var key = pair[0], value = pair[1];
      if (R.ranks.statuses.indexOf(key) < 0) { rankProblems.push("'" + key + "' is not a status that can have a rank."); }
      else if (!(value >= 1 && value <= R.ranks.max)) {
        rankProblems.push(capitalize(key) + ' rank ' + value + ' is outside the allowed 1 to ' + R.ranks.max + '.');
      }
    });
    if (owned.length > R.ranks.maxStatuses) {
      rankProblems.push(owned.length + ' ranked statuses, but a character can own at most ' + R.ranks.maxStatuses + '.');
    }
    if (owned.length) {
      setCost('Status ranks', sum(owned.filter(function (p) { return p[1] >= 1 && p[1] <= R.ranks.max; })
        .map(function (p) { return R.ranks.costs[p[1]]; })));
    }

    // Defense archetypes
    var ownedDef = state.archetypes || [];
    var defProblems = [];
    ownedDef.forEach(function (key) {
      if (!Object.prototype.hasOwnProperty.call(R.defense.archetypes, key)) {
        defProblems.push("'" + key + "' is not a defense archetype.");
        return;
      }
      if (R.defense.needsSpeed.indexOf(key) >= 0 && lo < R.defense.minSpeed) {
        defProblems.push(defenseLabel(key) + ' needs a lowest Speed of ' + R.defense.minSpeed + ' or more (it is ' + lo + ').');
      }
      if (R.defense.needsNoTier2.indexOf(key) >= 0 && !(tiers.length > 1 && !tiers[1])) {
        defProblems.push(defenseLabel(key) + ' needs Stagger Tier 2 to be removed.');
      }
    });
    if (unique(ownedDef).length > R.defense.max) {
      defProblems.push(unique(ownedDef).length + ' defense archetypes, but a character can own at most ' + R.defense.max + '.');
    }
    if (ownedDef.length) {
      setCost('Defense archetypes', sum(unique(ownedDef).map(function (k) { return R.defense.archetypes[k] || 0; })));
    }

    // Passive Points
    var ppUsed = sum((state.passives || []).map(function (p) { return intOr(p.cost, 0); }));
    var ppTotal = state.pools.pp;
    var ppProblems = [];
    var ppShort = Math.max(0, ppUsed - ppTotal);
    if (ppUsed > ppTotal && !opts.deferPassivePoints) {
      ppProblems.push('Passives and unique statuses use ' + ppUsed + ' Passive Points but only ' + ppTotal + ' are available.');
    }

    // Non-keyword effects (from the coins; trigger lines are priced by the bot)
    var nkTotal = 0;
    var nkLines = [];
    (state.skills || []).forEach(function (skill) {
      var read = readTriggers(skill.triggers);
      if (read.flags.indexOf('supermove') >= 0) { return; }
      coinGrants(skill).forEach(function (g) {
        if (R.nonKeyword.oneTurn.indexOf(g[0]) < 0) { return; }
        var size = R.nonKeyword.potencyTurn.indexOf(g[0]) >= 0 ? g[1] : Math.max(g[1], g[2]);
        size = Math.max(0, size);
        var cost = nonkeywordCost(g[0], size);
        nkTotal += cost;
        nkLines.push(skill.name + ': ' + title(g[0]) + ' ' + size + ' = ' + cost + ' BP');
      });
    });
    var nkProblems = [];
    if (nkTotal) { setCost('Non-keyword effects', nkTotal); }
    if (nkTotal > R.nonKeyword.cap) {
      nkProblems.push('Non-keyword effects (Haste, Fragile, Bind and so on) cost ' + nkTotal +
        ' BP, but a kit can spend at most ' + R.nonKeyword.cap + ' on them.');
    }
    if (resPaid || resRefunds) { setCost('Resistances', resPaid - Math.min(resRefunds, R.resist.refundCap)); }
    var physical = R.resist.damageTypes.map(function (k) { return round2(res[k] === undefined ? 1 : Number(res[k])); })
      .sort(function (a, b) { return a - b; });
    var physicalOk = R.resist.physicalSets.some(function (set) {
      return set.length === physical.length && set.every(function (v, i) { return v === physical[i]; });
    });
    if (!physicalOk) {
      resProblems.push('Slash, Blunt and Pierce must be either all 1.0x, or one each of 0.5x, 1.0x and 1.5x.');
    }

    var costMap = {};
    costs.forEach(function (pair) { costMap[pair[0]] = pair[1]; });
    var spent = sum(costs.map(function (pair) { return pair[1]; }));
    var problems = [];
    [resProblems, offdefProblems, rankProblems, defProblems, nkProblems, ppProblems].forEach(function (list) {
      list.forEach(function (p) { problems.push(p); });
    });

    if (hp < R.hp.base) { problems.push('HP ' + hp + ' is below the minimum of ' + R.hp.base + '.'); }
    if (hp > R.hp.max) { problems.push('HP ' + hp + ' is above the maximum of ' + R.hp.max + '.'); }
    if (power < R.power.base) { problems.push('Power ' + power + ' is below the minimum of ' + R.power.base + '.'); }
    if (power > R.power.max) { problems.push('Power ' + power + ' is above the maximum of ' + R.power.max + '.'); }
    var speedOut = lo < R.speed.lowest || hi > R.speed.highest;
    if (speedOut) {
      problems.push('Speed ' + lo + '-' + hi + ' is outside the allowed ' + R.speed.lowest + '-' + R.speed.highest + '.');
    } else if (hi - lo < R.speed.minSpread) {
      problems.push('Speed ' + lo + '-' + hi + ' is too narrow: the range must span at least ' + R.speed.minSpread + '.');
    }

    var singleCap = Math.trunc(total * R.caps.single);
    var pairCap = Math.trunc(total * R.caps.pair);
    ['HP', 'Power', 'Speed'].forEach(function (label) {
      if (label === 'Speed' && speedOut) { return; }
      if (costMap[label] > singleCap) {
        problems.push(label + ' uses ' + costMap[label] + ' BP, but one stat can use at most ' + singleCap +
          ' (40% of ' + total + ').');
      }
    });
    if (costMap.Power + costMap.Speed > pairCap) {
      problems.push('Power and Speed together use ' + (costMap.Power + costMap.Speed) + ' BP, the limit is ' +
        pairCap + ' (60% of ' + total + ').');
    }
    if (costMap.Power + costMap.HP > pairCap) {
      problems.push('Power and HP together use ' + (costMap.Power + costMap.HP) + ' BP, the limit is ' +
        pairCap + ' (60% of ' + total + ').');
    }
    if (spent > total) { problems.push('This build costs ' + spent + ' BP but only ' + total + ' BP are available.'); }

    var skillProblems = [];
    (state.skills || []).forEach(function (skill) {
      if (readTriggers(skill.triggers).flags.indexOf('supermove') >= 0) { return; }
      var issue = skillPowerProblem(power, intOr(skill.base, 0), intOr(skill.coin, 0));
      if (issue) { skillProblems.push("Skill '" + skill.name + "': " + issue); }
    });

    return {
      level: levelFor(total), total: total, spent: spent, costs: costs, breakdown: costMap,
      problems: problems, skillProblems: skillProblems, effectLines: nkLines,
      ppUsed: ppUsed, ppTotal: ppTotal, ppShort: ppShort, ok: !problems.length && !skillProblems.length
    };
  }

  // ---- everything the page shows ---------------------------------------------------------------

  function triggerCount(read) { return read.lines.length; }
  function skillSpCost(read) {
    if (read.flags.indexOf('supermove') >= 0) { return 0; }
    return Math.max(0, triggerCount(read) - 1) * R.sp.perExtraLine;
  }

  /*
   * One call for the whole page. Returns the Python-matching report plus the site's own checks,
   * and `blocking`, the list that must be empty before a code is worth exporting.
   */
  function review(state) {
    var report = checkBuild(state, { deferPassivePoints: true });
    var identity = [];
    var name = String(state.name || '');
    if (!name.trim()) { identity.push('Give the character a name.'); }
    else if (name.length > R.limits.name) { identity.push('Keep the name to ' + R.limits.name + ' characters or fewer.'); }
    else if (/[\\\/:*?"<>|\x00-\x1f]/.test(name) || name.trim().charAt(0) === '.') {
      identity.push('A name can\'t contain / \\ : * ? " < > | and can\'t start with a dot.');
    }
    var avatar = String(state.avatar || '').trim();
    if (avatar && (!/^https:\/\/\S+$/.test(avatar) || avatar.length > R.limits.avatar)) {
      identity.push('The portrait link has to start with https:// and be at most ' + R.limits.avatar + ' characters.');
    }
    if (!(state.pools.bp >= 1)) { identity.push('Build Points must be a number of 1 or more.'); }

    var tagProblems = [];
    var tags = (state.tags || []).map(function (t) { return t.split(/\s+/).filter(Boolean).join(' '); }).filter(Boolean);
    if (tags.length > R.tags.max) { tagProblems.push('A character can have at most ' + R.tags.max + ' group tags.'); }
    tags.forEach(function (t) {
      if (t.length > R.tags.maxLength) { tagProblems.push('The group tag "' + t.slice(0, 40) + '" is longer than ' + R.tags.maxLength + ' characters.'); }
    });

    var perPassive = (state.passives || []).map(function (p, index) {
      var list = [];
      var pname = String(p.name || '').trim();
      if (!pname) { list.push('Give the passive a name.'); }
      else if (pname.length > R.limits.passiveName) { list.push('The name is longer than ' + R.limits.passiveName + ' characters.'); }
      else if (state.passives.some(function (q, j) { return j < index && String(q.name || '').trim().toLowerCase() === pname.toLowerCase(); })) {
        list.push('Two passives are called ' + pname + '.');
      }
      var cost = intOr(p.cost, NaN);
      if (!(cost >= 1 && cost <= R.passives.costMax)) { list.push('The price has to be from 1 to ' + R.passives.costMax + ' Passive Points.'); }
      var lines = (p.lines || []).map(function (l) { return String(l).trim(); }).filter(Boolean);
      if (!lines.length) { list.push('A passive needs at least one rule line.'); }
      if (lines.length > R.passives.maxLines) { list.push('A passive has at most ' + R.passives.maxLines + ' rule lines.'); }
      lines.forEach(function (l) {
        if (l.length > R.limits.passiveLine) { list.push('A rule line is longer than ' + R.limits.passiveLine + ' characters.'); }
      });
      if (typeof p.flavour === 'string' && p.flavour.trim().length > R.limits.passiveFlavour) {
        list.push('The flavour text is longer than ' + R.limits.passiveFlavour + ' characters.');
      }
      return list;
    });
    var passiveCount = [];
    if ((state.passives || []).length > R.passives.max) {
      passiveCount.push('A character can have at most ' + R.passives.max + ' passives.');
    }

    var spUsed = 0;
    var perSkill = (state.skills || []).map(function (skill, index) {
      var list = [];
      var sname = String(skill.name || '').trim();
      var read = readTriggers(skill.triggers);
      var grants = coinGrants(skill);
      if (!sname) { list.push('Give the skill a name.'); }
      else if (sname.length > R.limits.skillName) { list.push('The name is longer than ' + R.limits.skillName + ' characters.'); }
      else if (state.skills.some(function (q, j) { return j < index && String(q.name || '').trim().toLowerCase() === sname.toLowerCase(); })) {
        list.push('Two skills are called ' + sname + '.');
      }
      var coins = intOr(skill.coins, NaN);
      if (!(coins >= 1 && coins <= R.skills.maxCoins)) { list.push('Coins must be between 1 and ' + R.skills.maxCoins + '.'); }
      if (!(intOr(skill.base, -1) >= 0) || !(intOr(skill.coin, -1) >= 0)) { list.push('Base Power and Coin Power must be whole numbers, 0 or more.'); }
      (skill.statuses || []).slice(0, coins >= 1 ? coins : 0).forEach(function (coin, i) {
        if (coin && coin.name && (!(intOr(coin.pot, -1) >= 0) || !(intOr(coin.cnt, -1) >= 0))) {
          list.push('Coin ' + (i + 1) + ': Potency and Count must be whole numbers, 0 or more.');
        }
      });
      if (read.flags.indexOf('supermove') >= 0) {
        list.push('A [Supermove] is bought with Build Points, so only an admin can add one. Take it out.');
      } else {
        var issue = skillPowerProblem(state.stats.power, intOr(skill.base, 0), intOr(skill.coin, 0));
        if (issue) { list.push(issue + ' A higher Power stat raises the limit.'); }
        skillRankProblems(state.ranks || {}, grants).forEach(function (p) { list.push(p); });
        var key = skillArchetype(read.flags);
        if (key && (state.archetypes || []).indexOf(key) < 0) {
          list.push(defenseLabel(key) + ' is a defense archetype (' + R.defense.archetypes[key] + ' BP) and this character does not own it.');
        }
        var oneCoin = read.flags.filter(function (f) { return ['guard', 'evade', 'clashable_guard'].indexOf(f) >= 0; });
        if (oneCoin.length && coins !== 1) {
          list.push(oneCoin.map(function (f) { return title(f); }).join(', ') + ' must be exactly 1 coin (got ' + coins + ').');
        }
      }
      if (String(skill.triggers || '').length > R.limits.triggers) { list.push('The trigger box is longer than ' + R.limits.triggers + ' characters.'); }
      spUsed += skillSpCost(read);
      return list;
    });
    var skillCount = [];
    if ((state.skills || []).length > R.skills.max) { skillCount.push('A code can hold at most ' + R.skills.max + ' skills.'); }
    var spProblems = [];
    if (spUsed > state.pools.sp) {
      spProblems.push('Extra trigger lines use ' + spUsed + ' Skill Points (the first line on a skill is free, each extra costs ' +
        R.sp.perExtraLine + '), but you have ' + state.pools.sp + '.');
    }

    var blocking = [];
    [identity, tagProblems, report.problems, passiveCount, skillCount, spProblems].forEach(function (list) {
      list.forEach(function (p) { blocking.push(p); });
    });
    perPassive.forEach(function (list, i) {
      list.forEach(function (p) { blocking.push('Passive "' + (state.passives[i].name || (i + 1)) + '": ' + p); });
    });
    perSkill.forEach(function (list, i) {
      list.forEach(function (p) { blocking.push('Skill "' + (state.skills[i].name || (i + 1)) + '": ' + p); });
    });

    var notes = [];
    if (report.ppShort > 0) {
      notes.push('Your passives ask for ' + report.ppUsed + ' Passive Points and you have ' + state.pools.pp +
        '. Admins give those out, so the build waits for an admin to add them before it is approved.');
    }
    if ((state.skills || []).some(function (s) { return triggerCount(readTriggers(s.triggers)) > 0; })) {
      notes.push('Trigger lines are checked by the bot when you import. Anything it refuses is told to you then, and nothing is created.');
    }
    return {
      report: report, blocking: blocking, notes: notes, perSkill: perSkill, perPassive: perPassive,
      spUsed: spUsed, level: report.level, nextLevel: nextLevelAt(state.pools.bp),
      needsAdmin: needsApproval(state), ok: blocking.length === 0
    };
  }

  // Anything that costs points has to be approved by an admin. Skills alone are the player's own.
  function needsApproval(state) {
    var spec = toSpec(state);
    return ['stats', 'ranks', 'archetypes', 'tags', 'passives'].some(function (key) {
      var v = spec[key];
      return v && (Array.isArray(v) ? v.length : Object.keys(v).length);
    });
  }

  // ---- the code ---------------------------------------------------------------------------------------

  function skillToRaw(skill) {
    var coins = intOr(skill.coins, 1);
    var tokens = [];
    for (var i = 0; i < coins; i++) {
      var coin = (skill.statuses || [])[i];
      tokens.push(coin && coin.name ? capitalize(coin.name) + ':' + intOr(coin.pot, 0) + ':' + intOr(coin.cnt, 0) : 'none');
    }
    var raw = {
      name: String(skill.name || '').trim(),
      stats: [intOr(skill.base, 0), intOr(skill.coin, 0), coins, capitalize(skill.type || 'blunt')].join(', ')
    };
    if (tokens.some(function (t) { return t !== 'none'; })) { raw.statuses = tokens.join(','); }
    var triggers = String(skill.triggers || '').split('\n').map(function (l) { return l.trim(); }).filter(Boolean).join('\n');
    if (triggers) { raw.triggers = triggers; }
    return raw;
  }

  function rawToSkill(raw, index) {
    var skill = newSkill(index);
    skill.name = String(raw.name || skill.name);
    var parts = String(raw.stats || '').split(',').map(function (p) { return p.trim(); });
    if (parts.length === 4) {
      skill.base = intOr(parts[0], 0); skill.coin = intOr(parts[1], 0);
      skill.coins = clamp(intOr(parts[2], 1), 1, R.skills.maxCoins);
      skill.type = R.skills.damageTypes.indexOf(parts[3].toLowerCase()) >= 0 ? parts[3].toLowerCase() : 'blunt';
    }
    skill.statuses = [];
    var tokens = String(raw.statuses || 'none').split(',').map(function (t) { return t.trim(); });
    for (var i = 0; i < skill.coins; i++) {
      var token = tokens.length === 1 && tokens[0].toLowerCase() === 'none' ? 'none' : (tokens[i] || 'none');
      var bits = token.split(':');
      if (bits.length === 3 && token.toLowerCase() !== 'none') {
        skill.statuses.push({ name: slug(bits[0]), pot: intOr(bits[1], 0), cnt: intOr(bits[2], 0) });
      } else { skill.statuses.push(emptyCoin()); }
    }
    skill.triggers = String(raw.triggers || '');
    return skill;
  }

  function toSpec(state) {
    var s = state.stats;
    var spec = { v: R.codeVersion, name: String(state.name || '').trim() };
    var avatar = String(state.avatar || '').trim();
    if (avatar) { spec.avatar = avatar; }
    spec.pools = { bp: state.pools.bp, sp: state.pools.sp, pp: state.pools.pp };
    var stats = {};
    if (s.hp !== R.hp.base) { stats.hp = s.hp; }
    if (s.power !== R.power.base) { stats.power = s.power; }
    if (s.speed[0] !== R.speed.start[0] || s.speed[1] !== R.speed.start[1]) { stats.speed = [s.speed[0], s.speed[1]]; }
    if (s.staggerOff.length) { stats.stagger_off = s.staggerOff.slice().sort(function (a, b) { return a - b; }); }
    if (s.offense) { stats.offense = s.offense; }
    if (s.defense) { stats.defense = s.defense; }
    var resist = {};
    Object.keys(s.resist || {}).forEach(function (k) { if (round2(Number(s.resist[k])) !== 1) { resist[k] = round2(Number(s.resist[k])); } });
    if (Object.keys(resist).length) { stats.resist = resist; }
    if (Object.keys(stats).length) { spec.stats = stats; }
    var ranks = {};
    Object.keys(state.ranks || {}).forEach(function (k) { if (state.ranks[k]) { ranks[k] = state.ranks[k]; } });
    if (Object.keys(ranks).length) { spec.ranks = ranks; }
    if ((state.archetypes || []).length) { spec.archetypes = state.archetypes.slice(); }
    var tags = (state.tags || []).map(function (t) { return t.split(/\s+/).filter(Boolean).join(' '); }).filter(Boolean);
    if (tags.length) { spec.tags = tags; }
    var passives = (state.passives || []).map(function (p) {
      var out = {
        name: String(p.name || '').trim(), kind: p.kind || 'passive', cost: intOr(p.cost, 1),
        lines: (p.lines || []).map(function (l) { return String(l).trim(); }).filter(Boolean)
      };
      var flavour = cleanFlavour(p.flavour);
      if (flavour) { out.flavour = flavour; }
      return out;
    });
    if (passives.length) { spec.passives = passives; }
    if ((state.skills || []).length) { spec.skills = state.skills.map(skillToRaw); }
    return spec;
  }

  // Builds a page state from a spec (a decoded code). Anything missing keeps its default.
  function fromSpec(spec) {
    var state = defaultState();
    if (!spec || typeof spec !== 'object') { return state; }
    state.name = String(spec.name || '');
    state.avatar = String(spec.avatar || '');
    var pools = spec.pools || {};
    ['bp', 'sp', 'pp'].forEach(function (k) { if (isInt(pools[k]) && pools[k] >= 0) { state.pools[k] = pools[k]; } });
    var s = spec.stats || {};
    if (isInt(s.hp)) { state.stats.hp = s.hp; }
    if (isInt(s.power)) { state.stats.power = s.power; }
    if (Array.isArray(s.speed) && s.speed.length === 2 && isInt(s.speed[0]) && isInt(s.speed[1])) { state.stats.speed = [s.speed[0], s.speed[1]]; }
    if (Array.isArray(s.stagger_off)) { state.stats.staggerOff = s.stagger_off.filter(isInt); }
    if (isInt(s.offense)) { state.stats.offense = s.offense; }
    if (isInt(s.defense)) { state.stats.defense = s.defense; }
    if (s.resist && typeof s.resist === 'object') {
      Object.keys(s.resist).forEach(function (k) { if (k in state.stats.resist && typeof s.resist[k] === 'number') { state.stats.resist[k] = s.resist[k]; } });
    }
    if (spec.ranks && typeof spec.ranks === 'object') { Object.keys(spec.ranks).forEach(function (k) { if (isInt(spec.ranks[k])) { state.ranks[k] = spec.ranks[k]; } }); }
    if (Array.isArray(spec.archetypes)) { state.archetypes = spec.archetypes.map(String); }
    if (Array.isArray(spec.tags)) { state.tags = spec.tags.map(String); }
    if (Array.isArray(spec.passives)) {
      state.passives = spec.passives.map(function (p) {
        return { name: String(p.name || ''), kind: p.kind === 'support' ? 'support' : 'passive', cost: intOr(p.cost, 1), lines: (p.lines || []).map(String), flavour: cleanFlavour(p.flavour) };
      });
    }
    if (Array.isArray(spec.skills)) { state.skills = spec.skills.map(rawToSkill); }
    return state;
  }

  // ---- encode / decode (the format game/character_import.py reads) ---------------------------------------

  function bytesToB64url(bytes) {
    var text = '';
    for (var i = 0; i < bytes.length; i += 0x8000) { text += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000)); }
    return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  function b64urlToBytes(text) {
    var clean = text.replace(/-/g, '+').replace(/_/g, '/');
    while (clean.length % 4) { clean += '='; }
    var raw = atob(clean);
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) { out[i] = raw.charCodeAt(i); }
    return out;
  }
  async function pipe(bytes, stream) {
    var result = new Blob([bytes]).stream().pipeThrough(stream);
    return new Uint8Array(await new Response(result).arrayBuffer());
  }

  // Makes the code to paste into /character import. Compressed when the browser can, plain when it cannot.
  async function encode(spec) {
    var bytes = new TextEncoder().encode(JSON.stringify(spec));
    if (typeof CompressionStream === 'function') {
      try { return PREFIX_COMPRESSED + bytesToB64url(await pipe(bytes, new CompressionStream('deflate'))); }
      catch (e) { /* fall through to the plain code */ }
    }
    return PREFIX_PLAIN + bytesToB64url(bytes);
  }

  // Opens a code. Resolves to { spec } or { error }.
  async function decode(text) {
    var cleaned = String(text || '').replace(/\s+/g, '').replace(/^[`'"<>“”‘’]+|[`'"<>“”‘’]+$/g, '');
    var upper = cleaned.toUpperCase();
    var packed;
    var body;
    if (upper.indexOf(PREFIX_COMPRESSED.toUpperCase()) === 0) { packed = true; body = cleaned.slice(PREFIX_COMPRESSED.length); }
    else if (upper.indexOf(PREFIX_PLAIN.toUpperCase()) === 0) { packed = false; body = cleaned.slice(PREFIX_PLAIN.length); }
    else { return { error: 'That does not look like a code from this page. A code starts with LUGIC1.' }; }
    if (!body || !/^[A-Za-z0-9_\-+\/=]+$/.test(body)) { return { error: 'The code is damaged. Copy it again, all of it.' }; }
    try {
      var bytes = b64urlToBytes(body);
      if (packed) {
        if (typeof DecompressionStream !== 'function') { return { error: 'This browser cannot open a packed code.' }; }
        bytes = await pipe(bytes, new DecompressionStream('deflate'));
      }
      var data = JSON.parse(new TextDecoder().decode(bytes));
      if (!data || typeof data !== 'object' || Array.isArray(data)) { return { error: 'The code does not hold a character.' }; }
      if (data.v !== R.codeVersion) { return { error: 'This code is from a different version of the builder.' }; }
      return { spec: data };
    } catch (e) {
      return { error: 'The code is damaged, or it was cut short when it was copied. Copy it again, all of it.' };
    }
  }

  return {
    rules: R, defaultState: defaultState, newSkill: newSkill, emptyCoin: emptyCoin,
    checkBuild: checkBuild, review: review, needsApproval: needsApproval,
    toSpec: toSpec, fromSpec: fromSpec, encode: encode, decode: decode, cleanFlavour: cleanFlavour,
    readTriggers: readTriggers, skillToRaw: skillToRaw, rawToSkill: rawToSkill,
    skillRankProblems: skillRankProblems, skillPowerProblem: skillPowerProblem, coinGrants: coinGrants,
    levelFor: levelFor, nextLevelAt: nextLevelAt, hpCost: hpCost, powerCost: powerCost, speedCost: speedCost,
    maxBasePower: maxBasePower, maxCoinPower: maxCoinPower, title: title, capitalize: capitalize
  };
});
