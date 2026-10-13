import { useState } from 'react'
import { Zap, Eye, EyeOff, Loader2 } from 'lucide-react'
import { login } from '../api/client'

export default function Login({ onSuccess }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [show, setShow] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const data = await login(username.trim(), password)
      onSuccess(data.access_token, data.user)
    } catch (err) {
      setError(err.status === 401 ? 'Incorrect username or password.' : err.message || 'Sign-in failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen grid lg:grid-cols-2 bg-white">
      <div className="hidden lg:flex flex-col justify-between p-12 bg-gradient-to-br from-brand-600 via-brand-700 to-brand-900 text-white">
        <div className="flex items-center gap-2.5">
          <div className="w-9 h-9 rounded-lg bg-white/15 flex items-center justify-center">
            <Zap className="w-5 h-5" />
          </div>
          <span className="font-semibold">Servicenowai</span>
        </div>
        <div>
          <h1 className="text-4xl font-semibold leading-tight">
            AI-assisted IT service
            <br />
            management, in one place.
          </h1>
          <p className="mt-4 text-brand-100 max-w-md">
            Describe a problem in plain language. The portal finds the right catalog item, pre-fills the form and
            files the ticket in ServiceNow, backed by GraphRAG over your support history.
          </p>
        </div>
        <p className="text-sm text-brand-200">Open-source stack: Ollama, Neo4j, FastAPI, React.</p>
      </div>

      <div className="flex items-center justify-center p-6">
        <form onSubmit={submit} className="w-full max-w-sm space-y-5">
          <div className="lg:hidden flex items-center gap-2.5 mb-2">
            <div className="w-9 h-9 rounded-lg bg-brand-600 flex items-center justify-center">
              <Zap className="w-5 h-5 text-white" />
            </div>
            <span className="font-semibold text-slate-900">Servicenowai</span>
          </div>
          <div>
            <h2 className="text-2xl font-semibold text-slate-900">Welcome back</h2>
            <p className="text-sm text-slate-500 mt-1">Sign in to continue to the support portal.</p>
          </div>

          <label className="block">
            <span className="text-sm font-medium text-slate-700">Username</span>
            <input
              className="input-field mt-1.5"
              autoComplete="username"
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
          </label>

          <label className="block">
            <span className="text-sm font-medium text-slate-700">Password</span>
            <div className="relative mt-1.5">
              <input
                className="input-field pr-10"
                type={show ? 'text' : 'password'}
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
              <button
                type="button"
                onClick={() => setShow((v) => !v)}
                className="absolute inset-y-0 right-0 px-3 text-slate-400 hover:text-slate-600"
                aria-label={show ? 'Hide password' : 'Show password'}
              >
                {show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </label>

          {error && (
            <div role="alert" className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
              {error}
            </div>
          )}

          <button className="btn-primary w-full py-2.5" disabled={busy || !username || !password}>
            {busy && <Loader2 className="w-4 h-4 animate-spin" />}
            Sign in
          </button>
        </form>
      </div>
    </div>
  )
}
