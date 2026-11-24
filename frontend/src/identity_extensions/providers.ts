/** Configured extensions are separate from the three built-in OAuth channels. */
export type ExtensionProvider = string & { readonly __identityExtension: unique symbol }

export type IdentityExtension = {
  id: ExtensionProvider
  display_name: string
  /** Optional for compatibility with servers that only expose id/display_name. */
  adapter?: string
}

export function isExtensionProvider(value: unknown): value is ExtensionProvider {
  return typeof value === 'string'
    && /^[a-z][a-z0-9_-]{0,47}$/.test(value)
    && value !== 'gitcode'
    && value !== 'github'
    && value !== 'agentos'
}

/** A built-in callback always retains precedence over an old extension selection. */
export function resolveExtensionCallbackProvider(
  providerFromUrl: string,
  activeProvider: string | null,
): ExtensionProvider | null {
  if (isExtensionProvider(providerFromUrl)) return providerFromUrl
  if (providerFromUrl === 'gitcode' || providerFromUrl === 'github' || providerFromUrl === 'agentos') return null
  const stored = (activeProvider || '').toLowerCase()
  return isExtensionProvider(stored) ? stored : null
}
