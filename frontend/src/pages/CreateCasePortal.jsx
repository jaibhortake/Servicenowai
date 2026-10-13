import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertCircle,
  Bell,
  Bot,
  Check,
  CheckCircle2,
  ChevronRight,
  Edit3,
  ExternalLink,
  HelpCircle,
  Loader2,
  Lock,
  MessageCircle,
  RotateCcw,
  Send,
  ShieldCheck,
  X,
} from "lucide-react";
import clsx from "clsx";
import toast from "react-hot-toast";
import { useStore } from "../store/useStore";
import {
  answerGraphRAGCase,
  cancelGraphRAGCase,
  completeGraphRAGCase,
  getGraphRAGCase,
  selectGraphRAGCaseIssue,
  startGraphRAGCase,
} from "../api/client";
import {
  LAST_CASE_SESSION_KEY,
  setCaseDashboardSession,
} from "../utils/caseSession";
import { setLastViewChatTarget } from "../utils/viewChatSession";
import { buildServiceNowRecordUrl } from "../utils/servicenowLinks";

const STEPS = [
  {
    key: "describe_issue",
    label: "Describe Issue",
    caption: "Tell us what is happening",
  },
  {
    key: "answer_questions",
    label: "Answer a Few Questions",
    caption: "We will ask to understand better",
  },
  {
    key: "review",
    label: "Review & Confirm",
    caption: "Review your case details",
  },
  {
    key: "case_created",
    label: "Case Created",
    caption: "We are on it",
  },
];

const PROMPTS = [
  "VM Analytics is not showing any data in Pure1.",
  "Cloud-backed storage is slow for a branch site.",
  "Upgrade pre-check failed after phone home validation.",
  "Alerts are missing for a degraded appliance.",
];

