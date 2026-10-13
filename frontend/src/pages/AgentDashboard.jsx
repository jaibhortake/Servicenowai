import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import clsx from "clsx";
import toast from "react-hot-toast";
import {
  AlertTriangle,
  ArrowRight,
  Bell,
  BookOpen,
  Bot,
  Box,
  BriefcaseBusiness,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Circle,
  Clock3,
  Copy,
  Database,
  Edit3,
  ExternalLink,
  FileCheck2,
  FileText,
  Gauge,
  GitBranch,
  Grid2X2,
  Headphones,
  Home,
  Layers3,
  LayoutList,
  Loader2,
  MessageSquare,
  Mic,
  MonitorCog,
  Network,
  RefreshCw,
  Search,
  Send,
  Server,
  Settings,
  ShieldAlert,
  Sparkles,
  Star,
  UserRound,
  WifiOff,
  X,
  Zap,
} from "lucide-react";
import {
  addComment,
  getGraphRAGCase,
  getTicket,
  listGraphCases,
  queryGraphRAG,
  updateTicket,
} from "../api/client";
import {
  LAST_CASE_DASHBOARD_SESSION_KEY,
  LAST_CASE_SESSION_KEY,
} from "../utils/caseSession";
import { useStore } from "../store/useStore";
import { setLastViewChatTarget } from "../utils/viewChatSession";

const MAIN_TABS = [
  "Overview",
  "Details",
  "Tasks",
  "Interactions",
  "Related Cases",
  "Attachments",
  "Audit Trail",
];

const ASSISTANT_TABS = [
  "Diagnosis",
  "Recommendations",
  "Runbooks",
  "Similar Cases",
];

const CONTEXT_TABS = [
  "Topology View",
  "Health Signals",
  "Recent Changes",
  "Alerts",
];

const INCIDENT_STATUS_VALUES = {
  Open: "1",
  "In Progress": "2",
  Pending: "3",
  Resolved: "6",
};

const CLOSE_CODE_SUGGESTIONS = [
  "Solved (Permanently)",
  "Solved (Work Around)",
  "Closed/Resolved by Caller",
  "Not Solved (Not Reproducible)",
  "Not Solved (Too Costly)",
];

const DEFAULT_CLOSE_CODE = CLOSE_CODE_SUGGESTIONS[0];

const TOP_NAV_ITEMS = [
  "My Work",
  "Cases",
  "Knowledge",
  "Dashboards",
  "Insights",
];

const RAIL_SECTIONS = [
  {
    title: "Workspace",
    items: [
      {
        key: "my_cases",
        label: "My Cases",
        icon: BriefcaseBusiness,
        count: 1,
        mainTab: "Overview",
      },
      {
        key: "my_tasks",
        label: "My Tasks",
        icon: FileCheck2,
        count: 3,
        mainTab: "Tasks",
      },
      {
        key: "watchlist",
        label: "Watchlist",
        icon: Clock3,
        mainTab: "Related Cases",
      },
      {
        key: "escalations",
        label: "Escalations",
        icon: ShieldAlert,
        count: 1,
        assistantTab: "Recommendations",
      },
    ],
  },
  {
    title: "Incident Management",
    items: [
      {
        key: "all_cases",
        label: "All Cases",
        icon: Grid2X2,
        mainTab: "Details",
      },
      {
        key: "open_cases",
        label: "Open Cases",
        icon: GitBranch,
        mainTab: "Overview",
      },
      {
        key: "pending_customer",
        label: "Pending Customer",
        icon: MessageSquare,
        mainTab: "Interactions",
      },
      {
        key: "resolved",
        label: "Resolved",
        icon: CheckCircle2,
        mainTab: "Audit Trail",
      },
    ],
  },
  {
    title: "Intelligence",
    items: [
      {
        key: "appliance_health",
        label: "Appliance Health",
        icon: Gauge,
        contextTab: "Health Signals",
      },
      {
        key: "known_issues",
        label: "Known Issues",
        icon: BookOpen,
        assistantTab: "Similar Cases",
      },
      {
        key: "recommendations",
        label: "Recommendations",
        icon: Sparkles,
        assistantTab: "Recommendations",
      },
    ],
  },
  {
    title: "Admin",
    items: [
      {
        key: "customers",
        label: "Customers",
        icon: UserRound,
        mainTab: "Details",
      },
      {
        key: "appliances",
        label: "Appliances",
        icon: MonitorCog,
        contextTab: "Topology View",
      },
      {
        key: "integrations",
        label: "Integrations",
        icon: Network,
        contextTab: "Alerts",
      },
      {
        key: "settings",
        label: "Settings",
        icon: Settings,
        mainTab: "Details",
      },
    ],
  },
];

function firstNonEmpty(...values) {
  return (
    values.find(
      (value) =>
        value !== undefined && value !== null && String(value).trim() !== "",
    ) || ""
  );
}

function valueOf(field, fallback = "") {
  if (field && typeof field === "object") {
    return firstNonEmpty(field.display_value, field.value, fallback);
  }
  return firstNonEmpty(field, fallback);
}

function safeText(value, fallback = "") {
  return firstNonEmpty(
    typeof value === "string" ? value.trim() : value,
    fallback,
  );
}

function initials(name) {
  return String(name || "Support Engineer")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

function toDate(value) {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime()) ? date : null;
}

function formatDate(value) {
  const date = toDate(value);
  if (!date) return "Today";
  return date.toLocaleDateString([], {
    month: "short",
    day: "2-digit",
    year: "numeric",
  });
}

