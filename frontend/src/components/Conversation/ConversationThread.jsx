import { useEffect, useRef } from 'react'
import { format } from 'date-fns'
import clsx from 'clsx'
import { ShieldCheck, User, Zap, Info } from 'lucide-react'
import { useStore } from '../../store/useStore'

const roleConfig = {
  user: { icon: User, bg: 'bg-brand-600', label: 'You', bubbleCls: 'bg-brand-600 text-white rounded-2xl rounded-br-none', right: true },
  admin: { icon: ShieldCheck, bg: 'bg-amber-500', label: 'Admin', bubbleCls: 'bg-amber-50 border border-amber-200 text-slate-800 rounded-2xl rounded-tl-none' },
  ai: { icon: Zap, bg: 'bg-brand-100', label: 'AI Assistant', bubbleCls: 'bg-white border border-slate-200 text-slate-800 rounded-2xl rounded-tl-none shadow-sm' },
  system: { icon: Info, bg: 'bg-slate-200', label: 'System', bubbleCls: 'bg-slate-100 text-slate-500 rounded-xl text-xs italic px-3 py-2' },
}

export default function ConversationThread({ messages }) {
  const { currentUser, currentUserEmail } = useStore()
  const endRef = useRef(null)
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  const displayMessages =
    messages?.[0]?.role === 'admin' ? messages.slice(1) : messages

  if (!displayMessages?.length) {
    return (
      <div className="flex items-center justify-center h-32 text-slate-400 text-sm">
        No messages yet
      </div>
    )
  }

  const formatMessageContent = (msg) => {
    if (msg.role !== 'user') return msg.content

    // For user messages, keep the prefix as-is if it exists
    // (it comes from ServiceNow with the user's actual name)
    const hasAnyPrefix = msg.content.match(/^\[.*?\]:/)

    if (hasAnyPrefix) {
      // Keep the existing prefix from ServiceNow
      return msg.content
    } else {
      // Only add prefix if missing
      const displayName = currentUser?.name || currentUserEmail || 'User'
      return `[${displayName}]: ${msg.content}`
    }
  }

  return (
    <div className="space-y-3 py-2">
      {displayMessages.map((msg) => {
        const cfg = roleConfig[msg.role] || roleConfig.ai
        const Icon = cfg.icon
        const isRight = cfg.right
        const displayContent = formatMessageContent(msg)
        return (
          <div key={msg.id || msg.timestamp} className={clsx('flex gap-2 animate-slide-up', isRight && 'flex-row-reverse')}>
            <div className={clsx('w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 self-end', cfg.bg)}>
              <Icon className="w-3.5 h-3.5 text-white" />
            </div>
            <div className={clsx('max-w-sm space-y-1', isRight && 'items-end flex flex-col')}>
              <p className="text-[10px] text-slate-400 px-1">{cfg.label}</p>
              <div className={clsx('px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap', cfg.bubbleCls, msg.role === 'user' && 'underline')}>
                {displayContent}
              </div>
              {msg.timestamp && (
                <p className="text-[10px] text-slate-300 px-1">
                  {format(new Date(msg.timestamp), 'MMM d, h:mm a')}
                </p>
              )}
            </div>
          </div>
        )
      })}
      <div ref={endRef} />
    </div>
  )
}
