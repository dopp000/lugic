// Run by tests/test_line_picker.py. Reads JSON on stdin ({ picks: [{ pick, passive }] }) and prints, for each one, what the picker says about it.
const fs = require('fs');
const L = require('../docs/builder/lines.js');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(input.picks.map((item) => {
  const problem = L.problem(item.pick, item.passive);
  return { problem, line: problem ? null : L.compose(item.pick) };
})));
