import { useEffect, useRef, useState } from 'react'
import { AlertCircle, Building2, Briefcase, Loader2, MapPin, RefreshCw, User } from 'lucide-react'
import toast from 'react-hot-toast'
import { getCatalogForm, getCatalogFormVersion, getMe, prefillCatalogForm } from '../../api/client'
import { useStore } from '../../store/useStore'

const FORM_SYNC_INTERVAL_MS = 15000

export default function DynamicCatalogForm({ catalogItem, description = '', interpretResult = null, onSubmit }) {
  const { currentUserEmail, currentUser, setCurrentUser } = useStore()
  const [catalogForm, setCatalogForm] = useState(null)
  const [values, setValues] = useState({})
  const [retiredFields, setRetiredFields] = useState([])
  const [notes, setNotes] = useState('')
  const [urgency, setUrgency] = useState('3')
  const [loading, setLoading] = useState(true)
  const [loadingSlow, setLoadingSlow] = useState(false)
  const [prefilling, setPrefilling] = useState(false)
  const [syncMessage, setSyncMessage] = useState('')
  const catalogFormRef = useRef(null)
  const valuesRef = useRef({})
  const retiredFieldsRef = useRef([])
  const versionRef = useRef('')
  const editedFieldsRef = useRef(new Set())

  useEffect(() => {
    catalogFormRef.current = catalogForm
  }, [catalogForm])

  useEffect(() => {
    valuesRef.current = values
  }, [values])

  useEffect(() => {
    retiredFieldsRef.current = retiredFields
  }, [retiredFields])

  useEffect(() => {
    if (!syncMessage) return undefined
    const timeoutId = window.setTimeout(() => setSyncMessage(''), 4000)
    return () => window.clearTimeout(timeoutId)
  }, [syncMessage])

  useEffect(() => {
    if (!catalogItem?.sys_id) {
      return undefined
    }

    let cancelled = false
    setLoading(true)
    setLoadingSlow(false)
    setCatalogForm(null)
    setValues({})
    setRetiredFields([])
    setSyncMessage('')
    versionRef.current = ''
    editedFieldsRef.current = new Set()
    const slowTimer = window.setTimeout(() => {
      if (!cancelled) setLoadingSlow(true)
    }, 8000)

    const loadInitialForm = async () => {
      try {
        const data = await getCatalogForm(catalogItem.sys_id)
        if (!cancelled) {
          // Log all variables and their conditional metadata
          const allVars = data?.variables || []
          const depsVars = allVars.filter(v => v.depends_on)
          allVars.forEach(v => {
            const defaultVal = v.type === 'checkbox' ? (v.default_value === 'true') : (v.default_value || '')
            const marker = v.depends_on ? '↳' : '•'
          })
          applyFormUpdate(data, { announce: false })

          if (description && data?.variables?.length > 0) {
            try {
              const intentContext = interpretResult ? {
                intent: interpretResult.intent,
                entities: interpretResult.entities || []
              } : null
              const result = await prefillCatalogForm(description, data.variables, catalogItem, intentContext)
              if (!cancelled) {
                applyPrefillValues(data.variables, result?.values || {})
              }
            } catch (err) {
              if (!cancelled) {
                setSyncMessage('Could not auto-fill from your request — please complete the form manually.')
              }
            }
          }

          try {
            const version = await getCatalogFormVersion(catalogItem.sys_id, {
              silent: true,
              params: { _ts: Date.now() },
            })
            if (!cancelled) {
              versionRef.current = buildVersionSignature(version)
            }
          } catch {
            versionRef.current = buildFormSignature(data)
          }
        }
      } catch (err) {
        if (!cancelled) {
          const msg = err?.response?.data?.detail || err?.message || 'Unable to load catalog form'
          toast.error(msg)
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    const syncFormChanges = async () => {
      try {
        const version = await getCatalogFormVersion(catalogItem.sys_id, {
          silent: true,
          params: { _ts: Date.now() },
        })
        const nextVersion = buildVersionSignature(version)
        if (cancelled || !nextVersion || nextVersion === versionRef.current) {
          return
        }
        const data = await getCatalogForm(catalogItem.sys_id, {
          silent: true,
          params: { _ts: Date.now() },
        })
        if (cancelled || !hasFormChanged(catalogFormRef.current, data)) {
          versionRef.current = nextVersion
          return
        }
        versionRef.current = nextVersion
        applyFormUpdate(data, { announce: true })
      } catch {
        // Avoid interrupting the user for transient background sync failures.
      }
    }

    void loadInitialForm()
    const intervalId = window.setInterval(() => {
      void syncFormChanges()
    }, FORM_SYNC_INTERVAL_MS)

    return () => {
      cancelled = true
      window.clearInterval(intervalId)
      window.clearTimeout(slowTimer)
    }
  }, [catalogItem?.sys_id])

  useEffect(() => {
    if (currentUserEmail && !currentUser) {
      setPrefilling(true)
      getMe(currentUserEmail)
        .then(setCurrentUser)
        .catch(() => { /* Profile is optional for manual submission. */ })
        .finally(() => setPrefilling(false))
    }
  }, [currentUserEmail])

  const applyPrefillValues = (variables, prefillMap) => {
    if (!prefillMap || Object.keys(prefillMap).length === 0) return

    const fieldsToApply = []
    for (const variable of variables) {
      const key = getVariableKey(variable)
      const lookupKeys = [variable.sys_id, variable.name].filter(Boolean)
      const matchedKey = lookupKeys.find((k) => k in prefillMap)
      if (!matchedKey) continue
      const proposed = prefillMap[matchedKey]
      if (proposed === undefined || proposed === null) continue
      const defaultVal = defaultValueForVariable(variable)
      const defaultIsEmpty =
        defaultVal === '' || defaultVal === undefined || defaultVal === null || defaultVal === false
      if (!defaultIsEmpty) continue
      // Don't clobber a value the user already typed while prefill was in flight.
      if (editedFieldsRef.current.has(key)) continue
      const currentVal = valuesRef.current[key]
      const currentIsEmpty =
        currentVal === '' || currentVal === undefined || currentVal === null || currentVal === false
      if (!currentIsEmpty) continue
      fieldsToApply.push({ key, value: proposed })
    }

    if (fieldsToApply.length === 0) return

    setValues((current) => {
      const next = { ...current }
      for (const { key, value } of fieldsToApply) {
        next[key] = value
      }
      return next
    })
    setSyncMessage(
      `AI prefilled ${fieldsToApply.length} field${fieldsToApply.length === 1 ? '' : 's'} from your request.`
    )
  }

  const applyFormUpdate = (nextForm, { announce }) => {
    const previousForm = catalogFormRef.current
    const previousValues = valuesRef.current
    const previousRetiredFields = retiredFieldsRef.current
    const nextValues = reconcileValues(previousForm, nextForm, previousValues, editedFieldsRef.current)
    const nextRetiredFields = reconcileRetiredFields(
      previousForm,
      nextForm,
      previousValues,
      previousRetiredFields
    )

    setCatalogForm(nextForm)
    setValues(nextValues)
    setRetiredFields(nextRetiredFields)

    if (announce && previousForm) {
      const message = buildSyncMessage(previousForm, nextForm, previousRetiredFields, nextRetiredFields)
      if (message) {
        setSyncMessage(message)
      }
    }
  }

  const isFieldVisible = (variable) => {
    try {
      if (!variable?.depends_on) return true

      const parentVariable = (catalogForm?.variables || []).find(
        (v) => v?.sys_id === variable.depends_on || v?.name === variable.depends_on
      )
      if (!parentVariable) {
        return true
      }

      const parentKey = parentVariable.sys_id || parentVariable.name
      const parentValue = String(values[parentKey] || '').trim()
      const activeOn = String(variable.active_on_value || '').trim()
      const inactiveOn = String(variable.inactive_on_value || '').trim()

      const parentValueLower = parentValue.toLowerCase()
      const activeOnLower = activeOn.toLowerCase()
      const inactiveOnLower = inactiveOn.toLowerCase()


      if (activeOnLower) return parentValueLower === activeOnLower
      if (inactiveOnLower) return parentValueLower !== inactiveOnLower
      return true
    } catch (error) {
      return true
    }
  }

  const handleSubmit = (e) => {
    e.preventDefault()
    // Filter out section headers: type='label', no name, or name contains 'label'
    const isSectionHeader = (v) =>
      v.type === 'label' ||
      !v.name ||
      (v.name && v.name.toLowerCase().includes('label'))
    const submitVariables = (catalogForm?.variables || []).filter(v => !isSectionHeader(v))
    const visibleVariables = submitVariables.filter(isFieldVisible)
    const missing = visibleVariables.filter(
      (variable) => variable.mandatory && !String(values[getVariableKey(variable)] || '').trim()
    )
    if (missing.length > 0) {
      toast.error(`Please complete: ${missing.map((variable) => variable.label).join(', ')}`)
      return
    }

    const submittedVariables = Object.fromEntries(
      visibleVariables.map((variable) => {
        const key = getVariableKey(variable)
        const value = values[key]
        return [variable.name, value]
      })
    )

    onSubmit({
      short_description: `${catalogItem?.name || 'Service Request'} - ${description || catalogItem?.short_description || ''}`.trim(),
      urgency,
      additional_notes: notes,
      variables: submittedVariables,
      user: currentUser,
    })
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-slate-500 py-6">
        <Loader2 className="w-4 h-4 animate-spin" />
        {loadingSlow
          ? 'Still working — this can take up to 20 seconds for new catalogs.'
          : 'Loading catalog form...'}
      </div>
    )
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col h-full">
      {/* Scrollable Content */}
      <div className="flex-1 overflow-y-auto space-y-4 pb-32">
        {prefilling && (
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <Loader2 className="w-4 h-4 animate-spin" />
            Fetching your profile from ServiceNow...
          </div>
        )}

        {syncMessage && (
          <div className="flex gap-2 rounded-lg border border-sky-200 bg-sky-50 p-3 text-xs text-sky-700">
            <RefreshCw className="w-4 h-4 flex-shrink-0" />
            {syncMessage}
          </div>
        )}

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

        {catalogForm?.variables?.length === 0 && (
          <div className="flex gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-700">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            This catalog item does not expose additional form fields. You can still submit the request with notes.
          </div>
        )}

        {/* Catalog Details Card */}
        {catalogForm?.variables && catalogForm.variables.length > 0 && (
          <div className="bg-white border border-slate-200 rounded-lg p-4 space-y-4">
            <h3 className="font-semibold text-slate-800 text-sm">Catalog Details</h3>
            {(() => {
              const allVars = catalogForm?.variables || []
              // Check visibility for all variables (including section headers)
              const visibleVars = allVars.filter(isFieldVisible)
              if (allVars.length > visibleVars.length) {
                const hiddenVars = allVars.filter(v => !isFieldVisible(v))
              }
              return visibleVars
            })().map((variable) => (
              <VariableField
                key={getVariableKey(variable)}
                variable={variable}
                value={values[getVariableKey(variable)]}
                onChange={(value) => {
                  const key = getVariableKey(variable)
                  editedFieldsRef.current.add(key)
                  setValues((current) => ({ ...current, [key]: value }))
                }}
              />
            ))}
          </div>
        )}

        {retiredFields.length > 0 && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 space-y-2">
            <div className="flex gap-2 text-xs text-amber-700">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              Some fields were removed from the latest catalog version. Your entered data is preserved below.
            </div>
            <div className="space-y-2">
              {retiredFields.map((field) => (
                <RetiredField key={field.key} field={field} />
              ))}
            </div>
          </div>
        )}

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">Urgency</label>
          <select className="input-field" value={urgency} onChange={(e) => setUrgency(e.target.value)}>
            <option value="1">Critical</option>
            <option value="2">High</option>
            <option value="3">Medium</option>
            <option value="4">Low</option>
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">Additional Notes</label>
          <textarea
            className="input-field resize-none"
            rows={2}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Any additional information..."
          />
        </div>
      </div>

      {/* Sticky Footer */}
      <div className="sticky bottom-0 left-0 right-0 bg-white border-t border-slate-200 p-4 space-y-2">
        <button type="submit" className="btn-primary w-full justify-center">
          Submit Service Request
        </button>
        <p className="text-xs text-slate-400 text-center">
          {catalogForm?.variables?.length === 0 ? 'Submit with notes' : 'Complete required fields to submit'}
        </p>
      </div>
    </form>
  )
}

function VariableField({ variable, value, onChange }) {
  // Section headers/labels are identified by:
  // 1. type === 'label'
  // 2. name contains 'label' (e.g., 'optional_label')
  // 3. no name at all
  const isSectionHeader =
    variable.type === 'label' ||
    !variable.name ||
    (variable.name && variable.name.toLowerCase().includes('label'))

  if (isSectionHeader) {
    return (
      <div className="pt-2 pb-1">
        <p className="text-sm font-semibold text-slate-700 border-b border-slate-200 pb-2">
          {variable.label}
        </p>
      </div>
    )
  }

  return (
    <div>
      <label className="block text-sm font-medium text-slate-700 mb-1">
        {variable.label}{variable.mandatory ? ' *' : ''}
      </label>
      {renderInput(variable, value, onChange)}
    </div>
  )
}

function renderInput(variable, value, onChange) {
  if (variable.type === 'textarea') {
    return <textarea className="input-field resize-none" rows={3} value={value || ''} onChange={(e) => onChange(e.target.value)} />
  }

  if (variable.type === 'select' || variable.type === 'radio') {
    const choices = variable.choices || []
    const hasCurrentValue = !value || choices.some((choice) => choice.value === value)
    const resolvedChoices = hasCurrentValue
      ? choices
      : [{ label: `${value} (no longer in catalog)`, value }, ...choices]

    return (
      <select className="input-field" value={value || ''} onChange={(e) => onChange(e.target.value)}>
        <option value="">Select...</option>
        {resolvedChoices.map((choice) => (
          <option key={`${choice.value}-${choice.label}`} value={choice.value}>{choice.label}</option>
        ))}
      </select>
    )
  }

  if (variable.type === 'checkbox') {
    return (
      <label className="inline-flex items-center gap-2 text-sm text-slate-600">
        <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
        Yes
      </label>
    )
  }

  const inputType = variable.type === 'date' ? 'date' : variable.type === 'datetime' ? 'datetime-local' : 'text'
  return <input className="input-field" type={inputType} value={value || ''} onChange={(e) => onChange(e.target.value)} />
}

function RetiredField({ field }) {
  return (
    <div>
      <p className="block text-sm font-medium text-slate-700 mb-1">{field.label}</p>
      <input className="input-field bg-slate-100 text-slate-500" value={formatRetiredValue(field.value)} disabled readOnly />
    </div>
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

function getVariableKey(variable) {
  return variable?.sys_id || variable?.name || ''
}

function defaultValueForVariable(variable) {
  if (variable.type === 'checkbox') {
    return variable.default_value === 'true'
  }
  return variable.default_value || ''
}

function reconcileValues(previousForm, nextForm, previousValues, editedKeys = new Set()) {
  const previousVariablesById = new Map(
    (previousForm?.variables || [])
      .filter((variable) => variable.sys_id)
      .map((variable) => [variable.sys_id, variable])
  )
  const nextValues = {}

  for (const variable of nextForm?.variables || []) {
    const nextKey = getVariableKey(variable)
    const previousVariable = variable.sys_id ? previousVariablesById.get(variable.sys_id) : null
    const candidateKeys = [nextKey]

    if (previousVariable) {
      candidateKeys.push(getVariableKey(previousVariable))
      candidateKeys.push(previousVariable.name)
    }

    candidateKeys.push(variable.name)

    // If the user has edited this field (or its previous-form alias), preserve their value
    // verbatim — never reapply schema defaults on a 15s sync.
    const editedKey = candidateKeys.find((key) => editedKeys.has(key))
    if (editedKey && Object.prototype.hasOwnProperty.call(previousValues, editedKey)) {
      nextValues[nextKey] = previousValues[editedKey]
      continue
    }

    const preservedValue = candidateKeys.find((key) => Object.prototype.hasOwnProperty.call(previousValues, key))
    nextValues[nextKey] = preservedValue ? previousValues[preservedValue] : defaultValueForVariable(variable)
  }

  return nextValues
}

function reconcileRetiredFields(previousForm, nextForm, previousValues, existingRetiredFields) {
  const activeIds = new Set((nextForm?.variables || []).map((variable) => variable.sys_id).filter(Boolean))
  const activeNames = new Set((nextForm?.variables || []).map((variable) => variable.name).filter(Boolean))
  const retired = []

  for (const variable of previousForm?.variables || []) {
    const value = previousValues[getVariableKey(variable)]
    if (!hasUserValue(value)) {
      continue
    }
    if ((variable.sys_id && activeIds.has(variable.sys_id)) || activeNames.has(variable.name)) {
      continue
    }
    retired.push({
      key: getVariableKey(variable),
      label: variable.label,
      value,
    })
  }

  for (const field of existingRetiredFields || []) {
    if (!retired.some((entry) => entry.key === field.key)) {
      retired.push(field)
    }
  }

  return retired
}

function hasFormChanged(previousForm, nextForm) {
  return buildFormSignature(previousForm) !== buildFormSignature(nextForm)
}

function buildFormSignature(form) {
  return JSON.stringify(
    (form?.variables || []).map((variable) => ({
      sys_id: variable.sys_id,
      name: variable.name,
      label: variable.label,
      type: variable.type,
      mandatory: variable.mandatory,
      default_value: variable.default_value,
      depends_on: variable.depends_on,
      inactive_on_value: variable.inactive_on_value,
      choices: (variable.choices || []).map((choice) => ({
        label: choice.label,
        value: choice.value,
      })),
    }))
  )
}

function buildSyncMessage(previousForm, nextForm, previousRetiredFields, nextRetiredFields) {
  const previousMap = new Map((previousForm?.variables || []).map((variable) => [getVariableKey(variable), variable]))
  let updatedCount = 0
  let addedCount = 0

  for (const variable of nextForm?.variables || []) {
    const previousVariable = previousMap.get(getVariableKey(variable))
    if (!previousVariable) {
      addedCount += 1
      continue
    }
    if (buildVariableSignature(previousVariable) !== buildVariableSignature(variable)) {
      updatedCount += 1
    }
  }

  const retiredCount = Math.max(0, nextRetiredFields.length - previousRetiredFields.length)
  const parts = []
  if (updatedCount > 0) {
    parts.push(`${updatedCount} field${updatedCount === 1 ? '' : 's'} updated`)
  }
  if (addedCount > 0) {
    parts.push(`${addedCount} new field${addedCount === 1 ? '' : 's'} added`)
  }
  if (retiredCount > 0) {
    parts.push(`${retiredCount} removed field${retiredCount === 1 ? '' : 's'} preserved`)
  }

  if (parts.length === 0) {
    return ''
  }
  return `Catalog updated: ${parts.join(', ')}.`
}

function buildVariableSignature(variable) {
  return JSON.stringify({
    name: variable.name,
    label: variable.label,
    type: variable.type,
    mandatory: variable.mandatory,
    default_value: variable.default_value,
    depends_on: variable.depends_on,
    inactive_on_value: variable.inactive_on_value,
    choices: (variable.choices || []).map((choice) => ({
      label: choice.label,
      value: choice.value,
    })),
  })
}

function buildVersionSignature(version) {
  if (!version) {
    return ''
  }
  return JSON.stringify({
    item_updated_at: version.item_updated_at || '',
    schema_key: version.schema_key || '',
    variable_count: version.variable_count || 0,
  })
}

function hasUserValue(value) {
  if (typeof value === 'boolean') {
    return value
  }
  return String(value || '').trim().length > 0
}

function formatRetiredValue(value) {
  if (typeof value === 'boolean') {
    return value ? 'Yes' : 'No'
  }
  return String(value || '')
}
