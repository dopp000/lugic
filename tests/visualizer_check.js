// Run by tests/test_builder_site.py. Reads JSON on stdin, prints JSON on stdout.
// Runs the Visualizer's plain functions (no canvas): how lines are coloured and wrapped, how the WARNING strip repeats, where the picture lands.
const fs = require('fs');
const P = require('../docs/builder/preview.js');
const V = require('../docs/builder/visualizer.js');

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
// every character is `unit` px wide, so the wrapping can be worked out by hand
const measure = (unit) => (text) => text.length * unit;

process.stdout.write(JSON.stringify({
  constants: { width: V.width, minHeight: V.minHeight, stripHeight: V.stripHeight },
  pieces: (input.lines || []).map((line) => V.piecesOf(P.tokenize(line))),
  wraps: (input.wraps || []).map((w) => V.wrapPieces(w.pieces, w.width, measure(w.unit || 10)).map((line) => ({
    text: line.runs.map((r) => r.text).join(''), width: line.width, colours: line.runs.filter((r) => !r.space).map((r) => r.color),
  }))),
  strips: (input.strips || []).map((s) => V.stripLayout(s.width, s.word, s.bars, s.gap)),
  arts: (input.arts || []).map((a) => V.artRect(a.art, a.width, a.height)),
  colours: Object.fromEntries(Object.entries(V.ink)),
  sinKeys: Object.keys(V.sins),
}));
