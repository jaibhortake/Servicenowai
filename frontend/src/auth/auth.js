// Token helpers. localStorage can throw (private mode / blocked storage) so every access is guarded.
const TOKEN_KEY = 'servicenowai.token'
const USER_KEY = 'servicenowai.user'
export const AUTH_LOGOUT_EVENT = 'servicenowai:logout'

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

export function getStoredUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || 'null')
  } catch {
    return null
  }
}

export function saveSession(token, user) {
  try {
    localStorage.setItem(TOKEN_KEY, token)
    localStorage.setItem(USER_KEY, JSON.stringify(user))
  } catch {
    /* ignore */
  }
}

export function clearSession() {
  try {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  } catch {
    /* ignore */
  }
}

/** Append ?token= to a websocket URL (browsers cannot set headers on WebSockets). */
export function withToken(url) {
  const token = getToken()
  if (!token) return url
  return `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`
}
