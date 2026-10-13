import { useMemo, useState } from "react";
import {
  Database,
  Loader2,
  Search,
  Sparkles,
  ArrowRight,
  ListTree,
  ExternalLink,
} from "lucide-react";
import clsx from "clsx";
import Header from "../components/Layout/Header";
import { queryGraphRAG, queryVectorRAG } from "../api/client";
import { useStore } from "../store/useStore";

const SUGGESTED_QUERIES = [
  //   "What are the other appliances which have had the same issue as Nginx Load Balancer in the last month?",
  //   "What are the other appliances which have had the same issue as Juniper MX204 WAN Router in the last month?",
  //   "What are the other appliances which have had the same issue as Tomcat App Server APP-01 in the last month?",
  //   "What are the other issues that F5 BIG-IP 2200s has had in the last week?",
  //   "What are the other issues that Apache Web Server WEB-02 has had in the last week?",
  //   "What is the triage or troubleshooting recommendation based on the SSL Wildcard Certificate Mass Expiry issue?",
  //   "What is the triage or troubleshooting recommendation based on the BGP Route Flapping problem for Juniper MX204 WAN Router?",
  //   "What is the triage or troubleshooting recommendation based on the Java Heap Exhaustion problem for Tomcat App Server APP-01?",
  //   "What is the triage or troubleshooting recommendation based on the Palo Alto Memory Leak problem?",
  //   "What is the triage or troubleshooting recommendation based on the Memory Leak problem?",
  //   "What is the triage or troubleshooting recommendation based on the Memory Leak problem and which other incidents has reported the same problem?",
];