function formatClock(value) {
  const date = toDate(value);
  if (!date) return "Now";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatDateTime(value) {
  const date = toDate(value);
  if (!date) return "Just now";
  return `${formatDate(value)}, ${formatClock(value)}`;
}

function formatRelative(value) {
  const date = toDate(value);
  if (!date) return "just now";
  const diffMs = Date.now() - date.getTime();
  const minutes = Math.max(1, Math.round(diffMs / 60000));
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

function titleCase(value) {
  return String(value || "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function statusMeta(status) {
  const normalized = String(status || "Open").toLowerCase();
  if (normalized.includes("resolved") || normalized.includes("closed")) {
    return {
      label: "Resolved",
      tone: "emerald",
      badge: "bg-emerald-50 border-emerald-200 text-emerald-700",
    };
  }
  if (normalized.includes("pending")) {
    return {
      label: "Pending",
      tone: "amber",
      badge: "bg-amber-50 border-amber-200 text-amber-700",
    };
  }
  if (normalized.includes("progress")) {
    return {
      label: "In Progress",
      tone: "blue",
      badge: "bg-blue-50 border-blue-200 text-blue-700",
    };
  }
  return {
    label: "Open",
    tone: "red",
    badge: "bg-red-50 border-red-200 text-red-700",
  };
}

function severityMeta(topCause, priority) {
  const joined =
    `${safeText(priority)} ${safeText(topCause?.severity)}`.toLowerCase();
  if (joined.includes("critical") || joined.startsWith("1")) {
    return { label: "Critical", priorityLabel: "1 - Critical", tone: "red" };
  }
  if (joined.includes("high") || joined.startsWith("2")) {
    return { label: "High", priorityLabel: "2 - High", tone: "red" };
  }
  if (joined.includes("medium") || joined.startsWith("3")) {
    return { label: "Medium", priorityLabel: "3 - Medium", tone: "amber" };
  }
  return { label: "Standard", priorityLabel: "4 - Standard", tone: "slate" };
}

function healthMeta(topCause, answers) {
  const problemSignals = [
    ...(topCause?.evidence || []),
    ...(answers || [])
      .filter(
        (answer) =>
          answer.skipped ||
          /not sure|no/i.test(answer.answer_label || answer.answer_value || ""),
      )
      .map((answer) => answer.question_text || answer.question_id),
  ];
  if (problemSignals.length >= 3) {
    return { label: "Attention Needed", tone: "red" };
  }
  if (problemSignals.length >= 1) {
    return { label: "Degraded", tone: "amber" };
  }
  return { label: "Healthy", tone: "emerald" };
}

function getSearchMatcher(query) {
  const normalized = String(query || "")
    .trim()
    .toLowerCase();
  return (...values) => {
    if (!normalized) return true;
    return values.some((value) =>
      String(value || "")
        .toLowerCase()
        .includes(normalized),
    );
  };
}

function detectEvidenceTone(text) {
  return !/\b(no|not|blocked|failed|missing|degraded|error|issue|unable|outage)\b/i.test(
    String(text || ""),
  );
}

function inferProductName(issue, causeName) {
  const combined = `${issue} ${causeName}`.toLowerCase();
  if (combined.includes("pure1")) return "Pure1";
  if (combined.includes("vm analytics")) return "VM Analytics";
  if (combined.includes("collector")) return "Collector";
  return "Support Platform";
}

function buildRelatedCases(issueCandidates, topCause, caseNumber) {
  const list = (issueCandidates || []).slice(0, 4).map((candidate, index) => ({
    id: candidate.issue_id || `related-${index}`,
    number: `SIM-${String(index + 1).padStart(4, "0")}`,
    title: candidate.label || "Related issue",
    summary:
      candidate.description ||
      `Pattern overlap with ${topCause?.name || "current diagnosis"}.`,
    fit: Math.max(48, Math.round((candidate.score || 0.5) * 100)),
  }));
  if (list.length) return list;
  return [
    {
      id: "current-pattern",
      number: `${caseNumber}-A`,
      title: topCause?.name || "Current incident pattern",
      summary:
        "Derived from the matched issue, workflow answers, and recommended resolution.",
      fit: 82,
    },
  ];
}

function graphTicketTypeLabel(ticketType) {
  const normalized = String(ticketType || "").toUpperCase();
  if (normalized === "INC") return "Incident";
  if (normalized === "SR") return "Service Request";
  if (normalized === "CH") return "Change Request";
  return titleCase(normalized) || "Case";
}

function normalizeGraphCase(record) {
  const stateLabel = firstNonEmpty(
    record?.incident_state_display,
    record?.state_display,
    record?.state,
    "Open",
  );
  const status = statusMeta(stateLabel);
  return {
    ...record,
    id:
      record?.sys_id ||
      record?.number ||
      `${record?.ticket_type || "case"}-${record?.opened_at || record?.updated_at || record?.short_description || "graph"}`,
    typeLabel: graphTicketTypeLabel(record?.ticket_type),
    stateLabel,
    statusTone: status.tone,
    statusLabel: status.label,
    shortDescription: firstNonEmpty(
      record?.short_description,
      record?.description,
      "No short description available",
    ),
    description: firstNonEmpty(record?.description, record?.short_description),
    contact: firstNonEmpty(
      record?.caller_name,
      record?.requested_for_name,
      record?.requested_by_name,
      "Unassigned",
    ),
    assignedTo: firstNonEmpty(record?.assigned_to_name, "Unassigned"),
    requestedBy: firstNonEmpty(
      record?.requested_by_name,
      record?.requested_for_name,
      "Unknown",
    ),
    openedLabel: formatDateTime(record?.opened_at),
    openedAgo: formatRelative(record?.opened_at),
    updatedLabel: formatDateTime(record?.updated_at || record?.opened_at),
    dueLabel: firstNonEmpty(record?.resolved_at, record?.close_notes, ""),
  };
}

function graphCaseMatchesRail(graphCase, activeRail, currentUser) {
  const state = String(
    firstNonEmpty(
      graphCase?.incident_state_display,
      graphCase?.stateLabel,
      graphCase?.state,
    ),
  ).toLowerCase();
  if (activeRail === "all_cases") return true;
  if (activeRail === "open_cases") {
    return !/(resolved|closed|completed|cancelled)/i.test(state);
  }
  if (activeRail === "pending_customer") {
    return /(pending|awaiting|waiting)/i.test(state);
  }
  if (activeRail === "resolved") {
    return /(resolved|closed|completed)/i.test(state);
  }
  if (activeRail === "my_cases") {
    const identity = [
      currentUser?.name,
      currentUser?.email,
      currentUser?.department,
    ]
      .map((value) => String(value || "").toLowerCase())
      .filter(Boolean);
    if (!identity.length) return true;
    return identity.some((needle) =>
      [
        graphCase?.assignedTo,
        graphCase?.contact,
        graphCase?.requestedBy,
        graphCase?.shortDescription,
      ].some((value) =>
        String(value || "")
          .toLowerCase()
          .includes(needle),
      ),
    );
  }
  return true;
}

function buildAttachments(model) {
  return [
    {
      id: "case-summary",
      label: "Case Summary",
      detail: `Generated from ${model.timeline.length} workflow events`,
      action: "copy_summary",
    },
    {
      id: "resolution-plan",
      label: "Resolution Plan",
      detail: `${model.steps.length} recommended actions ready to share`,
      action: "copy_resolution",
    },
    {
      id: "workflow-snapshot",
      label: "Workflow Snapshot",
      detail: `${model.answers.length} diagnostic answers captured`,
      action: "copy_workflow",
    },
  ];
}

function buildRunbooks(topCause, steps) {
  const entries = [
    ...(topCause?.runbooks || []).map((item) => ({
      title: titleCase(item),
      detail: "Graph-linked runbook",
    })),
    ...(topCause?.resolutions || []).map((item) => ({
      title: titleCase(item),
      detail: "Resolution play",
    })),
  ];
  if (entries.length) return entries.slice(0, 4);
  return steps.slice(0, 4).map((item, index) => ({
    title: `Runbook Step ${index + 1}`,
    detail: item,
  }));
}

function buildTopology(model) {
  return [
    {
      key: "customer",
      icon: Home,
      title: "Customer",
      detail: model.account,
      tone: "slate",
    },
    {
      key: "site",
      icon: Network,
      title: "Site",
      detail: model.environmentName,
      tone: "slate",
    },
    {
      key: "platform",
      icon: Grid2X2,
      title: "Platform",
      detail: model.source,
      tone: "green",
      status: "Connected",
    },
    {
      key: "appliance",
      icon: Box,
      title: model.applianceName,
      detail: model.productArea,
      tone:
        model.health.tone === "red"
          ? "red"
          : model.health.tone === "amber"
            ? "amber"
            : "green",
      status: model.health.label,
      wide: true,
    },
    {
      key: "cloud",
      icon: Database,
      title: inferProductName(model.issue, model.causeSummary),
      detail: "Cloud service",
      tone: "green",
      status: "Reachable",
    },
    {
      key: "dependency",
      icon: model.alerts.length ? WifiOff : Server,
      title: model.alerts.length ? "Connectivity" : "Telemetry Path",
      detail: model.alerts[0]?.text || "Stable",
      tone: model.alerts.length ? "red" : "green",
      status: model.alerts.length ? "Needs validation" : "Healthy",
    },
  ];
}

function buildDashboardModel({
  workflow,
  ticket,
  currentUser,
  statusOverride,
  shortDescriptionOverride,
  assignedToOverride,
  contactOverride,
  localEvents,
  localNotes,
  escalationRecord,
  resolvedLocally,
}) {
  const caseSummary = workflow?.case_summary || {};
  const topCause = workflow?.possible_causes?.[0] || null;
  const recommendation = workflow?.recommended_resolution || null;
  const createdTicket = workflow?.created_ticket || {};
  const ticketRaw = ticket?.raw || createdTicket.raw || {};
  const caseNumber = firstNonEmpty(
    workflow?.case_number,
    createdTicket.number,
    ticket?.number,
    "INC0000000",
  );
  const issue = firstNonEmpty(
    caseSummary.matched_issue,
    workflow?.selected_issue?.label,
    caseSummary.issue,
    workflow?.initial_query,
    "Issue summary unavailable",
  );
  const createdAt = firstNonEmpty(
    workflow?.created_at,
    ticket?.opened_at,
    createdTicket.opened_at,
  );
  const status = resolvedLocally
    ? "Resolved"
    : firstNonEmpty(
        statusOverride,
        ticket?.state,
        createdTicket.state,
        workflow?.status,
        "Open",
      );
  const statusInfo = statusMeta(status);
  const severity = severityMeta(
    topCause,
    firstNonEmpty(ticketRaw.priority, ticket?.priority),
  );
  const answers = workflow?.answered_questions || [];
  const account = firstNonEmpty(
    valueOf(ticketRaw.account),
    valueOf(ticketRaw.company),
    "Customer Account",
  );
  const contact = firstNonEmpty(
    contactOverride,
    valueOf(ticketRaw.caller_id),
    valueOf(ticketRaw.contact),
    caseSummary.user_email,
    currentUser?.name,
    "Primary Contact",
  );
  const assignedTo = firstNonEmpty(
    assignedToOverride,
    ticket?.assigned_to,
    createdTicket.assigned_to,
    "Support Intelligence Queue",
  );
  const shortDescription = firstNonEmpty(
    shortDescriptionOverride,
    ticket?.short_description,
    createdTicket.short_description,
    workflow?.initial_query,
    issue,
  );
  const causeSummary = firstNonEmpty(
    topCause?.name,
    caseSummary.likely_cause,
    "Diagnosis in progress",
  );
  const health = healthMeta(topCause, answers);
  const confidence = Math.max(
    42,
    Math.round(firstNonEmpty(topCause?.score, caseSummary.confidence, 72)),
  );
  const steps = [
    ...(recommendation?.steps || []),
    recommendation?.title,
    caseSummary.recommended_action,
  ]
    .filter(Boolean)
    .filter(
      (item, index, array) =>
        array.findIndex((entry) => entry === item) === index,
    )
    .slice(0, 5);
  if (!steps.length) {
    steps.push(
      "Validate the affected service path and confirm the current workflow answers.",
      "Share the strongest likely cause with the case owner and align on the next test.",
      "Capture outcome notes so the case can be resolved or escalated cleanly.",
    );
  }

  const evidenceSource = [
    ...(topCause?.evidence || []),
    ...answers.map((answer) =>
      answer.skipped
        ? `Question skipped: ${answer.question_text || answer.question_id}`
        : `${answer.question_text || answer.question_id}: ${answer.answer_label || answer.answer_value}`,
    ),
  ]
    .filter(Boolean)
    .slice(0, 6);
  const evidence = evidenceSource.length
    ? evidenceSource.map((text) => ({
        text,
        ok: detectEvidenceTone(text),
      }))
    : [
        { text: "Workflow context collected successfully.", ok: true },
        {
          text: "Additional validation is still required before closure.",
          ok: false,
        },
      ];

  const missingInfo = [
    ...answers
      .filter(
        (answer) =>
          answer.skipped ||
          /not sure|unknown/i.test(
            answer.answer_label || answer.answer_value || "",
          ),
      )
      .map((answer) => answer.question_text || titleCase(answer.question_id)),
    workflow?.current_question?.question_text,
  ]
    .filter(Boolean)
    .filter((item, index, array) => array.indexOf(item) === index)
    .slice(0, 4);

  const alerts = evidence
    .filter((item) => !item.ok)
    .map((item, index) => ({
      id: `alert-${index}`,
      text: item.text,
      severity: index === 0 ? "High" : "Medium",
    }));

  const workflowLog = (workflow?.workflow_log || []).map((entry, index) => ({
    id: `workflow-${index}`,
    label: entry.label || "Workflow event",
    detail: entry.detail || "No detail recorded.",
    timestamp: entry.timestamp || workflow?.updated_at || createdAt,
    tone: /created|opened/i.test(entry.label || "")
      ? "blue"
      : /completed|resolved|confirmed/i.test(entry.label || "")
        ? "green"
        : /analysis|matched|diagnosis/i.test(entry.label || "")
          ? "purple"
          : "blue",
    system: true,
  }));

  const noteEvents = (localNotes || []).map((note, index) => ({
    id: `note-${index}`,
    label: "Work Note Added",
    detail: note,
    timestamp: new Date().toISOString(),
    tone: "blue",
    system: false,
  }));

  const escalationEvents = escalationRecord
    ? [
        {
          id: "escalation-local",
          label: "Escalation Prepared",
          detail: `${escalationRecord.team}: ${escalationRecord.reason}`,
          timestamp: escalationRecord.timestamp,
          tone: "purple",
          system: false,
        },
      ]
    : [];

  const resolutionEvents = resolvedLocally
    ? [
        {
          id: "resolved-local",
          label: "Case Marked Resolved",
          detail: "Resolution was simulated locally for dashboard review.",
          timestamp: new Date().toISOString(),
          tone: "green",
          system: false,
        },
      ]
    : [];

  const timeline = [
    ...workflowLog,
    ...noteEvents,
    ...escalationEvents,
    ...resolutionEvents,
    ...(localEvents || []),
  ].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));

  return {
    caseNumber,
    issue,
    shortDescription,
    status: statusInfo,
    severity,
    createdAt,
    createdLabel: formatDateTime(createdAt),
    openedAgo: formatRelative(createdAt),
    account,
    contact,
    source: firstNonEmpty(
      valueOf(ticketRaw.contact_type),
      createdTicket.ticket_type,
      "Portal",
    ),
    assignedTo,
    priorityLabel: severity.priorityLabel,
    slaLabel:
      statusInfo.label === "Resolved"
        ? "Met"
        : confidence >= 70
          ? "On Track"
          : "Needs attention",
    issueMatch: issue,
    causeSummary,
    confidence,
    health,
    answers,
    answerSummary:
      answers.length > 0
        ? `${answers.length} answers captured`
        : "No diagnostic answers captured yet",
    environmentName: firstNonEmpty(
      valueOf(ticketRaw.cmdb_ci),
      valueOf(ticketRaw.u_environment),
      "Primary environment",
    ),
    applianceName: inferProductName(issue, causeSummary) + " Appliance",
    productArea: inferProductName(issue, causeSummary),
    lastSeen: workflow?.updated_at || createdAt,
    steps,
    evidence,
    missingInfo,
    alerts,
    recommendation,
    caseSummary,
    topCause,
    relatedCases: buildRelatedCases(
      workflow?.issue_candidates,
      topCause,
      caseNumber,
    ),
    runbooks: buildRunbooks(topCause, steps),
    timeline,
    topology: [],
    attachments: [],
  };
}

async function copyText(text, successMessage) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(successMessage);
  } catch {
    toast.error("Copy was blocked by the browser.");
  }
}

function RailButton({ icon: Icon, label, count, active, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        "flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-[13px] font-medium transition",
        active
          ? "bg-blue-500/15 text-white shadow-[inset_0_0_0_1px_rgba(96,165,250,0.35)]"
          : "text-blue-50/85 hover:bg-white/8 hover:text-white",
      )}
    >
      <Icon className="h-4 w-4 shrink-0" />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {count ? (
        <span className="rounded-full bg-white/15 px-2 py-0.5 text-[11px] font-semibold text-white">
          {count}
        </span>
      ) : null}
    </button>
  );
}

function StatusPill({ label, tone }) {
  const toneMap = {
    red: "border-red-200 bg-red-50 text-red-700",
    amber: "border-amber-200 bg-amber-50 text-amber-700",
    emerald: "border-emerald-200 bg-emerald-50 text-emerald-700",
    blue: "border-blue-200 bg-blue-50 text-blue-700",
    slate: "border-slate-200 bg-slate-50 text-slate-700",
  };
  return (
    <span
      className={clsx(
        "inline-flex max-w-full items-center justify-center rounded-full border px-2.5 py-1 text-center text-xs font-semibold leading-5 break-words",
        toneMap[tone] || toneMap.slate,
      )}
    >
      {label}
    </span>
  );
}

function SectionTabs({ items, active, onChange, compact = false }) {
  return (
    <div
      className={clsx(
        "flex gap-2 overflow-x-auto",
        compact ? "pb-1 text-xs" : "pb-1 text-sm",
      )}
    >
      {items.map((item) => (
        <button
          key={item}
          type="button"
          onClick={() => onChange(item)}
          className={clsx(
            "whitespace-nowrap rounded-full border px-3 py-1.5 font-medium transition",
            active === item
              ? "border-blue-200 bg-blue-50 text-blue-700"
              : "border-transparent bg-transparent text-slate-500 hover:border-slate-200 hover:bg-slate-50 hover:text-slate-800",
          )}
        >
          {item}
        </button>
      ))}
    </div>
  );
}

