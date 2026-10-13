import { useState, useRef, useEffect } from 'react'
import { Send, Sparkles, X, ChevronRight, RotateCcw } from 'lucide-react'
import toast from 'react-hot-toast'
import MessageBubble from '../components/Chat/MessageBubble'
import TypingIndicator from '../components/Chat/TypingIndicator'
import CatalogList from '../components/Catalog/CatalogList'
import DynamicCatalogForm from '../components/Forms/DynamicCatalogForm'
import PrefillForm from '../components/Forms/PrefillForm'
import TicketConfirmation from '../components/Forms/TicketConfirmation'
import Header from '../components/Layout/Header'
import { useStore } from '../store/useStore'
import { interpretRequest, createConversation, createSR, createCH } from '../api/client'
import { setLastViewChatTarget } from '../utils/viewChatSession'

const STEPS = { CHAT: 'chat', CATALOG: 'catalog', FORM: 'form', DONE: 'done' }

const SUGGESTED_PROMPTS = [
  'I need a new MacBook laptop',
  'Request VPN access for remote work',
  'My software license needs renewal',
  'Need to upgrade the production database server',
  'Request access to SharePoint site',
  'New employee hardware setup',
]

export default function RequestPortal() {
  const { sessionId, setSessionId, currentUser, currentUserEmail, setCurrentUserEmail, chatMessages, addChatMessage, setChatMessages, clearChat, setLastTicket } = useStore()
  const [input, setInput] = useState('')
  const [step, setStep] = useState(STEPS.CHAT)
  const [loading, setLoading] = useState(false)
  const [interpretResult, setInterpretResult] = useState(null)
  const [selectedCatalog, setSelectedCatalog] = useState(null)
  const [createdTicket, setCreatedTicket] = useState(null)
  const [showEmailModal, setShowEmailModal] = useState(!currentUserEmail)
  const [emailInput, setEmailInput] = useState(currentUserEmail || '')
  const [showCatalogPicker, setShowCatalogPicker] = useState(false)
  const endRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
  }, [step])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chatMessages, loading])

  const initSession = async (email) => {
    try {
      const session = await createConversation(email, email)
      setSessionId(session.session_id)
      return session.session_id
    } catch (err) {
      console.error('[initSession] Failed to create conversation:', err.message, err.response?.status)
      return null
    }
  }

  const handleEmailSubmit = async (e) => {
    e.preventDefault()
    if (!emailInput.trim()) return
    setCurrentUserEmail(emailInput.trim())
    await initSession(emailInput.trim())
    setShowEmailModal(false)
    inputRef.current?.focus()
  }

  const pushMsg = (role, content) => {
    addChatMessage({ role, content, timestamp: new Date().toISOString() })
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    const text = input.trim()
    if (!text || loading) return
    setInput('')
    pushMsg('user', text)
    setLoading(true)

    try {
      let sid = sessionId
      if (!sid) {
        sid = await initSession(currentUserEmail || 'guest@company.com')
      }
      const result = await interpretRequest(text, sid, currentUserEmail)
      setInterpretResult(result)
      const suggestedCount = result.suggested_catalogs?.length || 0
      const shouldChooseFromOptions = suggestedCount > 1

      if (result.clarification_needed && suggestedCount > 0) {
        const msg = result.clarification_question
          ? `${result.clarification_question}\n\nPlease select the closest catalog item below to continue.`
          : 'Multiple matching catalog items found. Please select one:'
        pushMsg('admin', msg)
        setStep(STEPS.CATALOG)
        setShowCatalogPicker(true)
      } else if (suggestedCount === 1) {
        const catalog = result.suggested_catalogs[0]
        handleCatalogSelect(catalog)
      } else {
        pushMsg('admin', 'No matching service catalogs found. Please try a different request.')
      }
    } catch (err) {
      console.error('Request interpretation failed:', err)
      pushMsg('admin', 'Failed to process request. Please try again.')
    }

    setLoading(false)
  }

  const handleCatalogSelect = (catalog) => {
    setSelectedCatalog(catalog)
    setStep(STEPS.FORM)
    setShowCatalogPicker(false)
  }

  const handleFormSubmit = async (formData) => {
    setLoading(true)
    try {
      const ticketType = interpretResult?.ticket_type || 'SR'
      let response

      if (ticketType === 'CH') {
        response = await createCH({
          short_description: formData.short_description,
          description: formData.description || formData.short_description,
          risk: formData.risk || 'low',
          impact: formData.impact || '3',
          change_type: formData.type || 'normal',
          start_date: formData.start_date,
          end_date: formData.end_date,
          requested_by: formData.requested_by,
          assignment_group: formData.assignment_group,
        })
      } else {
        response = await createSR({
          catalog_item_id: selectedCatalog.sys_id,
          requested_for: currentUser?.sys_id || currentUserEmail,
          variables: formData.variables || {},
          short_description: formData.short_description,
          description: formData.description || formData.short_description,
          urgency: formData.urgency || '3',
          impact: formData.impact || '3',
        })
      }

      setLastTicket(response)
      setLastViewChatTarget({
        ticketType: response.ticket_type,
        sysId: response.sys_id,
        sessionId: sessionId || '',
        number: response.number,
      })
      setCreatedTicket(response)
      setStep(STEPS.DONE)
      pushMsg('admin', `✓ Request created: ${response.number}`)
    } catch (err) {
      console.error('Form submission failed:', err)
      toast.error(`Failed to create ${interpretResult?.ticket_type === 'CH' ? 'change request' : 'service request'}`)
    }

    setLoading(false)
  }

  const handleReset = () => {
    clearChat()
    setStep(STEPS.CHAT)
    setInterpretResult(null)
    setSelectedCatalog(null)
    setCreatedTicket(null)
    setShowCatalogPicker(false)
    inputRef.current?.focus()
  }

  const openChangeCatalogPicker = () => {
    setShowCatalogPicker(true)
  }

  return (
    <div className="flex flex-col h-screen bg-white">
      {/* Email Modal */}
      {showEmailModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-2xl p-6 max-w-md w-full mx-4 shadow-lg">
            <h2 className="text-xl font-bold text-slate-800 mb-4">What's your email?</h2>
            <form onSubmit={handleEmailSubmit} className="space-y-4">
              <input
                type="email"
                className="input-field"
                placeholder="name@company.com"
                value={emailInput}
                onChange={(e) => setEmailInput(e.target.value)}
                autoFocus
              />
              <button type="submit" className="btn-primary w-full justify-center">
                Continue
              </button>
            </form>
          </div>
        </div>
      )}

      <Header title="Request Portal" />

      {/* Main content area */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Chat area (always visible, becomes background when modal is shown) */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Messages */}
          <div className="flex-1 overflow-y-auto p-4">
            {chatMessages.length === 0 && (
              <div className="flex flex-col items-center justify-center h-full text-center">
                <Sparkles className="w-12 h-12 text-brand-300 mb-3" />
                <h1 className="text-2xl font-bold text-slate-800 mb-2">Request Portal</h1>
                <p className="text-slate-500 mb-6">Describe what you need and we'll help you find the right request</p>
                <div className="grid grid-cols-2 gap-2">
                  {SUGGESTED_PROMPTS.map((p) => (
                    <button
                      key={p}
                      onClick={() => { setInput(p); inputRef.current?.focus() }}
                      className="text-left px-3 py-2.5 text-xs bg-white border border-slate-200 rounded-xl hover:border-brand-300 hover:bg-brand-50 transition-all text-slate-600 hover:text-brand-700"
                    >
                      {p}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {chatMessages.map((m, i) => <MessageBubble key={i} message={m} />)}
            {loading && <TypingIndicator />}
            <div ref={endRef} />
          </div>

          {/* Input */}
          {(step === STEPS.CHAT || interpretResult?.clarification_needed) && (
            <div className="border-t border-slate-200 bg-white px-4 py-3">
              <form onSubmit={handleSubmit} className="flex items-end gap-2">
                <div className="flex-1 relative">
                  <textarea
                    ref={inputRef}
                    className="input-field resize-none pr-12 min-h-[44px] max-h-32"
                    placeholder={interpretResult?.clarification_needed ? "Answer the question above..." : "Describe your request... (e.g., I need a new laptop)"}
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSubmit(e) } }}
                    rows={1}
                  />
                </div>
                <button type="submit" className="btn-primary px-3 py-3" disabled={loading || !input.trim()}>
                  <Send className="w-4 h-4" />
                </button>
              </form>
            </div>
          )}

          {step !== STEPS.CHAT && step !== STEPS.DONE && (
            <div className="border-t border-slate-200 bg-white px-4 py-2 flex justify-between items-center">
              <p className="text-xs text-slate-400">
                Step {step === STEPS.CATALOG ? '2' : '3'} of 3
              </p>
              <button className="btn-ghost text-xs gap-1" onClick={handleReset}>
                <RotateCcw className="w-3 h-3" /> Start Over
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Modal Overlay */}
      {(showCatalogPicker || (step === STEPS.FORM && selectedCatalog) || (step === STEPS.DONE && createdTicket)) && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-40 p-4">
          {/* Modal Container */}
          <div className="bg-white rounded-2xl max-w-2xl w-full max-h-[90vh] flex flex-col shadow-xl overflow-hidden">
            {/* Modal Header */}
            <div className="border-b border-slate-200 bg-slate-50 px-6 py-4 flex justify-between items-center">
              {showCatalogPicker ? (
                <h2 className="font-semibold text-slate-800">Select Catalog Item</h2>
              ) : step === STEPS.FORM ? (
                <h2 className="font-semibold text-slate-800">Review & Submit</h2>
              ) : (
                <h2 className="font-semibold text-slate-800">Request Created</h2>
              )}
              <button
                onClick={handleReset}
                className="p-1 hover:bg-slate-200 rounded transition-colors"
              >
                <X className="w-5 h-5 text-slate-500" />
              </button>
            </div>

            {/* Modal Content */}
            <div className="flex-1 overflow-y-auto p-6">
              {showCatalogPicker && (
                <div className="space-y-4">
                  <p className="text-sm text-slate-600">
                    {interpretResult?.clarification_needed
                      ? "Or select one of these options instead"
                      : "AI-ranked results based on your request"}
                  </p>
                  <CatalogList
                    items={interpretResult?.suggested_catalogs || []}
                    selected={selectedCatalog}
                    onSelect={handleCatalogSelect}
                  />
                </div>
              )}

              {step === STEPS.FORM && selectedCatalog && !showCatalogPicker && (
                <div className="space-y-6">
                  {/* Selected Product Strip */}
                  <div className="flex items-center gap-3 p-3 bg-brand-50 border border-brand-200 rounded-lg">
                    <div className="flex-1">
                      <p className="text-sm font-medium text-slate-800">{selectedCatalog.name}</p>
                      <p className="text-xs text-slate-500">{selectedCatalog.short_description}</p>
                    </div>
                    <button
                      onClick={openChangeCatalogPicker}
                      className="btn-ghost text-sm gap-1 flex-shrink-0"
                    >
                      Change
                      <ChevronRight className="w-3 h-3" />
                    </button>
                  </div>

                  {/* Form */}
                  {selectedCatalog ? (
                    <DynamicCatalogForm
                      catalogItem={selectedCatalog}
                      description={chatMessages.find(m => m.role === 'user')?.content || ''}
                      interpretResult={interpretResult}
                      onSubmit={handleFormSubmit}
                    />
                  ) : (
                    <PrefillForm
                      ticketType={interpretResult?.ticket_type || 'SR'}
                      description={chatMessages.find(m => m.role === 'user')?.content || ''}
                      onSubmit={handleFormSubmit}
                    />
                  )}
                </div>
              )}

              {step === STEPS.DONE && createdTicket && !showCatalogPicker && (
                <TicketConfirmation
                  ticket={createdTicket}
                  sessionId={sessionId}
                  onNewRequest={handleReset}
                />
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
