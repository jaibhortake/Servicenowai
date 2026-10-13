import { useCallback, useEffect, useState } from 'react'
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import Sidebar from './components/Layout/Sidebar'
import Topbar from './components/Layout/Topbar'
import RequestPortal from './pages/RequestPortal'
import GraphRAGPortal from './pages/GraphRAGPortal'
import CreateCasePortal from './pages/CreateCasePortal'
import AgentDashboard from './pages/AgentDashboard'
import Dashboard from './pages/Dashboard'
import TicketDetail from './pages/TicketDetail'
import AdminPanel from './pages/AdminPanel'
import Login from './pages/Login'
import { AUTH_LOGOUT_EVENT, clearSession, getStoredUser, getToken, saveSession } from './auth/auth'
import { applyTheme, getTheme } from './auth/theme'

const SIDEBAR_KEY = 'servicenowai.sidebar'

function readCollapsed() {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === '1'
  } catch {
    return false
  }
}

function AdminOnly({ user, children }) {
  return user?.role === 'admin' ? children : <Navigate to="/" replace />
}

export default function App() {
  const navigate = useNavigate()
  const [user, setUser] = useState(() => (getToken() ? getStoredUser() : null))
  const [theme, setTheme] = useState(getTheme)
  const [collapsed, setCollapsed] = useState(readCollapsed)

  useEffect(() => applyTheme(theme), [theme])

  const logout = useCallback(() => {
    clearSession()
    setUser(null)
    navigate('/', { replace: true })
  }, [navigate])

  // The API client raises this when any request comes back 401 (expired / invalid token).
  useEffect(() => {
    window.addEventListener(AUTH_LOGOUT_EVENT, logout)
    return () => window.removeEventListener(AUTH_LOGOUT_EVENT, logout)
  }, [logout])

  const toggleSidebar = () =>
    setCollapsed((c) => {
      try {
        localStorage.setItem(SIDEBAR_KEY, c ? '0' : '1')
      } catch {
        /* ignore */
      }
      return !c
    })

  if (!user) {
    return (
      <Login
        onSuccess={(token, nextUser) => {
          saveSession(token, nextUser)
          setUser(nextUser)
        }}
      />
    )
  }

  const isAdmin = user.role === 'admin'

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar collapsed={collapsed} onToggle={toggleSidebar} isAdmin={isAdmin} />
      <div className="flex-1 flex flex-col min-w-0">
        <Topbar
          user={user}
          theme={theme}
          onToggleTheme={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
          onLogout={logout}
        />
        <main className="flex-1 overflow-hidden">
          <Routes>
            <Route path="/" element={<RequestPortal />} />
            <Route path="/graphrag" element={<GraphRAGPortal />} />
            <Route path="/create-case" element={<CreateCasePortal />} />
            <Route
              path="/create-case/agent-dashboard"
              element={
                <AdminOnly user={user}>
                  <AgentDashboard />
                </AdminOnly>
              }
            />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/ticket/:type/:sysId" element={<TicketDetail />} />
            <Route
              path="/admin"
              element={
                <AdminOnly user={user}>
                  <AdminPanel />
                </AdminOnly>
              }
            />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}
