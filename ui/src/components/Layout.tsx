import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { api } from "../api";
import { getUnplayable, isGone, subscribeRemovals } from "../utils/puzzleRemoval";
import { getUnsavedAttempt, subscribeUnsavedAttempt } from "../utils/unsavedAttempt";

// A grouped compact top bar: the four primary pages inline, the rest under "More", and on a
// narrow screen a hamburger that lists everything. The header fetches nothing.
type Item = { to: string; label: string };
const PRIMARY: Item[] = [
  { to: "/", label: "Home" },
  { to: "/practice", label: "Practice" },
  { to: "/blunders", label: "Blunders" },
  { to: "/deviations", label: "Deviations" },
];
const MORE: Item[] = [
  { to: "/repertoire", label: "Repertoire" },
  { to: "/games", label: "Games" },
  { to: "/preferences", label: "Preferences" },
];

const linkClass = (isActive: boolean) => `text-sm ${isActive ? "text-zinc-900 dark:text-zinc-100" : "text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200"}`;

function Links({ items, held, heldTitle, onPick, role }: { items: Item[]; held: boolean; heldTitle: string; onPick?: () => void; role?: "menuitem" }) {
  return (
    <>
      {items.map((n) =>
        held ? (
          <span key={n.to} role={role} aria-disabled="true" title={heldTitle} className="text-sm text-zinc-300 dark:text-zinc-600">
            {n.label}
          </span>
        ) : (
          <NavLink key={n.to} to={n.to} end={n.to === "/"} role={role} onClick={onPick} className={({ isActive }) => linkClass(isActive)}>
            {n.label}
          </NavLink>
        ),
      )}
    </>
  );
}

export default function Layout({ onLoggedOut }: { onLoggedOut: () => void }) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  // While Practice holds an attempt it could not save, leaving the page would lose it, so
  // the header's links wait for the save.
  const unsaved = useSyncExternalStore(subscribeUnsavedAttempt, getUnsavedAttempt, getUnsavedAttempt) !== null;
  // Likewise while a puzzle removal is waiting for the server's answer.
  const removing = [...useSyncExternalStore(subscribeRemovals, getUnplayable, getUnplayable)].some((id) => !isGone(id));
  const held = unsaved || removing;
  const heldTitle = unsaved ? "Save your attempt on the Practice page first" : "A puzzle is being removed — one moment";
  const [moreOpen, setMoreOpen] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  const moreRef = useRef<HTMLDivElement>(null);
  const headerRef = useRef<HTMLElement>(null);
  const underMore = MORE.some((n) => pathname === n.to || pathname.startsWith(`${n.to}/`));

  // Both close on a route change (state adjusted during render), Escape, and a click outside.
  const [seenPath, setSeenPath] = useState(pathname);
  if (seenPath !== pathname) {
    setSeenPath(pathname);
    setMoreOpen(false);
    setPanelOpen(false);
  }
  useEffect(() => {
    if (!moreOpen && !panelOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setMoreOpen(false);
        setPanelOpen(false);
      }
    };
    const onClick = (e: MouseEvent) => {
      if (moreOpen && moreRef.current && !moreRef.current.contains(e.target as Node)) setMoreOpen(false);
      if (panelOpen && headerRef.current && !headerRef.current.contains(e.target as Node)) setPanelOpen(false);
    };
    window.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [moreOpen, panelOpen]);

  function onMenuKey(e: ReactKeyboardEvent) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const items = [...(moreRef.current?.querySelectorAll<HTMLAnchorElement>('a[role="menuitem"]') ?? [])];
    if (items.length === 0) return;
    const at = items.findIndex((el) => el === document.activeElement);
    const next = e.key === "ArrowDown" ? (at + 1) % items.length : (at - 1 + items.length) % items.length;
    items[next].focus();
  }

  async function logout() {
    if (held) return;
    await api.post("/logout");
    onLoggedOut();
    navigate("/login");
  }

  const close = () => {
    setMoreOpen(false);
    setPanelOpen(false);
  };

  return (
    <div className="min-h-screen">
      <header ref={headerRef} className="border-b border-zinc-200 dark:border-zinc-800">
        <nav className="mx-auto flex max-w-5xl items-center gap-4 px-4 py-3">
          <span className="font-semibold tracking-tight">BlundrIQ</span>
          <div className="hidden items-center gap-4 sm:flex">
            <Links items={PRIMARY} held={held} heldTitle={heldTitle} />
            <div ref={moreRef} className="relative" onKeyDown={onMenuKey}>
              <button type="button" aria-haspopup="menu" aria-expanded={moreOpen} onClick={() => setMoreOpen((o) => !o)} className={linkClass(underMore)}>
                More ▾
              </button>
              {moreOpen && (
                <div role="menu" className="absolute right-0 z-20 mt-2 flex min-w-32 flex-col gap-2 rounded border border-zinc-200 bg-white px-3 py-2 shadow dark:border-zinc-800 dark:bg-zinc-950">
                  <Links items={MORE} held={held} heldTitle={heldTitle} onPick={close} role="menuitem" />
                </div>
              )}
            </div>
          </div>
          <button type="button" aria-label="Menu" aria-expanded={panelOpen} aria-controls="nav-panel" onClick={() => setPanelOpen((o) => !o)} className="text-sm text-zinc-500 hover:text-zinc-800 sm:hidden dark:hover:text-zinc-200">
            ☰
          </button>
          <button onClick={logout} disabled={held} title={held ? heldTitle : undefined} className="ml-auto text-sm text-zinc-500 hover:text-zinc-800 disabled:opacity-50 dark:hover:text-zinc-200">
            Log out
          </button>
        </nav>
        {panelOpen && (
          <div id="nav-panel" className="border-t border-zinc-200 sm:hidden dark:border-zinc-800">
            <div className="mx-auto flex max-w-5xl flex-col gap-2 px-4 py-3">
              <Links items={[...PRIMARY, ...MORE]} held={held} heldTitle={heldTitle} onPick={close} />
              <button onClick={logout} disabled={held} title={held ? heldTitle : undefined} className="text-left text-sm text-zinc-500 hover:text-zinc-800 disabled:opacity-50 dark:hover:text-zinc-200">
                Log out
              </button>
            </div>
          </div>
        )}
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
