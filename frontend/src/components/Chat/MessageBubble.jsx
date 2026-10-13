import { format } from 'date-fns'
import clsx from 'clsx'
import { User, ShieldCheck, Zap } from 'lucide-react'

const roleConfig = {
  user: { label: 'You', bg: 'bg-brand-600', text: 'text-white', align: 'items-end', bubble: 'bg-brand-600 text-white rounded-2xl rounded-br-none', icon: User },
  ai: { label: 'AI', bg: 'bg-brand-100', text: 'text-brand-700', align: 'items-start', bubble: 'bg-white border border-slate-200 text-slate-800 rounded-2xl rounded-tl-none shadow-sm', icon: Zap },
  system: { label: 'System', bg: 'bg-slate-100', text: 'text-slate-600', align: 'items-start', bubble: 'bg-slate-100 text-slate-600 rounded-xl italic text-sm', icon: null },
  admin: { label: 'Admin', bg: 'bg-amber-100', text: 'text-amber-700', align: 'items-start', bubble: 'bg-amber-50 border border-amber-200 text-slate-800 rounded-2xl rounded-tl-none', icon: ShieldCheck },
}

export default function MessageBubble({ message }) {
  const { role, content, timestamp } = message
  const cfg = roleConfig[role] || roleConfig.ai
  const Icon = cfg.icon
  const isUser = role === 'user'
  const isSystem = role === 'system'

  return (
    <div className={clsx('flex flex-col gap-1 px-4 animate-slide-up', cfg.align)}>
      <div className={clsx('flex items-end gap-2', isUser && 'flex-row-reverse')}>
        {!isUser && !isSystem && Icon && (
          <div className={clsx('w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0', cfg.bg)}>
            <Icon className={clsx('w-3.5 h-3.5', cfg.text)} />
          </div>
        )}
        <div className={clsx('max-w-[480px] px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap', cfg.bubble)}>
          {content}
        </div>
      </div>
      <p className={clsx('text-[11px] text-slate-400', isUser ? 'pr-1 text-right' : 'pl-9')}>
        {timestamp ? format(new Date(timestamp), 'h:mm a') : ''}
      </p>
    </div>
  )
}