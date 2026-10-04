import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {parseRange, shiftRange} from '../assets/ranges.mjs';
import {rangeName, makeReview, validateImported, rangeRows} from '../assets/review.mjs';

const manifest = JSON.parse(readFileSync(new URL('../data/manifest.json', import.meta.url)));
const item = manifest.items.find(item => item.candidates);
const form = {choice: 'a', preferred: 'a', caption: 'a cartoon cat', notes: '', reasons: []};
const make = fields => makeReview(manifest, item, {...form, ...fields}, null, 'test-session');

test('ranges accept arbitrary consecutive groups and enforce boundaries', () => {
  assert.deepEqual(parseRange('450-474'), {start: 450, end: 474});
  assert.equal(rangeName(parseRange('0-24')), '000_024.jsonl');
  assert.deepEqual(shiftRange({start: 475, end: 499}, 1), {start: 475, end: 499});
  assert.throws(() => parseRange('0-25'));
  assert.throws(() => parseRange('480-504'));
});

test('rejections require a reason and clear acceptance', () => {
  assert.throws(() => make({choice: 'neither'}));
  assert.throws(() => make({choice: 'neither', reasons: ['other']}));
  const row = make({choice: 'neither', reasons: ['color', 'pose'], caption: ''});
  assert.equal(row.accepted, 'neither');
  assert.equal(row.preferred, 'neither');
  assert.deepEqual(row.rejection_reasons, ['color', 'pose']);
});

test('acceptance uses teacher-compatible fields and candidate identity', () => {
  const row = make({choice: 'both', preferred: 'tie', reasons: ['color']});
  assert.equal(row.accepted, 'both');
  assert.equal(row.preferred, 'tie');
  assert.deepEqual(row.rejection_reasons, []);
  assert.equal(row.source_sha256, item.sha256);
  assert.equal(row.item_id, item.legacy_id);
  assert.equal(row.master_hashes.a, item.master_hashes.a);
  assert.throws(() => make({caption: ''}));
  assert.throws(() => makeReview(manifest, {...item, candidates: null}, form, null, 'test'));
});

test('imports reject wrong rounds and changed candidates', () => {
  const row = make({});
  assert.deepEqual(validateImported(manifest, row), row);
  assert.throws(() => validateImported(manifest, {...row, round_id: 'other'}));
  assert.throws(() => validateImported(manifest, {...row, candidate_hashes: {a: 'modified', b: row.candidate_hashes.b}}));
  assert.throws(() => validateImported(manifest, {...row, accepted: 'neither', preferred: 'neither'}));
  assert.throws(() => validateImported(manifest, {...row, preferred: 'b'}));
});

test('range export includes only saved rows in numeric order', () => {
  const a = {...make({}), image_number: 24};
  const b = {...make({}), image_number: 0};
  const c = {...make({}), image_number: 25};
  assert.deepEqual(rangeRows({a, b, c}, {start: 0, end: 24}).map(r => r.image_number), [0, 24]);
});

test('unchanged user choices preserve the original revision and timestamp', () => {
  const item = manifest.items[0];
  const form = {choice: 'a', preferred: 'a', reasons: [], caption: 'a cartoon subject'};
  const previous = makeReview(manifest, item, form, null, 'session');
  assert.equal(makeReview(manifest, item, {...form, caption: '  a cartoon subject  '}, previous, 'session'), previous);
  const edited = makeReview(manifest, item, {...form, caption: 'a different cartoon subject'}, previous, 'session');
  assert.equal(edited.review_version, previous.review_version + 1);
});


test('resized source preview hashes remain distinct from the training original', () => {
  const preview = {...item, preview_sha256: 'd'.repeat(64)};
  const row = makeReview(manifest, preview, form, null, 'test-session');
  assert.equal(row.source_sha256, item.sha256);
  assert.equal(row.source.sha256, preview.preview_sha256);
  assert.equal(row.source.original_sha256, item.sha256);
  assert.equal('original_sha256' in make({}).source, false, 'Legacy records remain unchanged');
});
