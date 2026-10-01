// Fixed HTTPS entry point. GitHub credentials stay on the private receiver.
const DISCOVERY = 'https://raw.githubusercontent.com/jcheniu/cartoon-review/main/result/round_1/service.json';
const ROUND = 'round-20260930T231132-d69c0f';
const ORIGIN = 'https://jcheniu.github.io';
const MAX_BODY = 256 * 1024;
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

async function discover(force = false) {
  if (cached && !force && Date.now() < expires) return cached;
  const url = DISCOVERY + '?t=' + Math.floor(Date.now() / 15000);
  const response = await fetch(url, {headers: {'Accept': 'application/json'}, signal: AbortSignal.timeout(8000)});
  if (!response.ok) throw Error('Discovery unavailable');
  const config = await response.json();
  const endpoint = config.round_id === ROUND ? validEndpoint(config.endpoint) : null;
  if (!endpoint) throw Error('Invalid receiver discovery');
  cached = endpoint;
  expires = Date.now() + 30000;
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
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const endpoint = await discover(attempt > 0);
      const upstreamHeaders = {'Origin': ORIGIN, 'Accept': 'application/json'};
      if (!health) upstreamHeaders['X-Upload-Key'] = key;
      if (body) upstreamHeaders['Content-Type'] = 'application/json';
      const upstream = await fetch(endpoint + url.pathname + url.search, {
        method: request.method, headers: upstreamHeaders, body,
        redirect: 'error', signal: AbortSignal.timeout(5000),
      });
      if ([502, 503, 504, 530].includes(upstream.status)) { await upstream.body?.cancel(); continue; }
      if (!(upstream.headers.get('Content-Type') || '').includes('application/json')) {
        await upstream.body?.cancel(); continue;
      }
      return new Response(upstream.body, {status: upstream.status, headers: headers(origin)});
    } catch { /* Retry discovery and receiver connection without logging browser keys. */ }
  }
  return json(503, {error: '上传服务正在重连；标注保留在浏览器，稍后自动重试'}, origin);
}
export default {fetch: handle};
