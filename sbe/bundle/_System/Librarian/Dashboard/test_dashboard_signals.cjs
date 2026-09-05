const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
function harness() {
  const nodes = new Map();
  const element = () => ({ textContent: '', children: [], replaceChildren(...children) { this.children = children; } });
  const ctx = vm.createContext({ Intl, Date, document: {
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); },
    createElement: element,
  } });
  const source = fs.readFileSync(path.join(__dirname, 'dashboard.js'), 'utf8');
  vm.runInContext(source.slice(0, source.indexOf('$("run-button").addEventListener')), ctx);
  return { ctx, nodes };
}
test('client activity remains visible next to a historical quota error', () => {
  const { ctx, nodes } = harness();
  ctx.renderProvider('provider', { installed: true, status: 'unavailable', quota_exhausted: true,
    client_activity: [{ client: 'CLI', observed_at: '2026-09-04T23:57:00+02:00' }],
    last_orchestrated_call: { source: 'ruflo', status: 'unavailable', observed_at: '2026-09-04T22:27:00+02:00' },
    last_quota_check: { status: 'exhausted', observed_at: '2026-09-04T22:27:00+02:00' } });
  const lines = nodes.get('provider').children.map(x => x.textContent);
  assert.equal(lines.length, 3);
  assert.match(lines[0], /Attività CLI rilevata/);
  assert.match(lines[1], /Ultima chiamata Ruflo/);
  assert.match(lines[2], /all’ultimo tentativo/);
});
test('mixed deployment with an old server does not invent quota availability', () => {
  const { ctx, nodes } = harness();
  ctx.renderProvider('provider', { installed: true, status: 'started', general_activity: { observed_at: '2026-09-04T23:57:00+02:00' } });
  assert.equal(nodes.get('provider').children.at(-1).textContent, 'Quota non verificata');
  assert.equal(ctx.statusLabel('observed'), 'Attività rilevata');
});
