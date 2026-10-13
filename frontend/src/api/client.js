import toast from "react-hot-toast";
import { AUTH_LOGOUT_EVENT, clearSession, getToken } from "../auth/auth";

const baseURL = import.meta.env.VITE_API_URL || "";

async function apiFetch(url, options = {}) {
  const {
    method = "GET",
    body = null,
    params = {},
    silent = false,
    timeout = 50000,
    ...rest
  } = options;

  // Build URL with query params using URL constructor for proper encoding
  const urlObj = new URL(`${baseURL}${url}`, window.location.origin);
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined) {
      urlObj.searchParams.append(key, value);
    }
  });
  const fullURL = urlObj.toString();

  const controller = new AbortController();
  let didTimeout = false;
  const timeoutId = setTimeout(() => {
    didTimeout = true;
    controller.abort(
      new DOMException(
        `Request timed out after ${Math.round(timeout / 1000)}s`,
        "TimeoutError",
      ),
    );
  }, timeout);

  try {
    const response = await fetch(fullURL, {
      method,
      headers: {
        "Content-Type": "application/json",
        ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
        ...rest.headers,
      },
      body: body ? JSON.stringify(body) : null,
      signal: controller.signal,
      ...rest,
    });

    clearTimeout(timeoutId);

    const data = await response.json();

    if (response.status === 401 && !url.startsWith("/api/auth/login")) {
      clearSession();
      window.dispatchEvent(new Event(AUTH_LOGOUT_EVENT));
    }

    if (!response.ok) {
      const err = new Error(data?.detail || `HTTP ${response.status}`);
      err.status = response.status;
      err.data = data;
      throw err;
    }

    return data;
  } catch (err) {
    clearTimeout(timeoutId);
    if (err?.name === "AbortError" || err?.name === "TimeoutError") {
      const timeoutMessage = didTimeout
        ? `Request timed out after ${Math.round(timeout / 1000)}s. Try a narrower query or retry in a moment.`
        : "Request was cancelled before it completed.";
      const abortError = new Error(timeoutMessage);
      abortError.status = didTimeout ? "timeout" : "cancelled";
      if (!silent) {
        toast.error(timeoutMessage);
      }
      throw abortError;
    }
    if (!silent && err.status !== 401) {
      const msg =
        typeof err.message === "string" ? err.message : "An error occurred";
      toast.error(msg);
    }
    throw err;
  }
}

export const interpretRequest = (text, sessionId, userEmail) =>
  apiFetch("/api/interpret", {
    method: "POST",
    body: { text, session_id: sessionId, user_email: userEmail },
    timeout: 60000,
  });

export const listCatalogs = (category, limit = 50) =>
  apiFetch("/api/catalog", {
    params: { category, limit },
  });

export const getCatalogItem = (sysId) => apiFetch(`/api/catalog/${sysId}`);

export const getCatalogForm = (sysId, config = {}) => {
  const { params = {}, ...rest } = config;
  return apiFetch(`/api/catalog/${sysId}/form`, {
    params,
    timeout: 50000,
    ...rest,
  });
};

export const getCatalogFormVersion = (sysId, config = {}) => {
  const { params = {}, ...rest } = config;
  return apiFetch(`/api/catalog/${sysId}/form/version`, {
    params,
    ...rest,
  });
};

export const prefillCatalogForm = (
  query,
  variables,
  catalogItem = null,
  intentContext = null,
) =>
  apiFetch("/api/catalog/prefill", {
    method: "POST",
    body: {
      query,
      variables,
      catalog_item: catalogItem,
      intent_context: intentContext,
    },
    silent: true,
    timeout: 25000,
  });

export const getMe = (email) =>
  apiFetch("/api/users/me", {
    params: { email },
    silent: true,
  });

export const getUser = (sysId) => apiFetch(`/api/users/${sysId}`);

export const listTickets = (userSysId) =>
  apiFetch("/api/tickets", {
    params: { user_sys_id: userSysId },
  });

export const createSR = (payload, sessionId) =>
  apiFetch("/api/tickets/sr", {
    method: "POST",
    body: payload,
    params: { session_id: sessionId },
    timeout: 60000,
  });

export const createCH = (payload, sessionId) =>
  apiFetch("/api/tickets/ch", {
    method: "POST",
    body: payload,
    params: { session_id: sessionId },
    timeout: 60000,
  });

export const getTicket = (type, sysId) =>
  apiFetch(`/api/tickets/${type}/${sysId}`);

export const updateTicket = (type, sysId, payload) =>
  apiFetch(`/api/tickets/${type}/${sysId}`, {
    method: "PATCH",
    body: payload,
  });

export const addComment = (sysId, payload, sessionId) =>
  apiFetch(`/api/comments/${sysId}`, {
    method: "POST",
    body: payload,
    params: { session_id: sessionId },
  });

