// Run by tests/test_builder_site.py. Reads JSON on stdin ({ lines: [...], skills: [...] }), prints what the skill preview makes of it.
const fs = require('fs');
const P = require('../docs/builder/preview.js');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify({
  tokens: input.lines.map((line) => P.tokenize(line)),
  models: input.skills.map((skill) => P.model(skill)),
  tones: (input.tones || []).map((key) => P.toneOf(key)),
  classes: (input.timings || []).map((label) => P.timingClass(label)),
  frameTiers: P.frameTiers,
  arts: (input.arts || []).map((art) => ({ style: P.artStyle(art), clamped: P.clampArt(art) })),
}));
