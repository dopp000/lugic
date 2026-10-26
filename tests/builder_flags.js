// Run by tests/test_builder_site.py. Reads JSON on stdin, prints JSON on stdout.
// Asks the site's engine which part of the form each problem belongs to, how strong a coin status is and what a tag list is exported as.
const fs = require('fs');
const E = require('../docs/builder/engine.js');

const input = JSON.parse(fs.readFileSync(0, 'utf8'));

const strength = (input.strength || []).map(([name, pot, cnt]) => ({ keywordless: E.isKeywordless(name), strength: E.coinStrength(name, pot, cnt) }));
const reviews = (input.specs || []).map((spec) => {
  const state = E.fromSpec(spec);
  const review = E.review(state);
  return { parts: review.parts, own: review.own, ok: review.ok, problems: review.report.problems, problemParts: review.report.problemParts };
});
const exported = (input.tagLists || []).map((tags) => E.toSpec(Object.assign(E.fromSpec({ v: 1, name: 'X' }), { tags })).tags || null);
process.stdout.write(JSON.stringify({ strength, reviews, exported }));
