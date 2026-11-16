import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import { onlineManager, useQueryClient } from '@tanstack/react-query'
import type { QueryClient } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from '../src/App'
import { SessionGate } from '../src/api/session'
import { retainWorkflow, workflowKey } from '../src/api/workflow-state'
import type { Workflow } from '../src/api/client'
import { alignPairs } from '../src/reports/screens'
import { catalog, complete, finding, recommendation, workflow } from './fixtures'

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { 'Content-Type': 'application/json' },
})

async function mount(path: string, handler: (path: URL, options?: RequestInit) => Response | Promise<Response>,
                     onCache?: (cache: QueryClient) => void) {
  function ObserveCache() {
    const cache = useQueryClient()
    onCache?.(cache)
    return null
  }
  vi.stubGlobal('fetch', vi.fn((input: string, options?: RequestInit) => handler(new URL(input, 'http://127.0.0.1'), options)))
  const user = userEvent.setup()
  render(<MemoryRouter initialEntries={[path]}><SessionGate><ObserveCache /><App /></SessionGate></MemoryRouter>)
  await user.type(screen.getByLabelText('Local API token'), 'example-test-only-token')
  await user.click(screen.getByRole('button', { name: 'Connect' }))
  return user
}
afterEach(() => { cleanup(); onlineManager.setOnline(true); vi.unstubAllGlobals() })

function delayedResponse() {
  let send!: (response: Response) => void
  const response = new Promise<Response>(resolve => { send = resolve })
  return { response, send }
}

