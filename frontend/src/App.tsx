import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import { Empty } from "./components/ui";
import { About } from "./pages/About";
import { ExperimentDetail } from "./pages/ExperimentDetail";
import { Experiments } from "./pages/Experiments";
import { NewExperiment } from "./pages/NewExperiment";
import { ScenarioEditor } from "./pages/ScenarioEditor";
import { Scenarios } from "./pages/Scenarios";

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Scenarios />} />
      <Route path="/scenarios/new" element={<ScenarioEditor />} />
      <Route path="/scenarios/:id" element={<ScenarioEditor />} />
      <Route path="/experiments" element={<Experiments />} />
      <Route path="/experiments/new" element={<NewExperiment />} />
      <Route path="/experiments/:id" element={<ExperimentDetail />} />
      <Route path="/about" element={<About />} />
      <Route path="*" element={<Empty title="Page not found" />} />
    </Routes>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <header className="topbar">
        <div className="shell">
          <a className="brand" href="/">
            Simu<span>Loom</span>
          </a>
          <nav aria-label="Main">
            <NavLink to="/" end>
              Scenarios
            </NavLink>
            <NavLink to="/experiments" end>
              Experiments
            </NavLink>
            <NavLink to="/experiments/new">Run experiment</NavLink>
            <NavLink to="/about">About</NavLink>
          </nav>
        </div>
      </header>
      <main className="shell">
        <AppRoutes />
      </main>
    </BrowserRouter>
  );
}
