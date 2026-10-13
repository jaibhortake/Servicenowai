import { useEffect, useRef, useState } from 'react'
import { withToken } from '../auth/auth'
import { useParams, useLocation } from 'react-router-dom'
import { Send, RefreshCw, ArrowLeft } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import Header from '../components/Layout/Header'
import ConversationThread from '../components/Conversation/ConversationThread'
import { getTicket, getConversation, addComment, getComments, getWorkNotes } from '../api/client'
import { useStore } from '../store/useStore'
import toast from 'react-hot-toast'
import clsx from 'clsx'
import { getLastViewChatTarget } from '../utils/viewChatSession'

const stateMap = {
  '1': { label: 'Open', cls: 'badge-blue' },
  '2': { label: 'In Progress', cls: 'badge-yellow' },
  '6': { label: 'Resolved', cls: 'badge-green' },
  '7': { label: 'Closed', cls: 'badge-gray' },
  '-5': { label: 'New', cls: 'badge-blue' },
}

const deduplicateMessages = (msgs) => {
  const seen = new Map()
  const deduped = []

  // Helper to extract core message content (remove [UserName]: prefix)
  const getCoreContent = (content) => {
    if (!content) return content
    return content.replace(/^\[.*?\]:\s*/, '').trim()
  }

  for (const msg of msgs) {
    // Normalize timestamp to ISO string format (ignoring milliseconds for matching)
    const normalizedTime = new Date(msg.timestamp).toISOString().split('.')[0]
    // Use core content (without prefix) + timestamp as unique key
    const coreContent = getCoreContent(msg.content)
    const key = `${coreContent}|${normalizedTime}`

    if (!seen.has(key)) {
      seen.set(key, true)
      deduped.push(msg)
    } else {
    }
  }

  // Sort by timestamp
  deduped.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp))

  return deduped
}

