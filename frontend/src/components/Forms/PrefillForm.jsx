import { useEffect, useState } from 'react'
import { User, Building2, MapPin, Briefcase } from 'lucide-react'
import { getMe } from '../../api/client'
import { useStore } from '../../store/useStore'
import toast from 'react-hot-toast'

export default function PrefillForm({ onSubmit, ticketType = 'SR', description = '' }) {
  const { currentUserEmail, currentUser, setCurrentUser } = useStore()
  const [form, setForm] = useState({
    short_description: description,
    urgency: '3',
    risk: 'low',
    change_type: 'normal',
    start_date: '',
    end_date: '',
    additional_notes: '',
  })
  const [loading, setLoading] = useState(false)
  const [prefilling, setPrefilling] = useState(false)

  useEffect(() => {
    if (currentUserEmail && !currentUser) {
      setPrefilling(true)
      getMe(currentUserEmail)
        .then(setCurrentUser)
        .catch(() => { /* User not found in ServiceNow — form still usable manually */ })
        .finally(() => setPrefilling(false))
    }
  }, [currentUserEmail])

  const urgencyLabels = { '1': 'Critical', '2': 'High', '3': 'Medium', '4': 'Low' }

  const handleSubmit = (e) => {
    e.preventDefault()
    if (!form.short_description.trim()) {
      toast.error('Please enter a description')
      return
    }
    onSubmit({ ...form, user: currentUser })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {/* User Profile Preview */}
      {currentUser && (
        <div className="bg-slate-50 rounded-xl p-4 space-y-2">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">Auto-populated from ServiceNow</p>
          <div className="grid grid-cols-2 gap-3">
            <InfoRow icon={User} label="Name" value={currentUser.name} />
            <InfoRow icon={Building2} label="Department" value={currentUser.department} />
            <InfoRow icon={Briefcase} label="Manager" value={currentUser.manager} />
            <InfoRow icon={MapPin} label="Location" value={currentUser.location} />
          </div>
        </div>
      )}
      {prefilling && (
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <div className="w-4 h-4 border-2 border-brand-400 border-t-transparent rounded-full animate-spin" />
          Fetching your profile from ServiceNow...
        </div>
      )}

      {/* Description */}
      <div>
        <label className="block text-sm font-medium text-slate-700 mb-1">Request Description *</label>
        <textarea
          className="input-field resize-none"
          rows={3}
          value={form.short_description}
          onChange={(e) => setForm((f) => ({ ...f, short_description: e.target.value }))}
          placeholder="Describe what you need..."
        />
      </div>

      {ticketType === 'SR' ? (
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">Urgency</label>
          <select
            className="input-field"
            value={form.urgency}
            onChange={(e) => setForm((f) => ({ ...f, urgency: e.target.value }))}
          >
            {Object.entries(urgencyLabels).map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </select>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Risk Level</label>
            <select className="input-field" value={form.risk} onChange={(e) => setForm((f) => ({ ...f, risk: e.target.value }))}>
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
              <option value="critical">Critical</option>
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Change Type</label>
            <select className="input-field" value={form.change_type} onChange={(e) => setForm((f) => ({ ...f, change_type: e.target.value }))}>
              <option value="normal">Normal</option>
              <option value="standard">Standard</option>
              <option value="emergency">Emergency</option>
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Start Date</label>
            <input type="datetime-local" className="input-field" value={form.start_date}
              onChange={(e) => setForm((f) => ({ ...f, start_date: e.target.value }))} />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">End Date</label>
            <input type="datetime-local" className="input-field" value={form.end_date}
              onChange={(e) => setForm((f) => ({ ...f, end_date: e.target.value }))} />
          </div>
        </div>
      )}

      <div>
        <label className="block text-sm font-medium text-slate-700 mb-1">Additional Notes</label>
        <textarea
          className="input-field resize-none"
          rows={2}
          value={form.additional_notes}
          onChange={(e) => setForm((f) => ({ ...f, additional_notes: e.target.value }))}
          placeholder="Any additional information..."
        />
      </div>

      <button type="submit" className="btn-primary w-full justify-center" disabled={loading}>
        {loading ? 'Submitting...' : `Submit ${ticketType === 'CH' ? 'Change Request' : 'Service Request'}`}
      </button>
    </form>
  )
}

function InfoRow({ icon: Icon, label, value }) {
  if (!value) return null
  return (
    <div className="flex items-center gap-2">
      <Icon className="w-3.5 h-3.5 text-slate-400 flex-shrink-0" />
      <div className="min-w-0">
        <p className="text-[10px] text-slate-400 uppercase tracking-wide">{label}</p>
        <p className="text-xs font-medium text-slate-700 truncate">{value}</p>
      </div>
    </div>
  )
}