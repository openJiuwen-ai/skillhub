import type { ComponentType } from 'react'
import type { ExtensionProvider, IdentityExtension } from './providers'

export type ExtensionLoginButtonProps = {
  provider: IdentityExtension
  disabled: boolean
  onLogin: (provider: ExtensionProvider) => void
}

/** Each account-system directory can export its own UI through index.ts. */
export type IdentityExtensionUI = {
  LoginButton: ComponentType<ExtensionLoginButtonProps>
}
