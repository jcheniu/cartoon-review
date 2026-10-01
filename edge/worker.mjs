// Fixed HTTPS entry point. GitHub credentials stay on the private receiver.
const DISCOVERY = 'https://raw.githubusercontent.com/jcheniu/cartoon-review/main/result/round_1/service.json';
const FRESH_DISCOVERY = 'https://api.github.com/repos/jcheniu/cartoon-review/contents/result/round_1/service.json';
const ROUND = 'round-20260930T231132-d69c0f';
const ORIGIN = 'https://jcheniu.github.io';
const MAX_BODY = 256 * 1024;
let cached = null;
let expires = 0;
let freshRetryAt = 0;

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
export function resetDiscovery() { cached = null; expires = 0; freshRetryAt = 0; }

async function discover(force = false) {
  if (cached && !force && Date.now() < expires) return cached;
  // Raw GitHub files may retain a retired tunnel URL for several minutes.
  // Consult the Contents API on transport failure, with a per-isolate cooldown.
  if (force && Date.now() < freshRetryAt) {
    if (cached) return cached;
    throw Error('Discovery retry cooling down');
  }
  if (force) freshRetryAt = Date.now() + 60000;
  const url = force ? FRESH_DISCOVERY : DISCOVERY + '?t=' + Math.floor(Date.now() / 15000);
  const response = await fetch(url, {
    headers: {'Accept': force ? 'application/vnd.github.raw+json' : 'application/json',
      'User-Agent': 'cartoon-review-upload'},
    signal: AbortSignal.timeout(8000),
  });
  if (!response.ok) throw Error('Discovery HTTP ' + response.status);
  const config = await response.json();
  const endpoint = config.round_id === ROUND ? validEndpoint(config.endpoint) : null;
  if (!endpoint) throw Error('Invalid receiver discovery');
  cached = endpoint;
  expires = Date.now() + (force ? 300000 : 30000);
  return endpoint;
}

async function bodyBytes(request) {
  const declared = Number(request.headers.get('Content-Length') || 0);
  if (declared > MAX_BODY) throw Error('too-large');
  const reader = request.body?.getReader();
  if (!reader) return new Uint8Array();
  const parts = [];
  let length = 0;
  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > MAX_BODY) { await reader.cancel(); throw Error('too-large'); }
    parts.push(value);
  }
  const joined = new Uint8Array(length);
  let offset = 0;
  for (const part of parts) { joined.set(part, offset); offset += part.byteLength; }
  return joined;
}

export async function handle(request) {
  const url = new URL(request.url);
  const origin = request.headers.get('Origin');
  if (request.method === 'OPTIONS') {
    if (origin !== ORIGIN || !['/api/reviews', '/api/status', '/health'].includes(url.pathname)) return json(403, {error: 'Origin not permitted'}, origin);
    return new Response(null, {status: 204, headers: {
      ...headers(origin),
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, X-Upload-Key',
      'Access-Control-Max-Age': '600',
    }});
  }
  const health = url.pathname === '/health' && request.method === 'GET';
  if (!health && origin !== ORIGIN) return json(403, {error: 'Origin not permitted'}, origin);
  if (!(health || (url.pathname === '/api/status' && request.method === 'GET') ||
        (url.pathname === '/api/reviews' && request.method === 'POST' && !url.search))) {
    return json(404, {error: 'Not found'}, origin);
  }
  const key = request.headers.get('X-Upload-Key') || '';
  if (!health && !/^[a-f0-9]{64}$/.test(key)) return json(403, {error: 'Invalid upload session key'}, origin);
  let body;
  if (request.method === 'POST') {
    try { body = await bodyBytes(request); }
    catch { return json(413, {error: 'Upload too large'}, origin); }
    if (!body.length) return json(400, {error: 'Empty upload'}, origin);
  }
  let lastIssue = 'Unavailable';
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const endpoint = await discover(attempt > 0);
      const upstreamHeaders = {'Origin': ORIGIN, 'Accept': 'application/json'};
      if (!health) upstreamHeaders['X-Upload-Key'] = key;
      if (body) upstreamHeaders['Content-Type'] = 'application/json';
      const upstream = await fetch(endpoint + url.pathname + url.search, {
        method: request.method, headers: upstreamHeaders, body,
        redirect: 'manual', signal: AbortSignal.timeout(5000),
      });
      if (upstream.status >= 300 && upstream.status < 400) {
        lastIssue = 'Receiver redirect refused';
        await upstream.body?.cancel(); continue;
      }
      if ([502, 503, 504, 530].includes(upstream.status)) { lastIssue = 'Receiver HTTP ' + upstream.status; await upstream.body?.cancel(); continue; }
      if (!(upstream.headers.get('Content-Type') || '').includes('application/json')) {
        lastIssue = 'Receiver non-JSON HTTP ' + upstream.status;
        await upstream.body?.cancel(); continue;
      }
      return new Response(upstream.body, {status: upstream.status, headers: headers(origin)});
    } catch (error) { lastIssue = String(error?.message || 'Connection failed').slice(0, 160); }
  }
  return json(503, {error: '上传服务正在重连；标注保留在浏览器，稍后自动重试', ...(health ? {reason: lastIssue} : {})}, origin);
}
export default {fetch: handle};
