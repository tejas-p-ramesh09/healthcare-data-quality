import { useEffect, useRef } from 'react'
import { Link, Navigate, NavLink, Outlet, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useSession } from './api/session'
import type { WorkflowContext } from './ui'
import { ErrorNotice, Loading, Status } from './ui'
import { CreateWorkflow, Overview, WorkflowDetail } from './workflows/screens'
import { Quality, Matching, Provenance } from './reports/screens'

function Shell() {
  const { disconnect } = useSession()
  const { pathname } = useLocation()
  const content = useRef<HTMLElement>(null)
  useEffect(() => { content.current?.focus() }, [pathname])

  return <div className="workspace">
    <a className="skip" href="#main">Skip to content</a>
    <aside className="rail">
      <Link className="brand" to="/workflows">
        <span className="brand-mark" aria-hidden="true">+</span>
        <span>Workflow Explorer<small>Healthcare Data Quality</small></span>
      </Link>
      <nav aria-label="Workspace" className="primary-nav">
        <NavLink to="/workflows" end><span aria-hidden="true">▤</span> Workflows</NavLink>
        <NavLink to="/workflows/new"><span aria-hidden="true">＋</span> New workflow</NavLink>
      </nav>
      <div className="rail-foot"><span aria-hidden="true" /> Local synthetic workspace</div>
    </aside>
    <div className="main-stack">
      <header className="topbar">
        <span>Healthcare data operations</span>
        <button className="quiet" onClick={disconnect}>Disconnect</button>
      </header>
      <main id="main" tabIndex={-1} ref={content} className="page"><Outlet /></main>
    </div>
  </div>
}

function WorkflowRoute() {
  const id = Number(useParams().id)
  if (!Number.isInteger(id) || id < 1 || id > 2147483647) return <NotFound />
  return <WorkflowBoundary key={id} id={id} />
}

function WorkflowBoundary({ id }: { id: number }) {
  const { api } = useSession()
  const result = useQuery({ queryKey: ['workflow', id], queryFn: ({ signal }) => api.workflow(id, signal) })
  if (result.isPending) return <Loading>Loading workflow…</Loading>
  if (!result.data) return <ErrorNotice error={result.error} />
  const workflow = result.data
  const base = `/workflows/${id}`
  return <>
    <Link className="back" to="/workflows">← All workflows</Link>
    <div className="page-heading detail-heading">
      <div><p className="eyebrow">Workflow #{id}</p><h1>{workflow.inputs.map(i => i.source).join(' / ')}</h1>
        <p>Profile <strong>{workflow.profile}</strong></p></div>
      <Status state={workflow.status} />
    </div>
    <nav aria-label="Workflow sections" className="report-nav">
      <NavLink to={base} end>Execution</NavLink>
      <NavLink to={base + '/quality'}>Quality findings</NavLink>
      <NavLink to={base + '/matching'}>Matching</NavLink>
      <NavLink to={base + '/provenance'}>Provenance</NavLink>
    </nav>
    {result.isError && <ErrorNotice error={result.error} />}
    <Outlet context={{ workflow } satisfies WorkflowContext} />
  </>
}

function NotFound() {
  return <section className="surface"><h1>Page unavailable</h1><p>Check the workflow address or open the workflow list.</p>
    <Link to="/workflows">All workflows</Link></section>
}

export function App() {
  return <Routes><Route element={<Shell />}>
    <Route index element={<Navigate to="/workflows" replace />} />
    <Route path="workflows" element={<Overview />} />
    <Route path="workflows/new" element={<CreateWorkflow />} />
    <Route path="workflows/:id" element={<WorkflowRoute />}>
      <Route index element={<WorkflowDetail />} />
      <Route path="quality" element={<Quality />} />
      <Route path="matching" element={<Matching />} />
      <Route path="provenance" element={<Provenance />} />
    </Route>
    <Route path="*" element={<NotFound />} />
  </Route></Routes>
}
