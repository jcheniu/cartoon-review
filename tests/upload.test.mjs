import test from 'node:test';
import assert from 'node:assert/strict';
import {createUploader} from '../assets/upload.mjs';

const manifest = {round: {id: 'test-round'}};
const row = i => ({photo_id: String(i).padStart(3, '0'), image_number: i,
  accepted: 'a', preferred: 'a', caption: 'cartoon subject', review_version: 1, reviewed_at: '2026-10-03T00:00:00Z'});
const reply = (value, status = 200) => new Response(JSON.stringify(value), {status});
async function until(check, timeout = 4000) {
  const start = Date.now();
  while (!check()) {
    assert.ok(Date.now() - start < timeout, 'Uploader did not make progress in time');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}
function environment(t) {
  const storage = new Map();
  t.mock.method(globalThis, 'fetch', async () => { throw Error('Unexpected request'); });
  const oldStorage = globalThis.localStorage, oldWindow = globalThis.window;
  globalThis.localStorage = {getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v)};
  globalThis.window = {addEventListener() {}};
  t.after(() => { globalThis.localStorage = oldStorage; globalThis.window = oldWindow; });
  return storage;
}

test('a completed group supersedes a stalled draft and lost acknowledgements safely', async t => {
  const storage = environment(t);
  const state = {session: 'test-session', reviews: {'000': row(0)}};
  const posts = [], statuses = [];
  let stalled = false, aborted = 0, mode = '', revertSeen = false, failedOnce = false;
  const block = signal => new Promise((_, reject) => signal.addEventListener('abort', () => {
    aborted++; reject(new DOMException('Aborted', 'AbortError'));
  }, {once: true}));
  fetch.mock.mockImplementation(async (url, options) => {
    if (String(url).endsWith('upload-config.json')) return reply({endpoint: 'https://upload.example', round_id: manifest.round.id});
    assert.equal(options.method, 'POST');
    const body = JSON.parse(options.body);
    posts.push(body);
    if (body.records.length === 1) { stalled = true; return block(options.signal); }
    if (mode === 'lost' && body.records[0].accepted === 'b') return block(options.signal);
    if (mode === 'lost' && body.records[0].accepted === 'a') revertSeen = true;
    if (mode === 'retry' && body.records[0].caption === 'obsolete edit') {
      failedOnce = true; return reply({error: 'temporary failure'}, 503);
    }
    return reply({state: 'uploaded', count: 25, url: 'https://github.com/result'});
  });
  const uploader = await createUploader({manifest, getState: () => state, onStatus: s => statuses.push(s)});
  await until(() => stalled);
  for (let i = 0; i < 25; i++) state.reviews[row(i).photo_id] = row(i);
  uploader.enqueue({start: 0, end: 24});
  await until(() => statuses.at(-1).text.startsWith('已上传'));
  assert.equal(aborted, 1);
  assert.deepEqual(posts.map(p => p.records.length), [1, 25]);
  const before = posts.length;
  uploader.enqueue({start: 0, end: 24});
  await new Promise(resolve => setTimeout(resolve, 40));
  assert.equal(posts.length, before, 'Unchanged saves must not create another upload');

  mode = 'lost';
  state.reviews['000'] = {...row(0), accepted: 'b', preferred: 'b', review_version: 2};
  uploader.enqueue({start: 0, end: 24});
  await until(() => posts.at(-1).records[0].accepted === 'b');
  const pending = JSON.parse([...storage.entries()].find(([k]) => k.startsWith('cartoon-review-upload-state:'))[1]);
  assert.ok(pending.pending['000_024.jsonl'], 'Persist before an unacknowledged POST can reach the server');
  state.reviews['000'] = {...row(0), review_version: 3};
  uploader.enqueue({start: 0, end: 24});
  await until(() => revertSeen && statuses.at(-1).text.startsWith('已上传'));
  assert.equal(aborted, 2);
  assert.equal(posts.at(-1).records[0].accepted, 'a', 'Reverting must cancel the possibly delivered queued edit');

  mode = 'retry';
  state.reviews['000'] = {...row(0), caption: 'obsolete edit', review_version: 4};
  uploader.enqueue({start: 0, end: 24});
  await until(() => failedOnce);
  state.reviews['000'] = {...row(0), caption: 'latest edit', review_version: 5};
  uploader.enqueue({start: 0, end: 24});
  await until(() => posts.at(-1).records[0].caption === 'latest edit' && statuses.at(-1).text.startsWith('已上传'));
  assert.equal(posts.filter(p => p.records[0].caption === 'obsolete edit').length, 1,
    'Retry backoff must not resend an obsolete snapshot');
  assert.ok(statuses.every(s => !s.error), 'Superseded snapshots are not upload failures');
});

