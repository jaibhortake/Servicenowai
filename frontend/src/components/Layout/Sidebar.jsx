import { NavLink } from "react-router-dom";
import { useEffect, useState } from "react";
import {
  LayoutDashboard,
  MessageSquarePlus,
  MessageSquare,
  ShieldCheck,
  Zap,
  PanelLeftClose,
  PanelLeftOpen,
  DatabaseZap,
  FilePlus2,
  MonitorCog,
} from "lucide-react";
import clsx from "clsx";
import {
  CASE_SESSION_UPDATED_EVENT,
  LAST_CASE_DASHBOARD_SESSION_KEY,
} from "../../utils/caseSession";
import {
  getLastViewChatTarget,
  VIEW_CHAT_TARGET_UPDATED_EVENT,
} from "../../utils/viewChatSession";

export const nav = [
  { to: "/", icon: MessageSquarePlus, label: "New Request", end: true },
  { to: "/graphrag", icon: DatabaseZap, label: "Query Panel" },
  { to: "/create-case", icon: FilePlus2, label: "Create Case" },
  { to: "/dashboard", icon: LayoutDashboard, label: "My Tickets" },
  { to: "/admin", icon: ShieldCheck, label: "Admin Panel", adminOnly: true },
];

export default function Sidebar({ collapsed = false, onToggle, isAdmin = true }) {
  const [hasCaseDashboard, setHasCaseDashboard] = useState(() =>
    Boolean(localStorage.getItem(LAST_CASE_DASHBOARD_SESSION_KEY)),
  );
  const [lastViewChatTarget, setLastViewChatTarget] = useState(() =>
    getLastViewChatTarget(),
  );

  useEffect(() => {
    const syncDashboardVisibility = () => {
      setHasCaseDashboard(
        Boolean(localStorage.getItem(LAST_CASE_DASHBOARD_SESSION_KEY)),
      );
    };
    const syncViewChatTarget = () => {
      setLastViewChatTarget(getLastViewChatTarget());
    };
    window.addEventListener("storage", syncDashboardVisibility);
    window.addEventListener(CASE_SESSION_UPDATED_EVENT, syncDashboardVisibility);
    window.addEventListener("storage", syncViewChatTarget);
    window.addEventListener(VIEW_CHAT_TARGET_UPDATED_EVENT, syncViewChatTarget);
    return () => {
      window.removeEventListener("storage", syncDashboardVisibility);
      window.removeEventListener(
        CASE_SESSION_UPDATED_EVENT,
        syncDashboardVisibility,
      );
      window.removeEventListener("storage", syncViewChatTarget);
      window.removeEventListener(VIEW_CHAT_TARGET_UPDATED_EVENT, syncViewChatTarget);
    };
  }, []);

  const item = (isActive) =>
    clsx(
      "flex items-center gap-3 rounded-lg text-sm font-medium transition-colors",
      collapsed ? "justify-center px-0 py-2.5" : "px-3 py-2.5",
      isActive
        ? "bg-brand-50 text-brand-700"
        : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
    );
  const subItem = (isActive) =>
    clsx(
      "mt-1 ml-7 flex items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium transition-colors",
      isActive ? "bg-slate-100 text-slate-900" : "text-slate-500 hover:bg-slate-100 hover:text-slate-900",
    );

  return (
    <aside
      className={clsx(
        "bg-white border-r border-slate-200 flex flex-col h-screen sticky top-0 transition-[width] duration-200",
        collapsed ? "w-16" : "w-60",
      )}
    >
      <div className={clsx("h-14 flex items-center border-b border-slate-200", collapsed ? "justify-center" : "px-4")}>
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-8 h-8 bg-brand-600 rounded-lg flex items-center justify-center shrink-0">
            <Zap className="w-4 h-4 text-white" />
          </div>
          {!collapsed && (
            <p className="text-slate-900 font-semibold text-sm truncate">Servicenowai</p>
          )}
        </div>
      </div>

      <nav className="flex-1 px-2.5 py-4 space-y-1 overflow-y-auto">
        {nav
          .filter((n) => isAdmin || !n.adminOnly)
          .map(({ to, icon: Icon, label, end }) => (
            <div key={to}>
              <NavLink
                to={to}
                end={end}
                title={collapsed ? label : undefined}
                className={({ isActive }) => item(isActive)}
              >
                <Icon className="w-4 h-4 flex-shrink-0" />
                {!collapsed && label}
              </NavLink>
              {!collapsed && to === "/" && lastViewChatTarget && (
                <NavLink
                  to={`/ticket/${lastViewChatTarget.ticketType}/${lastViewChatTarget.sysId}`}
                  state={{ sessionId: lastViewChatTarget.sessionId || "" }}
                  className={({ isActive }) => subItem(isActive)}
                >
                  <MessageSquare className="h-3.5 w-3.5 shrink-0" />
                  View & Chat
                </NavLink>
              )}
              {!collapsed && to === "/create-case" && hasCaseDashboard && isAdmin && (
                <NavLink to="/create-case/agent-dashboard" className={({ isActive }) => subItem(isActive)}>
                  <MonitorCog className="h-3.5 w-3.5 shrink-0" />
                  Agent Dashboard
                </NavLink>
              )}
            </div>
          ))}
      </nav>

      <div className="p-2.5 border-t border-slate-200">
        <button
          onClick={onToggle}
          className={clsx("btn-ghost w-full", collapsed ? "justify-center px-0" : "")}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? <PanelLeftOpen className="w-4 h-4" /> : <PanelLeftClose className="w-4 h-4" />}
          {!collapsed && "Collapse"}
        </button>
      </div>
    </aside>
  );
}
