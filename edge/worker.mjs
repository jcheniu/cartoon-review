// Fixed upload URL. Address updates use signed KV writes; GitHub stores complete review groups only.
const ROUND = 'round-20260930T231132-d69c0f';
const ORIGIN = 'https://jcheniu.github.io';
const MAX_BODY = 256 * 1024;
const ENDPOINT_KEY = 'receiver:' + ROUND;
const PUBLIC_KEY = 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA283eqmOcABOUPeq23XlRpsYAaQ+vs7R6YEox1DsJ/DeJdeiruda0Z92ja3uiDVK8BdxjDNVOY3/AYB86ooGk+8UJfe2AakIL7i8+uOFZyJXhyRmMpE/uJnS2ThwkMonINj6bj6oTfcLb+kR89ZHDYaqn1ZPyyq8rHrvTqmArvZgd7UcCLRfb2BJLUKwkSgkK4nLTfJRIlfgiGz6pwrvvCU/g275gga7lL5K2h5J0YMESipsVSfIcH2RpWU6YeSeqqkg5RZJBuS6dm5shwL1s7OBMewi7vgwT09WF7TooDyxsTrn9ABIE/HPLqaoESdllPGDt3ge+v5mwnqe6az5npwIDAQAB';
let cached = null;
let expires = 0;

