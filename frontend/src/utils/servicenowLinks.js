const DEFAULT_SERVICE_NOW_INSTANCE_URL =
  import.meta.env.VITE_SERVICENOW_URL || "https://dev308390.service-now.com";

const TABLE_BY_TICKET_TYPE = {
  INC: "incident",
  CH: "change_request",
  SR: "sc_request",
};

export function getServiceNowInstanceUrl() {
  return (
    import.meta.env.VITE_SERVICENOW_INSTANCE_URL ||
    DEFAULT_SERVICE_NOW_INSTANCE_URL
  ).trim().replace(/\/+$/, "");
}

export function buildServiceNowRecordUrl(ticketType, number) {
  const instanceUrl = getServiceNowInstanceUrl();
  const normalizedType = String(ticketType || "").trim().toUpperCase();
  const table = TABLE_BY_TICKET_TYPE[normalizedType] || "incident";
  const ticketNumber = String(number || "").trim();
  if (!ticketNumber) return "";
  return `${instanceUrl}/${table}.do?sysparm_query=number=${encodeURIComponent(
    ticketNumber,
  )}`;
}

