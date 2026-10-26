// Run by tests/test_builder_site.py. Reads JSON on stdin ({ lines: [...], skills: [...], ... }), prints what the skill preview makes of it.
const fs = require('fs');
const P = require('../docs/builder/preview.js');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify({
  tokens: input.lines.map((line) => P.tokenize(line)),
  models: input.skills.map((skill) => P.model(skill)),
  tones: (input.tones || []).map((key) => P.toneOf(key)),
  classes: (input.timings || []).map((label) => P.timingClass(label)),
  definitions: (input.definitions || []).map(([kind, key]) => P.definition(kind, key)),
  frameTiers: P.frameTiers,
  passives: (input.passives || []).map((passive) => P.passiveModel(passive)),
  flavours: (input.flavours || []).map((text) => P.flavourOf(text)),
  flavourMax: P.flavourMax,
  frameChoices: P.frameChoices(),
  autoFrames: input.skills.map((skill) => P.autoFrame(skill)),
  prose: (input.prose || []).map((text) => P.proseTokens(text)),
  arts: (input.arts || []).map((art) => ({ style: P.artStyle(art, art.open), clamped: P.clampArt(art, art.open) })),
}));
