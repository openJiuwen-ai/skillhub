import assert from 'node:assert/strict'
import test from 'node:test'
import { createServer } from 'vite'

test('account-system directories are discovered and unknown adapters retain generic UI', async () => {
  const server = await createServer({
    configFile: false,
    server: { middlewareMode: true, watch: null },
    appType: 'custom',
    optimizeDeps: { noDiscovery: true, include: [] },
    esbuild: { jsx: 'automatic' },
  })
  try {
    const registry = await server.ssrLoadModule('/src/identity_extensions/registry.ts')
    const keycloak = registry.getIdentityExtensionUI('keycloak')
    assert.equal(keycloak.LoginButton.name, 'KeycloakLoginButton')
    assert.equal(registry.getIdentityExtensionUI().LoginButton.name, 'ProviderLoginButton')
    assert.equal(registry.getIdentityExtensionUI('future-system').LoginButton.name, 'ProviderLoginButton')
    assert.equal(registry.getIdentityExtensionUI('../keycloak').LoginButton.name, 'ProviderLoginButton')
  } finally {
    await server.close()
  }
})
