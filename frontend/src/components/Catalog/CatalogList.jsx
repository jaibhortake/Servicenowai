import { Search } from 'lucide-react'
import { useState } from 'react'
import CatalogCard from './CatalogCard'

export default function CatalogList({ items, selected, onSelect }) {
  const [filter, setFilter] = useState('')

  console.log('🟡 CatalogList rendered with items:', items)
  if (items?.length > 0) {
    console.log('  First item structure:', items[0])
    console.log('  Item keys:', Object.keys(items[0]))
  }

  const filtered = items.filter(
    (i) =>
      i.name?.toLowerCase().includes(filter.toLowerCase()) ||
      i.short_description?.toLowerCase().includes(filter.toLowerCase())
  )

  return (
    <div className="space-y-3">
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
        <input
          className="input-field pl-9"
          placeholder="Filter catalog items..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      </div>
      <div className="grid grid-cols-1 gap-2 max-h-72 overflow-y-auto pr-1">
        {filtered.length === 0 && (
          <p className="text-sm text-slate-400 text-center py-6">No matching catalog items</p>
        )}
        {filtered.map((item) => (
          <CatalogCard
            key={item.sys_id}
            item={item}
            selected={selected?.sys_id === item.sys_id}
            onSelect={onSelect}
          />
        ))}
      </div>
    </div>
  )
}