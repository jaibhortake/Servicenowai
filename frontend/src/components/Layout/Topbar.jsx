import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search, Moon, Sun, LogOut, ChevronDown, CornerDownLeft } from 'lucide-react'
import { nav } from './Sidebar'

function usePageMatches(query, isAdmin) {
  return useMemo(() => {
    const q = query.trim().toLowerCase()
    return nav
      .filter((n) => isAdmin || !n.adminOnly)
      .filter((n) => !q || n.label.toLowerCase().includes(q))
  }, [query, isAdmin])
}

function CommandPalette({ open, onClose, isAdmin }) {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef(null)
  const matches = usePageMatches(query, isAdmin)

  useEffect(() => {
    if (open) {
      setQuery('')
      setActive(0)
      setTimeout(() => inputRef.current?.focus(), 0)
    }
  }, [open])

  if (!open) return null

  const go = (to) => {
    navigate(to)
    onClose()
  }

  const onKeyDown = (e) => {
    if (e.key === 'Escape') onClose()
    else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((i) => Math.min(i + 1, matches.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter' && matches[active]) go(matches[active].to)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh] bg-slate-900/40 animate-fade-in" onClick={onClose}>
      <div
        className="w-full max-w-lg mx-4 bg-white rounded-xl border border-slate-200 shadow-pop overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Quick navigation"
      >
        <div className="flex items-center gap-2 px-4 border-b border-slate-200">
          <Search className="w-4 h-4 text-slate-400" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              setActive(0)
            }}
            onKeyDown={onKeyDown}
            placeholder="Jump to a page..."
            className="flex-1 py-3.5 text-sm bg-transparent outline-none placeholder:text-slate-400"
          />
          <kbd className="text-[10px] text-slate-400 border border-slate-200 rounded px-1.5 py-0.5">Esc</kbd>
        </div>
        <ul className="max-h-72 overflow-y-auto p-2">
          {matches.length === 0 && <li className="px-3 py-6 text-center text-sm text-slate-500">No matching pages</li>}
          {matches.map(({ to, label, icon: Icon }, i) => (
            <li key={to}>
              <button
                onMouseEnter={() => setActive(i)}
                onClick={() => go(to)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-left ${
                  i === active ? 'bg-brand-50 text-brand-700' : 'text-slate-700'
                }`}
              >
                <Icon className="w-4 h-4" />
                <span className="flex-1">{label}</span>
                {i === active && <CornerDownLeft className="w-3.5 h-3.5 opacity-60" />}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}

export default function Topbar({ user, theme, onToggleTheme, onLogout }) {
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef(null)
  const isAdmin = user?.role === 'admin'

  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen(true)
      }
    }
    const onClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false)
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('mousedown', onClick)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mousedown', onClick)
    }
  }, [])

  const initials = (user?.username || 'U').slice(0, 2).toUpperCase()

  return (
    <div className="h-14 bg-white border-b border-slate-200 px-4 flex items-center gap-3 shrink-0">
      <button
        onClick={() => setPaletteOpen(true)}
        className="flex-1 max-w-md flex items-center gap-2 px-3 py-1.5 rounded-lg border border-slate-200 bg-slate-50 text-sm text-slate-400 hover:border-slate-300 transition-colors"
      >
        <Search className="w-4 h-4" />
        <span className="flex-1 text-left">Search pages...</span>
        <kbd className="text-[10px] border border-slate-200 rounded px-1.5 py-0.5 bg-white">Ctrl K</kbd>
      </button>

      <div className="flex-1" />

      <button
        onClick={onToggleTheme}
        className="p-2 rounded-lg text-slate-500 hover:bg-slate-100 transition-colors"
        aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
      >
        {theme === 'dark' ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
      </button>

      <div className="relative" ref={menuRef}>
        <button
          onClick={() => setMenuOpen((v) => !v)}
          className="flex items-center gap-2 pl-1 pr-2 py-1 rounded-lg hover:bg-slate-100 transition-colors"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
        >
          <span className="w-7 h-7 rounded-full bg-brand-600 text-white text-xs font-semibold flex items-center justify-center">
            {initials}
          </span>
          <span className="hidden sm:block text-sm font-medium text-slate-700">{user?.username}</span>
          <ChevronDown className="w-3.5 h-3.5 text-slate-400" />
        </button>
        {menuOpen && (
          <div role="menu" className="absolute right-0 mt-2 w-52 bg-white rounded-xl border border-slate-200 shadow-pop p-1.5 animate-fade-in">
            <div className="px-3 py-2 border-b border-slate-100 mb-1">
              <p className="text-sm font-medium text-slate-900">{user?.username}</p>
              <p className="text-xs text-slate-500 capitalize">{user?.role}</p>
            </div>
            <button
              role="menuitem"
              onClick={onLogout}
              className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-slate-700 hover:bg-slate-100"
            >
              <LogOut className="w-4 h-4" />
              Sign out
            </button>
          </div>
        )}
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} isAdmin={isAdmin} />
    </div>
  )
}