function InfoCard({ title, subtitle, children, action }) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="break-words text-sm font-semibold text-slate-950">
            {title}
          </h3>
          {subtitle ? (
            <p className="mt-1 break-words text-xs text-slate-500">
              {subtitle}
            </p>
          ) : null}
        </div>
        <div className="shrink-0">{action}</div>
      </div>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function DetailRow({ label, value, emphasize = false }) {
  return (
    <div className="grid grid-cols-[minmax(0,120px)_minmax(0,1fr)] gap-3 border-b border-slate-100 py-3 text-sm last:border-b-0">
      <span className="min-w-0 break-words text-slate-500">{label}</span>
      <span
        className={clsx(
          "min-w-0 break-words text-slate-700",
          emphasize && "font-semibold text-slate-950",
        )}
      >
        {value || "-"}
      </span>
    </div>
  );
}

function EvidenceItem({ item }) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-slate-200 bg-slate-50/70 px-3 py-2.5 text-sm text-slate-700">
      <span
        className={clsx(
          "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border",
          item.ok
            ? "border-emerald-200 bg-emerald-50 text-emerald-600"
            : "border-red-200 bg-red-50 text-red-600",
        )}
      >
        {item.ok ? (
          <Check className="h-3.5 w-3.5" />
        ) : (
          <X className="h-3.5 w-3.5" />
        )}
      </span>
      <span className="min-w-0 break-words">{item.text}</span>
    </div>
  );
}

function TimelineItem({ item, last }) {
  const toneMap = {
    blue: "bg-blue-50 text-blue-700",
    green: "bg-emerald-50 text-emerald-700",
    purple: "bg-violet-50 text-violet-700",
  };
  const Icon = item.system ? Bell : MessageSquare;
  return (
    <div className="grid grid-cols-[88px_26px_1fr] gap-3 text-sm">
      <div className="pt-0.5 text-right text-xs text-slate-500">
        {formatClock(item.timestamp)}
      </div>
      <div className="relative flex justify-center">
        {!last ? (
          <span className="absolute top-5 h-[calc(100%+1rem)] w-px bg-slate-200" />
        ) : null}
        <span
          className={clsx(
            "relative z-10 flex h-6 w-6 items-center justify-center rounded-full",
            toneMap[item.tone] || toneMap.blue,
          )}
        >
          <Icon className="h-3.5 w-3.5" />
        </span>
      </div>
      <div className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 shadow-sm">
        <p className="break-words font-medium text-slate-900">{item.label}</p>
        <p className="mt-1 break-words text-slate-600">{item.detail}</p>
      </div>
    </div>
  );
}

