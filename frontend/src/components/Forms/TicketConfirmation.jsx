import { CheckCircle2, Ticket, ArrowRight, ExternalLink, MessageSquare } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { setLastViewChatTarget } from '../../utils/viewChatSession'
import { buildServiceNowRecordUrl } from '../../utils/servicenowLinks'

const stateLabel = {
  '1': 'Open', '2': 'Work in Progress', '3': 'Pending', '6': 'Resolved', '7': 'Closed',
  new: 'New', in_progress: 'In Progress', '-5': 'New',
}

export default function TicketConfirmation({ ticket, sessionId, onNewRequest }) {
  const navigate = useNavigate()
  const state = stateLabel[ticket.state] || ticket.state || 'Open'
  const serviceNowUrl = buildServiceNowRecordUrl(ticket.ticket_type, ticket.number)

  const handleViewAndChat = () => {
    setLastViewChatTarget({
      ticketType: ticket.ticket_type,
      sysId: ticket.sys_id,
      sessionId: sessionId || '',
      number: ticket.number,
    })
    navigate(`/ticket/${ticket.ticket_type}/${ticket.sys_id}`, { state: { sessionId: sessionId || '' } })
  }

  return (
    <div className="text-center space-y-6 animate-fade-in py-4">
      <div className="flex flex-col items-center gap-3">
        <div className="w-16 h-16 bg-emerald-100 rounded-full flex items-center justify-center">
          <CheckCircle2 className="w-8 h-8 text-emerald-500" />
        </div>
        <div>
          <h3 className="text-xl font-bold text-slate-800">
            {ticket.ticket_type === 'CH' ? 'Change Request' : 'Service Request'} Created!
          </h3>
          <p className="text-slate-500 text-sm mt-1">Your request has been submitted successfully</p>
        </div>
      </div>

      <div className="bg-slate-50 rounded-xl p-5 text-left space-y-3 border border-slate-200">
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold text-slate-500">Ticket Number</span>
          <span className="font-bold text-brand-600 text-lg">{ticket.number}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold text-slate-500">Status</span>
          <span className="badge-green">{state}</span>
        </div>
        <div className="flex items-start justify-between gap-4">
          <span className="text-sm font-semibold text-slate-500 flex-shrink-0">Description</span>
          <span className="text-sm text-slate-700 text-right">{ticket.short_description}</span>
        </div>
        {ticket.opened_at && (
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold text-slate-500">Opened</span>
            <span className="text-sm text-slate-600">{new Date(ticket.opened_at).toLocaleString()}</span>
          </div>
        )}
      </div>

      <div className="flex gap-3">
        <button
          className="btn-secondary flex-1 justify-center"
          onClick={handleViewAndChat}
        >
          <MessageSquare className="w-4 h-4" />
          View & Chat
        </button>
        <button className="btn-primary flex-1 justify-center" onClick={onNewRequest}>
          New Request
          <ArrowRight className="w-4 h-4" />
        </button>
      </div>

      {serviceNowUrl && (
        <a
          href={serviceNowUrl}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-2 text-sm font-semibold text-brand-700 hover:text-brand-800"
        >
          <ExternalLink className="w-4 h-4" />
          Open in ServiceNow
        </a>
      )}
    </div>
  )
}