describe('Workflow Explorer', () => {
  it('clears authenticated content when a request receives 401', async () => {
    await mount('/workflows', url => url.pathname.endsWith('catalog') ? json(catalog) : json({}, 401))
    await screen.findByRole('alert')
    expect(screen.getByLabelText('Local API token')).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Workflow history' })).toBeNull()
    expect(window.localStorage.length + window.sessionStorage.length).toBe(0)
  })

  it('retains a submission key after an ambiguous response', async () => {
    const keys: Array<string | null> = []
    const user = await mount('/workflows/new', (url, options) => {
      if (url.pathname.endsWith('catalog')) return json(catalog)
      if (options?.method === 'POST') {
        keys.push(new Headers(options.headers).get('Idempotency-Key'))
        if (keys.length === 1) throw new TypeError('lost')
      }
      return json(complete)
    })
    await user.selectOptions(await screen.findByLabelText('Left source'), 'demo-source-a')
    await user.selectOptions(screen.getByLabelText('Right source'), 'demo-source-b')
    await user.click(screen.getByRole('button', { name: 'Register workflow' }))
    await screen.findByRole('alert')
    await user.click(screen.getByRole('button', { name: 'Register workflow' }))
    await screen.findByRole('heading', { name: 'Execution stages' })
    expect(keys[0]).toMatch(/^[0-9a-f-]{36}$/)
    expect(keys).toEqual([keys[0], keys[0]])
  })

  it('traverses three finding pages exactly once and resets the cursor on side change', async () => {
    const user = await mount('/workflows/1/quality', url => {
      if (url.pathname.endsWith('catalog')) return json(catalog)
      if (!url.pathname.endsWith('quality-findings')) return json(complete)
      const cursor = url.searchParams.get('cursor')
      if (url.searchParams.get('side') === 'right') {
        expect(cursor).toBeNull()
        return json({ items: [], next_cursor: null })
      }
      const n = Number(cursor) || 1
      return json({ items: [finding(n)], next_cursor: n < 3 ? String(n + 1) : null })
    })
    for (let page = 1; page <= 3; page++) {
      await screen.findByRole('heading', { name: `Finding ${page}` })
      expect(screen.getAllByRole('article')).toHaveLength(1)
      if (page < 3) await user.click(screen.getByRole('button', { name: 'Next page' }))
    }
    expect((screen.getByRole('button', { name: 'Next page' }) as HTMLButtonElement).disabled).toBe(true)
    await user.click(screen.getByRole('link', { name: 'Right source' }))
    await screen.findByRole('heading', { name: 'No project quality findings for this source' })
    expect(screen.getByText('Page 1')).toBeTruthy()
  })

  it('reports invalid cursors and recovers from the first page', async () => {
    const user = await mount('/workflows/1/quality', url => {
      if (url.pathname.endsWith('catalog')) return json(catalog)
      if (!url.pathname.endsWith('quality-findings')) return json(complete)
      return url.searchParams.has('cursor')
        ? json({ error: { code: 'INVALID_CURSOR' } }, 400)
        : json({ items: [finding(1)], next_cursor: 'invalid' })
    })
    await screen.findByRole('heading', { name: 'Finding 1' })
    await user.click(screen.getByRole('button', { name: 'Next page' }))
    expect((await screen.findByRole('alert')).textContent).toContain('first page')
    await user.click(screen.getByRole('button', { name: 'First page' }))
    await screen.findByRole('heading', { name: 'Finding 1' })
  })

  it('shows interrupted and failed stages and retains earlier report navigation', async () => {
    const data = workflow(['completed', 'completed', 'completed', 'failed', 'interrupted'])
    data.stages[3].failure = { code: 'VALIDATION_ENGINE_FAILED', retryable: false }
    const user = await mount('/workflows/1', url => url.pathname.endsWith('catalog') ? json(catalog) :
      url.pathname.endsWith('quality-findings') ? json({ items: [finding(1)], next_cursor: null }) : json(data))
    const execution = await screen.findByRole('region', { name: 'Execution stages' })
    expect(within(execution).getByText('Interrupted')).toBeTruthy()
    expect((within(execution).getByRole('button', { name: 'Retry stage' }) as HTMLButtonElement).disabled).toBe(true)
    await user.click(screen.getByRole('link', { name: 'Quality findings' }))
    await screen.findByRole('heading', { name: 'Finding 1' })
    await user.click(screen.getByRole('link', { name: 'Right source' }))
    expect(screen.getByText('This report could not be published.')).toBeTruthy()
  })

  it.each(['failed', 'mismatched'])('reports a %s matching page explicitly', async problem => {
    await mount('/workflows/1/matching', url => {
      if (url.pathname.endsWith('catalog')) return json(catalog)
      if (!url.pathname.endsWith('recommendations')) return json(complete)
      if (url.searchParams.get('method') === 'exact') return json({ items: [recommendation('exact')], next_cursor: null })
      return problem === 'failed' ? json({ error: { code: 'WORKFLOW_NOT_FOUND' } }, 404) :
        json({ items: [recommendation('statistical', 2)], next_cursor: null })
    })
    expect((await screen.findByRole('alert')).textContent).toContain('Comparison unavailable')
    expect(screen.queryByRole('heading', { name: 'Candidate pair' })).toBeNull()
  })

  it('sends one guarded request for rapid clicks and checks status after contention', async () => {
    const data = workflow([])
    const attempts: Array<string | null> = []
    let refreshes = 0
    const user = await mount('/workflows/1', (url, options) => {
      if (url.pathname.endsWith('catalog')) return json(catalog)
      if (options?.method === 'POST') {
        attempts.push(new Headers(options.headers).get('X-Expected-Stage'))
        return json({ error: { code: 'EXECUTION_CAPACITY_BUSY' } }, 503)
      }
      refreshes++
      return json(refreshes > 1 ? workflow(['running']) : data)
    })
    await user.dblClick(await screen.findByRole('button', { name: 'Advance one stage' }))
    await screen.findByRole('button', { name: 'Check and resume' })
    expect(attempts).toEqual(['ingest_left:pending:0'])
    expect(refreshes).toBeGreaterThan(1)
  })

  it.each(['submission', 'advancement'])('attempts offline %s once and never replays on reconnect', async action => {
    let cache!: QueryClient
    const posts: Array<string | null> = []
    const user = await mount(action === 'submission' ? '/workflows/new' : '/workflows/1', (url, options) => {
      if (url.pathname.endsWith('catalog')) return json(catalog)
      if (options?.method === 'POST') {
        posts.push(new Headers(options.headers).get('Idempotency-Key'))
        throw new TypeError('offline')
      }
      return json(workflow([]))
    }, c => { cache = c })
    if (action === 'submission') {
      await user.selectOptions(await screen.findByLabelText('Left source'), 'demo-source-a')
      await user.selectOptions(screen.getByLabelText('Right source'), 'demo-source-b')
    } else {
      await screen.findByRole('heading', { name: 'Execution stages' })
    }
    onlineManager.setOnline(false)
    const button = screen.getByRole('button', { name: action === 'submission' ? 'Register workflow' : 'Advance one stage' })
    await user.click(button)
    expect(posts).toHaveLength(1)
    expect(cache.getMutationCache().getAll()[0].state).toMatchObject({ status: 'error', isPaused: false })
    await act(async () => {
      onlineManager.setOnline(true)
      await cache.resumePausedMutations()
    })
    expect(posts).toHaveLength(1)
    if (action === 'submission') {
      await user.click(button)
      expect(posts).toEqual([posts[0], posts[0]])
    }
  })

  it('cancels a delayed detail GET before applying the confirmed POST and targets the next stage', async () => {
    let cache!: QueryClient
    let gets = 0
    const old = delayedResponse()
    const held = delayedResponse()
    const headers: Array<string | null> = []
    let olderSignal: AbortSignal | null | undefined
    const user = await mount('/workflows/1', (url, options) => {
      if (url.pathname.endsWith('catalog')) return json(catalog)
      if (options?.method === 'POST') {
        headers.push(new Headers(options.headers).get('X-Expected-Stage'))
        return headers.length === 1 ? held.response : json(workflow(['completed', 'completed']))
      }
      if (++gets === 1) return json(workflow([]))
      olderSignal = options?.signal
      return old.response
    }, c => { cache = c })
    await screen.findByText('0 of 5 stages completed')
    let get!: Promise<void>
    act(() => { get = cache.refetchQueries({ queryKey: workflowKey(1), exact: true }) })
    await waitFor(() => expect(gets).toBe(2))
    await user.click(screen.getByRole('button', { name: 'Advance one stage' }))
    await act(async () => { held.send(json(workflow(['completed']))) })
    await screen.findByText('1 of 5 stages completed')
    expect(olderSignal?.aborted).toBe(true)
    await act(async () => { old.send(json(workflow([]))); await get })
    expect(cache.getQueryData<Workflow>(workflowKey(1))?.stages[0].status).toBe('completed')
    expect(screen.getByText('1 of 5 stages completed')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Advance one stage' }))
    expect(headers).toEqual(['ingest_left:pending:0', 'ingest_right:pending:0'])
  })

  it.each(['manual', 'recovery'])('keeps newer progress when an overlapping %s read finishes late', async kind => {
    let cache!: QueryClient
    const old = delayedResponse()
    let gets = 0
    const user = await mount('/workflows/1', (url, options) => {
      if (url.pathname.endsWith('catalog')) return json(catalog)
      if (options?.method === 'POST') throw new TypeError('response lost')
      if (++gets === 1) return json(workflow([]))
      return gets === 2 ? old.response : json(workflow(['completed']))
    }, c => { cache = c })
    await screen.findByText('0 of 5 stages completed')
    await user.click(screen.getByRole('button', { name: kind === 'manual' ? 'Check status' : 'Advance one stage' }))
    await waitFor(() => expect(gets).toBe(2))
    await act(async () => { await cache.refetchQueries({ queryKey: workflowKey(1), exact: true }) })
    await screen.findByText('1 of 5 stages completed')
    await act(async () => { old.send(json(workflow([]))) })
    await waitFor(() => expect((screen.getByRole('button', { name: 'Check status' }) as HTMLButtonElement).disabled)
      .toBe(false))
    expect(cache.getQueryData<Workflow>(workflowKey(1))?.stages[0].status).toBe('completed')
    expect(screen.getByText('1 of 5 stages completed')).toBeTruthy()
    expect(gets).toBe(3)
  })

  it('rejects an older detail GET at cache commit after a newer manual result', async () => {
    let cache!: QueryClient
    const old = delayedResponse()
    let gets = 0
    const user = await mount('/workflows/1', url => {
      if (url.pathname.endsWith('catalog')) return json(catalog)
      if (++gets === 1) return json(workflow([]))
      return gets === 2 ? old.response : json(workflow(['completed']))
    }, c => { cache = c })
    await screen.findByText('0 of 5 stages completed')
    let get!: Promise<void>
    act(() => { get = cache.refetchQueries({ queryKey: workflowKey(1), exact: true }) })
    await waitFor(() => expect(gets).toBe(2))
    await user.click(screen.getByRole('button', { name: 'Check status' }))
    await screen.findByText('1 of 5 stages completed')
    await act(async () => { old.send(json(workflow([]))); await get })
    expect(cache.getQueryData<Workflow>(workflowKey(1))?.stages[0].status).toBe('completed')
    expect(screen.getByText('1 of 5 stages completed')).toBeTruthy()
  })

  it.each(['failed', 'interrupted', 'completed'] as const)('retains a durable %s outcome against an older running read', status => {
    const running = workflow(['running'])
    const outcome = workflow([status])
    expect(retainWorkflow(running, outcome)).toEqual(outcome)
    expect(retainWorkflow(outcome, running)).toBe(outcome)
    if (status !== 'completed') {
      running.stages[0].attempt_count++
      expect(retainWorkflow(outcome, running)).toEqual(running)
    }
  })

  it('aligns methods by identity instead of array position', () => {
    const exact = { items: [recommendation('exact', 1), recommendation('exact', 2)], next_cursor: null }
    const other = { items: [recommendation('statistical', 2), recommendation('statistical', 1)], next_cursor: null }
    expect(alignPairs(exact, other).map(row => row.statistical.left_id)).toEqual(['left-1', 'left-2'])
    expect(() => alignPairs(exact, { ...other, items: [other.items[0]] })).toThrow()
  })
})
