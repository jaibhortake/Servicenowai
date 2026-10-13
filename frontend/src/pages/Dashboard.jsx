import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { RefreshCw, Ticket, GitPullRequest, Clock, CheckCircle, AlertCircle, Search } from 'lucide-react'
import Header from '../components/Layout/Header'
import { listTickets, getMe } from '../api/client'
import { useStore } from '../store/useStore'
import { format } from 'date-fns'
import clsx from 'clsx'

const stateMap = {
  '1': { label: 'Open', cls: 'badge-blue' },
  '2': { label: 'In Progress', cls: 'badge-yellow' },
  '3': { label: 'Pending', cls: 'badge-yellow' },
  '6': { label: 'Resolved', cls: 'badge-green' },
  '7': { label: 'Closed', cls: 'badge-gray' },
  '-5': { label: 'New', cls: 'badge-blue' },
  new: { label: 'New', cls: 'badge-blue' },
  in_progress: { label: 'In Progress', cls: 'badge-yellow' },
}

const riskMap = {
  low: 'badge-green', medium: 'badge-yellow', high: 'badge-red', critical: 'badge-red',
}

export default function Dashboard() {
  const navigate = useNavigate()
  const { currentUser, currentUserEmail, setCurrentUser, tickets, setTickets } = useStore()
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('all')

  const fetchTickets = async () => {
    setLoading(true)
    try {
      let userSysId = currentUser?.sys_id
      if (!userSysId && currentUserEmail) {
        try {
          const user = await getMe(currentUserEmail)
          setCurrentUser(user)
          userSysId = user.sys_id
        } catch {
          // User not in ServiceNow — fetch all tickets instead
        }
      }
      const data = await listTickets(userSysId)
      setTickets(data)
    } catch {
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchTickets() }, [])

  const filtered = tickets.filter((t) => {
    const matchText = !filter ||
      t.number?.toLowerCase().includes(filter.toLowerCase()) ||
      t.short_description?.toLowerCase().includes(filter.toLowerCase())
    const matchType = typeFilter === 'all' || t.ticket_type === typeFilter
    return matchText && matchType
  })

  const stats = {
    total: tickets.length,
    open: tickets.filter(t => ['1', '-5', 'new'].includes(String(t.state))).length,
    inProgress: tickets.filter(t => ['2', 'in_progress'].includes(String(t.state))).length,
    resolved: tickets.filter(t => ['6', '7'].includes(String(t.state))).length,
  }

  return (
    <div className="flex flex-col h-screen">
      <Header title="My Tickets" />
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {/* Stats */}
        <div className="grid grid-cols-4 gap-4">
          <StatCard label="Total" value={stats.total} icon={Ticket} color="blue" />
          <StatCard label="Open" value={stats.open} icon={AlertCircle} color="orange" />
          <StatCard label="In Progress" value={stats.inProgress} icon={Clock} color="yellow" />
          <StatCard label="Resolved" value={stats.resolved} icon={CheckCircle} color="green" />
        </div>

        {/* Filters */}
        <div className="flex items-center gap-3">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
            <input className="input-field pl-9" placeholder="Search tickets..." value={filter} onChange={e => setFilter(e.target.value)} />
          </div>
          <div className="flex gap-1 bg-white border border-slate-200 rounded-lg p-1">
            {['all', 'SR', 'CH'].map(t => (
              <button key={t} onClick={() => setTypeFilter(t)}
                className={clsx('px-3 py-1 text-sm rounded-md font-medium transition-colors',
                  typeFilter === t ? 'bg-brand-600 text-white' : 'text-slate-600 hover:bg-slate-100')}>
                {t === 'all' ? 'All' : t === 'SR' ? 'Service Requests' : 'Change Requests'}
              </button>
            ))}
          </div>
          <button className="btn-secondary" onClick={fetchTickets} disabled={loading}>
            <RefreshCw className={clsx('w-4 h-4', loading && 'animate-spin')} />
          </button>
        </div>

        {/* Table */}
        <div className="card overflow-hidden">
          {loading ? (
            <div className="flex items-center justify-center py-16 gap-2 text-slate-400">
              <RefreshCw className="w-4 h-4 animate-spin" /> Loading tickets...
            </div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-16 text-slate-400">
              <Ticket className="w-10 h-10 mx-auto mb-3 opacity-30" />
              <p className="font-medium">No tickets found</p>
              <p className="text-sm mt-1">Submit a new request to get started</p>
            </div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-100">
                  {['Number', 'Type', 'Description', 'State', 'Opened', ''].map(h => (
                    <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {filtered.map((t, i) => {
                  const state = stateMap[String(t.state)] || { label: t.state, cls: 'badge-gray' }
                  const dateVal = t.opened_at?.value || t.opened_at
                  return (
                    <tr key={i} className="hover:bg-slate-50 transition-colors">
                      <td className="px-4 py-3">
                        <span className="font-mono text-sm font-semibold text-brand-600">{t.number}</span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={t.ticket_type === 'CH' ? 'badge-yellow' : 'badge-blue'}>
                          {t.ticket_type}
                        </span>
                      </td>
                      <td className="px-4 py-3 max-w-xs">
                        <p className="text-sm text-slate-700 truncate">{t.short_description}</p>
                      </td>
                      <td className="px-4 py-3">
                        <span className={state.cls}>{state.label}</span>
                      </td>
                      <td className="px-4 py-3 text-sm text-slate-400">
                        {dateVal ? format(new Date(dateVal), 'MMM d, yyyy') : '—'}
                      </td>
                      <td className="px-4 py-3">
                        <button
                          className="text-xs text-brand-600 hover:underline font-medium"
                          onClick={() => navigate(`/ticket/${t.ticket_type}/${t.sys_id}`)}
                        >
                          View
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}

function StatCard({ label, value, icon: Icon, color }) {
  const colorMap = {
    blue: 'bg-blue-50 text-blue-600',
    orange: 'bg-orange-50 text-orange-600',
    yellow: 'bg-amber-50 text-amber-600',
    green: 'bg-emerald-50 text-emerald-600',
  }
  return (
    <div className="card p-4 flex items-center gap-3">
      <div className={clsx('w-10 h-10 rounded-xl flex items-center justify-center', colorMap[color])}>
        <Icon className="w-5 h-5" />
      </div>
      <div>
        <p className="text-2xl font-bold text-slate-800">{value}</p>
        <p className="text-xs text-slate-400">{label}</p>
      </div>
    </div>
  )
}