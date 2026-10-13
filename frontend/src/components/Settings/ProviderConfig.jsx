import { useEffect, useState } from 'react'
import { Send, Check, AlertCircle, Loader, Edit2 } from 'lucide-react'
import toast from 'react-hot-toast'

const API_URL = import.meta.env.VITE_API_URL || ''

export default function ProviderConfig() {
  const [step, setStep] = useState('initial') // initial, form, review, edit, edit-review
  const [providers, setProviders] = useState({})
  const [currentProvider, setCurrentProvider] = useState(null)
  const [selectedProvider, setSelectedProvider] = useState(null)
  const [questions, setQuestions] = useState([])
  const [formData, setFormData] = useState({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [isEditing, setIsEditing] = useState(false)

  useEffect(() => {
    loadProviders()
  }, [])

  const loadProviders = async () => {
    try {
      const res = await fetch(`${API_URL}/api/settings/providers`)
      const data = await res.json()
      setProviders(data.providers)
      setCurrentProvider(data.current)
    } catch (e) {
      setError('Failed to load providers')
      console.error(e)
    }
  }

  const selectProvider = async (providerKey) => {
    setSelectedProvider(providerKey)
    try {
      const res = await fetch(`${API_URL}/api/settings/providers/${providerKey}`)
      const template = await res.json()
      setQuestions(template.questions || [])
      setFormData({})
      setError('')
      setSuccess('')
      setStep('form')
    } catch (e) {
      setError(`Failed to load ${providerKey} configuration`)
      console.error(e)
    }
  }

  const editCurrentProvider = async () => {
    if (!currentProvider) return
    setSelectedProvider(currentProvider)
    setIsEditing(true)
    try {
      const res = await fetch(`${API_URL}/api/settings/providers/${currentProvider}`)
      const template = await res.json()
      setQuestions(template.questions || [])

      const settingsRes = await fetch(`${API_URL}/api/settings/current`)
      const settings = await settingsRes.json()
      setFormData({ ...settings.settings })
      setError('')
      setSuccess('')
      setStep('edit')
    } catch (e) {
      setError('Failed to load provider settings')
      console.error(e)
    }
  }

  const handleInputChange = (id, value) => {
    setFormData(prev => ({ ...prev, [id]: value }))
  }

  const isFormValid = () => {
    return questions.every(q => !q.required || formData[q.id]?.trim())
  }

  const saveConfiguration = async () => {
    if (!selectedProvider || !isFormValid()) return

    setLoading(true)
    try {
      const res = await fetch(`${API_URL}/api/settings/configure`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: selectedProvider,
          settings: formData
        })
      })

      const data = await res.json()

      if (!res.ok) {
        setError(data.detail || 'Failed to save configuration')
        return
      }

      setSuccess(`Provider '${providers[selectedProvider]?.name}' configured successfully!`)
      setCurrentProvider(selectedProvider)
      setStep('initial')
      toast.success('Configuration saved!')
      setIsEditing(false)

      setTimeout(() => {
        setSuccess('')
        resetForm()
      }, 2000)
    } catch (e) {
      setError('Failed to save configuration')
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  const resetForm = () => {
    setStep('initial')
    setSelectedProvider(null)
    setFormData({})
    setError('')
    setSuccess('')
    setIsEditing(false)
  }

  const getProviderIcon = (icon) => {
    const icons = { servicenow: '☁️', jira: '⚙️', freshdesk: '💬', zendesk: '🎯' }
    return icons[icon] || '📦'
  }

  return (
    <div className="flex flex-col h-full bg-white rounded-lg shadow-lg overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 bg-gradient-to-r from-blue-600 to-blue-700 text-white">
        <h2 className="text-lg font-semibold">Provider Configuration</h2>
        <p className="text-sm opacity-90">Configure your catalog provider</p>
      </div>

      {/* Current Provider */}
      {currentProvider && (
        <div
          onClick={editCurrentProvider}
          className="px-6 py-3 bg-blue-50 border-b border-blue-200 cursor-pointer hover:bg-blue-100 transition-colors"
        >
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm">
                <span className="font-semibold text-slate-700">Current Provider: </span>
                <span className="text-blue-600 font-medium">{providers[currentProvider]?.name}</span>
              </p>
            </div>
            <Edit2 className="w-4 h-4 text-blue-600 opacity-70" />
          </div>
          <p className="text-xs text-blue-600 mt-1">Click to edit configuration</p>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        {/* Initial Step */}
        {step === 'initial' && (
          <div>
            <p className="text-slate-600 mb-4">Select a catalog provider to configure</p>
            <div className="grid grid-cols-2 gap-3">
              {Object.entries(providers).map(([key, provider]) => (
                <button
                  key={key}
                  onClick={() => selectProvider(key)}
                  className="p-4 border-2 rounded-lg transition-all text-left hover:border-blue-300 hover:bg-slate-50 border-slate-200"
                >
                  <div className="text-2xl mb-2">{getProviderIcon(provider.icon)}</div>
                  <p className="font-semibold text-slate-800 text-sm">{provider.name}</p>
                  <p className="text-xs text-slate-500 mt-1">{provider.description}</p>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Form Step */}
        {(step === 'form' || step === 'edit') && (
          <div>
            <p className="text-slate-700 font-medium mb-4">
              {step === 'edit' ? 'Edit' : 'Configure'} {providers[selectedProvider]?.name}
            </p>
            {questions.map(q => (
              <div key={q.id} className="mb-4">
                <label className="block text-sm font-medium text-slate-700 mb-2">
                  {q.label}
                  {q.required && <span className="text-red-500 ml-1">*</span>}
                </label>
                <input
                  type={q.type}
                  placeholder={q.placeholder}
                  value={formData[q.id] || ''}
                  onChange={(e) => handleInputChange(q.id, e.target.value)}
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                {q.help && <p className="text-xs text-slate-500 mt-1">{q.help}</p>}
              </div>
            ))}
          </div>
        )}

        {/* Review Step */}
        {(step === 'review' || step === 'edit-review') && (
          <div>
            <p className="text-slate-700 font-medium mb-4">Review your configuration:</p>
            <div className="bg-slate-50 rounded-lg p-4 space-y-3">
              <div className="flex justify-between">
                <span className="font-medium text-slate-700">Provider:</span>
                <span className="text-blue-600">{providers[selectedProvider]?.name}</span>
              </div>
              {questions.map(q => (
                <div key={q.id} className="flex justify-between">
                  <span className="font-medium text-slate-700">{q.label}:</span>
                  <span className="text-slate-600">
                    {q.type === 'password' ? '••••••••' : formData[q.id]}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="flex items-start gap-3 p-4 bg-red-50 border border-red-200 rounded-lg">
            <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-medium text-red-800">Error</p>
              <p className="text-sm text-red-700">{error}</p>
            </div>
          </div>
        )}

        {/* Success */}
        {success && (
          <div className="flex items-start gap-3 p-4 bg-green-50 border border-green-200 rounded-lg">
            <Check className="w-5 h-5 text-green-600 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-medium text-green-800">Success</p>
              <p className="text-sm text-green-700">{success}</p>
            </div>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="px-6 py-4 border-t border-slate-200 bg-slate-50 flex justify-end gap-3">
        {(step === 'form' || step === 'edit') && (
          <>
            <button
              onClick={() => setStep('initial')}
              className="px-4 py-2 text-slate-700 hover:bg-slate-200 rounded-lg transition-colors"
            >
              Back
            </button>
            <button
              onClick={() => setStep(step === 'edit' ? 'edit-review' : 'review')}
              disabled={!isFormValid()}
              className={`px-4 py-2 rounded-lg font-medium transition-colors ${
                isFormValid()
                  ? 'bg-blue-600 text-white hover:bg-blue-700'
                  : 'bg-slate-300 text-slate-500 cursor-not-allowed'
              }`}
            >
              {step === 'edit' ? 'Review Changes' : 'Review Configuration'}
            </button>
          </>
        )}

        {(step === 'review' || step === 'edit-review') && (
          <>
            <button
              onClick={() => setStep(step === 'edit-review' ? 'edit' : 'form')}
              className="px-4 py-2 text-slate-700 hover:bg-slate-200 rounded-lg transition-colors"
            >
              Back
            </button>
            <button
              onClick={saveConfiguration}
              disabled={loading}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors flex items-center gap-2"
            >
              {loading ? <Loader className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
              {loading ? 'Saving...' : 'Save Configuration'}
            </button>
          </>
        )}

        {step === 'initial' && selectedProvider && (
          <button
            onClick={() => setStep('form')}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
          >
            Continue
          </button>
        )}
      </div>
    </div>
  )
}
