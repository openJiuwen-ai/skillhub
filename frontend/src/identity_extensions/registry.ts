import type { IdentityExtensionUI } from './contracts'
import { ProviderLoginButton } from './ProviderLoginButton'

// Directory names match configured backend adapter names, not provider/channel IDs.
const adapters = import.meta.glob<{ default: IdentityExtensionUI }>('./*/index.ts', { eager: true })
const fallback: IdentityExtensionUI = { LoginButton: ProviderLoginButton }

export function getIdentityExtensionUI(adapter?: string): IdentityExtensionUI {
  return adapters[`./${adapter}/index.ts`]?.default ?? fallback
}
