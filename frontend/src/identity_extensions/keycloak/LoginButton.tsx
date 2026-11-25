import { KeyRound } from 'lucide-react'
import type { ExtensionLoginButtonProps } from '../contracts'
import { ProviderLoginButton } from '../ProviderLoginButton'

export function KeycloakLoginButton(props: ExtensionLoginButtonProps) {
  return <ProviderLoginButton {...props} icon={<KeyRound className="h-4 w-4" aria-hidden />} />
}
