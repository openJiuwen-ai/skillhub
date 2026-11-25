import type { ReactNode } from 'react'
import { ArrowRight, Sparkles } from 'lucide-react'
import type { ExtensionLoginButtonProps } from './contracts'

export function ProviderLoginButton({
  provider, disabled, onLogin, icon,
}: ExtensionLoginButtonProps & { icon?: ReactNode }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onLogin(provider.id)}
      className="mt-3 inline-flex h-12 w-full items-center justify-center gap-2 rounded-xl border border-slate-300 bg-white px-5 text-[15px] font-semibold text-slate-700 shadow-sm hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#c7d2fe] disabled:cursor-not-allowed disabled:opacity-60"
    >
      {icon ?? <Sparkles className="h-4 w-4" aria-hidden />}
      <span>{provider.display_name}</span>
      <ArrowRight className="h-4 w-4" aria-hidden />
    </button>
  )
}