function formatTime(value) {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatDateTime(value) {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString([], {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function stepIndexFor(workflow) {
  if (!workflow) return 0;
  if (workflow.step === "case_created") return 3;
  if (workflow.step === "review") return 2;
  if (workflow.step === "answer_questions") return 1;
  return 0;
}

function initials(name, email) {
  const source = String(name || email || "Guest User").trim();
  const parts = source.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
  return source.slice(0, 2).toUpperCase();
}

function statusLabel(workflow) {
  if (!workflow) return "Draft";
  if (workflow.status === "Completed") return "Completed";
  if (workflow.status === "Cancelled") return "Cancelled";
  return "In Progress";
}

function MessageBubble({ role, children, time }) {
  const isUser = role === "user";
  return (
    <div className={clsx("flex gap-3", isUser && "justify-end")}>
      {!isUser && (
        <div className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-orange-100 bg-orange-50 text-orange-600">
          <Bot className="h-5 w-5" />
        </div>
      )}
      <div
        className={clsx(
          "max-w-[72%] rounded-lg border px-4 py-3 text-sm leading-6 shadow-sm",
          isUser
            ? "border-blue-100 bg-blue-50 text-slate-800"
            : "border-slate-200 bg-white text-slate-700",
        )}
      >
        {children}
        {time && (
          <span className="ml-3 whitespace-nowrap text-[11px] text-slate-400">
            {time}
          </span>
        )}
      </div>
      {isUser && (
        <div className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-slate-200 text-xs font-semibold text-slate-600">
          RK
        </div>
      )}
    </div>
  );
}

function Stepper({ activeIndex }) {
  return (
    <div className="border-b border-slate-200 bg-white px-9 py-6">
      <div className="grid grid-cols-4 items-start gap-4">
        {STEPS.map((step, index) => {
          const complete = index < activeIndex;
          const active = index === activeIndex;
          return (
            <div key={step.key} className="relative flex items-start gap-3">
              {index < STEPS.length - 1 && (
                <div className="absolute left-[46px] top-4 h-px w-[calc(100%-20px)] bg-slate-200" />
              )}
              <div
                className={clsx(
                  "relative z-10 flex h-9 w-9 shrink-0 items-center justify-center rounded-full border text-sm font-semibold",
                  complete || active
                    ? "border-blue-700 bg-blue-700 text-white"
                    : "border-slate-300 bg-slate-50 text-slate-500",
                )}
              >
                {complete ? <Check className="h-4 w-4" /> : index + 1}
              </div>
              <div className="min-w-0">
                <p
                  className={clsx(
                    "text-sm font-semibold",
                    active || complete ? "text-blue-800" : "text-slate-700",
                  )}
                >
                  {step.label}
                </p>
                <p className="mt-1 text-xs text-slate-500">{step.caption}</p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ProgressCard({ workflow }) {
  const progress = workflow?.progress || {};
  const percent =
    workflow?.step === "case_created" ? 100 : progress.percent || 0;
  const circumference = 2 * Math.PI * 44;
  const dashOffset = circumference - (percent / 100) * circumference;
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-900">Case Progress</h3>
        <span className="text-xs text-slate-500">
          {Math.min(stepIndexFor(workflow) + 1, 4)} of 4 completed
        </span>
      </div>
      <div className="mt-5 flex items-center gap-5">
        <div className="relative h-28 w-28">
          <svg className="h-28 w-28 -rotate-90">
            <circle
              cx="56"
              cy="56"
              r="44"
              strokeWidth="8"
              className="fill-none stroke-slate-100"
            />
            <circle
              cx="56"
              cy="56"
              r="44"
              strokeWidth="8"
              strokeLinecap="round"
              className="fill-none stroke-blue-700 transition-all"
              style={{
                strokeDasharray: circumference,
                strokeDashoffset: dashOffset,
              }}
            />
          </svg>
          <div className="absolute inset-0 flex items-center justify-center text-xl font-bold text-slate-900">
            {percent}%
          </div>
        </div>
        <div className="space-y-3 text-sm">
          <div>
            <p className="text-xs text-slate-500">Questions Answered</p>
            <p className="font-semibold text-slate-900">
              {progress.answered || 0} of {progress.total || 0}
            </p>
          </div>
          <div>
            <p className="text-xs text-slate-500">Estimated Time</p>
            <p className="font-semibold text-slate-900">
              {progress.estimated_minutes || 1} min
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function Checklist({ workflow }) {
  const activeIndex = stepIndexFor(workflow);
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      <h3 className="text-sm font-semibold text-slate-900">Case Steps</h3>
      <div className="mt-5 space-y-4">
        {STEPS.map((step, index) => {
          const complete =
            index < activeIndex || workflow?.step === "case_created";
          const active =
            index === activeIndex && workflow?.step !== "case_created";
          return (
            <div key={step.key} className="flex gap-3">
              <div
                className={clsx(
                  "flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
                  complete
                    ? "border-emerald-600 bg-emerald-600 text-white"
                    : active
                      ? "border-blue-700 bg-blue-700 text-white"
                      : "border-slate-300 bg-slate-50 text-slate-500",
                )}
              >
                {complete ? <Check className="h-3.5 w-3.5" /> : index + 1}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-sm font-semibold text-slate-900">
                    {step.label}
                  </p>
                  <span className="text-xs text-slate-400">
                    {workflow ? formatTime(workflow.updated_at) : ""}
                  </span>
                </div>
                <p
                  className={clsx(
                    "mt-0.5 text-xs",
                    complete
                      ? "text-emerald-600"
                      : active
                        ? "text-blue-700"
                        : "text-slate-500",
                  )}
                >
                  {complete ? "Completed" : active ? "In Progress" : "Pending"}
                </p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SummaryCard({ workflow, onChangeIssue }) {
  const summary = workflow?.case_summary || {};
  const topCause = workflow?.possible_causes?.[0];
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-900">
          Your Case Summary
        </h3>
        {workflow?.issue_candidates?.length > 1 && (
          <button
            type="button"
            onClick={onChangeIssue}
            className="text-xs font-semibold text-blue-700 hover:text-blue-800"
          >
            Edit
          </button>
        )}
      </div>
      <div className="mt-4 space-y-4 text-sm">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Issue
          </p>
          <p className="mt-1 text-slate-800">
            {summary.issue || workflow?.initial_query || "Not provided"}
          </p>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Matched Issue
            </p>
            <p className="mt-1 text-slate-800">
              {summary.matched_issue || workflow?.selected_issue?.label || "-"}
            </p>
          </div>
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Likely Cause
            </p>
            <p className="mt-1 text-slate-800">
              {summary.likely_cause || topCause?.name || "-"}
            </p>
          </div>
        </div>
        {workflow?.recommended_resolution?.title && (
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Recommended Action
            </p>
            <p className="mt-1 text-slate-800">
              {workflow.recommended_resolution.title}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

export default function CreateCasePortal() {
  const navigate = useNavigate();
  const { currentUser, currentUserEmail, sessionId } = useStore();
  const [description, setDescription] = useState("");
  const [workflow, setWorkflow] = useState(null);
  const [loading, setLoading] = useState(false);
  const [completing, setCompleting] = useState(false);
  const [textAnswer, setTextAnswer] = useState("");
  const [showIssuePicker, setShowIssuePicker] = useState(false);
  const endRef = useRef(null);

  const activeIndex = stepIndexFor(workflow);
  const currentQuestion = workflow?.current_question;
  const canStart = description.trim().length > 0 && !loading;
  const canComplete =
    workflow &&
    workflow.step === "review" &&
    workflow.status !== "Completed" &&
    !completing;

  const displayCaseNumber =
    workflow?.case_number || workflow?.created_ticket?.number;
  const headerNumber = displayCaseNumber || "Draft";
  const serviceNowUrl = buildServiceNowRecordUrl(
    workflow?.created_ticket?.ticket_type,
    displayCaseNumber,
  );

  const transcript = useMemo(() => {
    const items = [];
    // items.push({
    //   role: "assistant",
    //   key: "hello",
    //   time: workflow ? formatTime(workflow.created_at) : "",
    //   content: "Hi. Please describe the issue you are facing.",
    // });
    if (workflow?.initial_query) {
      items.push({
        role: "user",
        key: "initial",
        time: formatTime(workflow.created_at),
        content: workflow.initial_query,
      });
    }
    if (workflow) {
      items.push({
        role: "assistant",
        key: "acknowledge",
        time: formatTime(workflow.created_at),
        content:
          "Thanks for sharing the issue. I will ask a few graph-backed questions to narrow this down.",
      });
    }
    for (const answer of workflow?.answered_questions || []) {
      items.push({
        role: "assistant",
        key: `q-${answer.answer_id}`,
        time: formatTime(answer.answered_at),
        content: answer.question_text,
      });
      items.push({
        role: "user",
        key: `a-${answer.answer_id}`,
        time: formatTime(answer.answered_at),
        content: answer.skipped
          ? "Skipped"
          : answer.answer_label || answer.answer_value,
      });
    }
    if (currentQuestion) {
      items.push({
        role: "assistant",
        key: "current-question",
        time: formatTime(workflow.updated_at),
        content: currentQuestion.question_text,
      });
    } else if (workflow?.step === "review") {
      items.push({
        role: "assistant",
        key: "review",
        time: formatTime(workflow.updated_at),
        content:
          "I have enough information to prepare the case. Review the likely cause and recommended action before creating it.",
      });
    } else if (workflow?.step === "case_created") {
      items.push({
        role: "assistant",
        key: "created",
        time: formatTime(workflow.updated_at),
        content: `Case ${displayCaseNumber || ""} has been created.`,
      });
    }
    return items;
  }, [workflow, currentQuestion, displayCaseNumber]);

  useEffect(() => {
    const lastSessionId = localStorage.getItem(LAST_CASE_SESSION_KEY);
    if (!lastSessionId) return;
    let active = true;
    getGraphRAGCase(lastSessionId)
      .then((state) => {
        if (!active) return;
        setWorkflow(state);
        setDescription(state.initial_query || "");
      })
      .catch(() => {
        localStorage.removeItem(LAST_CASE_SESSION_KEY);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcript.length, loading, workflow?.current_question?.question_id]);

  const context = {
    userSysId: currentUser?.sys_id,
    userEmail: currentUserEmail,
    sessionId,
  };

  const handleStart = async (event) => {
    event?.preventDefault();
    if (!canStart) return;
    setLoading(true);
    try {
      const state = await startGraphRAGCase(description.trim(), context);
      setWorkflow(state);
      localStorage.setItem(LAST_CASE_SESSION_KEY, state.session_id);
      if (!state.current_question && state.qa_available && state.message) {
        toast(state.message);
      }
    } finally {
      setLoading(false);
    }
  };

  const submitAnswer = async (option, skipped = false) => {
    if (!workflow?.session_id || !currentQuestion || loading) return;
    setLoading(true);
    try {
      const state = await answerGraphRAGCase(workflow.session_id, {
        question_id: currentQuestion.question_id,
        answer_value: skipped ? "" : option?.value || textAnswer,
        answer_label: skipped ? "" : option?.label || textAnswer,
        skipped,
      });
      setWorkflow(state);
      setTextAnswer("");
    } finally {
      setLoading(false);
    }
  };

  const handleTextAnswer = async (event) => {
    event.preventDefault();
    if (!textAnswer.trim()) return;
    await submitAnswer({ value: textAnswer.trim(), label: textAnswer.trim() });
  };

  const handleIssueSelect = async (candidate) => {
    if (!workflow?.session_id) return;
    setLoading(true);
    try {
      const state = await selectGraphRAGCaseIssue(workflow.session_id, {
        issue_id: candidate.issue_id,
        issue_label: candidate.label,
      });
      setWorkflow(state);
      setShowIssuePicker(false);
    } finally {
      setLoading(false);
    }
  };

  const handleComplete = async () => {
    if (!canComplete) return;
    setCompleting(true);
    try {
      const state = await completeGraphRAGCase(workflow.session_id, context);
      setWorkflow(state);
      localStorage.setItem(LAST_CASE_SESSION_KEY, state.session_id);
      setCaseDashboardSession(state.session_id);
      toast.success(
        `Case ${state.case_number || state.created_ticket?.number} created`,
      );
    } finally {
      setCompleting(false);
    }
  };

  const handleCancel = async () => {
    if (!workflow?.session_id) {
      setDescription("");
      return;
    }
    setLoading(true);
    try {
      const state = await cancelGraphRAGCase(workflow.session_id);
      setWorkflow(state);
      localStorage.removeItem(LAST_CASE_SESSION_KEY);
    } finally {
      setLoading(false);
    }
  };

  const handleReset = () => {
    setWorkflow(null);
    setDescription("");
    setTextAnswer("");
    setShowIssuePicker(false);
    localStorage.removeItem(LAST_CASE_SESSION_KEY);
  };

  const handleViewDashboard = () => {
    if (!workflow?.session_id) return;
    setCaseDashboardSession(workflow.session_id);
    navigate("/create-case/agent-dashboard");
  };

  const handleOpenCaseChat = () => {
    const ticketType = workflow?.created_ticket?.ticket_type;
    const ticketSysId = workflow?.created_ticket?.sys_id;
    const ticketNumber = workflow?.created_ticket?.number;
    if (!ticketType || !ticketSysId) {
      toast.error("Create the case first to open View & Chat.");
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
  };

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-slate-50">
      <header className="flex h-[72px] shrink-0 items-center justify-between border-b border-slate-200 bg-white px-8">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold tracking-tight text-slate-950">
              Case {headerNumber}
            </h1>
            <span
              className={clsx(
                "rounded-full px-3 py-1 text-xs font-semibold",
                workflow?.status === "Completed"
                  ? "bg-emerald-100 text-emerald-700"
                  : workflow?.status === "Cancelled"
                    ? "bg-slate-100 text-slate-600"
                    : "bg-blue-100 text-blue-700",
              )}
            >
              {statusLabel(workflow)}
            </span>
          </div>
          <p className="mt-1 text-xs text-slate-500">
            Created on {formatDateTime(workflow?.created_at)}
          </p>
        </div>
        <div className="flex items-center gap-6">
          <button
            type="button"
            onClick={handleOpenCaseChat}
            className="flex items-center gap-2 text-sm font-medium text-slate-700 hover:text-blue-700"
          >
            <MessageCircle className="h-4 w-4" />
            Chat with support
          </button>
          <button className="relative rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-800">
            <Bell className="h-5 w-5" />
            <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-red-500" />
          </button>
          <div className="flex items-center gap-3">
            <div className="relative flex h-10 w-10 items-center justify-center rounded-full bg-slate-200 text-sm font-semibold text-slate-600">
              {initials(currentUser?.name, currentUserEmail)}
              <span className="absolute bottom-0 right-0 h-2.5 w-2.5 rounded-full border-2 border-white bg-emerald-500" />
            </div>
          </div>
        </div>
      </header>

      <Stepper activeIndex={activeIndex} />

      <div className="flex min-h-0 flex-1 gap-4 overflow-hidden p-5">
        <main className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-200 px-6 py-5">
            <div>
              <h2 className="text-lg font-bold text-slate-950">
                {workflow
                  ? "Let's understand your issue"
                  : "Describe the issue"}
              </h2>
              <p className="mt-1 text-sm text-slate-600">
                {workflow?.message ||
                  "The assistant will use the graph to ask the next best question."}
              </p>
            </div>
            <div className="inline-flex items-center gap-2 rounded-md bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-700">
              <Lock className="h-3.5 w-3.5" />
              Secure & Confidential
            </div>
          </div>

          <div className="flex-1 overflow-y-auto px-6 py-5">
            <div className="mx-auto max-w-5xl space-y-5">
              {transcript.map((item) => (
                <MessageBubble key={item.key} role={item.role} time={item.time}>
                  {item.content}
                </MessageBubble>
              ))}

              {!workflow && (
                <form onSubmit={handleStart} className="mt-6 space-y-4">
                  <textarea
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                    className="input-field min-h-[116px] resize-none border-slate-300 text-sm leading-6"
                    placeholder="Describe what is not working..."
                  />
                  <div className="grid grid-cols-2 gap-3">
                    {PROMPTS.map((prompt) => (
                      <button
                        key={prompt}
                        type="button"
                        onClick={() => setDescription(prompt)}
                        className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-3 text-left text-sm text-slate-600 transition hover:border-blue-300 hover:bg-blue-50 hover:text-blue-800"
                      >
                        {prompt}
                      </button>
                    ))}
                  </div>
                  <div className="flex justify-end">
                    <button
                      type="submit"
                      disabled={!canStart}
                      className="btn-primary min-w-[150px] justify-center"
                    >
                      {loading ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Send className="h-4 w-4" />
                      )}
                      Start Case
                    </button>
                  </div>
                </form>
              )}

              {workflow?.issue_candidates?.length > 1 && showIssuePicker && (
                <div className="ml-12 rounded-lg border border-blue-200 bg-blue-50 p-4">
                  <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-blue-900">
                    <Edit3 className="h-4 w-4" />
                    Select the closest issue
                  </div>
                  <div className="grid gap-2 md:grid-cols-2">
                    {workflow.issue_candidates.map((candidate) => (
                      <button
                        key={candidate.issue_id}
                        type="button"
                        onClick={() => handleIssueSelect(candidate)}
                        className={clsx(
                          "rounded-lg border px-3 py-2 text-left text-sm transition",
                          workflow.selected_issue?.issue_id ===
                            candidate.issue_id
                            ? "border-blue-700 bg-white text-blue-800"
                            : "border-blue-100 bg-white/70 text-slate-700 hover:border-blue-400",
                        )}
                      >
                        <span className="font-semibold">{candidate.label}</span>
                        {candidate.description && (
                          <span className="mt-1 block line-clamp-2 text-xs text-slate-500">
                            {candidate.description}
                          </span>
                        )}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {currentQuestion && (
                <div className="ml-12 max-w-3xl space-y-4">
                  {currentQuestion.options?.length > 0 ? (
                    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                      {currentQuestion.options.map((option) => (
                        <button
                          key={option.value}
                          type="button"
                          onClick={() => submitAnswer(option)}
                          disabled={loading}
                          className="relative min-h-[52px] rounded-lg border border-slate-300 bg-white px-4 py-3 text-center text-sm font-semibold text-slate-800 transition hover:border-blue-600 hover:bg-blue-50 hover:text-blue-800 disabled:opacity-60"
                        >
                          {option.label}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <form onSubmit={handleTextAnswer} className="flex gap-3">
                      <input
                        value={textAnswer}
                        onChange={(event) => setTextAnswer(event.target.value)}
                        className="input-field h-12"
                        placeholder="Type your answer"
                      />
                      <button
                        type="submit"
                        disabled={!textAnswer.trim() || loading}
                        className="btn-primary"
                      >
                        <Send className="h-4 w-4" />
                        Answer
                      </button>
                    </form>
                  )}
                  <button
                    type="button"
                    onClick={() => submitAnswer(null, true)}
                    disabled={loading}
                    className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-60"
                  >
                    Skip this question
                  </button>
                </div>
              )}

              {workflow?.step === "review" && (
                <div className="ml-12 rounded-lg border border-slate-200 bg-slate-50 p-5">
                  <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-slate-900">
                    <ShieldCheck className="h-4 w-4 text-blue-700" />
                    Review & Confirm
                  </div>
                  <div className="grid gap-4 md:grid-cols-2">
                    {(workflow.possible_causes || [])
                      .slice(0, 4)
                      .map((cause, index) => (
                        <div
                          key={cause.cause_id}
                          className={clsx(
                            "rounded-lg border p-4 transition",
                            index === 0
                              ? "border-amber-300 bg-amber-50/70 shadow-sm"
                              : "border-slate-200 bg-white",
                          )}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <div className="flex items-center gap-2">
                                <p className="font-semibold text-slate-900">
                                {cause.name}
                                </p>
                                {index === 0 && (
                                  <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-700">
                                    Most likely
                                  </span>
                                )}
                              </div>
                              {cause.severity && (
                                <p className="mt-1 text-xs text-slate-500">
                                  Severity: {cause.severity}
                                </p>
                              )}
                            </div>
                            <span
                              className={clsx(
                                "rounded-full px-2 py-1 text-xs font-semibold",
                                index === 0
                                  ? "bg-amber-100 text-amber-700"
                                  : "bg-blue-100 text-blue-700",
                              )}
                            >
                              {Math.round(cause.score)}%
                            </span>
                          </div>
                          {cause.evidence?.length > 0 && (
                            <ul className="mt-3 space-y-1 text-xs leading-5 text-slate-600">
                              {cause.evidence.slice(0, 3).map((item) => (
                                <li key={item}>- {item}</li>
                              ))}
                            </ul>
                          )}
                        </div>
                      ))}
                  </div>
                </div>
              )}

              {loading && workflow && (
                <MessageBubble role="assistant">
                  <span className="inline-flex items-center gap-2">
                    <Loader2 className="h-4 w-4 animate-spin text-blue-700" />
                    Checking the graph...
                  </span>
                </MessageBubble>
              )}
              <div ref={endRef} />
            </div>
          </div>

          <div className="flex shrink-0 items-center justify-between border-t border-slate-200 bg-white px-6 py-4">
            <button
              type="button"
              onClick={handleReset}
              className="btn-secondary"
            >
              <RotateCcw className="h-4 w-4" />
              Back
            </button>
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={
                  workflow?.step === "case_created"
                    ? handleViewDashboard
                    : () => toast.success("Case workflow saved")
                }
                disabled={!workflow}
                className="btn-secondary min-w-[140px] justify-center"
              >
                {workflow?.step === "case_created"
                  ? "View in Agent Dashboard"
                  : "Save and Exit"}
              </button>
              <button
                type="button"
                onClick={handleCancel}
                className="inline-flex min-w-[160px] items-center justify-center gap-2 rounded-lg bg-blue-950 px-4 py-2 font-medium text-white transition hover:bg-blue-900 disabled:opacity-60"
                disabled={loading || completing}
              >
                <X className="h-4 w-4" />
                Cancel
              </button>
              <button
                type="button"
                onClick={
                  workflow?.step === "review" ? handleComplete : handleStart
                }
                disabled={workflow ? !canComplete : !canStart}
                className="btn-primary min-w-[160px] justify-center"
              >
                {loading || completing ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : workflow?.step === "review" ? (
                  <CheckCircle2 className="h-4 w-4" />
                ) : (
                  <ChevronRight className="h-4 w-4" />
                )}
                {workflow?.step === "review" ? "Create Case" : "Continue"}
              </button>
            </div>
          </div>
        </main>

        <aside className="flex w-[360px] shrink-0 flex-col gap-4 overflow-y-auto">
          <ProgressCard workflow={workflow} />
          <Checklist workflow={workflow} />
          <SummaryCard
            workflow={workflow}
            onChangeIssue={() => setShowIssuePicker((value) => !value)}
          />
          <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <HelpCircle className="h-4 w-4 text-blue-700" />
              Why we ask these questions?
            </div>
            <p className="mt-3 text-sm leading-6 text-slate-600">
              Your answers help the assistant validate graph conditions, compare
              known cases, and route the case with better evidence.
            </p>
          </div>
          <div className="rounded-lg border border-blue-200 bg-blue-50 p-5">
            <div className="flex items-start gap-3">
              <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-blue-700" />
              <div>
                <h3 className="text-sm font-semibold text-blue-950">
                  Need immediate help?
                </h3>
                <p className="mt-2 text-sm leading-6 text-blue-900">
                  Chat with the support team now.
                </p>
                <button
                  type="button"
                  onClick={handleOpenCaseChat}
                  className="mt-4 inline-flex items-center gap-2 rounded-lg border border-blue-300 bg-white px-4 py-2 text-sm font-semibold text-blue-700 hover:bg-blue-50"
                >
                  <MessageCircle className="h-4 w-4" />
                  Start Chat
                </button>
              </div>
            </div>
          </div>
          {workflow?.step === "case_created" && serviceNowUrl ? (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-5 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-sm font-semibold text-emerald-950">
                    ServiceNow record
                  </h3>
                  <p className="mt-2 text-sm leading-6 text-emerald-900">
                    Open the created case directly in ServiceNow.
                  </p>
                </div>
                <ExternalLink className="mt-0.5 h-4 w-4 text-emerald-700" />
              </div>
              <a
                href={serviceNowUrl}
                target="_blank"
                rel="noreferrer"
                className="mt-4 inline-flex items-center gap-2 rounded-lg border border-emerald-300 bg-white px-4 py-2 text-sm font-semibold text-emerald-700 transition hover:bg-emerald-50"
              >
                <ExternalLink className="h-4 w-4" />
                Open in ServiceNow
              </a>
            </div>
          ) : null}
          <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
            <h3 className="text-sm font-semibold text-slate-900">
              Session Details
            </h3>
            <div className="mt-4 space-y-3 text-sm">
              <div className="flex justify-between gap-4">
                <span className="text-slate-500">Session ID</span>
                <span className="max-w-[180px] truncate font-medium text-slate-800">
                  {workflow?.session_id || "-"}
                </span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-slate-500">Started By</span>
                <span className="max-w-[180px] truncate font-medium text-slate-800">
                  {currentUser?.name || currentUserEmail || "Guest User"}
                </span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-slate-500">Status</span>
                <span className="font-medium text-slate-800">
                  {statusLabel(workflow)}
                </span>
              </div>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
