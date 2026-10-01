import {test, afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {handle, validEndpoint, resetDiscovery} from '../edge/worker.mjs';
const originalFetch = globalThis.fetch;
const origin = 'https://jcheniu.github.io';
afterEach(() => {globalThis.fetch = originalFetch; resetDiscovery();});
const reviewRequest = body => new Request('https://api.asuperstrongfrog.com/api/reviews', {
  method: 'POST', headers: {Origin: origin, 'Content-Type': 'application/json', 'X-Upload-Key': 'a'.repeat(64)}, body,
});
test('discovery cannot redirect browser keys to arbitrary hosts', () => {
  assert.equal(validEndpoint('https://abcd.lhr.life'), 'https://abcd.lhr.life');
  for (const value of ['http://abcd.lhr.life', 'https://evil.example', 'https://abcd.lhr.life.evil.com',
    'https://user@abcd.lhr.life', 'https://abcd.lhr.life/private', 'https://abcd.lhr.life:8080']) assert.equal(validEndpoint(value), null);
});
test('preflight accepts only the review website', async () => {
  const valid = await handle(new Request('https://api.asuperstrongfrog.com/api/reviews', {method: 'OPTIONS', headers: {Origin: origin}}));
  assert.equal(valid.status, 204);
  assert.equal(valid.headers.get('Access-Control-Allow-Origin'), origin);
  const bad = await handle(new Request('https://api.asuperstrongfrog.com/api/reviews', {method: 'OPTIONS', headers: {Origin: 'https://evil.example'}}));
  assert.equal(bad.status, 403);
});
test('uploads preserve request body and session key and hide upstream headers', async () => {
  const sent = [];
  globalThis.fetch = async (url, options) => {
    if (url.startsWith('https://raw.githubusercontent.com/')) return Response.json({round_id: 'round-20260930T231132-d69c0f', endpoint: 'https://abcd.lhr.life'});
    sent.push({url, options});
    return Response.json({state: 'pending'}, {status: 202, headers: {'Set-Cookie': 'do-not-forward'}});
  };
  const response = await handle(reviewRequest('{"records":[]}'));
  assert.equal(response.status, 202);
  assert.equal(response.headers.get('Set-Cookie'), null);
  assert.equal(sent[0].url, 'https://abcd.lhr.life/api/reviews');
  assert.equal(sent[0].options.headers['X-Upload-Key'], 'a'.repeat(64));
  assert.equal(new TextDecoder().decode(sent[0].options.body), '{"records":[]}');
});
test('an intermittent tunnel failure is retried with fresh discovery', async () => {
  let attempts = 0;
  globalThis.fetch = async url => {
    if (url.startsWith('https://raw.githubusercontent.com/')) return Response.json({round_id: 'round-20260930T231132-d69c0f', endpoint: 'https://abcd.lhr.life'});
    attempts++;
    return attempts === 1 ? new Response('no tunnel', {status: 503}) : Response.json({state: 'uploaded'});
  };
  assert.equal((await handle(reviewRequest('{}'))).status, 200);
  assert.equal(attempts, 2);
});
test('unsupported paths and oversized bodies never reach the receiver', async () => {
  globalThis.fetch = async () => {throw Error('Network must not run');};
  assert.equal((await handle(new Request('https://api.asuperstrongfrog.com/admin', {headers: {Origin: origin}}))).status, 404);
  assert.equal((await handle(reviewRequest('a'.repeat(256*1024+1)))).status, 413);
});
