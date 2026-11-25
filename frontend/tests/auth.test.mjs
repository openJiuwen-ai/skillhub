import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import test from 'node:test'

// Use Vite's existing esbuild dependency; no browser or extra test framework.
const { build } = createRequire(import.meta.resolve('vite/package.json'))('esbuild')
const bundled = await build({
  stdin: {
    contents: `export * from './src/auth/gitcodeStorage'; export * from './src/api/auth'; export * from './src/identity_extensions/providers'`,
    resolveDir: resolve('.'),
    loader: 'ts',
  },
  bundle: true,
  write: false,
  platform: 'node',
  format: 'esm',
  alias: { '@': resolve('src') },
  plugins: [{
    name: 'http-fixture',
    setup(builder) {
      builder.onResolve({ filter: /^axios$/ }, () => ({ path: 'axios', namespace: 'fixture' }))
      builder.onResolve({ filter: /^(?:\.\/config|\.\.\/api\/config)$/ }, () => ({ path: 'config', namespace: 'fixture' }))
      builder.onLoad({ filter: /.*/, namespace: 'fixture' }, ({ path }) => ({
        contents: path === 'config'
          ? `export const API_CONFIG = { BASE_URL: '/api/v1', TIMEOUT: 1000, HEADERS: {} }`
          : `export default {
              async post(url, body) {
                globalThis.httpCalls.push({url, body});
                const provider = url.split('/').at(-2);
                return {data: {code: 200, data: {provider, access_token: 'token', user: {id: 'u'}}}};
              },
              async get(url, config) {
                globalThis.httpCalls.push({url, config});
                return {data: {code: 200, data: {id: 'u', login: 'alice', name: 'Alice'}}};
              }
            }`,
      }))
    },
  }],
})
const auth = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString('base64')}`)

test('built-in and configured extension providers share the existing OAuth routes', async () => {
  globalThis.httpCalls = []
  for (const provider of ['gitcode', 'github', 'agentos', 'enterprise', 'future-idp']) {
    assert.equal(auth.getOAuthStartUrl(provider), `/api/v1/auth/oauth/${provider}/start`)
    const result = await auth.exchangeOAuthSession(provider, 'pending-session')
    assert.equal(result.provider, provider)
    assert.equal(globalThis.httpCalls.at(-1).url, `/api/v1/auth/oauth/${provider}/session`)
    assert.deepEqual(auth.parseOAuthPending(auth.stringifyOAuthPending({provider, session: 'pending-session'})),
      {provider, session: 'pending-session'})
    await auth.fetchOAuthMe('token', provider)
    assert.equal(globalThis.httpCalls.at(-1).config.headers['X-OAuth-Provider'], provider)
  }
})

test('refresh preserves configured extension ID and logout clears the existing session keys', () => {
  const values = new Map()
  globalThis.sessionStorage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
  }
  assert.equal(auth.getStoredOAuthProvider(), 'gitcode')
  for (const provider of ['gitcode', 'github', 'agentos', 'enterprise']) {
    auth.setOAuthSession('token', {id: 'u', login: 'alice', name: 'Alice'}, provider)
    assert.equal(auth.getStoredOAuthProvider(), provider)
    assert.equal(auth.getStoredOAuthToken(), 'token')
  }
  auth.clearOAuthSession()
  assert.equal(auth.getStoredOAuthToken(), null)
  assert.equal(auth.getStoredOAuthUser(), null)
  assert.equal(auth.getStoredOAuthProvider(), 'gitcode')
  assert.equal(auth.parseOAuthPending('{"provider":"../bad","session":"s"}'), null)
})

test('extension callback handling leaves built-in selection and fallback to the original flow', () => {
  for (const provider of ['gitcode', 'github', 'agentos']) {
    assert.equal(auth.isExtensionProvider(provider), false)
    assert.equal(auth.resolveExtensionCallbackProvider(provider, 'enterprise'), null)
    assert.equal(auth.resolveExtensionCallbackProvider('', provider), null)
    assert.equal(auth.resolveExtensionCallbackProvider('../invalid', provider), null)
  }
  assert.equal(auth.resolveExtensionCallbackProvider('enterprise', 'github'), 'enterprise')
  assert.equal(auth.resolveExtensionCallbackProvider('', 'enterprise'), 'enterprise')
  assert.equal(auth.resolveExtensionCallbackProvider('../invalid', null), null)
})
