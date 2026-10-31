import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

type Handler = (url: string, init?: RequestInit) => unknown;
export interface Routed {
  match: string | RegExp;
  method?: string;
  status?: number;
  body?: unknown | Handler;
}

/** Replaces global fetch with a router over (method, url) → canned JSON. Unmatched calls fail loudly. */
export function mockApi(routes: Routed[]) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const method = (init?.method ?? "GET").toUpperCase();
    const r = routes.find((x) => (x.method ?? "GET") === method && (typeof x.match === "string" ? url.includes(x.match) : x.match.test(url)));
    if (!r) throw new Error(`unmocked ${method} ${url}`);
    const body = typeof r.body === "function" ? (r.body as Handler)(url, init) : r.body;
    const status = r.status ?? 200;
    return new Response(status === 204 ? null : JSON.stringify(body ?? null), { status, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

export function renderAt(ui: ReactElement, path: string, pattern = "*") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={pattern} element={ui} />
      </Routes>
    </MemoryRouter>,
  );
}
