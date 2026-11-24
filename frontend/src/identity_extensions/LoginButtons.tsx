import type { ExtensionProvider, IdentityExtension } from './providers'
import { getIdentityExtensionUI } from './registry'

type Props = {
  providers: IdentityExtension[]
  disabled: boolean
  onLogin: (provider: ExtensionProvider) => void
}

export function ExtensionLoginButtons({ providers, disabled, onLogin }: Props) {
  return <>{providers.map(provider => {
    const { LoginButton } = getIdentityExtensionUI(provider.adapter)
    return <LoginButton key={provider.id} provider={provider} disabled={disabled} onLogin={onLogin} />
  })}</>
}
