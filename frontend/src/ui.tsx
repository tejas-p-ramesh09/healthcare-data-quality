import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError } from './api/client'
import type { Side, Workflow } from './api/client'

export type WorkflowContext = { workflow: Workflow }

export function ErrorNotice({ error }: { error: unknown }) {
  const apiError = error instanceof ApiError ? error : null
  return <div className="callout error" role="alert">
    <strong>{apiError?.message ?? 'This view could not be loaded.'}</strong>
    {apiError?.requestId && <small>Request ID <code>{apiError.requestId}</code></small>}
  </div>
}

export function Loading({ children = 'Loading…' }: { children?: string }) {
  return <p className="callout" role="status">{children}</p>
}

export function Status({ state }: { state: string }) {
  const labels: Record<string, string> = {
    pending: 'Pending', running: 'Running', failed: 'Failed',
    interrupted: 'Interrupted', completed: 'Completed',
  }
  return <span className={`status status-${Object.hasOwn(labels, state) ? state : 'unknown'}`}>
    <span aria-hidden="true" />{labels[state] ?? 'Unknown'}
  </span>
}

export function DateTime({ value }: { value: string }) {
  const time = new Date(value)
  return Number.isFinite(time.valueOf())
    ? <time dateTime={value} title={value}>{new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium', timeStyle: 'short',
    }).format(time)}</time> : <span>Unknown time</span>
}

export function usePages<T>(first: T) {
  const [pages, setPages] = useState([first])
  return {
    index: pages.length, cursor: pages.at(-1)!,
    back: () => setPages(previous => previous.length > 1 ? previous.slice(0, -1) : previous),
    next: (cursor: T) => setPages(previous => [...previous, cursor]),
    reset: () => setPages([first]),
  }
}

export function Pagination({ index, back, next, reset }: {
  index: number; back: () => void; next?: () => void; reset: () => void
}) {
  return <nav className="pagination" aria-label="Pagination">
    <span aria-live="polite">Page {index}</span>
    <button className="quiet" onClick={reset} disabled={index === 1}>First page</button>
    <button className="quiet" onClick={back} disabled={index === 1}>Previous page</button>
    <button className="secondary" onClick={next} disabled={!next}>Next page</button>
  </nav>
}

export function SideNav({ side, url }: { side: Side; url: string }) {
  return <nav aria-label="Source side" className="segments">
    {(['left', 'right'] as const).map(s => <Link key={s} to={`${url}?side=${s}`}
      aria-current={s === side ? 'page' : undefined}>{s === 'left' ? 'Left source' : 'Right source'}</Link>)}
  </nav>
}

export function readCount(counts: Record<string, unknown> | null, key: string) {
  const value = counts?.[key]
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : undefined
}

export function Hash({ value }: { value: string }) {
  return <code className="hash">{value}</code>
}

export function provenanceUrl(id: number, side: Side, batch: number, label: string, hash: string, entry: number) {
  return `/workflows/${id}/provenance?${new URLSearchParams({
    side, batch: String(batch), file: label, sha: hash, entry: String(entry),
  })}`
}