export const getComments = (sysId, ticketType) =>
  apiFetch(`/api/comments/${sysId}`, {
    params: { ticket_type: ticketType },
    silent: true,
  });

export const getWorkNotes = (sysId, ticketType) =>
  apiFetch(`/api/comments/${sysId}`, {
    params: { ticket_type: ticketType, include_work_notes: true },
    silent: true,
  });

export const createConversation = (userId, userEmail) =>
  apiFetch("/api/conversations", {
    method: "POST",
    params: { user_id: userId, user_email: userEmail },
  });

export const getConversation = (sessionId) =>
  apiFetch(`/api/conversations/${sessionId}`, {
    silent: true,
  });

export const listConversations = (userEmail) =>
  apiFetch("/api/conversations", {
    params: { user_email: userEmail },
  });

export const sendAdminQuestion = (sessionId, question) =>
  apiFetch(`/api/conversations/${sessionId}/admin-question`, {
    method: "POST",
    params: { question },
  });

export const queryGraphRAG = (query, context = {}) =>
  apiFetch("/api/graphrag/query", {
    method: "POST",
    body: {
      query,
      user_sys_id: context.userSysId,
      user_email: context.userEmail,
      session_id: context.sessionId,
    },
    timeout: 60000,
  });

export const startGraphRAGDiagnosis = (query, context = {}) =>
  apiFetch("/api/graphrag/diagnosis/start", {
    method: "POST",
    body: {
      query,
      user_sys_id: context.userSysId,
      user_email: context.userEmail,
      incident_sys_id: context.incidentSysId,
      product_hint: context.productHint,
    },
    timeout: 60000,
  });

export const getGraphRAGDiagnosis = (sessionId) =>
  apiFetch(`/api/graphrag/diagnosis/${sessionId}`, {
    timeout: 30000,
  });

export const answerGraphRAGDiagnosis = (sessionId, payload) =>
  apiFetch(`/api/graphrag/diagnosis/${sessionId}/answer`, {
    method: "POST",
    body: payload,
    timeout: 60000,
  });

export const selectGraphRAGDiagnosisIssue = (sessionId, payload) =>
  apiFetch(`/api/graphrag/diagnosis/${sessionId}/select-issue`, {
    method: "POST",
    body: payload,
    timeout: 60000,
  });

export const completeGraphRAGDiagnosis = (sessionId) =>
  apiFetch(`/api/graphrag/diagnosis/${sessionId}/complete`, {
    method: "POST",
    timeout: 60000,
  });

export const cancelGraphRAGDiagnosis = (sessionId) =>
  apiFetch(`/api/graphrag/diagnosis/${sessionId}/cancel`, {
    method: "POST",
    timeout: 60000,
  });

export const startGraphRAGCase = (query, context = {}) =>
  apiFetch("/api/graphrag/cases/start", {
    method: "POST",
    body: {
      query,
      user_sys_id: context.userSysId,
      user_email: context.userEmail,
    },
    timeout: 60000,
  });

export const getGraphRAGCase = (sessionId) =>
  apiFetch(`/api/graphrag/cases/${sessionId}`, {
    timeout: 30000,
  });

export const listGraphCases = (config = {}) =>
  apiFetch("/api/graphrag/cases/all", {
    params: {
      ticket_type: config.ticketType,
      states:
        Array.isArray(config.states) && config.states.length
          ? config.states.join(",")
          : config.states,
      limit: config.limit,
    },
    silent: true,
    timeout: 30000,
  });

export const answerGraphRAGCase = (sessionId, payload) =>
  apiFetch(`/api/graphrag/cases/${sessionId}/answer`, {
    method: "POST",
    body: payload,
    timeout: 60000,
  });

export const selectGraphRAGCaseIssue = (sessionId, payload) =>
  apiFetch(`/api/graphrag/cases/${sessionId}/select-issue`, {
    method: "POST",
    body: payload,
    timeout: 60000,
  });

export const completeGraphRAGCase = (sessionId, context = {}) =>
  apiFetch(`/api/graphrag/cases/${sessionId}/complete`, {
    method: "POST",
    body: {
      user_sys_id: context.userSysId,
      user_email: context.userEmail,
    },
    timeout: 60000,
  });

export const cancelGraphRAGCase = (sessionId) =>
  apiFetch(`/api/graphrag/cases/${sessionId}/cancel`, {
    method: "POST",
    timeout: 60000,
  });

export const queryVectorRAG = (query, context = {}) =>
  apiFetch("/api/vectorrag/query", {
    method: "POST",
    body: {
      query,
      user_sys_id: context.userSysId,
      user_email: context.userEmail,
      session_id: context.sessionId,
    },
    timeout: 60000,
  });


export const login = (username, password) =>
  apiFetch("/api/auth/login", {
    method: "POST",
    body: { username, password },
    silent: true,
  });
