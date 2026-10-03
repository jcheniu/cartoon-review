import {test, afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {generateKeyPairSync, sign} from 'node:crypto';
import {handle, validEndpoint, resetDiscovery} from '../edge/worker.mjs';
const originalFetch=globalThis.fetch, origin='https://jcheniu.github.io', round='round-20260930T231132-d69c0f';
const {privateKey,publicKey}=generateKeyPairSync('rsa',{modulusLength:2048});
const publicBase64=publicKey.export({type:'spki',format:'der'}).toString('base64');
afterEach(()=>{globalThis.fetch=originalFetch;resetDiscovery();});
function storage(value={endpoint:'https://abcd.lhr.life',round_id:round,updated_at:0}){
  return {value,reads:0,writes:0,async get(){this.reads++;return this.value;},
    async put(key,text){this.writes++;this.value=JSON.parse(text);}};
}
function environment(kv=storage()){return {ENDPOINTS:kv,ENDPOINT_PUBLIC_KEY:publicBase64};}
function update(config={},extra={}){
  const body=JSON.stringify({endpoint:'https://abcd.lhr.life',round_id:round,updated_at:Math.floor(Date.now()/1000),...config});
  return new Request('https://api.asuperstrongfrog.com/api/endpoint',{method:'POST',body,
    headers:{'X-Endpoint-Signature':sign('RSA-SHA256',Buffer.from(body),privateKey).toString('base64'),...extra}});
}
const review=body=>new Request('https://api.asuperstrongfrog.com/api/reviews',{
  method:'POST',headers:{Origin:origin,'Content-Type':'application/json','X-Upload-Key':'a'.repeat(64)},body});
test('discovery cannot forward browser keys to arbitrary hosts',()=>{
  assert.equal(validEndpoint('https://abcd.lhr.life'),'https://abcd.lhr.life');
  for(const value of ['http://abcd.lhr.life','https://evil.example','https://abcd.lhr.life.evil.com',
    'https://user@abcd.lhr.life','https://abcd.lhr.life/private','https://abcd.lhr.life:8080'])assert.equal(validEndpoint(value),null);
});
test('signed endpoint update persists in KV and duplicate retries do not write',async()=>{
  const kv=storage(null),env=environment(kv);
  assert.equal((await handle(update(),env)).status,200);
  assert.equal(kv.writes,1);
  assert.equal((await handle(update(),env)).status,200);
  assert.equal(kv.writes,1);
  assert.equal(kv.value.endpoint,'https://abcd.lhr.life');
});
test('unsigned, tampered, expired and invalid endpoint updates cannot write KV',async()=>{
  const kv=storage(null),env=environment(kv);
  assert.equal((await handle(new Request('https://api.asuperstrongfrog.com/api/endpoint',{method:'POST',body:'{}'}),env)).status,403);
  assert.equal((await handle(update({}, {'X-Endpoint-Signature':Buffer.alloc(256).toString('base64')}),env)).status,403);
  for(const config of [{updated_at:Math.floor(Date.now()/1000)-301},{endpoint:'https://evil.example'},{round_id:'wrong'}])
    assert.equal((await handle(update(config),env)).status,400);
  assert.equal(kv.writes,0);
});
test('older signed address updates cannot replace a newer address',async()=>{
  const kv=storage({endpoint:'https://new.lhr.life',round_id:round,updated_at:Math.floor(Date.now()/1000)+1});
  assert.equal((await handle(update(),environment(kv))).status,409);
  assert.equal(kv.writes,0);
});
test('preflight accepts the website but endpoint maintenance has no browser CORS',async()=>{
  assert.equal((await handle(new Request('https://api.asuperstrongfrog.com/api/reviews',{method:'OPTIONS',headers:{Origin:origin}}))).status,204);
  assert.equal((await handle(new Request('https://api.asuperstrongfrog.com/api/reviews',{method:'OPTIONS',headers:{Origin:'https://evil.example'}}))).status,403);
  assert.equal((await handle(new Request('https://api.asuperstrongfrog.com/api/endpoint',{method:'OPTIONS',headers:{Origin:origin}}))).status,403);
});
test('uploads preserve body and session key without any GitHub discovery request',async()=>{
  const sent=[];
  globalThis.fetch=async(url,options)=>{
    assert.ok(url.startsWith('https://abcd.lhr.life/'),'Only the approved receiver is contacted');
    sent.push({url,options});return Response.json({state:'draft',count:1},{headers:{'Set-Cookie':'hidden'}});
  };
  const response=await handle(review('{"records":[]}'),environment());
  assert.equal(response.status,200);assert.equal(response.headers.get('Set-Cookie'),null);
  assert.equal(sent[0].options.headers['X-Upload-Key'],'a'.repeat(64));
  assert.equal(sent[0].options.redirect,'manual');
  assert.equal(new TextDecoder().decode(sent[0].options.body),'{"records":[]}');
});
test('retired transport refreshes from KV without GitHub requests',async()=>{
  const kv=storage();kv.get=async function(){this.reads++;return {round_id:round,endpoint:this.reads===1?'https://old.lhr.life':'https://new.lhr.life'};};
  globalThis.fetch=async url=>url.startsWith('https://old.lhr.life/')?new Response('retired',{status:503}):Response.json({status:'ok'});
  assert.equal((await handle(new Request('https://api.asuperstrongfrog.com/health'),environment(kv))).status,200);
  assert.equal(kv.reads,2);
});
test('unsupported paths and oversized bodies never contact the receiver',async()=>{
  globalThis.fetch=async()=>{throw Error('Must not fetch');};
  assert.equal((await handle(new Request('https://api.asuperstrongfrog.com/admin',{headers:{Origin:origin}}),environment())).status,404);
  assert.equal((await handle(review('a'.repeat(256*1024+1)),environment())).status,413);
});
test('receiver redirects are refused and keys never reach redirected hosts',async()=>{
  const hosts=[];
  globalThis.fetch=async(url,options)=>{hosts.push(new URL(url).hostname);assert.equal(options.redirect,'manual');return new Response(null,{status:302,headers:{Location:'https://evil.example'}});};
  const response=await handle(review('{}'),environment());
  assert.equal(response.status,503);assert.equal(response.headers.get('Location'),null);
  assert.deepEqual(hosts,['abcd.lhr.life','abcd.lhr.life']);
});
test('missing address returns retryable 503 and preserves browser draft semantics',async()=>{
  globalThis.fetch=async()=>{throw Error('Must not fetch');};
  assert.equal((await handle(review('{}'),environment(storage(null)))).status,503);
});
