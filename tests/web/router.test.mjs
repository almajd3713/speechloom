import assert from 'node:assert/strict';
import { test } from 'node:test';

import { jobHref, navKey, parseRoute } from '../../src/speechloom/web/js/router.js';

test('known routes', () => {
  assert.equal(parseRoute('').name, 'new');
  assert.equal(parseRoute('#/').name, 'new');
  assert.equal(parseRoute('#').name, 'new');
  assert.equal(parseRoute('#/jobs').name, 'jobs');
  assert.equal(parseRoute('#/jobs/').name, 'jobs');
  assert.equal(parseRoute('#/storage').name, 'storage');
  assert.equal(parseRoute('#/setup').name, 'setup');
});

test('a job route carries its id', () => {
  const route = parseRoute('#/jobs/abc123');
  assert.equal(route.name, 'job');
  assert.equal(route.params.id, 'abc123');
  assert.equal(navKey(route), 'jobs');
});

test('job ids round-trip through the href', () => {
  const id = 'a b/c?d';
  assert.equal(parseRoute(jobHref(id)).params.id, id);
});

test('the query string is parsed and does not change the route', () => {
  const route = parseRoute('#/jobs?state=failed&offset=50');
  assert.equal(route.name, 'jobs');
  assert.deepEqual(route.query, { state: 'failed', offset: '50' });
});

test('unknown routes are not found', () => {
  for (const hash of ['#/nope', '#/jobs/a/b', '#/storage/x', '#/setup/x', '#/jobs/%E0%A4%A']) {
    assert.equal(parseRoute(hash).name, 'not-found', hash);
  }
});