function headers(origin) {
  const result = {'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'Vary': 'Origin'};
  if (origin === ORIGIN) result['Access-Control-Allow-Origin'] = origin;
  return result;
}
function json(status, value, origin) {
  return new Response(JSON.stringify(value), {status, headers: headers(origin)});
}
export function validEndpoint(value) {
  try {
    const endpoint = new URL(value);
    if (endpoint.protocol !== 'https:' || endpoint.username || endpoint.password || endpoint.port ||
        endpoint.pathname !== '/' || endpoint.search || endpoint.hash ||
        !/^[a-z0-9-]+\.(lhr\.life|trycloudflare\.com)$/.test(endpoint.hostname)) return null;
    return endpoint.origin;
  } catch { return null; }
}
export function resetDiscovery() { cached = null; expires = 0; }
async function discover(env, force = false) {
  if (cached && !force && Date.now() < expires) return cached;
  const config = await env.ENDPOINTS.get(ENDPOINT_KEY, {type: 'json', cacheTtl: 30});
  const endpoint = config?.round_id === ROUND ? validEndpoint(config.endpoint) : null;
  if (!endpoint) throw Error('Receiver address not yet published');
  cached = endpoint;
  expires = Date.now() + 15000;
  return endpoint;
}
async function bodyBytes(request, limit = MAX_BODY) {
  const declared = Number(request.headers.get('Content-Length') || 0);
  if (declared > limit) throw Error('too-large');
  const reader = request.body?.getReader();
  if (!reader) return new Uint8Array();
  const parts = [];
  let length = 0;
  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > limit) { await reader.cancel(); throw Error('too-large'); }
    parts.push(value);
  }
  const joined = new Uint8Array(length);
  let offset = 0;
  for (const part of parts) { joined.set(part, offset); offset += part.byteLength; }
  return joined;
}
async function updateEndpoint(request, env) {
  if (!env.ENDPOINTS) return json(503, {error: 'Address storage unavailable'});
  let body;
  try { body = await bodyBytes(request, 2048); }
  catch { return json(413, {error: 'Update too large'}); }
  try {
    const signature = request.headers.get('X-Endpoint-Signature') || '';
    if (!/^[A-Za-z0-9+/]{342}==$/.test(signature)) return json(403, {error: 'Invalid endpoint signature'});
    const fromBase64 = value => Uint8Array.from(atob(value), ch => ch.charCodeAt(0));
    const key = await crypto.subtle.importKey('spki', fromBase64(env.ENDPOINT_PUBLIC_KEY || PUBLIC_KEY),
      {name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256'}, false, ['verify']);
    if (!await crypto.subtle.verify('RSASSA-PKCS1-v1_5', key, fromBase64(signature), body)) {
      return json(403, {error: 'Invalid endpoint signature'});
    }
    const config = JSON.parse(new TextDecoder().decode(body));
    const endpoint = config.round_id === ROUND ? validEndpoint(config.endpoint) : null;
    if (!endpoint || !Number.isSafeInteger(config.updated_at) ||
        Math.abs(Date.now()/1000 - config.updated_at) > 300) return json(400, {error: 'Invalid or expired endpoint update'});
    const previous = await env.ENDPOINTS.get(ENDPOINT_KEY, {type: 'json', cacheTtl: 30});
    if (previous?.updated_at > config.updated_at) return json(409, {error: 'Older endpoint update'});
    // Duplicate retries and supervisor restarts do not create more KV versions.
    if (previous?.endpoint !== endpoint || previous?.round_id !== ROUND) {
      await env.ENDPOINTS.put(ENDPOINT_KEY, JSON.stringify({endpoint, round_id: ROUND, updated_at: config.updated_at}));
    }
    cached = endpoint; expires = Date.now() + 15000;
    return json(200, {status: 'ok', round_id: ROUND});
  } catch { return json(503, {error: 'Endpoint update temporarily unavailable'}); }
}
export async function handle(request, env = {}) {
  const url = new URL(request.url);
  const origin = request.headers.get('Origin');
  if (url.pathname === '/api/endpoint' && request.method === 'POST' && !url.search) return updateEndpoint(request, env);
  if (request.method === 'OPTIONS') {
    if (origin !== ORIGIN || !['/api/reviews', '/api/status', '/health'].includes(url.pathname)) return json(403, {error: 'Origin not permitted'}, origin);
    return new Response(null, {status: 204, headers: {
      ...headers(origin), 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, X-Upload-Key', 'Access-Control-Max-Age': '600',
    }});
  }
  const health = url.pathname === '/health' && request.method === 'GET';
  if (!health && origin !== ORIGIN) return json(403, {error: 'Origin not permitted'}, origin);
  if (!(health || (url.pathname === '/api/status' && request.method === 'GET') ||
        (url.pathname === '/api/reviews' && request.method === 'POST' && !url.search))) return json(404, {error: 'Not found'}, origin);
  const key = request.headers.get('X-Upload-Key') || '';
  if (!health && !/^[a-f0-9]{64}$/.test(key)) return json(403, {error: 'Invalid upload session key'}, origin);
  let body;
  if (request.method === 'POST') {
    try { body = await bodyBytes(request); }
    catch { return json(413, {error: 'Upload too large'}, origin); }
    if (!body.length) return json(400, {error: 'Empty upload'}, origin);
  }
  let lastIssue = 'Unavailable';
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const endpoint = await discover(env, attempt > 0);
      const upstreamHeaders = {'Origin': ORIGIN, 'Accept': 'application/json'};
      if (!health) upstreamHeaders['X-Upload-Key'] = key;
      if (body) upstreamHeaders['Content-Type'] = 'application/json';
      const upstream = await fetch(endpoint + url.pathname + url.search, {
        method: request.method, headers: upstreamHeaders, body,
        redirect: 'manual', signal: AbortSignal.timeout(5000),
      });
      if (upstream.status >= 300 && upstream.status < 400) {
        lastIssue = 'Receiver redirect refused'; await upstream.body?.cancel(); continue;
      }
      if ([502, 503, 504, 530].includes(upstream.status)) {
        lastIssue = 'Receiver HTTP ' + upstream.status; await upstream.body?.cancel(); continue;
      }
      if (!(upstream.headers.get('Content-Type') || '').includes('application/json')) {
        lastIssue = 'Receiver non-JSON HTTP ' + upstream.status; await upstream.body?.cancel(); continue;
      }
      return new Response(upstream.body, {status: upstream.status, headers: headers(origin)});
    } catch (error) { lastIssue = String(error?.message || 'Connection failed').slice(0, 160); }
  }
  return json(503, {error: '上传服务正在重连；标注保留在浏览器，稍后自动重试', ...(health ? {reason: lastIssue} : {})}, origin);
}
export default {fetch: handle};
