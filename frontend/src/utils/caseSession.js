export const LAST_CASE_SESSION_KEY = "graphrag:create-case:last-session";
export const LAST_CASE_DASHBOARD_SESSION_KEY =
  "graphrag:create-case:last-dashboard-session";
export const CASE_SESSION_UPDATED_EVENT = "graphrag:create-case:updated";

export function setCaseDashboardSession(sessionId) {
  if (!sessionId) return;
  localStorage.setItem(LAST_CASE_DASHBOARD_SESSION_KEY, sessionId);
  window.dispatchEvent(new CustomEvent(CASE_SESSION_UPDATED_EVENT));
}

export function clearCaseDashboardSession() {
  localStorage.removeItem(LAST_CASE_DASHBOARD_SESSION_KEY);
  window.dispatchEvent(new CustomEvent(CASE_SESSION_UPDATED_EVENT));
}
