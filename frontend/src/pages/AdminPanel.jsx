import { useEffect, useState } from 'react'
import { ShieldCheck, RefreshCw, MessageSquare, Send, ChevronDown, Settings } from 'lucide-react'
import Header from '../components/Layout/Header'
import { listConversations, sendAdminQuestion, listTickets } from '../api/client'
import ConversationThread from '../components/Conversation/ConversationThread'
import ProviderConfig from '../components/Settings/ProviderConfig'
import toast from 'react-hot-toast'
import clsx from 'clsx'
import { format } from 'date-fns'

export default function AdminPanel() {
  const [sessions, setSessions] = useState([])
  const [tickets, setTickets] = useState([])
  const [selected, setSelected] = useState(null)
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [sending, setSending] = useState(false)
  const [activeTab, setActiveTab] = useState('sessions')

  const fetchData = async () => {
    setLoading(true)
    try {
      const [sess, tix] = await Promise.all([listConversations(), listTickets()])
      setSessions(sess || [])
      setTickets(tix || [])
    } catch {
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchData() }, [])

  const handleSendQuestion = async (e) => {
    e.preventDefault()
    if (!question.trim() || !selected || sending) return
    setSending(true)
    try {
      await sendAdminQuestion(selected.session_id, question.trim())
      toast.success('Question sent to user')
      setQuestion('')
      fetchData()
    } catch {
      toast.error('Failed to send question')
    } finally {
      setSending(false)
    }
  }

  const stateColors = {
    OPEN: 'badge-blue', WAITING_USER: 'badge-yellow',
    WAITING_ADMIN: 'badge-green', RESOLVED: 'badge-gray',
  }

  return (
    <div className="flex flex-col h-screen">
      <Header title="Admin Panel" />
      <div className="flex flex-1 overflow-hidden">
        {/* Left: Sessions list */}
        <div className="w-80 border-r border-slate-200 bg-white flex flex-col flex-shrink-0">
          <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between">
            <div className="flex gap-1">
              {['sessions', 'tickets', 'settings'].map(t => (
                <button key={t} onClick={() => setActiveTab(t)}
                  className={clsx('px-3 py-1.5 text-xs rounded-md font-medium capitalize transition-colors flex items-center gap-1',
                    activeTab === t ? 'bg-brand-600 text-white' : 'text-slate-500 hover:bg-slate-100')}>
                  {t === 'settings' && <Settings className="w-3 h-3" />}
                  {t}
                </button>
              ))}
            </div>
            <button onClick={fetchData} className="p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-md">
              <RefreshCw className={clsx('w-3.5 h-3.5', loading && 'animate-spin')} />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto divide-y divide-slate-50">
            {activeTab === 'sessions' && (
              sessions.length === 0
                ? <EmptyState icon={MessageSquare} text="No active sessions" />
                : sessions.map(s => (
                  <button key={s.session_id} onClick={() => setSelected(s)}
                    className={clsx('w-full text-left px-4 py-3 hover:bg-slate-50 transition-colors',
                      selected?.session_id === s.session_id && 'bg-brand-50')}>
                    <div className="flex items-center justify-between mb-1">
                      <p className="text-sm font-medium text-slate-800 truncate">{s.user_email}</p>
                      <span className={clsx('badge text-[10px]', stateColors[s.state] || 'badge-gray')}>{s.state}</span>
                    </div>
                    <p className="text-xs text-slate-400">{s.ticket_id ? `Ticket: ${s.ticket_id.slice(0, 8)}...` : 'No ticket yet'}</p>
                    <p className="text-xs text-slate-300 mt-0.5">{format(new Date(s.last_activity), 'MMM d, h:mm a')}</p>
                  </button>
                ))
            )}
            {activeTab === 'tickets' && (
              tickets.length === 0
                ? <EmptyState icon={ShieldCheck} text="No tickets found" />
                : tickets.map((t, i) => (
                  <div key={i} className="px-4 py-3 hover:bg-slate-50">
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-xs font-semibold text-brand-600">{t.number}</span>
                      <span className={t.ticket_type === 'CH' ? 'badge-yellow' : 'badge-blue'}>{t.ticket_type}</span>
                    </div>
                    <p className="text-xs text-slate-600 mt-1 truncate">{t.short_description}</p>
                  </div>
                ))
            )}
            {activeTab === 'settings' && (
              <div className="text-center py-8 text-slate-400">
                <Settings className="w-8 h-8 mx-auto mb-2 opacity-30" />
                <p className="text-sm">Configuration in main panel</p>
              </div>
            )}
          </div>
        </div>

        {/* Right: Conversation + admin tools */}
        <div className="flex-1 flex flex-col">
          {activeTab === 'settings' ? (
            <ProviderConfig />
          ) : !selected ? (
            <div className="flex items-center justify-center h-full">
              <div className="text-center text-slate-400">
                <ShieldCheck className="w-10 h-10 mx-auto mb-3 opacity-30" />
                <p className="font-medium">Select a session to manage</p>
                <p className="text-sm mt-1">View conversations and send follow-up questions</p>
              </div>
            </div>
          ) : (
            <>
              <div className="px-5 py-3 border-b border-slate-100 bg-white flex items-center justify-between">
                <div>
                  <p className="font-semibold text-slate-800 text-sm">{selected.user_email}</p>
                  <p className="text-xs text-slate-400">
                    Session: {selected.session_id.slice(0, 8)}... ·{' '}
                    {selected.messages?.length || 0} messages
                  </p>
                </div>
                <span className={clsx('badge', stateColors[selected.state] || 'badge-gray')}>{selected.state}</span>
              </div>

              <div className="flex-1 overflow-y-auto p-5">
                <ConversationThread messages={selected.messages || []} />
              </div>

              {/* Admin question form */}
              <div className="border-t border-slate-200 bg-slate-50 p-4 space-y-2">
                <p className="text-xs font-semibold text-slate-500 flex items-center gap-1">
                  <ShieldCheck className="w-3.5 h-3.5" /> Send Follow-up Question to User
                </p>
                <form onSubmit={handleSendQuestion} className="flex gap-2">
                  <input
                    className="input-field flex-1"
                    placeholder="Ask the user a follow-up question..."
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                  />
                  <button className="btn-primary px-3" type="submit" disabled={sending || !question.trim()}>
                    <Send className="w-4 h-4" />
                  </button>
                </form>
                <p className="text-xs text-slate-400">
                  AI will professionally rephrase your question before delivering it to the user.
                </p>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function EmptyState({ icon: Icon, text }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-slate-400">
      <Icon className="w-8 h-8 mb-2 opacity-30" />
      <p className="text-sm">{text}</p>
    </div>
  )
}