export default function TypingIndicator() {
  return (
    <div className="flex items-center gap-2 px-4 py-3">
      <div className="w-7 h-7 bg-brand-100 rounded-full flex items-center justify-center flex-shrink-0">
        <span className="text-brand-600 text-xs font-bold">AI</span>
      </div>
      <div className="flex items-center gap-1 bg-white border border-slate-200 rounded-2xl rounded-tl-none px-4 py-2.5 shadow-sm">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="w-1.5 h-1.5 bg-slate-400 rounded-full inline-block animate-pulse-dot"
            style={{ animationDelay: `${i * 0.16}s` }}
          />
        ))}
      </div>
    </div>
  )
}