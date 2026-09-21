import { NavLink, Outlet, useNavigate } from "react-router";
import { api } from "../api";

// Pages arrive phase by phase; a link is added here when its page exists.
const NAV: Array<{ to: string; label: string }> = [
  { to: "/", label: "Home" },
  { to: "/practice", label: "Practice" },
  { to: "/games", label: "Games" },
  { to: "/preferences", label: "Preferences" },
];

export default function Layout({ onLoggedOut }: { onLoggedOut: () => void }) {
  const navigate = useNavigate();
  async function logout() {
    await api.post("/logout");
    onLoggedOut();
    navigate("/login");
  }
  return (
    <div className="min-h-screen">
      <header className="border-b border-zinc-200 dark:border-zinc-800">
        <nav className="mx-auto flex max-w-5xl items-center gap-4 px-4 py-3">
          <span className="font-semibold tracking-tight">BlundrIQ</span>
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/"}
              className={({ isActive }) =>
                `text-sm ${isActive ? "text-zinc-900 dark:text-zinc-100" : "text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200"}`
              }
            >
              {n.label}
            </NavLink>
          ))}
          <button onClick={logout} className="ml-auto text-sm text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200">
            Log out
          </button>
        </nav>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