function cleanAnswerText(answer) {
  return String(answer || "")
    .replace(/\[(?:INC|REQ|RITM|CHG|PRB)\d+\]/gi, "")
    .replace(
      /\s*(?:supported by|cited (?:incidents?|records?))[\s\.,;:]*$/i,
      "",
    )
    .split("\n")
    .map((line) =>
      line
        .replace(/\s+([,.;:!?])/g, "$1")
        .replace(/(?:,\s*){2,}/g, ", ")
        .replace(/,\s*(?:and|or)\s*([.;:!?])/gi, "$1")
        .replace(/\b(?:and|or)\s*([.;:!?])/gi, "$1")
        .replace(/,\s*([.;:!?])/g, "$1")
        .replace(/[ \t]{2,}/g, " ")
        .trim(),
    )
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export default function GraphRAGPortal() {
  const { currentUser, currentUserEmail, sessionId } = useStore();
  const [query, setQuery] = useState("");
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState([]);
  const [mode, setMode] = useState("graphrag");
  const [loading, setLoading] = useState(false);
  const [history, setHistory] = useState([]);

  const canSubmit = query.trim().length > 0 && !loading;

  const recentQueries = useMemo(() => history.slice(0, 6), [history]);

  const runQuery = async (rawQuery, forcedMode = null) => {
    const nextQuery = rawQuery.trim();
    if (!nextQuery || loading) return;
    const activeMode = forcedMode || mode;

    setLoading(true);
    setQuery(nextQuery);

    try {
      const queryFn =
        activeMode === "vectorrag" ? queryVectorRAG : queryGraphRAG;
      const result = await queryFn(nextQuery, {
        userSysId: currentUser?.sys_id,
        userEmail: currentUserEmail,
        sessionId,
      });
      const nextCitations =
        activeMode === "graphrag" ? result.citations || [] : [];
      setAnswer(result.answer || "");
      setCitations(nextCitations);
      setHistory((current) => {
        const deduped = current.filter(
          (entry) => !(entry.query === nextQuery && entry.mode === activeMode),
        );
        return [
          {
            query: nextQuery,
            answer: result.answer || "",
            citations: nextCitations,
            mode: activeMode,
          },
          ...deduped,
        ];
      });
    } catch (error) {
      setAnswer(
        error?.message ||
          "The query failed before an answer could be returned.",
      );
      setCitations([]);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    await runQuery(query);
  };

  return (
    <div className="flex flex-col h-screen bg-slate-50">
      <Header title="ServiceNow Explorer" />

      <div className="flex-1 overflow-hidden">
        <div className="h-full p-6">
          <div className="grid h-full grid-cols-[380px_minmax(0,1fr)] gap-6">
            <aside className="card flex flex-col overflow-hidden">
              <div className="border-b border-slate-200 px-5 py-4">
                <div className="flex items-center gap-2 text-slate-800">
                  <ListTree className="h-4 w-4 text-brand-600" />
                  <h2 className="text-sm font-semibold">User Queries</h2>
                </div>
                <p className="mt-1 text-xs text-slate-500">
                  Ask natural ServiceNow questions here without using the main
                  request chat.
                </p>
              </div>

              <div className="flex-1 overflow-y-auto px-4 py-4">
                {recentQueries.length > 0 && (
                  <div>
                    <p className="mb-3 text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">
                      Recent
                    </p>
                    <div className="space-y-2">
                      {recentQueries.map((entry) => (
                        <button
                          key={`${entry.mode}:${entry.query}`}
                          onClick={() => {
                            setMode(entry.mode || "graphrag");
                            runQuery(entry.query, entry.mode || "graphrag");
                          }}
                          className="w-full rounded-xl bg-slate-100 px-3 py-3 text-left text-sm text-slate-600 transition hover:bg-slate-200"
                        >
                          <div className="text-[11px] uppercase tracking-[0.14em] text-slate-400">
                            {(entry.mode || "graphrag") === "vectorrag"
                              ? "VectorRAG"
                              : "GraphRAG"}
                          </div>
                          <div>{entry.query}</div>
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                <div className={recentQueries.length > 0 ? "mt-6" : undefined}>
                  <p className="mb-3 text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">
                    Suggestions
                  </p>
                  <div className="space-y-2">
                    {SUGGESTED_QUERIES.map((item) => (
                      <button
                        key={item}
                        onClick={() => runQuery(item)}
                        className="w-full rounded-xl border border-slate-200 bg-white px-3 py-3 text-left text-sm text-slate-600 transition hover:border-brand-300 hover:bg-brand-50 hover:text-brand-700"
                      >
                        {item}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </aside>

            <section className="card flex min-w-0 flex-col overflow-hidden">
              <div className="border-b border-slate-200 px-6 py-5">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <div className="inline-flex items-center gap-2 rounded-full bg-brand-50 px-3 py-1 text-xs font-semibold text-brand-700">
                      <Database className="h-3.5 w-3.5" />
                      {mode === "vectorrag"
                        ? "VectorRAG solution answers"
                        : "ServiceNow-grounded answers"}
                    </div>
                    <h2 className="mt-3 text-2xl font-bold text-slate-900">
                      Ask Questions
                    </h2>
                    <p className="mt-2 max-w-2xl text-sm text-slate-500">
                      {mode === "vectorrag"
                        ? "Answer from solution library using traditional retrieve-and-generate flow."
                        : "Ask about catalog items, forms, schema details, and your tickets in one grounded workspace."}
                    </p>
                  </div>
                  <div className="flex rounded-xl border border-slate-200 bg-white p-1">
                    <button
                      type="button"
                      onClick={() => setMode("graphrag")}
                      className={clsx(
                        "rounded-lg px-3 py-1.5 text-xs font-semibold transition",
                        mode === "graphrag"
                          ? "bg-brand-600 text-white"
                          : "text-slate-600 hover:bg-slate-100",
                      )}
                    >
                      GraphRAG
                    </button>
                    <button
                      type="button"
                      onClick={() => setMode("vectorrag")}
                      className={clsx(
                        "rounded-lg px-3 py-1.5 text-xs font-semibold transition",
                        mode === "vectorrag"
                          ? "bg-brand-600 text-white"
                          : "text-slate-600 hover:bg-slate-100",
                      )}
                    >
                      VectorRAG
                    </button>
                  </div>
                </div>

                <form onSubmit={handleSubmit} className="mt-5 flex gap-3">
                  <div className="relative flex-1">
                    <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                    <input
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      placeholder={
                        mode === "vectorrag"
                          ? "Ask something like 'How do we mitigate SSL certificate expiry incidents?'"
                          : "Ask something like 'What triage path fits BGP resets during high switch CPU events?'"
                      }
                      className="input-field h-12 pl-10 pr-4"
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={!canSubmit}
                    className="btn-primary min-w-[140px] justify-center"
                  >
                    {loading ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <ArrowRight className="h-4 w-4" />
                    )}
                    Run Query
                  </button>
                </form>
              </div>

              <div className="flex-1 overflow-y-auto bg-[radial-gradient(circle_at_top,_rgba(37,99,235,0.06),_transparent_42%),linear-gradient(180deg,_#ffffff,_#f8fafc)] px-6 py-6">
                {!answer && !loading && (
                  <div className="flex h-full flex-col items-center justify-center text-center">
                    <Sparkles className="mb-4 h-12 w-12 text-brand-300" />
                    <h3 className="text-2xl font-bold text-slate-800">
                      Standalone Query Workspace
                    </h3>
                    <p className="mt-2 max-w-xl text-sm text-slate-500">
                      Start with one of the suggested questions or type your own
                      ServiceNow question above.
                    </p>
                  </div>
                )}

                {loading && (
                  <div className="flex h-full flex-col items-center justify-center text-center">
                    <Loader2 className="mb-4 h-10 w-10 animate-spin text-brand-500" />
                    <h3 className="text-lg font-semibold text-slate-800">
                      {mode === "vectorrag"
                        ? "Searching solution library"
                        : "Searching graph context"}
                    </h3>
                    <p className="mt-2 text-sm text-slate-500">
                      {mode === "vectorrag"
                        ? "Retrieving relevant solution documents from Neo4j vector index."
                        : "Retrieving catalog, schema, and ticket relationships from Neo4j."}
                    </p>
                  </div>
                )}

                {!loading && answer && (
                  <div className="mx-auto max-w-4xl">
                    <div className="rounded-2xl border border-slate-200 bg-white/90 p-6 shadow-sm backdrop-blur">
                      <div className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.2em] text-brand-600">
                        <Database className="h-3.5 w-3.5" />
                        {mode === "vectorrag"
                          ? "VectorRAG Answer"
                          : "GraphRAG Answer"}
                      </div>
                      <div
                        className={clsx(
                          "whitespace-pre-wrap text-sm leading-7 text-slate-700",
                          answer.length < 140 && "text-base",
                        )}
                      >
                        {cleanAnswerText(answer)}
                      </div>
                      {mode === "graphrag" && citations.length > 0 && (
                        <div className="mt-5 border-t border-slate-200 pt-5">
                          <div className="mb-3 text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">
                            Related records
                          </div>
                          <div className="grid gap-3 md:grid-cols-2">
                            {citations.map((citation, index) => (
                              <a
                                key={`${citation.sys_id || citation.number || index}`}
                                href={citation.url}
                                target="_blank"
                                rel="noreferrer"
                                className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700 transition hover:border-brand-300 hover:bg-brand-50"
                              >
                                <div className="flex items-center justify-between gap-3">
                                  <span className="font-semibold text-slate-900">
                                    {citation.number ||
                                      citation.name ||
                                      citation.short_description ||
                                      "Record"}
                                  </span>
                                  <ExternalLink className="h-4 w-4 shrink-0 text-brand-600" />
                                </div>
                                {(citation.label || citation.table) && (
                                  <div className="mt-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-brand-600">
                                    {citation.label || citation.table}
                                  </div>
                                )}
                                {citation.short_description && (
                                  <div className="mt-1 line-clamp-2 text-xs leading-5 text-slate-600">
                                    {citation.short_description}
                                  </div>
                                )}
                                <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] uppercase tracking-[0.12em] text-slate-400">
                                  {citation.state && (
                                    <span>{citation.state}</span>
                                  )}
                                  {citation.opened_at && (
                                    <span>{citation.opened_at}</span>
                                  )}
                                </div>
                              </a>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
