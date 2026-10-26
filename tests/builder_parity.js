// Run by tests/test_builder_site.py. Reads JSON on stdin, prints JSON on stdout.
// Runs the site's engine (docs/builder/engine.js) on the same builds the Python side checks.
const fs = require('fs');
const E = require('../docs/builder/engine.js');

const input = JSON.parse(fs.readFileSync(0, 'utf8'));

(async () => {
  const checks = input.specs.map((spec) => {
    const state = E.fromSpec(spec);
    const report = E.checkBuild(state);
    return {
      spent: report.spent,
      breakdown: report.breakdown,
      problems: report.problems,
      skillProblems: report.skillProblems,
      skillRank: state.skills.map((skill) => E.skillRankProblems(state.ranks, E.coinGrants(skill))),
      toSpec: E.toSpec(state),
    };
  });
  const codes = [];
  for (const spec of input.encode) { codes.push(await E.encode(spec)); }
  const decoded = [];
  for (const code of input.decode) { decoded.push(await E.decode(code)); }
  const cleaned = (input.flavours || []).map((text) => E.cleanFlavour(text));
  process.stdout.write(JSON.stringify({ checks, codes, decoded, cleaned }));
})().catch((error) => { console.error(error); process.exit(1); });
