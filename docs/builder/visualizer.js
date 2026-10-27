/*
 * Lugic character builder: the Visualizer. It draws a character's skills, or passives, as one tall picture in the style of the
 * Limbus Company identity teasers: the character's picture dimmed behind everything, a name plate at the top, one block for each
 * skill or passive, and a WARNING strip along the top and the bottom that shows the picture again, in full colour and inverted.
 *
 * The page is one column, 1500 px wide, and starts as a square: it only gets taller when there is more to show. The drawing is done
 * on a canvas, so what the page shows and what the Download button saves are the same picture.
 *
 * wrapPieces(), piecesOf(), stripLayout() and artRect() are plain functions (tests/visualizer_check.js runs them in Node);
 * everything that touches a canvas lives under create().
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) { module.exports = factory(require('./engine.js'), require('./preview.js')); }
  else { root.LugicVisualizer = factory(root.LugicEngine, root.LugicPreview); }
})(typeof self !== 'undefined' ? self : this, function (E, P) {
  'use strict';

  var WIDTH = 1500;           // every picture is this wide
  var MIN_HEIGHT = 1500;      // and at least this tall: it starts as a square
  var MARGIN = 70;
  var STRIP_H = 64;           // the WARNING strip along the top and the bottom
  var BOX_PAD = 28;
  var GAP = 26;               // between two blocks
  var BODY = 26;              // body text, in px of the 1500 px picture
  var LINE = 38;
  var HEADER_BOTTOM = 356;    // where the first block starts
  var EMBLEM = 200;           // the skill frame
  var RARITY_MAX = 3;

  // ---- colours -------------------------------------------------------------------------------------------------
  // Read off the identity teasers: cream body text, yellow for what helps, red for what hurts, brown for the rest, and the timing
  // tags blue (On Use), orange (Clash Win), red (Clash Lose) or green (anything else). The Sins are the wiki's.
  var INK = {
    cream: '#f3e6cf', hot: '#fff3d6', dim: '#8d7a64', big: '#3d2b1b', brown: '#b27a43', bar: '#a76d3a', barDark: '#5e3a1e',
    use: '#3cbced', hit: '#9be85a', win: '#ff8a1f', lose: '#ee2210', flag: '#d98a3d',
    positive: '#fed81c', negative: '#ee1c0c', neutral: '#b9763a'
  };
  var SINS = {
    wrath: ['#fe0000', '#520000'], lust: ['#fb6500', '#521b00'], sloth: ['#f7c729', '#4f3f06'], gluttony: ['#9dfe00', '#2f5300'],
    gloom: ['#0dc1eb', '#013d4b'], pride: ['#0049d3', '#001142'], envy: ['#9a4dff', '#2a0a4a'], none: ['#9f693a', '#301d0c']
  };
  function sinColours(sin) { return SINS[sin] || SINS.none; }

  // ---- text ----------------------------------------------------------------------------------------------------

  function colourOf(token) {
    if (token.kind === 'tag') {
      if (token.cls === 'use') { return INK.use; }
      if (token.cls === 'clashwin') { return INK.win; }
      if (token.cls === 'clashlose') { return INK.lose; }
      if (token.cls === 'flag') { return INK.flag; }
      return INK.hit;
    }
    var tone = token.kind === 'status' ? P.toneOf(token.key) : token.tone;
    return INK[tone] && tone !== 'cream' ? INK[tone] : INK.cream;
  }

  // The coloured pieces of a line: [{ text, color }]. A timing tag is followed by a space, the way the page draws it.
  function piecesOf(tokens) {
    var out = [];
    (tokens || []).forEach(function (t) {
      out.push({ text: t.text, color: colourOf(t) });
      if (t.kind === 'tag') { out.push({ text: ' ', color: INK.cream }); }
    });
    return out;
  }

  // Breaks pieces into lines no wider than `width`: [{ runs: [{ text, color, x }], width }]. `measure(text)` is the width of the text in
  // the font the lines are drawn in. A word is never split, unless it is wider than a whole line.
  function wrapPieces(pieces, width, measure) {
    var words = [];
    pieces.forEach(function (p) {
      String(p.text).split(/(\s+)/).forEach(function (part) {
        if (part) { words.push({ text: /^\s+$/.test(part) ? ' ' : part, color: p.color, space: /^\s+$/.test(part) }); }
      });
    });
    var lines = [];
    var line = { runs: [], width: 0 };
    function newLine() {
      while (line.runs.length && line.runs[line.runs.length - 1].space) { var gone = line.runs.pop(); line.width -= gone.w; }
      lines.push(line);
      line = { runs: [], width: 0 };
    }
    function put(w, w2) { line.runs.push({ text: w.text, color: w.color, x: line.width, w: w2, space: w.space }); line.width += w2; }
    words.forEach(function (w) {
      var ww = measure(w.text);
      if (w.space) { if (line.runs.length) { put(w, ww); } return; }
      if (line.width + ww > width && line.runs.length) { newLine(); }
      if (ww > width) {   // one word wider than the line: cut it where it no longer fits
        var rest = w.text;
        while (rest.length) {
          var n = rest.length;
          while (n > 1 && line.width + measure(rest.slice(0, n)) > width) { n--; }
          if (line.width + measure(rest.slice(0, n)) > width && line.runs.length) { newLine(); continue; }
          put({ text: rest.slice(0, n), color: w.color, space: false }, measure(rest.slice(0, n)));
          rest = rest.slice(n);
          if (rest.length) { newLine(); }
        }
        return;
      }
      put(w, ww);
    });
    if (line.runs.length || !lines.length) { newLine(); }
    return lines;
  }

  // ---- the WARNING strip -----------------------------------------------------------------------------------------
  // The pattern is the word, then a run of slanted bars, then the word again, all the way across and cut off at both ends,
  // the way the teasers' SEASON strip is. Returns what to draw: [{ kind: 'word'|'bars', x }], starting a little left of the
  // edge so both ends are cut. `word` is the width of the word, `bars` the width of the run of bars, `gap` the space round each.
  function stripLayout(width, word, bars, gap) {
    var unit = word + gap + bars + gap;
    var out = [];
    var x = -Math.round(unit * 0.3);
    while (x < width) {
      out.push({ kind: 'word', x: x });
      out.push({ kind: 'bars', x: x + word + gap });
      x += unit;
    }
    return out;
  }

  // ---- the picture behind everything ---------------------------------------------------------------------------
  // art = { a (width / height), x, y, zoom }. Where the picture lands on a canvas of the given size: it covers the whole canvas at
  // zoom 1, and x and y move it as a share of the canvas. The same placement rules as a skill's picture (LugicPreview.clampArt).
  function artRect(art, width, height) {
    var c = P.clampArt({ src: art.src, a: art.a > 0 ? art.a : 1, x: art.x, y: art.y, zoom: art.zoom }, width / height);
    return { x: (0.5 - c.w / 2 + c.x) * width, y: (0.5 - c.h / 2 + c.y) * height, w: c.w * width, h: c.h * height, art: c };
  }

  // ---- everything below needs a canvas -----------------------------------------------------------------------------

  function rng(seed) {      // small seeded generator, so the speckles are the same at every redraw
    var s = seed >>> 0;
    return function () {
      s = (s + 0x6D2B79F5) >>> 0;
      var t = s;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function poly(ctx, points) {
    ctx.beginPath();
    points.forEach(function (p, i) { if (i) { ctx.lineTo(p[0], p[1]); } else { ctx.moveTo(p[0], p[1]); } });
    ctx.closePath();
  }
  function hex(ctx, cx, cy, r) {          // a hexagon standing on a point
    var pts = [];
    for (var i = 0; i < 6; i++) { var a = Math.PI / 3 * i - Math.PI / 2; pts.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]); }
    poly(ctx, pts);
  }
  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function hasFont(family) {
    try {
      if (typeof document === 'undefined' || !document.fonts) { return false; }
      var found = false;
      document.fonts.forEach(function (face) { if (String(face.family).replace(/["']/g, '') === family && face.status === 'loaded') { found = true; } });
      return found;
    } catch (e) { return false; }
  }
  // The names and titles are Mikodacs when the page has it; without it they are drawn in heavy Pretendard so the picture still reads.
  function fonts() {
    var mik = hasFont('Mikodacs');
    return {
      head: function (px) { return mik ? '400 ' + px + 'px Mikodacs, Pretendard, sans-serif' : '600 ' + px + 'px Pretendard, "Segoe UI", Arial, sans-serif'; },
      body: function (px, weight) { return (weight || 400) + ' ' + px + 'px Pretendard, "Segoe UI", Arial, sans-serif'; },
      italic: function (px) { return 'italic 400 ' + px + 'px Pretendard, "Segoe UI", Arial, sans-serif'; },
      tall: function (px) { return '400 ' + px + 'px "Bebas Neue", Impact, "Arial Narrow", sans-serif'; },
      mikodacs: mik
    };
  }

  // The fonts the picture asks for, loaded before the first real drawing. Resolves when they are all in (or have failed).
  function loadFonts() {
    if (typeof document === 'undefined' || !document.fonts || !document.fonts.load) { return Promise.resolve(); }
    var wanted = ['400 30px Pretendard', '600 30px Pretendard', 'italic 400 30px Pretendard', '400 30px "Bebas Neue"', '400 30px Mikodacs'];
    return Promise.all(wanted.map(function (f) { return document.fonts.load(f).catch(function () { return []; }); }));
  }

  // ---- the drawing itself -------------------------------------------------------------------------------------------

  // create(canvas, source) -> { draw, redraw, download, height }
  // source.data()      -> { view, name, level, lines: [two small lines], skills: [{ model, art }], passives: [passiveModel] }
  // source.settings()  -> { strength, zoom, x, y, title, strip, marks, boxes, accent, iconSize }
  // source.images()    -> { bg, icon, logo }: { src, a } or null
  // source.iconFile(name) -> a path for the bot's own icon (slash, blunt, pierce, coin_base), or null
  function create(canvas, source) {
    var cache = {};
    var pending = null;
    var texture = {};

    function picture(src) {
      if (!src) { return null; }
      var c = cache[src];
      if (c) { return c.ok ? c.img : null; }
      var img = new Image();
      c = cache[src] = { img: img, ok: false };
      img.onload = function () { c.ok = true; redraw(); };
      img.onerror = function () { c.failed = true; };
      img.src = src;
      return null;
    }
    function bot(name) { var f = source.iconFile ? source.iconFile(name) : null; return f ? picture(f) : null; }

    function drawArt(ctx, art, img, w, h, alpha, dy) {
      var r = artRect(art, w, h);
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.drawImage(img, r.x, r.y + (dy || 0), r.w, r.h);
      ctx.restore();
    }

    // -- small pieces
    function text(ctx, s, x, y, font, color, o) {
      o = o || {};
      ctx.save();
      ctx.font = font;
      ctx.textAlign = o.align || 'left';
      ctx.textBaseline = o.base || 'alphabetic';
      if ('letterSpacing' in ctx) { ctx.letterSpacing = (o.spacing || 0) + 'px'; }
      if (o.shadow) { ctx.fillStyle = o.shadow[2]; ctx.fillText(s, x + o.shadow[0], y + o.shadow[1]); }
      if (o.stroke) { ctx.strokeStyle = o.stroke[1]; ctx.lineWidth = o.stroke[0]; ctx.lineJoin = 'round'; ctx.strokeText(s, x, y); }
      ctx.fillStyle = color;
      ctx.fillText(s, x, y);
      ctx.restore();
    }
    function width(ctx, s, font, spacing) {
      ctx.save();
      ctx.font = font;
      if ('letterSpacing' in ctx) { ctx.letterSpacing = (spacing || 0) + 'px'; }
      var w = ctx.measureText(s).width;
      ctx.restore();
      return w;
    }
    // the biggest size of a font (from `px` down to `least`) where the text fits `max`
    function fit(ctx, s, make, px, least, max) {
      var size = px;
      while (size > least && width(ctx, s, make(size)) > max) { size -= 2; }
      return size;
    }

    function lineBlock(ctx, F, pieces, maxWidth, o) {
      o = o || {};
      var px = o.px || BODY;
      var font = o.font || F.body(px);
      var lines = wrapPieces(pieces, maxWidth, function (t) { return width(ctx, t, font); });
      var lh = o.lh || LINE;
      return {
        h: lines.length * lh,
        draw: function (c, x, y) {
          lines.forEach(function (ln, i) {
            ln.runs.forEach(function (run) { if (!run.space) { text(c, run.text, x + run.x, y + i * lh + px + (lh - px) / 2 - 4, font, o.color || run.color); } });
          });
        }
      };
    }

    // -- the textures: a halftone of dots, and fine speckle, both from a fixed seed
    function speckle(ctx, x, y, w, h, seed, count, dark, light) {
      var r = rng(seed);
      for (var i = 0; i < count; i++) {
        ctx.fillStyle = r() < 0.6 ? dark.replace('A', (0.06 + r() * 0.2).toFixed(2)) : light.replace('A', (0.08 + r() * 0.2).toFixed(2));
        ctx.fillRect(x + r() * w, y + r() * h, 1 + r() * 2.6, 1 + r() * 2.6);
      }
    }
    function halftone(ctx, x, y, w, h, from, color) {
      var step = 9;
      for (var gy = 0, row = 0; gy < h + step; gy += step, row++) {
        for (var gx = from; gx < w + step; gx += step) {
          var t = (gx - from) / Math.max(1, w - from);
          var rad = 0.5 + 2.7 * t;
          ctx.beginPath();
          ctx.arc(x + gx + (row % 2 ? step / 2 : 0), y + gy, rad, 0, Math.PI * 2);
          ctx.fillStyle = color;
          ctx.fill();
        }
      }
    }

    // -- the plate with the character's name, the line above it, and the rarity marks
    function drawPlate(ctx, F, x, y, w, h, accent, name) {
      var skew = 46;
      ctx.save();
      poly(ctx, [[x, y], [x + w, y], [x + w - skew, y + h], [x, y + h]]);
      ctx.fillStyle = accent;
      ctx.fill();
      ctx.clip();
      speckle(ctx, x, y, w, h, 11, 360, 'rgba(30,36,44,A)', 'rgba(255,255,255,A)');
      halftone(ctx, x, y, w, h, w * 0.42, 'rgba(34,40,48,0.5)');
      ctx.restore();
      var label = name || 'Unnamed';
      var room = w - skew - 48;
      var cx = x + (w - skew) / 2 + 10;
      var size = fit(ctx, label, F.head, 68, 40, room);
      var lines = [label];
      if (width(ctx, label, F.head(size)) > room) {
        // a long name goes on two lines, split at the space that makes them most even; one with no space is cut short with "..."
        var best = null;
        for (var i = 0; i < label.length; i++) {
          if (label.charAt(i) !== ' ') { continue; }
          var a = label.slice(0, i), b = label.slice(i + 1);
          var longest = Math.max(width(ctx, a, F.head(34)), width(ctx, b, F.head(34)));
          if (!best || longest < best.longest) { best = { a: a, b: b, longest: longest }; }
        }
        if (best) { lines = [best.a, best.b]; } else { lines = [label]; }
        size = Math.min.apply(null, lines.map(function (line) { return fit(ctx, line, F.head, 40, 24, room); }));
        lines = lines.map(function (line) {
          var cut = line, shown = line;
          while (cut.length > 1 && width(ctx, shown, F.head(size)) > room) { cut = cut.slice(0, -1); shown = cut.replace(/\s+$/, '') + '...'; }
          return shown;
        });
      }
      lines.forEach(function (line, i) {
        var centre = y + h / 2 + (i - (lines.length - 1) / 2) * size * 1.02;
        text(ctx, line, cx, centre + size * 0.34, F.head(size), '#f6dcae', { align: 'center', shadow: [3, 3, '#5a3112'] });
      });
    }
    function drawTitle(ctx, F, x, y, label, accent, maxWidth) {
      var size = fit(ctx, label, F.head, 38, 22, maxWidth);
      var w = width(ctx, label, F.head(size));
      ctx.save();
      ctx.fillStyle = accent;
      ctx.globalAlpha = 0.85;
      ctx.fillRect(x - 6, y - 6, w + 18, 20);
      ctx.restore();
      speckle(ctx, x - 6, y - 6, w + 18, 20, 5, 90, 'rgba(30,36,44,A)', 'rgba(255,255,255,A)');
      text(ctx, label, x, y, F.head(size), INK.brown, { shadow: [2, 2, 'rgba(0,0,0,0.85)'] });
    }
    function drawRarity(ctx, cx, cy, count) {
      var w = 38, h = 66, step = 36;
      var x0 = cx - ((count - 1) * step) / 2;
      for (var i = 0; i < count; i++) {
        var x = x0 + i * step;
        ctx.save();
        ctx.shadowColor = '#ff9a1a';
        ctx.shadowBlur = 16;
        ctx.beginPath();
        ctx.moveTo(x, cy - h / 2);
        ctx.quadraticCurveTo(x + w * 0.62, cy - h * 0.2, x, cy + h / 2);
        ctx.quadraticCurveTo(x - w * 0.62, cy - h * 0.2 + 0, x, cy - h / 2);
        ctx.closePath();
        var g = ctx.createLinearGradient(x, cy - h / 2, x, cy + h / 2);
        g.addColorStop(0, '#ffd75e');
        g.addColorStop(1, '#ff8a12');
        ctx.fillStyle = g;
        ctx.fill();
        ctx.shadowBlur = 0;
        ctx.lineWidth = 3;
        ctx.strokeStyle = '#ffe596';
        ctx.stroke();
        ctx.lineWidth = 5;
        ctx.strokeStyle = '#b84f00';
        ctx.beginPath();
        ctx.ellipse(x, cy, 6.5, 12, 0, 0, Math.PI * 2);
        ctx.moveTo(x - 9, cy + 15);
        ctx.lineTo(x + 9, cy - 15);
        ctx.stroke();
        ctx.restore();
      }
    }

    // -- the strips along the top and the bottom
    function glyphs(ctx, F, y, word, color, stroke) {
      var fontPx = 42;
      var font = F.tall(fontPx);
      var spacing = 3;
      var wordW = width(ctx, word, font, spacing);
      var bars = 4, barW = 10, barGap = 9, barH = 30, slant = 14;
      var barsW = bars * barW + (bars - 1) * barGap + slant;
      var layout = stripLayout(WIDTH, wordW, barsW, 30);
      ctx.save();
      ctx.fillStyle = color;
      ctx.strokeStyle = color;
      layout.forEach(function (item) {
        if (item.kind === 'word') {
          text(ctx, word, item.x, y + STRIP_H / 2 + fontPx * 0.35, font, color, { spacing: spacing, stroke: stroke ? [2.2, color] : null });
        } else {
          for (var i = 0; i < bars; i++) {
            var bx = item.x + i * (barW + barGap);
            var by = y + (STRIP_H - barH) / 2;
            poly(ctx, [[bx + slant, by], [bx + slant + barW, by], [bx + barW, by + barH], [bx, by + barH]]);
            ctx.fill();
          }
        }
      });
      ctx.restore();
    }
    function drawStrip(ctx, F, y, word, accent, art, img) {
      var label = (word || '').trim().toUpperCase() || 'WARNING';
      ctx.save();
      ctx.fillStyle = 'rgba(0,0,0,0.78)';
      ctx.fillRect(0, y, WIDTH, STRIP_H);
      ctx.restore();
      glyphs(ctx, F, y, label, accent, false);      // where there is no picture the letters are the accent colour, dimly
      if (art && img) {
        // the picture at full strength, only inside the letters, with its colours inverted
        var tmp = texture.strip || (texture.strip = document.createElement('canvas'));
        tmp.width = WIDTH;
        tmp.height = STRIP_H;
        var t = tmp.getContext('2d');
        t.clearRect(0, 0, WIDTH, STRIP_H);
        glyphs(t, F, 0, label, '#ffffff', true);
        t.globalCompositeOperation = 'source-in';
        drawArt(t, art, img, WIDTH, canvas.height, 1, -y);
        t.globalCompositeOperation = 'source-over';
        var data = t.getImageData(0, 0, WIDTH, STRIP_H);
        var d = data.data;
        for (var i = 0; i < d.length; i += 4) { d[i] = 255 - d[i]; d[i + 1] = 255 - d[i + 1]; d[i + 2] = 255 - d[i + 2]; }
        t.putImageData(data, 0, 0);
        ctx.drawImage(tmp, 0, y);
      }
    }

    // -- a skill's emblem, coins, banner, and the numbered hexagon of a coin row
    function drawEmblem(ctx, x, y, size, m, art) {
      var colours = sinColours(m.frame.sin);
      var open = { x: x + size * 0.17, y: y + size * 0.23, w: size * 0.66, h: size * 0.66 };
      var clip = [[1.0, 35.3], [28.2, 1.1], [69.0, 1.1], [98.2, 35.3], [88.7, 80.6], [47.1, 98.9], [9.8, 79.8]];
      ctx.save();
      poly(ctx, clip.map(function (p) { return [open.x + open.w * p[0] / 100, open.y + open.h * p[1] / 100]; }));
      ctx.clip();
      ctx.fillStyle = '#050404';
      ctx.fillRect(open.x, open.y, open.w, open.h);
      var img = art && art.src ? picture(art.src) : null;
      if (img) {
        var s = P.artStyle({ src: art.src, a: art.a, x: art.x, y: art.y, zoom: art.zoom });
        ctx.drawImage(img, open.x + open.w * s.left / 100, open.y + open.h * s.top / 100, open.w * s.width / 100, open.h * s.height / 100);
      } else {
        var kind = bot(m.type);
        if (kind) { ctx.drawImage(kind, open.x + open.w * 0.3, open.y + open.h * 0.3, open.w * 0.4, open.h * 0.4); }
      }
      ctx.restore();
      var frame = m.frame.file ? picture(m.frame.file) : null;
      if (frame) { ctx.drawImage(frame, x, y, size, size); }
      else if (!m.frame.file) {
        var k = size / 396;
        ctx.save();
        poly(ctx, [[51, 178], [132, 75], [255, 75], [343, 178], [314, 314], [189, 369], [77, 312]].map(function (p) { return [x + p[0] * k, y + p[1] * k]; }));
        ctx.lineWidth = 14 * k;
        ctx.lineJoin = 'round';
        ctx.strokeStyle = colours[0];
        ctx.stroke();
        ctx.restore();
      }
      // the damage type, in a small hexagon at the foot of the frame
      var bx = x + size * 0.5, by = y + size * 0.93, br = 30;
      ctx.save();
      hex(ctx, bx, by, br);
      ctx.fillStyle = '#16100c';
      ctx.fill();
      ctx.lineWidth = 5;
      ctx.strokeStyle = colours[0];
      ctx.stroke();
      ctx.restore();
      var badge = bot(m.type);
      if (badge) { ctx.drawImage(badge, bx - 19, by - 19, 38, 38); }
    }
    function drawCoin(ctx, x, y, size) {
      var coin = bot('coin_base') || bot('coin_heads');
      if (coin) { ctx.drawImage(coin, x, y, size, size); return; }
      ctx.save();
      hex(ctx, x + size / 2, y + size / 2, size / 2);
      ctx.fillStyle = '#c98b4a';
      ctx.fill();
      ctx.restore();
    }
    function drawBanner(ctx, F, x, y, label, sin, maxWidth) {
      var colours = sinColours(sin);
      var h = 54, slant = 30, tail = 78;
      var size = fit(ctx, label, F.head, 34, 20, maxWidth - 40 - slant - 8);
      var tw = width(ctx, label, F.head(size));
      var w = Math.min(maxWidth, tw + 40 + slant);
      ctx.save();
      poly(ctx, [[x, y], [x + w - slant, y], [x + w, y + h], [x, y + h]]);
      ctx.clip();
      var g = ctx.createLinearGradient(0, y, 0, y + h);
      g.addColorStop(0, colours[0]);
      g.addColorStop(1, colours[0]);
      ctx.globalAlpha = 0.82;
      ctx.fillStyle = g;
      ctx.fillRect(x, y, w, h);
      ctx.globalAlpha = 1;
      // the dark, striped, slanted end
      poly(ctx, [[x + w - slant - tail, y + h], [x + w - slant - tail + slant, y], [x + w, y], [x + w, y + h]]);
      ctx.fillStyle = colours[1];
      ctx.fill();
      ctx.fillStyle = colours[0];
      for (var i = 0; i < 4; i++) {
        var sx = x + w - slant - tail + 24 + i * 14;
        poly(ctx, [[sx + slant, y], [sx + slant + 6, y], [sx + 6, y + h], [sx, y + h]]);
        ctx.globalAlpha = 0.55;
        ctx.fill();
      }
      ctx.restore();
      text(ctx, label, x + 20, y + h / 2 + size * 0.34, F.head(size), '#fffbde', { shadow: [2, 2, 'rgba(0,0,0,0.85)'] });
      return { w: w, h: h };
    }
    function drawNumber(ctx, F, cx, cy, n) {
      ctx.save();
      hex(ctx, cx, cy, 22);
      ctx.fillStyle = 'rgba(0,0,0,0.6)';
      ctx.fill();
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = INK.cream;
      ctx.stroke();
      ctx.restore();
      var numeral = ['I', 'II', 'III', 'IV', 'V', 'VI'][n - 1] || String(n);
      text(ctx, numeral, cx, cy + 8, F.body(n > 3 ? 18 : 22, 600), INK.cream, { align: 'center' });
    }
    function drawBar(ctx, F, x, y, label, maxWidth) {
      var h = 50, slant = 34;
      var size = fit(ctx, label, F.head, 32, 20, maxWidth - 70 - slant);
      var w = Math.min(maxWidth, width(ctx, label, F.head(size)) + 56 + slant);
      ctx.save();
      poly(ctx, [[x, y], [x + w - slant, y], [x + w, y + h], [x, y + h]]);
      var g = ctx.createLinearGradient(x, 0, x + w, 0);
      g.addColorStop(0, INK.bar);
      g.addColorStop(0.7, INK.bar);
      g.addColorStop(1, INK.barDark);
      ctx.fillStyle = g;
      ctx.fill();
      ctx.clip();
      ctx.fillStyle = 'rgba(255,214,150,0.35)';
      for (var i = 0; i < 4; i++) {
        var sx = x + w - slant - 62 + i * 14;
        poly(ctx, [[sx + slant, y], [sx + slant + 6, y], [sx + 6, y + h], [sx, y + h]]);
        ctx.fill();
      }
      ctx.restore();
      text(ctx, label, x + 22, y + h / 2 + size * 0.34, F.head(size), '#fff0cf', { shadow: [2, 2, 'rgba(0,0,0,0.85)'] });
      return { w: w, h: h };
    }

    // -- the blocks
    function skillBlock(ctx, F, entry, label, inner) {
      var m = entry.model;
      var colX = EMBLEM + 30;
      var colW = inner - colX;
      var parts = [];     // { y, h, draw(c, x, y) }
      var y = 0;
      // coins, then the banner
      var coins = m.coins;
      parts.push({ y: y + 2, draw: function (c, x, top) { for (var i = 0; i < coins; i++) { drawCoin(c, x + i * 40, top, 36); } } });
      y += 44;
      var bannerBox = { w: 0, h: 54 };
      parts.push({ y: y, draw: function (c, x, top) { bannerBox = drawBanner(c, F, x, top, m.name, m.frame.sin, colW - 190); } });
      y += 54 + 14;
      // the numbers: damage type, base power, power per coin
      parts.push({
        y: y, draw: function (c, x, top) {
          var kind = bot(m.type);
          if (kind) { c.drawImage(kind, x, top + 2, 40, 40); }
          var base = String(m.base);
          text(c, base, x + 52, top + 38, F.tall(54), INK.hot);
          var bw = width(c, base, F.tall(54));
          text(c, (m.coin >= 0 ? '+' : '') + m.coin + ' per coin', x + 52 + bw + 14, top + 36, F.body(25, 600), INK.positive);
        }
      });
      y += 52;
      if (m.flags.length) {
        var flagLine = lineBlock(ctx, F, m.flags.reduce(function (all, t, i) { return all.concat(i ? [{ text: ' ', color: INK.cream }] : [], [{ text: t.text, color: INK.flag }]); }, []), colW);
        var fy = y;
        parts.push({ y: fy, draw: function (c, x, top) { flagLine.draw(c, x, top); } });
        y += flagLine.h + 4;
      }
      m.general.forEach(function (tokens) {
        var block = lineBlock(ctx, F, piecesOf(tokens), colW);
        var gy = y;
        parts.push({ y: gy, draw: function (c, x, top) { block.draw(c, x, top); } });
        y += block.h + 6;
      });
      y += 4;
      m.rows.forEach(function (row) {
        var bodyW = colW - 62;
        var blocks = row.lines.length
          ? row.lines.map(function (tokens) { return lineBlock(ctx, F, piecesOf(tokens), bodyW); })
          : [lineBlock(ctx, F, [{ text: 'No effect', color: INK.dim }], bodyW)];
        var h = Math.max(46, blocks.reduce(function (sum, b) { return sum + b.h; }, 0));
        var ry = y;
        parts.push({
          y: ry, draw: function (c, x, top) {
            drawNumber(c, F, x + 22, top + 22, row.n);
            var yy = top + (h - blocks.reduce(function (sum, b) { return sum + b.h; }, 0)) / 2;
            blocks.forEach(function (b) { b.draw(c, x + 62, yy); yy += b.h; });
          }
        });
        y += h + 8;
      });
      var h = Math.max(EMBLEM + 10, y);
      return {
        h: h,
        draw: function (c, x, top) {
          text(c, label, x + inner, top + 78, F.tall(92), INK.big, { align: 'right' });
          drawEmblem(c, x, top, EMBLEM, m, entry.art);
          parts.forEach(function (part) { part.draw(c, x + colX, top + part.y); });
        }
      };
    }

    function passiveBlock(ctx, F, p, inner) {
      var label = p.kind === 'support' ? 'SUPPORT PASSIVE' : 'PASSIVE';
      var parts = [];
      var y = 0;
      parts.push({ y: 0, draw: function (c, x, top) { drawBar(c, F, x, top, p.name, inner - 300); } });
      y += 50 + 12;
      var cost = p.cost + (p.cost === 1 ? ' Passive Point' : ' Passive Points');
      parts.push({ y: y, draw: function (c, x, top) { text(c, cost, x, top + 22, F.body(21, 600), INK.dim); } });
      y += 34;
      if (!p.lines.length) {
        var none = lineBlock(ctx, F, [{ text: 'No rule lines yet', color: INK.dim }], inner);
        parts.push({ y: y, draw: function (c, x, top) { none.draw(c, x, top); } });
        y += none.h;
      }
      p.lines.forEach(function (tokens) {
        var block = lineBlock(ctx, F, piecesOf(tokens), inner);
        var ly = y;
        parts.push({ y: ly, draw: function (c, x, top) { block.draw(c, x, top); } });
        y += block.h + 8;
      });
      p.flavour.forEach(function (line) {
        var block = lineBlock(ctx, F, [{ text: line, color: '#9b7448' }], inner, { font: F.italic(BODY - 2) });
        var fy = y + 4;
        parts.push({ y: fy, draw: function (c, x, top) { block.draw(c, x, top); } });
        y += block.h + 12;
      });
      return {
        h: Math.max(y, 96),
        draw: function (c, x, top) {
          text(c, label, x + inner, top + 74, F.tall(92), INK.big, { align: 'right' });
          parts.forEach(function (part) { part.draw(c, x, top + part.y); });
        }
      };
    }

    function emptyBlock(ctx, F, message, inner) {
      var block = lineBlock(ctx, F, [{ text: message, color: INK.dim }], inner);
      return { h: Math.max(block.h, 60), draw: function (c, x, top) { block.draw(c, x, top + 10); } };
    }

    // -- everything
    var measureCanvas = null;
    function draw() {
      var data = source.data();
      var S = source.settings();
      var images = source.images() || {};
      var F = fonts();
      measureCanvas = measureCanvas || document.createElement('canvas');
      var mctx = measureCanvas.getContext('2d');

      var inner = WIDTH - 2 * MARGIN - 2 * BOX_PAD;
      var blocks = [];
      if (data.view === 'passives') {
        if (!data.passives.length) { blocks.push(emptyBlock(mctx, F, 'No passives yet. Add some in the Passives tab and they show up here.', inner)); }
        data.passives.forEach(function (p) { blocks.push(passiveBlock(mctx, F, p, inner)); });
      } else {
        if (!data.skills.length) { blocks.push(emptyBlock(mctx, F, 'No skills yet. Add some in the Skills tab and they show up here.', inner)); }
        var n = 0;
        data.skills.forEach(function (entry) {
          var defense = entry.model.frame.sin === 'def';
          if (!defense) { n += 1; }
          blocks.push(skillBlock(mctx, F, entry, defense ? 'DEFENSE' : 'SKILL ' + n, inner));
        });
      }
      var total = HEADER_BOTTOM;
      blocks.forEach(function (b) { total += b.h + 2 * BOX_PAD + GAP; });
      var height = Math.max(MIN_HEIGHT, total + 24 + STRIP_H);
      canvas.width = WIDTH;
      canvas.height = height;
      var ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, WIDTH, height);
      ctx.fillStyle = '#000';
      ctx.fillRect(0, 0, WIDTH, height);

      // the picture, dimmed, behind everything
      var bg = images.bg;
      var bgImg = bg ? picture(bg.src) : null;
      var art = bg ? { src: bg.src, a: bg.a, x: S.x || 0, y: S.y || 0, zoom: S.zoom || 1 } : null;
      if (bgImg) {
        drawArt(ctx, art, bgImg, WIDTH, height, Math.max(0, Math.min(1, (S.strength === undefined ? 55 : S.strength) / 100)), 0);
        var shade = ctx.createLinearGradient(0, 0, 0, height);
        shade.addColorStop(0, 'rgba(0,0,0,0.25)');
        shade.addColorStop(0.5, 'rgba(0,0,0,0.05)');
        shade.addColorStop(1, 'rgba(0,0,0,0.35)');
        ctx.fillStyle = shade;
        ctx.fillRect(0, 0, WIDTH, height);
      }

      var accent = S.accent || '#aeb7bf';

      // the header: logo and the big dim title on the left; title, name plate and rarity on the right
      var right = WIDTH - MARGIN;
      var plate = { x: right - 560, y: 176, w: 450, h: 86 };
      var iconImg = images.icon ? picture(images.icon.src) : null;
      if (iconImg) {
        var side = Math.max(40, S.iconSize || 300);
        var ratio = iconImg.naturalWidth / Math.max(1, iconImg.naturalHeight);
        var iw = ratio >= 1 ? side : side * ratio, ih = ratio >= 1 ? side / ratio : side;
        ctx.save();
        ctx.globalAlpha = 0.24;
        ctx.drawImage(iconImg, plate.x + plate.w / 2 - iw / 2, plate.y + plate.h / 2 - ih / 2, iw, ih);
        ctx.restore();
      }
      var leftX = MARGIN;
      var logoImg = images.logo ? picture(images.logo.src) : null;
      if (logoImg) {
        var lr = logoImg.naturalWidth / Math.max(1, logoImg.naturalHeight);
        var lw = Math.min(260, 120 * lr), lh = lw / lr;
        ctx.drawImage(logoImg, leftX, 96 + (120 - lh) / 2, lw, lh);
        leftX += lw + 30;
      }
      var heading = data.view === 'passives' ? 'PASSIVE INFO' : 'SKILL INFO';
      var headSize = fit(ctx, heading, F.tall, 132, 60, plate.x - 40 - leftX);
      text(ctx, heading, leftX, 196, F.tall(headSize), INK.big);
      // the two small lines stay clear of the name plate: they shrink, and as a last resort are cut with "..."
      var roomForLines = plate.x - 24 - (leftX + 4);
      (data.lines || []).slice(0, 2).forEach(function (line, i) {
        var small = fit(ctx, line, F.body, 22, 15, roomForLines);
        var shown = line, cut = line;
        while (cut.length > 1 && width(ctx, shown, F.body(small)) > roomForLines) {
          cut = cut.slice(0, -1);
          shown = cut.replace(/[\s·/]+$/, '') + '...';
        }
        text(ctx, shown, leftX + 4, 238 + i * 30, F.body(small), '#8d6a43');
      });
      if ((S.title || '').trim()) { drawTitle(ctx, F, plate.x + 18, 150, S.title.trim(), accent, plate.w + 90); }
      drawPlate(ctx, F, plate.x, plate.y, plate.w, plate.h, accent, data.name);
      var marks = Math.max(0, Math.min(RARITY_MAX, S.marks === undefined ? RARITY_MAX : S.marks));
      if (marks) { drawRarity(ctx, right - 46, 132, marks); }

      // the blocks
      var y = HEADER_BOTTOM;
      blocks.forEach(function (b) {
        if (S.boxes !== false) {
          ctx.save();
          roundRect(ctx, MARGIN, y, WIDTH - 2 * MARGIN, b.h + 2 * BOX_PAD, 8);
          ctx.fillStyle = 'rgba(0,0,0,0.62)';
          ctx.fill();
          ctx.lineWidth = 2;
          ctx.strokeStyle = 'rgba(255,255,255,0.26)';
          ctx.stroke();
          ctx.restore();
        }
        b.draw(ctx, MARGIN + BOX_PAD, y + BOX_PAD);
        y += b.h + 2 * BOX_PAD + GAP;
      });

      // the strips are last, on top of everything
      drawStrip(ctx, F, 0, S.strip, accent, bgImg ? art : null, bgImg);
      drawStrip(ctx, F, height - STRIP_H, S.strip, accent, bgImg ? art : null, bgImg);
      return { width: WIDTH, height: height, mikodacs: F.mikodacs };
    }

    function redraw() {
      if (pending) { return; }
      var run = function () { pending = null; try { draw(); } catch (e) { if (typeof console !== 'undefined') { console.error(e); } } };
      pending = typeof requestAnimationFrame === 'function' ? requestAnimationFrame(run) : setTimeout(run, 0);
    }

    function download(name) {
      return new Promise(function (resolve, reject) {
        draw();
        try {
          canvas.toBlob(function (blob) {
            if (!blob) { reject(new Error('The browser could not make the picture.')); return; }
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = name;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
            resolve(blob.size);
          }, 'image/png');
        } catch (e) { reject(e); }
      });
    }

    loadFonts().then(redraw);
    return { draw: draw, redraw: redraw, download: download };
  }

  return {
    create: create, wrapPieces: wrapPieces, piecesOf: piecesOf, stripLayout: stripLayout, artRect: artRect, colourOf: colourOf,
    width: WIDTH, minHeight: MIN_HEIGHT, stripHeight: STRIP_H, ink: INK, sins: SINS
  };
});