export default function TicketDetail() {
  const { type, sysId } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const { currentUser, currentUserEmail } = useStore()
  const lastViewChatTarget = getLastViewChatTarget()
  const routeHasSessionId = Boolean(
    location.state &&
    Object.prototype.hasOwnProperty.call(location.state, 'sessionId')
  )
  const sid = routeHasSessionId
    ? location.state.sessionId
    : lastViewChatTarget?.ticketType === type && lastViewChatTarget?.sysId === sysId
    ? (lastViewChatTarget.sessionId || '')
    : ''

  const [ticket, setTicket] = useState(null)
  const [session, setSession] = useState(null)
  const [messages, setMessages] = useState([])
  const [reply, setReply] = useState('')
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const wsRef = useRef(null)
  const commentsWsRef = useRef(null)
  const seenMessageIds = useRef(new Set())
  const endRef = useRef(null)

  const mapJournalEntries = (entries, defaultRole = 'admin') => {
    const possibleNames = [currentUser?.name, currentUserEmail, 'User'].filter(Boolean)
    const userPrefixes = possibleNames.map(name => `[${name}]:`)
    return (entries || []).map((entry) => ({
      id: `snow-${entry.sys_created_on}-${String(entry.value || '').substring(0,20)}-${entry.element || defaultRole}`,
      role: entry.element === 'work_notes'
        ? 'admin'
        : userPrefixes.some(p => String(entry.value || '').startsWith(p))
          ? 'user'
          : defaultRole,
      content: entry.value || '',
      timestamp: entry.sys_created_on,
    }))
  }

  useEffect(() => {
    fetchAll()
    if (sid) connectWs(sid)
    connectCommentsWs(sysId, type)

    // Initialize seen messages with current messages
    setMessages(prev => {
      prev.forEach(msg => seenMessageIds.current.add(msg.id))
      return prev
    })

    // Poll comments every 5 seconds
    const pollInterval = setInterval(async () => {
      try {
        const [freshComments, freshWorkNotes] = await Promise.all([
          getComments(sysId, type),
          getWorkNotes(sysId, type),
        ])
        const freshEntries = [...(freshComments || []), ...(freshWorkNotes || [])]
        if (!freshEntries.length) {
          return
        }

        const newMsgs = []
        for (const c of freshEntries) {
          const msgId = `snow-${c.sys_created_on}-${String(c.value || '').substring(0,20)}-${c.element || 'comment'}`

          // Only add if we haven't seen this message before
          if (!seenMessageIds.current.has(msgId)) {
            seenMessageIds.current.add(msgId)
            newMsgs.push(mapJournalEntries([c])[0])
          }
        }

        // Only update state if there are new messages
        if (newMsgs.length > 0) {
          setMessages(prev => {
            const merged = [...prev, ...newMsgs]
            return deduplicateMessages(merged)
          })
        }
      } catch (err) {
      }
    }, 5000)

    return () => {
      clearInterval(pollInterval)
      wsRef.current?.close()
      commentsWsRef.current?.close()
    }
  }, [sysId, sid, type])

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  const fetchAll = async () => {
    setLoading(true)

    // Fetch ticket — required
    let t = null
    try {
      t = await getTicket(type, sysId)
      setTicket(t)
    } catch {
      toast.error('Could not load ticket from ServiceNow')
      setLoading(false)
      return
    }

    // Fetch comments — optional, pass ticket_type so backend resolves RITM
    let snowMsgs = []
    try {
      const [comments, workNotes] = await Promise.all([
        getComments(sysId, type),
        getWorkNotes(sysId, type),
      ])
      snowMsgs = deduplicateMessages([
        ...mapJournalEntries(comments || []),
        ...mapJournalEntries(workNotes || []),
      ])
    } catch { /* comments are non-critical */ }

    // Fetch session conversation — optional
    if (sid) {
      try {
        const sess = await getConversation(sid)
        setSession(sess)
        // Merge ServiceNow comments with session messages, deduplicate by content
        if (sess?.messages?.length) {
          const merged = mergeMessages(snowMsgs, sess.messages)
          setMessages(merged)
        } else if (snowMsgs.length) {
          setMessages(snowMsgs)
        }
      } catch { /* session may not exist */ }
    } else if (snowMsgs.length) {
      setMessages(snowMsgs)
    }

    setLoading(false)
  }

  const mergeMessages = (snowMsgs, sessionMsgs) => {
    // Combine ServiceNow comments and session messages
    const combined = [...snowMsgs, ...(sessionMsgs || [])]
    // Deduplicate and sort
    return deduplicateMessages(combined)
  }

  const connectWs = (sessionId) => {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(withToken(`${wsProtocol}//${window.location.host}/api/conversations/ws/${sessionId}`))
    wsRef.current = ws
    ws.onmessage = (e) => {
      const data = JSON.parse(e.data)
      if (data.type === 'connected' && data.messages?.length) {
        setMessages(data.messages)
      } else if (data.type === 'ai_response' || data.type === 'admin_question') {
        setMessages(prev => [...prev, {
          role: data.type === 'admin_question' ? 'admin' : 'ai',
          content: data.message,
          timestamp: new Date().toISOString(),
        }])
      }
    }
  }

  const connectCommentsWs = (ticketSysId, ticketType) => {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const apiBase = new URL(import.meta.env.VITE_API_URL || window.location.origin, window.location.origin)
    apiBase.protocol = wsProtocol
    const wsUrl = `${apiBase.origin}/api/comments/ws/${ticketSysId}?ticket_type=${ticketType}`
    const ws = new WebSocket(withToken(wsUrl))
    commentsWsRef.current = ws

    ws.onopen = () => {
    }

    ws.onerror = (err) => {
    }

    ws.onmessage = (e) => {
      const data = JSON.parse(e.data)
      if (data.type === 'new_comment' && data.comment) {
        const comment = data.comment
        const msgId = `snow-${comment.sys_created_on}-${comment.value.substring(0,20)}`

        setMessages(prev => {
          // Detect role based on user prefixes
          const possibleNames = [currentUser?.name, currentUserEmail, 'User'].filter(Boolean)
          const userPrefixes = possibleNames.map(name => `[${name}]:`)
          const role = userPrefixes.some(p => comment.value?.startsWith(p)) ? 'user' : 'admin'

          const newMsg = {
            id: msgId,
            role,
            content: comment.value,
            timestamp: comment.sys_created_on,
          }

          // Add new message and deduplicate
          const allMessages = [...prev, newMsg]
          return deduplicateMessages(allMessages)
        })
      }
    }
  }

  const handleSend = async (e) => {
    e.preventDefault()
    if (!reply.trim() || sending) return
    const text = reply.trim()
    setReply('')
    setSending(true)

    // Ensure we have a user name to prefix the comment
    const displayName = currentUser?.name || currentUserEmail || 'User'

    try {
      await addComment(sysId, {
        ticket_sys_id: sysId,
        ticket_table: type,
        comment: text,
        is_work_note: false,
        user_name: displayName,
      }, sid)
      toast.success('Reply added to ticket')
    } catch {
      toast.error('Failed to send reply')
    }

    setSending(false)
  }

  if (loading) {
    return (
      <div className="flex flex-col h-screen">
        <Header title="Ticket Detail" />
        <div className="flex items-center justify-center flex-1 gap-2 text-slate-400">
          <RefreshCw className="w-4 h-4 animate-spin" /> Loading...
        </div>
      </div>
    )
  }

  const state = ticket ? (stateMap[String(ticket.state)] || { label: ticket.state, cls: 'badge-gray' }) : null

  return (
    <div className="flex flex-col h-screen">
      <Header title={ticket?.number || 'Ticket Detail'} />
      <div className="flex flex-1 overflow-hidden">
        {/* Left: Ticket info */}
        <div className="w-80 border-r border-slate-200 bg-white overflow-y-auto flex-shrink-0 p-5 space-y-5">
          <button className="btn-ghost -ml-1 text-sm" onClick={() => navigate('/dashboard')}>
            <ArrowLeft className="w-4 h-4" /> Back to Dashboard
          </button>

          {ticket && (
            <div className="space-y-4">
              <div>
                <span className={clsx('badge mb-2', type === 'CH' ? 'badge-yellow' : 'badge-blue')}>{type === 'CH' ? 'Change Request' : 'Service Request'}</span>
                <h2 className="text-xl font-bold text-slate-800">{ticket.number}</h2>
              </div>
              <InfoItem label="Status" value={<span className={state?.cls}>{state?.label}</span>} />
              <InfoItem label="Description" value={ticket.short_description} />
              {ticket.assigned_to && <InfoItem label="Assigned To" value={ticket.assigned_to} />}
              {ticket.opened_at && <InfoItem label="Opened" value={new Date(ticket.opened_at).toLocaleString()} />}
              {ticket.raw?.urgency && (
                <InfoItem label="Urgency" value={['', 'Critical', 'High', 'Medium', 'Low'][parseInt(ticket.raw.urgency?.value || ticket.raw.urgency)] || 'Medium'} />
              )}
              {type === 'CH' && ticket.raw?.risk && (
                <InfoItem label="Risk" value={ticket.raw.risk?.display_value || ticket.raw.risk} />
              )}
            </div>
          )}
        </div>

        {/* Right: Conversation */}
        <div className="flex-1 flex flex-col">
          <div className="px-4 py-2 border-b border-slate-100 bg-slate-50">
            <p className="text-xs text-slate-500 font-medium">Conversation Thread</p>
          </div>
          <div className="flex-1 overflow-y-auto p-4">
            <ConversationThread messages={messages} />
            <div ref={endRef} />
          </div>
          <div className="border-t border-slate-200 bg-white p-4">
            <form onSubmit={handleSend} className="flex gap-2">
              <input
                className="input-field flex-1"
                placeholder="Reply to this ticket..."
                value={reply}
                onChange={(e) => setReply(e.target.value)}
              />
              <button className="btn-primary px-3" type="submit" disabled={sending || !reply.trim()}>
                <Send className="w-4 h-4" />
              </button>
            </form>
            <p className="text-xs text-slate-400 mt-2">Your reply will be added to the ServiceNow ticket</p>
          </div>
        </div>
      </div>
    </div>
  )
}

function InfoItem({ label, value }) {
  return (
    <div>
      <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-0.5">{label}</p>
      <div className="text-sm text-slate-700">{value}</div>
    </div>
  )
}
