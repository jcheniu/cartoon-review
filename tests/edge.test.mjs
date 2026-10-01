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
    if (url.startsWith('https://raw.githubusercontent.com/') || url.startsWith('https://api.github.com/')) return Response.json({round_id: 'round-20260930T231132-d69c0f', endpoint: 'https://abcd.lhr.life'});
    sent.push({url, options});
    return Response.json({state: 'pending'}, {status: 202, headers: {'Set-Cookie': 'do-not-forward'}});
  };
  const response = await handle(reviewRequest('{"records":[]}'));
  assert.equal(response.status, 202);
  assert.equal(response.headers.get('Set-Cookie'), null);
  assert.equal(sent[0].url, 'https://abcd.lhr.life/api/reviews');
  assert.equal(sent[0].options.headers['X-Upload-Key'], 'a'.repeat(64));
  assert.equal(sent[0].options.redirect, 'manual');
  assert.equal(new TextDecoder().decode(sent[0].options.body), '{"records":[]}');
});
test('an intermittent tunnel failure is retried with fresh discovery', async () => {
  let attempts = 0;
  globalThis.fetch = async url => {
    if (url.startsWith('https://raw.githubusercontent.com/') || url.startsWith('https://api.github.com/')) return Response.json({round_id: 'round-20260930T231132-d69c0f', endpoint: 'https://abcd.lhr.life'});
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

test('stale raw discovery recovers through GitHub API and retains the new endpoint', async () => {
  let apiCalls = 0;
  let oldCalls = 0;
  globalThis.fetch = async (url, options) => {
    if (url.startsWith('https://raw.githubusercontent.com/')) return Response.json({round_id: 'round-20260930T231132-d69c0f', endpoint: 'https://old.lhr.life'});
    if (url.startsWith('https://api.github.com/')) {
      apiCalls++;
      assert.equal(options.headers.Accept, 'application/vnd.github.raw+json');
      return Response.json({round_id: 'round-20260930T231132-d69c0f', endpoint: 'https://new.lhr.life'});
    }
    if (url.startsWith('https://old.lhr.life/')) {oldCalls++; return new Response('no tunnel', {status: 503});}
    assert.ok(url.startsWith('https://new.lhr.life/'));
    return Response.json({status: 'ok'});
  };
  for (let i=0; i<3; i++) assert.equal((await handle(new Request('https://api.asuperstrongfrog.com/health'))).status, 200);
  assert.equal(oldCalls, 1);
  assert.equal(apiCalls, 1);
});
test('repeated transport failures do not hammer the public GitHub API', async () => {
  let apiCalls = 0;
  globalThis.fetch = async url => {
    if (url.startsWith('https://raw.githubusercontent.com/') || url.startsWith('https://api.github.com/')) {
      if (url.startsWith('https://api.github.com/')) apiCalls++;
      return Response.json({round_id: 'round-20260930T231132-d69c0f', endpoint: 'https://abcd.lhr.life'});
    }
    return new Response('no tunnel', {status: 503});
  };
  for (let i=0; i<2; i++) assert.equal((await handle(reviewRequest('{}'))).status, 503);
  assert.equal(apiCalls, 1);
});

test('receiver redirects are refused without forwarding session keys elsewhere', async () => {
  const hosts = [];
  globalThis.fetch = async (url, options) => {
    const host = new URL(url).hostname;
    hosts.push(host);
    if (host === 'raw.githubusercontent.com' || host === 'api.github.com') return Response.json({round_id: 'round-20260930T231132-d69c0f', endpoint: 'https://abcd.lhr.life'});
    assert.equal(options.redirect, 'manual');
    return new Response(null, {status: 302, headers: {Location: 'https://evil.example/steal'}});
  };
  const result = await handle(reviewRequest('{}'));
  assert.equal(result.status, 503);
  assert.equal(result.headers.get('Location'), null);
  assert.ok(!hosts.includes('evil.example'));
});
