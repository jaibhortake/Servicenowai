import { useStore } from "../../store/useStore";

export default function Header({ title, subtitle, actions }) {
  const currentUser = useStore((s) => s.currentUser);

  return (
    <header className="bg-white/80 backdrop-blur border-b border-slate-200 px-6 py-3.5 flex items-center justify-between sticky top-0 z-10">
      <div className="min-w-0">
        <h1 className="text-base font-semibold text-slate-900 truncate">{title}</h1>
        {subtitle && <p className="text-xs text-slate-500 truncate">{subtitle}</p>}
      </div>
      <div className="flex items-center gap-3">
        {actions}
        {currentUser?.name && (
          <span className="hidden sm:inline text-xs text-slate-500">{currentUser.name}</span>
        )}
      </div>
    </header>
  );
}