function ContextNode({ node, wide = false }) {
  const toneMap = {
    slate: "border-slate-200 bg-white",
    green: "border-emerald-200 bg-emerald-50",
    amber: "border-amber-200 bg-amber-50",
    red: "border-red-200 bg-red-50",
  };
  const statusMap = {
    green: "text-emerald-700",
    amber: "text-amber-700",
    red: "text-red-700",
    slate: "text-slate-500",
  };
  const Icon = node.icon;
  return (
    <div
      className={clsx(
        "rounded-xl border px-3 py-3 shadow-sm",
        toneMap[node.tone] || toneMap.slate,
        wide ? "col-span-2" : "",
      )}
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5 rounded-lg bg-white/80 p-2 text-slate-700 shadow-sm">
          <Icon className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <p className="break-words text-sm font-semibold text-slate-900">
            {node.title}
          </p>
          <p className="mt-1 break-words text-xs text-slate-500">
            {node.detail}
          </p>
          {node.status ? (
            <p
              className={clsx(
                "mt-2 break-words text-xs font-semibold",
                statusMap[node.tone] || statusMap.slate,
              )}
            >
              {node.status}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ChecklistItem({ checked, onToggle, title, detail, compact = false }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={clsx(
        "flex w-full items-start gap-3 rounded-xl border border-slate-200 text-left transition",
        compact ? "px-3 py-2.5" : "px-3 py-3",
        checked ? "bg-emerald-50" : "bg-white hover:bg-slate-50",
      )}
    >
      <span
        className={clsx(
          "mt-0.5 flex h-5 w-5 items-center justify-center rounded-full border",
          checked
            ? "border-emerald-300 bg-emerald-500 text-white"
            : "border-slate-300 text-slate-300",
        )}
      >
        {checked ? (
          <Check className="h-3.5 w-3.5" />
        ) : (
          <Circle className="h-3.5 w-3.5" />
        )}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block break-words text-sm font-medium text-slate-900">
          {title}
        </span>
        {detail ? (
          <span className="mt-1 block break-words text-xs text-slate-500">
            {detail}
          </span>
        ) : null}
      </span>
    </button>
  );
}

function Drawer({ title, subtitle, onClose, children }) {
  return (
    <div className="absolute inset-0 z-30 flex justify-end bg-slate-950/20 backdrop-blur-[1px]">
      <div className="flex h-full w-full max-w-[420px] flex-col border-l border-slate-200 bg-white shadow-2xl">
        <div className="flex items-start justify-between gap-3 border-b border-slate-200 px-5 py-4">
          <div>
            <h3 className="text-base font-semibold text-slate-950">{title}</h3>
            {subtitle ? (
              <p className="mt-1 text-sm text-slate-500">{subtitle}</p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-2 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-5">{children}</div>
      </div>
    </div>
  );
}

function EmptyPanel({ icon: Icon, title, detail, action }) {
  return (
    <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center">
      <Icon className="mx-auto h-9 w-9 text-slate-400" />
      <h3 className="mt-4 text-base font-semibold text-slate-900">{title}</h3>
      <p className="mt-2 text-sm text-slate-500">{detail}</p>
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}

export default function AgentDashboard() {
  const navigate = useNavigate();
  const { currentUser, currentUserEmail } = useStore();
  const [workflow, setWorkflow] = useState(null);
  const [ticket, setTicket] = useState(null);
  const [graphCases, setGraphCases] = useState([]);
  const [graphCasesLoading, setGraphCasesLoading] = useState(false);
  const [graphCasesError, setGraphCasesError] = useState("");
  const [selectedGraphCaseId, setSelectedGraphCaseId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [activeNav, setActiveNav] = useState("Cases");
  const [activeRail, setActiveRail] = useState("my_cases");
  const [mainTab, setMainTab] = useState("Overview");
  const [assistantTab, setAssistantTab] = useState("Diagnosis");
  const [contextTab, setContextTab] = useState("Topology View");
  const [assistantCollapsed, setAssistantCollapsed] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [moreOpen, setMoreOpen] = useState(false);
  const [showSystemEvents, setShowSystemEvents] = useState(true);
  const [followed, setFollowed] = useState(false);

  const [statusOverride, setStatusOverride] = useState("");
  const [shortDescriptionOverride, setShortDescriptionOverride] = useState("");
  const [assignedToOverride, setAssignedToOverride] = useState("");
  const [contactOverride, setContactOverride] = useState("");
  const [localNotes, setLocalNotes] = useState([]);
  const [localEvents, setLocalEvents] = useState([]);
  const [resolvedLocally, setResolvedLocally] = useState(false);
  const [escalationRecord, setEscalationRecord] = useState(null);

  const [drawer, setDrawer] = useState(null);
  const [statusDraft, setStatusDraft] = useState("In Progress");
  const [workNoteDraft, setWorkNoteDraft] = useState("");
  const [resolveDraft, setResolveDraft] = useState({
    closeCode: DEFAULT_CLOSE_CODE,
    closeNotes: "",
    resolutionNotes: "",
    rootCause: "",
    workaround: "",
  });
  const [editDraft, setEditDraft] = useState({
    shortDescription: "",
    assignedTo: "",
    contact: "",
  });
  const [problemDraft, setProblemDraft] = useState("");
  const [escalationDraft, setEscalationDraft] = useState({
    team: "Platform Support",
    reason: "",
  });
  const [assistantQuery, setAssistantQuery] = useState("");
  const [assistantQueryAnswer, setAssistantQueryAnswer] = useState("");
  const [assistantQueryCitations, setAssistantQueryCitations] = useState([]);
  const [assistantQueryHistory, setAssistantQueryHistory] = useState([]);
  const [assistantQueryLoading, setAssistantQueryLoading] = useState(false);
  const [taskChecks, setTaskChecks] = useState({});
  const [savingAction, setSavingAction] = useState(false);

  useEffect(() => {
    let active = true;
    const sessionId =
      localStorage.getItem(LAST_CASE_DASHBOARD_SESSION_KEY) ||
      localStorage.getItem(LAST_CASE_SESSION_KEY);

    if (!sessionId) {
      setLoading(false);
      setError("No completed case is available for the agent dashboard.");
      return () => {
        active = false;
      };
    }

    setGraphCasesLoading(true);
    getGraphRAGCase(sessionId)
      .then(async (state) => {
        if (!active) return;
        setWorkflow(state);
        const createdTicket = state.created_ticket;
        if (createdTicket?.sys_id) {
          try {
            const ticketData = await getTicket(
              createdTicket.ticket_type || "INC",
              createdTicket.sys_id,
            );
            if (active) setTicket(ticketData);
          } catch {
            if (active) setTicket(createdTicket);
          }
        }
      })
      .catch(() => {
        if (active) setError("Could not load the completed case workflow.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    listGraphCases({ limit: 250 })
      .then((records) => {
        if (!active) return;
        const normalized = (records || []).map(normalizeGraphCase);
        setGraphCases(normalized);
        if (!selectedGraphCaseId && normalized.length) {
          setSelectedGraphCaseId(normalized[0].id);
        }
        setGraphCasesError("");
      })
      .catch(() => {
        if (active) {
          setGraphCasesError("Could not load graph case directory.");
        }
      })
      .finally(() => {
        if (active) setGraphCasesLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!workflow) return;
    setEditDraft((current) => ({
      shortDescription:
        current.shortDescription ||
        workflow?.created_ticket?.short_description ||
        workflow?.initial_query ||
        "",
      assignedTo:
        current.assignedTo ||
        workflow?.created_ticket?.assigned_to ||
        "Support Intelligence Queue",
      contact:
        current.contact ||
        workflow?.case_summary?.user_email ||
        currentUser?.name ||
        "",
    }));
    setProblemDraft(
      (current) =>
        current ||
        `Investigate recurring pattern for ${workflow.case_number || workflow.created_ticket?.number || "current case"}.`,
    );
  }, [workflow, currentUser?.name]);

  const baseModel = useMemo(
    () =>
      buildDashboardModel({
        workflow,
        ticket,
        currentUser: currentUser || {
          name: currentUserEmail || "Support Engineer",
        },
        statusOverride,
        shortDescriptionOverride,
        assignedToOverride,
        contactOverride,
        localEvents,
        localNotes,
        escalationRecord,
        resolvedLocally,
      }),
    [
      workflow,
      ticket,
      currentUser,
      currentUserEmail,
      statusOverride,
      shortDescriptionOverride,
      assignedToOverride,
      contactOverride,
      localEvents,
      localNotes,
      escalationRecord,
      resolvedLocally,
    ],
  );

  const model = useMemo(() => {
    const enriched = {
      ...baseModel,
      topology: buildTopology(baseModel),
      attachments: buildAttachments(baseModel),
    };
    return enriched;
  }, [baseModel]);

  const matchesSearch = useMemo(
    () => getSearchMatcher(searchText),
    [searchText],
  );

  const filteredTimeline = useMemo(
    () =>
      model.timeline.filter(
        (item) =>
          (showSystemEvents || !item.system) &&
          matchesSearch(item.label, item.detail, item.timestamp),
      ),
    [model.timeline, matchesSearch, showSystemEvents],
  );

  const filteredEvidence = useMemo(
    () => model.evidence.filter((item) => matchesSearch(item.text)),
    [model.evidence, matchesSearch],
  );

  const filteredSteps = useMemo(
    () => model.steps.filter((step) => matchesSearch(step)),
    [model.steps, matchesSearch],
  );

  const filteredMissingInfo = useMemo(
    () => model.missingInfo.filter((item) => matchesSearch(item)),
    [model.missingInfo, matchesSearch],
  );

  const filteredRelatedCases = useMemo(
    () =>
      model.relatedCases.filter((item) =>
        matchesSearch(item.number, item.title, item.summary),
      ),
    [model.relatedCases, matchesSearch],
  );

  const filteredAttachments = useMemo(
    () =>
      model.attachments.filter((item) =>
        matchesSearch(item.label, item.detail),
      ),
    [model.attachments, matchesSearch],
  );

  const filteredRunbooks = useMemo(
    () =>
      model.runbooks.filter((item) => matchesSearch(item.title, item.detail)),
    [model.runbooks, matchesSearch],
  );

  const filteredAlerts = useMemo(
    () =>
      model.alerts.filter((item) => matchesSearch(item.text, item.severity)),
    [model.alerts, matchesSearch],
  );

  const visibleGraphCases = useMemo(() => {
    const filtered = graphCases.filter((graphCase) => {
      if (!graphCaseMatchesRail(graphCase, activeRail, currentUser)) {
        return false;
      }
      return matchesSearch(
        graphCase.number,
        graphCase.shortDescription,
        graphCase.description,
        graphCase.stateLabel,
        graphCase.assignedTo,
        graphCase.contact,
        graphCase.requestedBy,
        graphCase.typeLabel,
        graphCase.category,
        graphCase.subcategory,
      );
    });
    return filtered;
  }, [graphCases, activeRail, currentUser, matchesSearch]);

  const selectedGraphCase = useMemo(() => {
    if (!visibleGraphCases.length) return null;
    return (
      visibleGraphCases.find((item) => item.id === selectedGraphCaseId) ||
      visibleGraphCases[0]
    );
  }, [visibleGraphCases, selectedGraphCaseId]);

  const graphCaseCounts = useMemo(
    () => ({
      total: graphCases.length,
      open: graphCases.filter(
        (item) =>
          !/(resolved|closed|completed|cancelled)/i.test(
            String(
              firstNonEmpty(
                item.incident_state_display,
                item.stateLabel,
                item.state,
              ),
            ),
          ),
      ).length,
      pending: graphCases.filter((item) =>
        /(pending|awaiting|waiting)/i.test(
          String(
            firstNonEmpty(
              item.incident_state_display,
              item.stateLabel,
              item.state,
            ),
          ),
        ),
      ).length,
      resolved: graphCases.filter((item) =>
        /(resolved|closed|completed)/i.test(
          String(
            firstNonEmpty(
              item.incident_state_display,
              item.stateLabel,
              item.state,
            ),
          ),
        ),
      ).length,
    }),
    [graphCases],
  );

  const activeRailLabel = useMemo(
    () =>
      RAIL_SECTIONS.flatMap((section) => section.items).find(
        (item) => item.key === activeRail,
      )?.label || "Case Lens",
    [activeRail],
  );

  const railSections = useMemo(
    () =>
      RAIL_SECTIONS.map((section) => ({
        ...section,
        items: section.items.map((item) => ({
          ...item,
          count:
            item.key === "all_cases"
              ? graphCaseCounts.total
              : item.key === "open_cases"
                ? graphCaseCounts.open
                : item.key === "pending_customer"
                  ? graphCaseCounts.pending
                  : item.key === "resolved"
                    ? graphCaseCounts.resolved
                    : item.key === "my_cases"
                      ? workflow
                        ? 1
                        : 0
                      : item.count,
        })),
      })),
    [graphCaseCounts, workflow],
  );

  const showGraphDirectory = [
    "all_cases",
    "open_cases",
    "pending_customer",
    "resolved",
  ].includes(activeRail);

  function addLocalEvent(label, detail, tone = "blue") {
    setLocalEvents((current) => [
      ...current,
      {
        id: `local-${Date.now()}-${current.length}`,
        label,
        detail,
        timestamp: new Date().toISOString(),
        tone,
        system: false,
      },
    ]);
  }

  function handleRailSelect(item) {
    setActiveRail(item.key);
    if (item.mainTab) setMainTab(item.mainTab);
    if (item.assistantTab) setAssistantTab(item.assistantTab);
    if (item.contextTab) setContextTab(item.contextTab);
  }

  function openDrawer(type) {
    setDrawer(type);
    setMoreOpen(false);
  }

  function currentTicketTarget() {
    return {
      ticketType:
        ticket?.ticket_type || workflow?.created_ticket?.ticket_type || "INC",
      ticketSysId: ticket?.sys_id || workflow?.created_ticket?.sys_id || "",
    };
  }

  function prefillResolveDraft() {
    const suggestedPlan = filteredSteps.slice(0, 4).join("\n");
    setResolveDraft((current) => ({
      closeCode: current.closeCode || DEFAULT_CLOSE_CODE,
      closeNotes: current.closeNotes,
      resolutionNotes: current.resolutionNotes || suggestedPlan,
      rootCause: current.rootCause || model.causeSummary || "",
      workaround: current.workaround || filteredSteps[0] || "",
    }));
  }

  function openResolveDrawer() {
    setStatusDraft("Resolved");
    prefillResolveDraft();
    openDrawer("status");
  }

  function buildIncidentUpdatePayload(stateValue) {
    if (stateValue !== INCIDENT_STATUS_VALUES.Resolved) {
      return { incident_state: stateValue };
    }

    const closeCode = resolveDraft.closeCode.trim();
    const closeNotes = resolveDraft.closeNotes.trim();
    const resolutionNotes = resolveDraft.resolutionNotes.trim();
    const rootCause = resolveDraft.rootCause.trim();
    const workaround = resolveDraft.workaround.trim();

    if (!closeCode) {
      toast.error("Resolution code is required to resolve the incident.");
      return null;
    }
    if (!closeNotes) {
      toast.error("Close notes are required to resolve the incident.");
      return null;
    }

    return {
      incident_state: stateValue,
      close_code: closeCode,
      close_notes: closeNotes,
      resolution_notes: resolutionNotes || closeNotes,
      root_cause: rootCause,
      workaround,
    };
  }

  async function handleCopyQuestions() {
    if (!filteredMissingInfo.length) {
      toast.error("There are no customer questions to copy right now.");
      return;
    }
    const text = filteredMissingInfo
      .map((item, index) => `${index + 1}. ${item}`)
      .join("\n");
    await copyText(text, "Customer questions copied.");
  }

  async function handleAttachmentAction(item) {
    if (item.action === "copy_summary") {
      await copyText(
        `${model.caseNumber}\n${model.shortDescription}\nLikely cause: ${model.causeSummary}`,
        "Case summary copied.",
      );
      return;
    }
    if (item.action === "copy_resolution") {
      await copyText(
        filteredSteps.map((step, index) => `${index + 1}. ${step}`).join("\n"),
        "Resolution plan copied.",
      );
      return;
    }
    await copyText(
      model.answers
        .map(
          (answer) =>
            `${answer.question_text || answer.question_id}: ${answer.answer_label || answer.answer_value || "Skipped"}`,
        )
        .join("\n"),
      "Workflow snapshot copied.",
    );
  }

  async function submitAssistantQuery() {
    const query = assistantQuery.trim();
    if (!query || assistantQueryLoading) return;

    setAssistantQueryLoading(true);
    try {
      const result = await queryGraphRAG(query, {
        userSysId: currentUser?.sys_id,
        userEmail: currentUserEmail,
        sessionId: workflow?.session_id || ticket?.sys_id || "",
      });

      const citations = result?.citations || [];
      const answer =
        result?.answer ||
        "No matching data found in the graph for this question.";
      setAssistantQueryAnswer(answer);
      setAssistantQueryCitations(citations);
      setAssistantQueryHistory((current) => {
        const next = [
          {
            id: `query-${Date.now()}`,
            query,
            answer,
            citations,
          },
          ...current.filter((entry) => entry.query !== query),
        ];
        return next.slice(0, 5);
      });
      addLocalEvent("Graph Query Ran", query, "purple");
    } finally {
      setAssistantQueryLoading(false);
    }
  }

  async function applyStatusUpdate() {
    const { ticketType, ticketSysId } = currentTicketTarget();
    if (ticketType !== "INC" || !ticketSysId) {
      toast.error("A synced incident is required before updating status.");
      return;
    }
    const stateValue = INCIDENT_STATUS_VALUES[statusDraft];
    if (!stateValue) {
      toast.error("Unsupported incident status.");
      return;
    }
    const payload = buildIncidentUpdatePayload(stateValue);
    if (!payload) return;
    setSavingAction(true);
    try {
      const updated = await updateTicket(ticketType, ticketSysId, payload);
      setTicket(updated);
      setStatusOverride("");
      setResolvedLocally(statusDraft === "Resolved");
      addLocalEvent(
        "Status Updated",
        statusDraft === "Resolved"
          ? `Incident resolved in ServiceNow with close code ${payload.close_code}.`
          : `Case status changed to ${statusDraft} in ServiceNow.`,
        statusDraft === "Resolved" ? "green" : "blue",
      );
      setDrawer(null);
      toast.success(
        statusDraft === "Resolved"
          ? "Incident resolved in ServiceNow and synced to the graph."
          : "Status updated in ServiceNow and synced to the graph.",
      );
    } finally {
      setSavingAction(false);
    }
  }

  async function applyWorkNote() {
    const note = workNoteDraft.trim();
    if (!note) return;
    const { ticketType, ticketSysId } = currentTicketTarget();
    if (!ticketSysId) {
      toast.error("A synced ticket is required before adding a work note.");
      return;
    }
    setSavingAction(true);
    try {
      await addComment(
        ticketSysId,
        {
          ticket_sys_id: ticketSysId,
          ticket_table: ticketType,
          comment: note,
          is_work_note: true,
          user_name:
            currentUser?.name || currentUserEmail || "Support Engineer",
        },
        "",
      );
      setLocalNotes((current) => [...current, note]);
      addLocalEvent("Work Note Added", `${note} (saved to ServiceNow)`, "blue");
      setWorkNoteDraft("");
      setDrawer(null);
      toast.success("Work note added to ServiceNow and synced to the graph.");
    } finally {
      setSavingAction(false);
    }
  }

  function applyEdit() {
    setShortDescriptionOverride(editDraft.shortDescription.trim());
    setAssignedToOverride(editDraft.assignedTo.trim());
    setContactOverride(editDraft.contact.trim());
    addLocalEvent(
      "Case Details Updated",
      `Header details were refined for ${model.caseNumber}.`,
      "blue",
    );
    setDrawer(null);
    toast.success("Case details updated in the dashboard view.");
  }

  function applyEscalation() {
    const reason = escalationDraft.reason.trim();
    if (!reason) return;
    const record = {
      team: escalationDraft.team,
      reason,
      timestamp: new Date().toISOString(),
    };
    setEscalationRecord(record);
    addLocalEvent(
      "Escalation Prepared",
      `${record.team}: ${record.reason}`,
      "purple",
    );
    setDrawer(null);
    toast.success("Escalation draft prepared.");
  }

  function applyProblemDraft() {
    const draft = problemDraft.trim();
    if (!draft) return;
    addLocalEvent("Problem Record Drafted", draft, "purple");
    setDrawer(null);
    toast.success("Problem draft generated for review.");
  }

  function openCaseChat() {
    const ticketType =
      ticket?.ticket_type || workflow?.created_ticket?.ticket_type;
    const ticketSysId = ticket?.sys_id || workflow?.created_ticket?.sys_id;
    const ticketNumber = ticket?.number || workflow?.created_ticket?.number;
    if (!ticketType || !ticketSysId) {
      toast.error("No synced ticket is available for View & Chat yet.");
      return;
    }
    setLastViewChatTarget({
      ticketType,
      sysId: ticketSysId,
      sessionId: workflow?.session_id || "",
      number: ticketNumber || "",
    });
    navigate(`/ticket/${ticketType}/${ticketSysId}`, {
      state: { sessionId: workflow?.session_id || "" },
    });
  }

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-slate-50 text-slate-600">
        <Loader2 className="mr-2 h-5 w-5 animate-spin text-blue-700" />
        Loading agent dashboard...
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-screen items-center justify-center bg-slate-50 px-6">
        <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 text-center shadow-sm">
          <AlertTriangle className="mx-auto h-10 w-10 text-amber-500" />
          <h1 className="mt-4 text-lg font-semibold text-slate-900">
            Agent Dashboard Unavailable
          </h1>
          <p className="mt-2 text-sm leading-6 text-slate-600">{error}</p>
          <div className="mt-5 flex justify-center gap-3">
            <button
              type="button"
              onClick={() => navigate("/create-case")}
              className="btn-primary justify-center"
            >
              Create Case
            </button>
            <button
              type="button"
              onClick={() => navigate("/dashboard")}
              className="btn-secondary"
            >
              View Tickets
            </button>
          </div>
        </div>
      </div>
    );
  }

  const summaryItems = [
    { label: "Account", value: model.account, icon: Home },
    { label: "Contact", value: model.contact, icon: UserRound },
    { label: "Source", value: model.source, icon: ExternalLink },
    { label: "Assigned To", value: model.assignedTo, icon: BriefcaseBusiness },
    { label: "Priority", value: model.priorityLabel, icon: ShieldAlert },
    { label: "SLA", value: model.slaLabel, icon: Clock3 },
  ];

  const detailsRows = [
    ["Case Number", model.caseNumber],
    ["Matched Issue", model.issueMatch],
    ["Likely Cause", model.causeSummary],
    ["Created", model.createdLabel],
    ["Product Area", model.productArea],
    ["Assigned To", model.assignedTo],
    ["Source", model.source],
    ["Search Filter", searchText || "Not applied"],
  ];

  return (
    <div className="relative flex h-screen overflow-hidden bg-[#f8fafc] text-slate-900">
      <aside className="flex w-[232px] shrink-0 flex-col bg-[#061f3d] text-white">
        <div className="flex h-[62px] items-center gap-3 border-b border-white/10 px-4">
          <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-white/10 shadow-sm">
            <Headphones className="h-5 w-5 text-blue-100" />
          </div>
          <div>
            <p className="text-sm font-semibold leading-tight">
              Support Intelligence
            </p>
            <p className="text-[11px] text-blue-100/75"></p>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto px-3 py-4">
          {railSections.map((section) => (
            <div key={section.title} className="mb-5">
              <p className="px-2 pb-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-blue-100/55">
                {section.title}
              </p>
              <div className="space-y-1.5">
                {section.items.map((item) => (
                  <RailButton
                    key={item.key}
                    icon={item.icon}
                    label={item.label}
                    count={item.count}
                    active={activeRail === item.key}
                    onClick={() => handleRailSelect(item)}
                  />
                ))}
              </div>
            </div>
          ))}
        </nav>

        <div className="border-t border-white/10 px-4 py-4 text-xs text-blue-100/75">
          <p className="font-medium text-white">Current lens</p>
          <p className="mt-1">
            {
              railSections
                .flatMap((section) => section.items)
                .find((item) => item.key === activeRail)?.label
            }
          </p>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-[62px] shrink-0 items-center justify-between border-b border-slate-200 bg-[#061a34] px-6 text-white">
          <div className="flex h-full items-center gap-5 text-sm">
            {TOP_NAV_ITEMS.map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => setActiveNav(item)}
                className={clsx(
                  "relative h-full px-1 font-medium text-blue-50/80 transition",
                  activeNav === item && "text-white",
                )}
              >
                {item}
                {activeNav === item ? (
                  <span className="absolute bottom-0 left-0 h-[2px] w-full bg-blue-400" />
                ) : null}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-4">
            <div className="flex h-10 w-[300px] items-center gap-2 rounded-xl border border-white/10 bg-white/10 px-3 text-sm text-blue-100">
              <Search className="h-4 w-4" />
              <input
                value={searchText}
                onChange={(event) => setSearchText(event.target.value)}
                placeholder="Filter visible content..."
                className="w-full bg-transparent text-white placeholder:text-blue-100/60 focus:outline-none"
              />
            </div>
            <button
              type="button"
              className="relative rounded-lg p-2 text-blue-100 transition hover:bg-white/10"
              onClick={() =>
                toast.success(
                  "Notification center is client-side in this dashboard pass.",
                )
              }
            >
              <Bell className="h-5 w-5" />
              <span className="absolute -right-1 -top-1 rounded-full bg-red-500 px-1.5 text-[10px] font-bold text-white">
                {filteredAlerts.length || 1}
              </span>
            </button>
            <div className="flex items-center gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-full bg-slate-500 text-xs font-semibold">
                {initials(
                  currentUser?.name || currentUserEmail || model.contact,
                )}
              </div>
              <div className="text-xs leading-4">
                <p className="font-semibold text-white">
                  {currentUser?.name || model.contact}
                </p>
                <p className="text-blue-100/75">
                  {currentUser?.department || "Support Engineer"}
                </p>
              </div>
            </div>
          </div>
        </header>

        <div className="flex min-h-0 flex-1 overflow-hidden">
          <main className="min-w-0 flex-1 overflow-y-auto border-r border-slate-200 bg-[#f8fafc]">
            <section className="border-b border-slate-200 bg-white px-6 pb-5 pt-5">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-3 text-xs text-slate-500">
                    <button
                      type="button"
                      onClick={() => navigate("/create-case")}
                      className="inline-flex items-center gap-1 text-slate-500 transition hover:text-blue-700"
                    >
                      <ChevronLeft className="h-3.5 w-3.5" />
                      Case
                    </button>
                    <span>/</span>
                    <span className="break-words">{model.caseNumber}</span>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-3">
                    <h1 className="break-words text-2xl font-semibold tracking-tight text-slate-950">
                      {model.caseNumber}
                    </h1>
                    <StatusPill
                      label={model.severity.label}
                      tone={model.severity.tone}
                    />
                    <span className="text-slate-300">•</span>
                    <span className="break-words text-sm font-medium text-slate-700">
                      {model.status.label}
                    </span>
                    <span className="text-slate-300">•</span>
                    <span className="break-words text-sm text-slate-500">
                      {model.openedAgo}
                    </span>
                  </div>
                  <h2 className="mt-3 max-w-4xl break-words text-xl font-semibold text-slate-950">
                    {model.shortDescription}
                  </h2>
                  <p className="mt-2 text-sm text-slate-500">
                    Case-driven layout using workflow, ticket metadata, and
                    graph-backed diagnosis context.
                  </p>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setFollowed((current) => !current);
                      toast.success(
                        followed ? "Case unfollowed." : "Case followed.",
                      );
                    }}
                    className={clsx(
                      "inline-flex h-10 items-center gap-2 rounded-xl border px-4 text-sm font-medium transition",
                      followed
                        ? "border-blue-200 bg-blue-50 text-blue-700"
                        : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50",
                    )}
                  >
                    <Star
                      className={clsx("h-4 w-4", followed && "fill-current")}
                    />
                    {followed ? "Following" : "Follow"}
                  </button>
                  <button
                    type="button"
                    onClick={() => openDrawer("edit")}
                    className="inline-flex h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
                  >
                    <Edit3 className="h-4 w-4" />
                    Edit
                  </button>
                  <div className="relative">
                    <button
                      type="button"
                      onClick={() => setMoreOpen((current) => !current)}
                      className="inline-flex h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
                    >
                      More
                      <ChevronDown className="h-4 w-4" />
                    </button>
                    {moreOpen ? (
                      <div className="absolute right-0 z-20 mt-2 w-56 rounded-2xl border border-slate-200 bg-white p-2 shadow-xl">
                        {[
                          {
                            label: "Open ticket detail",
                            action: openCaseChat,
                          },
                          {
                            label: "Copy case summary",
                            action: () =>
                              copyText(
                                `${model.caseNumber}\n${model.shortDescription}\n${model.causeSummary}`,
                                "Case summary copied.",
                              ),
                          },
                          {
                            label: "Reset local edits",
                            action: () => {
                              setStatusOverride("");
                              setShortDescriptionOverride("");
                              setAssignedToOverride("");
                              setContactOverride("");
                              setResolvedLocally(false);
                              setEscalationRecord(null);
                              setLocalNotes([]);
                              setLocalEvents([]);
                              toast.success("Local dashboard changes reset.");
                            },
                          },
                        ].map((item) => (
                          <button
                            key={item.label}
                            type="button"
                            onClick={() => {
                              item.action();
                              setMoreOpen(false);
                            }}
                            className="flex w-full items-center rounded-xl px-3 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-50"
                          >
                            {item.label}
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </div>
              </div>

              <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-6">
                {summaryItems.map((item) => {
                  const Icon = item.icon;
                  return (
                    <div
                      key={item.label}
                      className="rounded-2xl border border-slate-200 bg-slate-50/70 px-4 py-3"
                    >
                      <p className="break-words text-xs text-slate-500">
                        {item.label}
                      </p>
                      <p className="mt-2 flex items-start gap-2 text-sm font-medium text-slate-900">
                        <Icon className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" />
                        <span className="min-w-0 break-words">
                          {item.value}
                        </span>
                      </p>
                    </div>
                  );
                })}
              </div>

              <div className="mt-5">
                <SectionTabs
                  items={MAIN_TABS}
                  active={mainTab}
                  onChange={setMainTab}
                />
              </div>
            </section>

            <section className="relative p-5">
              {mainTab === "Overview" ? (
                <div className="grid gap-4 xl:grid-cols-[300px_minmax(0,1fr)]">
                  <div className="space-y-4">
                    <InfoCard
                      title="Case Information"
                      subtitle="Normalized from workflow and ticket data"
                    >
                      <DetailRow
                        label="Case Number"
                        value={model.caseNumber}
                        emphasize
                      />
                      <DetailRow
                        label="Matched Issue"
                        value={model.issueMatch}
                      />
                      <DetailRow
                        label="Likely Cause"
                        value={model.causeSummary}
                      />
                      <DetailRow label="Created" value={model.createdLabel} />
                      <DetailRow label="Contact" value={model.contact} />
                      <DetailRow label="Assigned" value={model.assignedTo} />
                    </InfoCard>

                    <InfoCard
                      title="Affected Environment"
                      subtitle={`${model.applianceName} in ${model.environmentName}`}
                      action={
                        <StatusPill
                          label={model.health.label}
                          tone={model.health.tone}
                        />
                      }
                    >
                      <div className="flex gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-3 py-3">
                        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-white shadow-sm">
                          <Server className="h-6 w-6 text-slate-600" />
                        </div>
                        <div className="min-w-0">
                          <p className="font-medium text-slate-950">
                            {model.applianceName}
                          </p>
                          <p className="mt-1 text-sm text-slate-500">
                            {model.productArea}
                          </p>
                          <p className="mt-3 text-sm text-slate-600">
                            Last analyzed{" "}
                            <span className="font-medium text-slate-900">
                              {formatRelative(model.lastSeen)}
                            </span>
                          </p>
                          <button
                            type="button"
                            onClick={() => setContextTab("Topology View")}
                            className="mt-4 inline-flex items-center gap-2 text-sm font-medium text-blue-700"
                          >
                            Inspect environment view
                            <ChevronRight className="h-4 w-4" />
                          </button>
                        </div>
                      </div>
                    </InfoCard>

                    <InfoCard
                      title="Diagnostic Progress"
                      subtitle="Interactive progress based on captured steps"
                    >
                      <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4">
                        <div className="flex items-center justify-between">
                          <div>
                            <p className="text-sm font-medium text-slate-950">
                              {model.answerSummary}
                            </p>
                            <p className="mt-1 text-xs text-slate-500">
                              Confidence {model.confidence}% with{" "}
                              {filteredAlerts.length} active alerts
                            </p>
                          </div>
                          <Sparkles className="h-5 w-5 text-violet-600" />
                        </div>
                        <div className="mt-4 h-2 rounded-full bg-slate-200">
                          <div
                            className="h-2 rounded-full bg-blue-600"
                            style={{
                              width: `${Math.min(
                                100,
                                Math.max(
                                  20,
                                  resolvedLocally ? 100 : model.confidence,
                                ),
                              )}%`,
                            }}
                          />
                        </div>
                      </div>
                    </InfoCard>
                  </div>

                  <div className="space-y-4">
                    <InfoCard
                      title="Appliance and Environment Context"
                      subtitle="Switch between topology, health, changes, and alert views"
                      action={
                        <button
                          type="button"
                          onClick={() =>
                            toast.success(
                              "Context refresh is visual-only in this pass.",
                            )
                          }
                          className="inline-flex items-center gap-2 text-xs font-medium text-slate-500 transition hover:text-blue-700"
                        >
                          <RefreshCw className="h-3.5 w-3.5" />
                          Refresh
                        </button>
                      }
                    >
                      <SectionTabs
                        items={CONTEXT_TABS}
                        active={contextTab}
                        onChange={setContextTab}
                        compact
                      />

                      {contextTab === "Topology View" ? (
                        <div className="mt-4">
                          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                            {model.topology
                              .filter((node) =>
                                matchesSearch(
                                  node.title,
                                  node.detail,
                                  node.status,
                                ),
                              )
                              .map((node) => (
                                <ContextNode
                                  key={node.key}
                                  node={node}
                                  wide={node.wide}
                                />
                              ))}
                          </div>
                          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-4">
                            <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
                              <StatusPill label="Healthy" tone="emerald" />
                              <StatusPill label="Warning" tone="amber" />
                              <StatusPill label="Error" tone="red" />
                            </div>
                            <button
                              type="button"
                              onClick={() => {
                                setMainTab("Details");
                                toast.success(
                                  "Expanded into the detailed case view.",
                                );
                              }}
                              className="text-sm font-medium text-blue-700"
                            >
                              View full topology
                            </button>
                          </div>
                        </div>
                      ) : null}

                      {contextTab === "Health Signals" ? (
                        <div className="mt-4 grid gap-3 md:grid-cols-2">
                          {[
                            {
                              label: "Health",
                              value: model.health.label,
                              detail: `${filteredEvidence.length} evidence signals reviewed`,
                              icon: Gauge,
                            },
                            {
                              label: "Confidence",
                              value: `${model.confidence}%`,
                              detail: "Graph-backed cause confidence",
                              icon: Sparkles,
                            },
                            {
                              label: "Open Alerts",
                              value: String(filteredAlerts.length),
                              detail: "Search-aware alert list",
                              icon: AlertTriangle,
                            },
                            {
                              label: "Case Notes",
                              value: String(localNotes.length),
                              detail: "Locally captured work notes",
                              icon: FileText,
                            },
                          ]
                            .filter((card) =>
                              matchesSearch(
                                card.label,
                                card.value,
                                card.detail,
                              ),
                            )
                            .map((card) => {
                              const Icon = card.icon;
                              return (
                                <div
                                  key={card.label}
                                  className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4"
                                >
                                  <div className="flex items-center justify-between">
                                    <div>
                                      <p className="text-xs uppercase tracking-wide text-slate-500">
                                        {card.label}
                                      </p>
                                      <p className="mt-2 text-2xl font-semibold text-slate-950">
                                        {card.value}
                                      </p>
                                      <p className="mt-1 text-sm text-slate-500">
                                        {card.detail}
                                      </p>
                                    </div>
                                    <Icon className="h-5 w-5 text-slate-400" />
                                  </div>
                                </div>
                              );
                            })}
                        </div>
                      ) : null}

                      {contextTab === "Recent Changes" ? (
                        <div className="mt-4 space-y-3">
                          {filteredTimeline
                            .slice(-5)
                            .reverse()
                            .map((item) => (
                              <div
                                key={item.id}
                                className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3"
                              >
                                <p className="text-sm font-medium text-slate-900">
                                  {item.label}
                                </p>
                                <p className="mt-1 text-sm text-slate-600">
                                  {item.detail}
                                </p>
                                <p className="mt-2 text-xs text-slate-500">
                                  {formatDateTime(item.timestamp)}
                                </p>
                              </div>
                            ))}
                        </div>
                      ) : null}

                      {contextTab === "Alerts" ? (
                        <div className="mt-4 space-y-3">
                          {filteredAlerts.length ? (
                            filteredAlerts.map((item) => (
                              <div
                                key={item.id}
                                className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3"
                              >
                                <div className="flex items-center justify-between gap-3">
                                  <p className="text-sm font-medium text-red-800">
                                    {item.text}
                                  </p>
                                  <StatusPill
                                    label={item.severity}
                                    tone="red"
                                  />
                                </div>
                              </div>
                            ))
                          ) : (
                            <EmptyPanel
                              icon={CheckCircle2}
                              title="No matching alerts"
                              detail="The current search filter removed alert results, or the case has no active alert signals."
                            />
                          )}
                        </div>
                      ) : null}
                    </InfoCard>

                    <InfoCard
                      title="Case Timeline"
                      subtitle="Workflow events plus local dashboard actions"
                      action={
                        <label className="inline-flex items-center gap-2 text-xs text-slate-500">
                          <input
                            type="checkbox"
                            checked={showSystemEvents}
                            onChange={(event) =>
                              setShowSystemEvents(event.target.checked)
                            }
                            className="rounded border-slate-300"
                          />
                          Show system events
                        </label>
                      }
                    >
                      {filteredTimeline.length ? (
                        <div className="space-y-4">
                          {filteredTimeline.map((item, index) => (
                            <TimelineItem
                              key={item.id}
                              item={item}
                              last={index === filteredTimeline.length - 1}
                            />
                          ))}
                        </div>
                      ) : (
                        <EmptyPanel
                          icon={LayoutList}
                          title="Nothing matches the current filters"
                          detail="Try clearing the search field or re-enabling system events."
                        />
                      )}
                      <button
                        type="button"
                        onClick={() => setMainTab("Interactions")}
                        className="mt-4 inline-flex items-center gap-2 text-sm font-medium text-blue-700"
                      >
                        View all interactions
                        <ChevronRight className="h-4 w-4" />
                      </button>
                    </InfoCard>
                  </div>
                </div>
              ) : null}

              {mainTab === "Details" ? (
                <div className="space-y-4">
                  {showGraphDirectory ? (
                    <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
                      <InfoCard
                        title={`${activeRailLabel} Directory`}
                        subtitle="Fetched directly from the graph with ticket details"
                        action={
                          <div className="flex items-center gap-2">
                            <StatusPill
                              label={`${graphCaseCounts.total} total`}
                              tone="blue"
                            />
                            {graphCasesLoading ? (
                              <StatusPill label="Syncing" tone="amber" />
                            ) : null}
                          </div>
                        }
                      >
                        <div className="mb-4 grid gap-3 md:grid-cols-3">
                          {[
                            { label: "Open", value: graphCaseCounts.open },
                            {
                              label: "Pending",
                              value: graphCaseCounts.pending,
                            },
                            {
                              label: "Resolved",
                              value: graphCaseCounts.resolved,
                            },
                          ].map((item) => (
                            <div
                              key={item.label}
                              className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3"
                            >
                              <p className="text-xs uppercase tracking-wide text-slate-500">
                                {item.label}
                              </p>
                              <p className="mt-2 text-2xl font-semibold text-slate-950">
                                {item.value}
                              </p>
                            </div>
                          ))}
                        </div>

                        {graphCasesError ? (
                          <EmptyPanel
                            icon={AlertTriangle}
                            title="Graph cases unavailable"
                            detail={graphCasesError}
                          />
                        ) : visibleGraphCases.length ? (
                          <div className="max-h-[560px] space-y-3 overflow-y-auto pr-1">
                            {visibleGraphCases.map((item) => {
                              const selected =
                                selectedGraphCase?.id === item.id;
                              return (
                                <button
                                  key={item.id}
                                  type="button"
                                  onClick={() =>
                                    setSelectedGraphCaseId(item.id)
                                  }
                                  className={clsx(
                                    "w-full rounded-2xl border px-4 py-4 text-left shadow-sm transition",
                                    selected
                                      ? "border-blue-200 bg-blue-50/70"
                                      : "border-slate-200 bg-white hover:border-blue-200 hover:bg-blue-50/40",
                                  )}
                                >
                                  <div className="flex flex-wrap items-center justify-between gap-3">
                                    <div>
                                      <p className="text-sm font-semibold text-slate-950">
                                        {item.number || "Unnumbered case"}
                                      </p>
                                      <p className="mt-1 text-xs uppercase tracking-[0.14em] text-slate-500">
                                        {item.typeLabel}
                                      </p>
                                    </div>
                                    <StatusPill
                                      label={item.statusLabel}
                                      tone={item.statusTone}
                                    />
                                  </div>
                                  <p className="mt-3 text-sm font-medium text-slate-900">
                                    {item.shortDescription}
                                  </p>
                                  <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
                                    <span>{item.openedLabel}</span>
                                    <span>Assigned: {item.assignedTo}</span>
                                    <span>Contact: {item.contact}</span>
                                  </div>
                                </button>
                              );
                            })}
                          </div>
                        ) : (
                          <EmptyPanel
                            icon={Grid2X2}
                            title="No graph cases matched"
                            detail="Try changing the rail filter or clearing the search box to reveal more cases."
                          />
                        )}
                      </InfoCard>

                      <InfoCard
                        title="Selected Case Snapshot"
                        subtitle="Detailed graph record for the highlighted case"
                      >
                        {selectedGraphCase ? (
                          <div>
                            <div className="mb-4 flex items-start justify-between gap-3">
                              <div>
                                <p className="text-lg font-semibold text-slate-950">
                                  {selectedGraphCase.number}
                                </p>
                                <p className="mt-1 text-sm text-slate-500">
                                  {selectedGraphCase.shortDescription}
                                </p>
                              </div>
                              <StatusPill
                                label={selectedGraphCase.statusLabel}
                                tone={selectedGraphCase.statusTone}
                              />
                            </div>

                            <div className="space-y-1">
                              {[
                                ["Type", selectedGraphCase.typeLabel],
                                ["State", selectedGraphCase.stateLabel],
                                ["Opened", selectedGraphCase.openedLabel],
                                ["Updated", selectedGraphCase.updatedLabel],
                                ["Assigned To", selectedGraphCase.assignedTo],
                                ["Contact", selectedGraphCase.contact],
                                ["Requested By", selectedGraphCase.requestedBy],
                                [
                                  "Category",
                                  selectedGraphCase.category || "Not set",
                                ],
                                [
                                  "Subcategory",
                                  selectedGraphCase.subcategory || "Not set",
                                ],
                                [
                                  "Priority",
                                  selectedGraphCase.priority || "Not set",
                                ],
                                [
                                  "Impact",
                                  selectedGraphCase.impact || "Not set",
                                ],
                                [
                                  "Urgency",
                                  selectedGraphCase.urgency || "Not set",
                                ],
                                [
                                  "Close Code",
                                  selectedGraphCase.close_code || "Not closed",
                                ],
                                [
                                  "Resolution Notes",
                                  selectedGraphCase.resolution_notes || "None",
                                ],
                              ].map(([label, value]) => (
                                <DetailRow
                                  key={label}
                                  label={label}
                                  value={value}
                                  emphasize={label === "Type"}
                                />
                              ))}
                            </div>

                            <div className="mt-4 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
                              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">
                                Description
                              </p>
                              <p className="mt-2 text-sm leading-6 text-slate-700">
                                {selectedGraphCase.description ||
                                  "No description available."}
                              </p>
                            </div>
                          </div>
                        ) : (
                          <EmptyPanel
                            icon={LayoutList}
                            title="No case selected"
                            detail="Choose a case from the directory to inspect its full graph-backed details."
                          />
                        )}
                      </InfoCard>
                    </div>
                  ) : null}

                  <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
                    <InfoCard
                      title="Case Details"
                      subtitle="Core fields and live dashboard state"
                    >
                      {detailsRows.map(([label, value]) => (
                        <DetailRow
                          key={label}
                          label={label}
                          value={value}
                          emphasize={label === "Case Number"}
                        />
                      ))}
                    </InfoCard>

                    <InfoCard
                      title="Current State"
                      subtitle="What changed locally in this review"
                    >
                      <div className="space-y-3">
                        {[
                          `Status: ${model.status.label}`,
                          `Following: ${followed ? "Yes" : "No"}`,
                          `Local notes: ${localNotes.length}`,
                          `Escalation draft: ${escalationRecord ? escalationRecord.team : "None"}`,
                        ].map((item) => (
                          <div
                            key={item}
                            className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-700"
                          >
                            {item}
                          </div>
                        ))}
                      </div>
                    </InfoCard>
                  </div>
                </div>
              ) : null}

              {mainTab === "Tasks" ? (
                <InfoCard
                  title="Recommended Tasks"
                  subtitle="Interactive checklist driven by the current case"
                >
                  <div className="space-y-3">
                    {filteredSteps.map((step, index) => {
                      const key = `${index}-${step}`;
                      return (
                        <ChecklistItem
                          key={key}
                          checked={Boolean(taskChecks[key])}
                          onToggle={() =>
                            setTaskChecks((current) => ({
                              ...current,
                              [key]: !current[key],
                            }))
                          }
                          title={step}
                          detail={
                            taskChecks[key]
                              ? "Marked complete in this dashboard session"
                              : "Click to track progress locally"
                          }
                        />
                      );
                    })}
                  </div>
                </InfoCard>
              ) : null}

              {mainTab === "Interactions" ? (
                <div className="grid gap-4 xl:grid-cols-[1fr_0.9fr]">
                  <InfoCard
                    title="Customer Interactions"
                    subtitle="Local notes, queries, and the shared View & Chat page"
                    action={
                      <button
                        type="button"
                        onClick={openCaseChat}
                        className="inline-flex items-center gap-2 text-sm font-medium text-blue-700"
                      >
                        <MessageSquare className="h-4 w-4" />
                        View & Chat
                      </button>
                    }
                  >
                    <div className="space-y-3">
                      {assistantQueryHistory.length ? (
                        assistantQueryHistory.map((entry) => (
                          <button
                            key={entry.id}
                            type="button"
                            onClick={() => {
                              setAssistantQuery(entry.query);
                              setAssistantQueryAnswer(entry.answer);
                              setAssistantQueryCitations(entry.citations || []);
                              setAssistantCollapsed(false);
                            }}
                            className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-left transition hover:border-blue-200 hover:bg-blue-50/50"
                          >
                            <p className="text-sm font-medium text-slate-900">
                              {entry.query}
                            </p>
                            <p className="mt-2 line-clamp-2 text-sm text-slate-600">
                              {entry.answer}
                            </p>
                          </button>
                        ))
                      ) : (
                        <EmptyPanel
                          icon={MessageSquare}
                          title="Support questions"
                          detail="Ask the Agent about this case and surface grounded context."
                        />
                      )}
                    </div>
                  </InfoCard>

                  <InfoCard
                    title="Suggested Questions"
                    subtitle="Derived from missing or uncertain workflow data"
                  >
                    <div className="space-y-3">
                      {filteredMissingInfo.length ? (
                        filteredMissingInfo.map((item) => (
                          <div
                            key={item}
                            className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700"
                          >
                            {item}
                          </div>
                        ))
                      ) : (
                        <EmptyPanel
                          icon={CheckCircle2}
                          title="No follow-up gaps"
                          detail="The case currently has enough detail to move forward."
                        />
                      )}
                      <button
                        type="button"
                        onClick={handleCopyQuestions}
                        className="btn-secondary mt-2"
                      >
                        <Copy className="h-4 w-4" />
                        Copy Questions
                      </button>
                    </div>
                  </InfoCard>
                </div>
              ) : null}

              {mainTab === "Related Cases" ? (
                <InfoCard
                  title="Similar Cases"
                  subtitle="Pattern-based related incidents from the case workflow"
                >
                  <div className="grid gap-3 md:grid-cols-2">
                    {filteredRelatedCases.map((item) => (
                      <button
                        key={item.id}
                        type="button"
                        onClick={() => {
                          setAssistantTab("Similar Cases");
                          addLocalEvent(
                            "Related Case Reviewed",
                            `${item.number}: ${item.title}`,
                            "purple",
                          );
                        }}
                        className="rounded-2xl border border-slate-200 bg-white px-4 py-4 text-left shadow-sm transition hover:border-blue-200 hover:bg-blue-50/40"
                      >
                        <div className="flex items-center justify-between gap-3">
                          <p className="font-medium text-slate-950">
                            {item.number}
                          </p>
                          <StatusPill label={`${item.fit}% fit`} tone="blue" />
                        </div>
                        <p className="mt-3 text-sm font-medium text-slate-900">
                          {item.title}
                        </p>
                        <p className="mt-2 text-sm text-slate-600">
                          {item.summary}
                        </p>
                      </button>
                    ))}
                  </div>
                </InfoCard>
              ) : null}

              {mainTab === "Attachments" ? (
                <InfoCard
                  title="Case Attachments"
                  subtitle="Client-side artifacts you can copy or reuse"
                >
                  <div className="space-y-3">
                    {filteredAttachments.map((item) => (
                      <div
                        key={item.id}
                        className="flex items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3"
                      >
                        <div>
                          <p className="text-sm font-medium text-slate-900">
                            {item.label}
                          </p>
                          <p className="mt-1 text-sm text-slate-500">
                            {item.detail}
                          </p>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleAttachmentAction(item)}
                          className="btn-secondary"
                        >
                          <Copy className="h-4 w-4" />
                          Copy
                        </button>
                      </div>
                    ))}
                  </div>
                </InfoCard>
              ) : null}

              {mainTab === "Audit Trail" ? (
                <InfoCard
                  title="Audit Trail"
                  subtitle="Workflow log rendered as a searchable event feed"
                >
                  <div className="space-y-3">
                    {model.timeline.map((item) => (
                      <div
                        key={item.id}
                        className="rounded-2xl border border-slate-200 bg-white px-4 py-3"
                      >
                        <div className="flex items-center justify-between gap-3">
                          <p className="text-sm font-medium text-slate-900">
                            {item.label}
                          </p>
                          <span className="text-xs text-slate-500">
                            {formatDateTime(item.timestamp)}
                          </span>
                        </div>
                        <p className="mt-2 text-sm text-slate-600">
                          {item.detail}
                        </p>
                      </div>
                    ))}
                  </div>
                </InfoCard>
              ) : null}
            </section>
          </main>

          <aside
            className={clsx(
              "shrink-0 overflow-y-auto border-l border-slate-200 bg-white transition-all duration-200",
              assistantCollapsed ? "w-[72px]" : "w-[380px]",
            )}
          >
            <div className="border-b border-slate-200 p-4">
              <div className="flex items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-3">
                  <span className="rounded-xl bg-violet-50 p-2 text-violet-600">
                    <Sparkles className="h-4 w-4" />
                  </span>
                  {!assistantCollapsed ? (
                    <div className="min-w-0">
                      <h2 className="truncate text-sm font-semibold text-slate-950">
                        Support Intelligence Assistant
                      </h2>
                      <p className="mt-1 text-xs text-slate-500">
                        Interactive guidance across diagnosis, runbooks, and
                        related patterns
                      </p>
                    </div>
                  ) : null}
                </div>
                <button
                  type="button"
                  onClick={() => setAssistantCollapsed((current) => !current)}
                  className="rounded-lg p-2 text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
                >
                  {assistantCollapsed ? (
                    <ChevronLeft className="h-4 w-4 rotate-180" />
                  ) : (
                    <ChevronRight className="h-4 w-4" />
                  )}
                </button>
              </div>

              {!assistantCollapsed ? (
                <div className="mt-4">
                  <SectionTabs
                    items={ASSISTANT_TABS}
                    active={assistantTab}
                    onChange={setAssistantTab}
                    compact
                  />
                </div>
              ) : null}
            </div>

            {!assistantCollapsed ? (
              <div className="space-y-4 p-4">
                {assistantTab === "Diagnosis" ? (
                  <>
                    <InfoCard
                      title="Likely Cause"
                      subtitle="Case-derived summary"
                      action={
                        <StatusPill
                          label={`${model.confidence}% confidence`}
                          tone="amber"
                        />
                      }
                    >
                      <div className="rounded-2xl border border-amber-200 bg-gradient-to-br from-amber-50 to-white p-4 shadow-sm">
                        <div className="mb-2 flex items-center gap-2">
                          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-700">
                            Primary signal
                          </span>
                          <span className="text-xs font-medium text-amber-700">
                            Highest-confidence cause
                          </span>
                        </div>
                        <p className="text-sm leading-6 font-semibold text-slate-900">
                          {model.causeSummary}
                        </p>
                      </div>
                      <div className="mt-5 space-y-3">
                        {filteredEvidence.length ? (
                          filteredEvidence.map((item, index) => (
                            <EvidenceItem
                              key={`${item.text}-${index}`}
                              item={item}
                            />
                          ))
                        ) : (
                          <EmptyPanel
                            icon={Search}
                            title="No evidence matches the filter"
                            detail="Clear the search field to see the case evidence again."
                          />
                        )}
                      </div>
                    </InfoCard>

                    <InfoCard
                      title="Missing Information"
                      subtitle="Items you can take back to the customer"
                    >
                      {filteredMissingInfo.length ? (
                        <div className="space-y-3">
                          {filteredMissingInfo.map((item) => (
                            <div
                              key={item}
                              className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-700"
                            >
                              {item}
                            </div>
                          ))}
                        </div>
                      ) : (
                        <EmptyPanel
                          icon={CheckCircle2}
                          title="No missing information"
                          detail="The workflow already has enough detail to proceed."
                        />
                      )}
                      <div className="mt-4 flex items-center justify-between gap-3">
                        <button
                          type="button"
                          onClick={openCaseChat}
                          className="text-sm font-medium text-blue-700"
                        >
                          Ask customer
                        </button>
                        <button
                          type="button"
                          onClick={handleCopyQuestions}
                          className="btn-secondary"
                        >
                          <Copy className="h-4 w-4" />
                          Copy Questions
                        </button>
                      </div>
                    </InfoCard>
                  </>
                ) : null}

                {assistantTab === "Recommendations" ? (
                  <InfoCard
                    title="Recommended Next Steps"
                    subtitle="Toggle tasks locally or move them into your workflow"
                  >
                    <div className="space-y-3">
                      {filteredSteps.map((step, index) => {
                        const key = `recommendation-${index}-${step}`;
                        return (
                          <ChecklistItem
                            key={key}
                            checked={Boolean(taskChecks[key])}
                            onToggle={() =>
                              setTaskChecks((current) => ({
                                ...current,
                                [key]: !current[key],
                              }))
                            }
                            title={step}
                            detail="Click to mark this recommendation as handled"
                            compact
                          />
                        );
                      })}
                    </div>
                  </InfoCard>
                ) : null}

                {assistantTab === "Runbooks" ? (
                  <InfoCard
                    title="Runbook Guidance"
                    subtitle="Derived from graph-backed resolutions and fallback step sequencing"
                  >
                    <div className="space-y-3">
                      {filteredRunbooks.map((item, index) => (
                        <button
                          key={`${item.title}-${index}`}
                          type="button"
                          onClick={() => {
                            setMainTab("Tasks");
                            toast.success(
                              `Focused task view on ${item.title}.`,
                            );
                          }}
                          className="flex w-full items-start justify-between gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-left transition hover:border-blue-200 hover:bg-blue-50/40"
                        >
                          <div>
                            <p className="text-sm font-medium text-slate-900">
                              {item.title}
                            </p>
                            <p className="mt-1 text-sm text-slate-500">
                              {item.detail}
                            </p>
                          </div>
                          <ArrowRight className="mt-1 h-4 w-4 shrink-0 text-slate-400" />
                        </button>
                      ))}
                    </div>
                  </InfoCard>
                ) : null}

                {assistantTab === "Similar Cases" ? (
                  <InfoCard
                    title="Similar Cases"
                    subtitle="Pattern overlap built from issue candidates"
                  >
                    <div className="space-y-3">
                      {filteredRelatedCases.map((item) => (
                        <div
                          key={item.id}
                          className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3"
                        >
                          <div className="flex items-center justify-between gap-3">
                            <p className="text-sm font-medium text-slate-900">
                              {item.number}
                            </p>
                            <StatusPill
                              label={`${item.fit}% match`}
                              tone="blue"
                            />
                          </div>
                          <p className="mt-3 text-sm font-medium text-slate-900">
                            {item.title}
                          </p>
                          <p className="mt-1 text-sm text-slate-500">
                            {item.summary}
                          </p>
                        </div>
                      ))}
                    </div>
                  </InfoCard>
                ) : null}

                <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h3 className="text-sm font-semibold text-slate-950">
                        Questions
                      </h3>
                      <p className="mt-1 text-xs text-slate-500">
                        Ask tquestions regarding the case.
                      </p>
                    </div>
                    <StatusPill
                      label={assistantQueryLoading ? "Running" : "AI Agent"}
                      tone={assistantQueryLoading ? "amber" : "blue"}
                    />
                  </div>

                  {assistantQueryHistory.length > 0 && (
                    <div className="mt-4 space-y-2">
                      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">
                        Recent queries
                      </p>
                      {assistantQueryHistory.map((entry) => (
                        <button
                          key={entry.id}
                          type="button"
                          onClick={() => {
                            setAssistantQuery(entry.query);
                            setAssistantQueryAnswer(entry.answer);
                            setAssistantQueryCitations(entry.citations || []);
                          }}
                          className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-left text-sm text-slate-700 transition hover:border-blue-200 hover:bg-blue-50/50"
                        >
                          <p className="line-clamp-2">{entry.query}</p>
                        </button>
                      ))}
                    </div>
                  )}

                  {assistantQueryAnswer ? (
                    <div className="mt-4 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4">
                      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">
                        Answer
                      </p>
                      <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">
                        {assistantQueryAnswer}
                      </p>
                      {assistantQueryCitations.length > 0 && (
                        <div className="mt-4 grid gap-2">
                          {assistantQueryCitations.map((citation, index) => (
                            <a
                              key={`${citation.sys_id || citation.number || index}`}
                              href={citation.url}
                              target="_blank"
                              rel="noreferrer"
                              className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600 transition hover:border-blue-200 hover:bg-blue-50/40"
                            >
                              <span className="min-w-0 truncate font-medium text-slate-800">
                                {citation.number ||
                                  citation.name ||
                                  citation.short_description ||
                                  "Record"}
                              </span>
                              <ExternalLink className="h-3.5 w-3.5 shrink-0 text-blue-600" />
                            </a>
                          ))}
                        </div>
                      )}
                    </div>
                  ) : null}

                  <form
                    onSubmit={(event) => {
                      event.preventDefault();
                      submitAssistantQuery();
                    }}
                    className="mt-4"
                  >
                    <div className="rounded-xl border border-slate-100 p-3">
                      <textarea
                        value={assistantQuery}
                        onChange={(event) =>
                          setAssistantQuery(event.target.value)
                        }
                        placeholder="Ask the assistant to summarize, reframe, or organize this case..."
                        className="h-20 w-full resize-none bg-transparent text-sm text-slate-700 placeholder:text-slate-400 focus:outline-none"
                      />
                    </div>
                    <div className="mt-3 flex items-center justify-between">
                      <div className="flex gap-3 text-slate-400">
                        <ExternalLink className="h-4 w-4" />
                        <Mic className="h-4 w-4" />
                      </div>
                      <button
                        type="submit"
                        disabled={
                          assistantQueryLoading || !assistantQuery.trim()
                        }
                        className="flex h-9 w-9 items-center justify-center rounded-xl bg-blue-700 text-white transition hover:bg-blue-800 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        <Send className="h-4 w-4" />
                      </button>
                    </div>
                  </form>
                </div>
                <p className="text-center text-[11px] text-slate-500">
                  Answers are grounded in the current case context and live
                  graph data. Verify before updating external systems.
                </p>
              </div>
            ) : (
              <div className="flex flex-col items-center gap-3 px-2 py-4">
                {[Sparkles, Bot, BookOpen].map((Icon, index) => (
                  <button
                    key={index}
                    type="button"
                    onClick={() => setAssistantCollapsed(false)}
                    className="rounded-xl border border-slate-200 bg-white p-3 text-slate-500 transition hover:bg-slate-50 hover:text-blue-700"
                  >
                    <Icon className="h-4 w-4" />
                  </button>
                ))}
              </div>
            )}
          </aside>
        </div>

        <footer className="flex h-[62px] shrink-0 items-center justify-center gap-3 border-t border-slate-200 bg-white px-6">
          <button
            type="button"
            onClick={() => openDrawer("status")}
            disabled={savingAction}
            className="btn-secondary min-w-[146px] justify-center"
          >
            Update Status
          </button>
          <button
            type="button"
            onClick={() => openDrawer("work-note")}
            disabled={savingAction}
            className="btn-secondary min-w-[146px] justify-center text-blue-700"
          >
            Add Work Note
          </button>
          <button
            type="button"
            onClick={() => openDrawer("escalate")}
            disabled={savingAction}
            className="btn-secondary min-w-[110px] justify-center text-blue-700"
          >
            Escalate
          </button>
          <button
            type="button"
            onClick={() => openDrawer("problem")}
            disabled={savingAction}
            className="btn-secondary min-w-[138px] justify-center text-blue-700"
          >
            Create Problem
          </button>
          <button
            type="button"
            onClick={openResolveDrawer}
            disabled={savingAction}
            className="btn-primary min-w-[138px] justify-center bg-blue-800 hover:bg-blue-900"
          >
            <Check className="h-4 w-4" />
            Resolve Case
          </button>
        </footer>
      </div>

      {drawer === "status" ? (
        <Drawer
          title="Update Status"
          subtitle={
            statusDraft === "Resolved"
              ? "Resolve the incident in ServiceNow with the required closure details"
              : "Update the incident in ServiceNow and re-sync the graph"
          }
          onClose={() => setDrawer(null)}
        >
          <div className="space-y-3">
            {["Open", "In Progress", "Pending", "Resolved"].map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => {
                  setStatusDraft(item);
                  if (item === "Resolved") {
                    prefillResolveDraft();
                  }
                }}
                className={clsx(
                  "flex w-full items-center justify-between rounded-2xl border px-4 py-3 text-left transition",
                  statusDraft === item
                    ? "border-blue-200 bg-blue-50 text-blue-700"
                    : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50",
                )}
              >
                <span>{item}</span>
                {statusDraft === item ? <Check className="h-4 w-4" /> : null}
              </button>
            ))}
          </div>
          {statusDraft === "Resolved" ? (
            <div className="mt-5 space-y-4 rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
              <div>
                <label className="mb-2 block text-sm font-medium text-slate-700">
                  Resolution Code
                </label>
                <input
                  list="incident-close-code-options"
                  value={resolveDraft.closeCode}
                  onChange={(event) =>
                    setResolveDraft((current) => ({
                      ...current,
                      closeCode: event.target.value,
                    }))
                  }
                  className="input-field"
                  placeholder="Solved (Permanently)"
                />
                <datalist id="incident-close-code-options">
                  {CLOSE_CODE_SUGGESTIONS.map((item) => (
                    <option key={item} value={item} />
                  ))}
                </datalist>
                <p className="mt-1 text-xs text-slate-500">
                  Use the exact ServiceNow resolution code if your instance has
                  a custom list.
                </p>
              </div>
              <div>
                <label className="mb-2 block text-sm font-medium text-slate-700">
                  Close Notes
                </label>
                <textarea
                  value={resolveDraft.closeNotes}
                  onChange={(event) =>
                    setResolveDraft((current) => ({
                      ...current,
                      closeNotes: event.target.value,
                    }))
                  }
                  placeholder="Summarize what was fixed and how the issue was verified."
                  className="input-field min-h-[110px] resize-none"
                />
              </div>
              <div>
                <label className="mb-2 block text-sm font-medium text-slate-700">
                  Solution / Resolution Notes
                </label>
                <textarea
                  value={resolveDraft.resolutionNotes}
                  onChange={(event) =>
                    setResolveDraft((current) => ({
                      ...current,
                      resolutionNotes: event.target.value,
                    }))
                  }
                  placeholder="Capture the final remediation steps for future reference."
                  className="input-field min-h-[110px] resize-none"
                />
              </div>
              <div>
                <label className="mb-2 block text-sm font-medium text-slate-700">
                  Root Cause
                </label>
                <textarea
                  value={resolveDraft.rootCause}
                  onChange={(event) =>
                    setResolveDraft((current) => ({
                      ...current,
                      rootCause: event.target.value,
                    }))
                  }
                  placeholder="Describe the underlying cause that triggered the incident."
                  className="input-field min-h-[96px] resize-none"
                />
              </div>
              <div>
                <label className="mb-2 block text-sm font-medium text-slate-700">
                  Workaround
                </label>
                <textarea
                  value={resolveDraft.workaround}
                  onChange={(event) =>
                    setResolveDraft((current) => ({
                      ...current,
                      workaround: event.target.value,
                    }))
                  }
                  placeholder="Optional temporary workaround that helped contain the issue."
                  className="input-field min-h-[96px] resize-none"
                />
              </div>
            </div>
          ) : null}
          <button
            type="button"
            onClick={applyStatusUpdate}
            disabled={savingAction}
            className="btn-primary mt-5 w-full justify-center"
          >
            {statusDraft === "Resolved" ? "Resolve Incident" : "Apply Status"}
          </button>
        </Drawer>
      ) : null}

      {drawer === "work-note" ? (
        <Drawer
          title="Add Work Note"
          subtitle="Save an internal note to ServiceNow and re-sync the graph"
          onClose={() => setDrawer(null)}
        >
          <textarea
            value={workNoteDraft}
            onChange={(event) => setWorkNoteDraft(event.target.value)}
            placeholder="Capture the latest observation, action, or customer reply..."
            className="input-field min-h-[140px] resize-none"
          />
          <button
            type="button"
            onClick={applyWorkNote}
            disabled={savingAction}
            className="btn-primary mt-5 w-full justify-center"
          >
            Save Work Note
          </button>
        </Drawer>
      ) : null}

      {drawer === "edit" ? (
        <Drawer
          title="Edit Case View"
          subtitle="Tidy the case summary and assignment details shown in the dashboard"
          onClose={() => setDrawer(null)}
        >
          <div className="space-y-4">
            <div>
              <label className="mb-2 block text-sm font-medium text-slate-700">
                Short Description
              </label>
              <textarea
                value={editDraft.shortDescription}
                onChange={(event) =>
                  setEditDraft((current) => ({
                    ...current,
                    shortDescription: event.target.value,
                  }))
                }
                className="input-field min-h-[100px] resize-none"
              />
            </div>
            <div>
              <label className="mb-2 block text-sm font-medium text-slate-700">
                Assigned To
              </label>
              <input
                value={editDraft.assignedTo}
                onChange={(event) =>
                  setEditDraft((current) => ({
                    ...current,
                    assignedTo: event.target.value,
                  }))
                }
                className="input-field"
              />
            </div>
            <div>
              <label className="mb-2 block text-sm font-medium text-slate-700">
                Contact
              </label>
              <input
                value={editDraft.contact}
                onChange={(event) =>
                  setEditDraft((current) => ({
                    ...current,
                    contact: event.target.value,
                  }))
                }
                className="input-field"
              />
            </div>
          </div>
          <button
            type="button"
            onClick={applyEdit}
            className="btn-primary mt-5 w-full justify-center"
          >
            Apply Edits
          </button>
        </Drawer>
      ) : null}

      {drawer === "escalate" ? (
        <Drawer
          title="Prepare Escalation"
          subtitle="Create a visible escalation record inside the dashboard"
          onClose={() => setDrawer(null)}
        >
          <div className="space-y-4">
            <div>
              <label className="mb-2 block text-sm font-medium text-slate-700">
                Escalation Team
              </label>
              <select
                value={escalationDraft.team}
                onChange={(event) =>
                  setEscalationDraft((current) => ({
                    ...current,
                    team: event.target.value,
                  }))
                }
                className="input-field"
              >
                <option>Platform Support</option>
                <option>Networking Team</option>
                <option>Appliance Engineering</option>
                <option>Customer Success</option>
              </select>
            </div>
            <div>
              <label className="mb-2 block text-sm font-medium text-slate-700">
                Reason
              </label>
              <textarea
                value={escalationDraft.reason}
                onChange={(event) =>
                  setEscalationDraft((current) => ({
                    ...current,
                    reason: event.target.value,
                  }))
                }
                className="input-field min-h-[120px] resize-none"
                placeholder="Explain what is blocked and why the escalation is needed..."
              />
            </div>
          </div>
          <button
            type="button"
            onClick={applyEscalation}
            className="btn-primary mt-5 w-full justify-center"
          >
            Save Escalation
          </button>
        </Drawer>
      ) : null}

      {drawer === "problem" ? (
        <Drawer
          title="Create Problem Draft"
          subtitle="Build a local problem statement from the active case"
          onClose={() => setDrawer(null)}
        >
          <textarea
            value={problemDraft}
            onChange={(event) => setProblemDraft(event.target.value)}
            className="input-field min-h-[160px] resize-none"
          />
          <div className="mt-5 flex gap-3">
            <button
              type="button"
              onClick={applyProblemDraft}
              className="btn-primary flex-1 justify-center"
            >
              Save Draft
            </button>
            <button
              type="button"
              onClick={() => copyText(problemDraft, "Problem draft copied.")}
              className="btn-secondary"
            >
              <Copy className="h-4 w-4" />
              Copy
            </button>
          </div>
        </Drawer>
      ) : null}
    </div>
  );
}
