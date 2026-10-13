import { CheckCircle, Package, ArrowRight } from 'lucide-react'
import clsx from 'clsx'

function parseCategory(raw) {
  if (!raw) return null
  if (typeof raw === 'object') return raw.display_value || raw.value || null
  // Strip raw ServiceNow dict strings like "{'link': '...', 'value': '...'}"
  if (typeof raw === 'string' && raw.startsWith('{')) return null
  return raw
}

export default function CatalogCard({ item, selected, onSelect }) {
  const category = parseCategory(item.category)

  return (
    <button
      onClick={() => onSelect(item)}
      className={clsx(
        'w-full text-left p-4 rounded-xl border-2 transition-all duration-150 group',
        selected
          ? 'border-brand-500 bg-brand-50'
          : 'border-slate-200 bg-white hover:border-brand-300 hover:shadow-md'
      )}
    >
      <div className="flex items-start gap-3">
        <div className={clsx(
          'w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0',
          selected ? 'bg-brand-500' : 'bg-slate-100 group-hover:bg-brand-100'
        )}>
          <Package className={clsx('w-4 h-4', selected ? 'text-white' : 'text-slate-500 group-hover:text-brand-600')} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm font-semibold text-slate-800 truncate">{item.name}</p>
            {selected
              ? <CheckCircle className="w-4 h-4 text-brand-500 flex-shrink-0" />
              : <ArrowRight className="w-4 h-4 text-slate-300 group-hover:text-brand-400 flex-shrink-0 transition-colors" />
            }
          </div>
          {item.short_description && (
            <p className="text-xs text-slate-500 mt-0.5 line-clamp-2">{item.short_description}</p>
          )}
          {item.match_reason && (
            <p className="text-[11px] text-slate-400 mt-1 line-clamp-2">{item.match_reason}</p>
          )}
          <div className="flex items-center gap-2 mt-2">
            {category && (
              <span className="badge-gray">{category}</span>
            )}
            {item.relevance_score > 0 && (
              <span className="text-[10px] text-brand-600 font-medium">
                {Math.round(item.relevance_score * 100)}% match
              </span>
            )}
          </div>
        </div>
      </div>
    </button>
  )
}
