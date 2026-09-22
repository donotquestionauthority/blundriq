import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router";
import { api, ApiError } from "./api";
import Layout from "./components/Layout";
import Blunders from "./pages/Blunders";
import Games from "./pages/Games";
import Home from "./pages/Home";
import Login from "./pages/Login";
import Practice from "./pages/Practice";
import Preferences from "./pages/Preferences";

type AuthState = "checking" | "in" | "out";

export default function App() {
  const [auth, setAuth] = useState<AuthState>("checking");
  const location = useLocation();

  useEffect(() => {
    api
      .get<{ authenticated: boolean }>("/me")
      .then(() => setAuth("in"))
      .catch((e: unknown) => setAuth(e instanceof ApiError && e.status === 401 ? "out" : "out"));
  }, [location.pathname]);

  if (auth === "checking") return <p className="p-6 text-sm text-zinc-500">…</p>;
  if (auth === "out" && location.pathname !== "/login") return <Navigate to="/login" replace />;

  return (
    <Routes>
      <Route path="/login" element={<Login onLoggedIn={() => setAuth("in")} />} />
      <Route element={<Layout onLoggedOut={() => setAuth("out")} />}>
        <Route path="/" element={<Home />} />
        <Route path="/blunders" element={<Blunders />} />
        <Route path="/games" element={<Games />} />
        <Route path="/practice" element={<Practice />} />
        <Route path="/preferences" element={<Preferences />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