test('all complete groups are submitted before waiting for publication', async t => {
  environment(t);
  const state = {session: 'another-session', reviews: Object.fromEntries(Array.from({length: 50}, (_, i) => [row(i).photo_id, row(i)]))};
  const requests = [], statuses = [];
  fetch.mock.mockImplementation(async (url, options) => {
    if (String(url).endsWith('upload-config.json')) return reply({endpoint: 'https://upload.example', round_id: manifest.round.id});
    const parsed = new URL(url);
    requests.push(options.method);
    if (options.method === 'POST') {
      assert.equal(JSON.parse(options.body).records.length, 25);
      return reply({state: 'pending', count: 25, ready_at: Date.now() / 1000 + 30});
    }
    assert.equal(parsed.pathname, '/api/status');
    return reply({state: 'uploaded', count: 25, url: 'https://github.com/result'});
  });
  await createUploader({manifest, getState: () => state, onStatus: s => statuses.push(s)});
  await until(() => statuses.filter(s => s.text.startsWith('已上传')).length === 2);
  assert.deepEqual(requests, ['POST', 'POST', 'GET', 'GET']);
  assert.ok(statuses.every(s => !s.error));
});

test('a newer full group interrupts older publication polling', async t => {
  environment(t);
  const state = {session: 'poll-session', reviews: Object.fromEntries(Array.from({length: 25}, (_, i) => [row(i).photo_id, row(i)]))};
  const requests = [], statuses = [];
  let polling = false, cancelled = false;
  fetch.mock.mockImplementation(async (url, options) => {
    if (String(url).endsWith('upload-config.json')) return reply({endpoint: 'https://upload.example', round_id: manifest.round.id});
    if (options.method === 'POST') {
      const start = JSON.parse(options.body).range.start;
      requests.push('POST ' + start);
      return reply({state: start ? 'uploaded' : 'pending', count: 25});
    }
    requests.push('GET 0');
    if (!polling) {
      polling = true;
      return new Promise((_, reject) => options.signal.addEventListener('abort', () => {
        cancelled = true; reject(new DOMException('Aborted', 'AbortError'));
      }, {once: true}));
    }
    return reply({state: 'uploaded', count: 25});
  });
  const uploader = await createUploader({manifest, getState: () => state, onStatus: s => statuses.push(s)});
  await until(() => polling);
  for (let i = 25; i < 50; i++) state.reviews[row(i).photo_id] = row(i);
  uploader.enqueue({start: 25, end: 49});
  await until(() => statuses.filter(s => s.text.startsWith('已上传')).length === 2);
  assert.equal(cancelled, true);
  assert.deepEqual(requests, ['POST 0', 'GET 0', 'POST 25', 'GET 0']);
});

test('a pending revert invalidates cached publication and is polled to completion', async t => {
  environment(t);
  const state = {session: 'revert-session', reviews: Object.fromEntries(Array.from({length: 25}, (_, i) => [row(i).photo_id, row(i)]))};
  const statuses = [];
  let posts = 0, gets = 0;
  fetch.mock.mockImplementation(async (url, options) => {
    if (String(url).endsWith('upload-config.json')) return reply({endpoint: 'https://upload.example', round_id: manifest.round.id});
    if (options.method === 'POST') {
      posts++;
      return reply({state: posts === 1 ? 'uploaded' : 'pending', count: 25});
    }
    gets++;
    return reply({state: 'uploaded', count: 25});
  });
  const uploader = await createUploader({manifest, getState: () => state, onStatus: s => statuses.push(s)});
  await until(() => statuses.at(-1).text.startsWith('已上传'));
  state.reviews['000'] = {...row(0), accepted: 'b', preferred: 'b', review_version: 2};
  uploader.enqueue({start: 0, end: 24});
  await until(() => posts === 2 && statuses.at(-1).text.includes('JSONL 已保存'));
  state.reviews['000'] = {...row(0), review_version: 3};
  uploader.enqueue({start: 0, end: 24});
  await until(() => posts === 3);
  await until(() => statuses.at(-1).text.startsWith('已上传'));
  assert.equal(gets, 1, 'A cached earlier publication must not skip the current server status');
});
