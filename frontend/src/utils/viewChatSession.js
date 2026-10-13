export const LAST_VIEW_CHAT_TARGET_KEY = "support-portal:last-view-chat-target";
export const VIEW_CHAT_TARGET_UPDATED_EVENT = "support-portal:view-chat-target-updated";

export function setLastViewChatTarget(target) {
  if (!target?.ticketType || !target?.sysId) return;
  localStorage.setItem(LAST_VIEW_CHAT_TARGET_KEY, JSON.stringify(target));
  window.dispatchEvent(new CustomEvent(VIEW_CHAT_TARGET_UPDATED_EVENT));
}

export function getLastViewChatTarget() {
  const raw = localStorage.getItem(LAST_VIEW_CHAT_TARGET_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (!parsed?.ticketType || !parsed?.sysId) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function clearLastViewChatTarget() {
  localStorage.removeItem(LAST_VIEW_CHAT_TARGET_KEY);
  window.dispatchEvent(new CustomEvent(VIEW_CHAT_TARGET_UPDATED_EVENT));
}
