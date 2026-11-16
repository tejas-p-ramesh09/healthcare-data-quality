import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createContext, useContext, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { ApiError, makeApi, retryRead } from './client'
import type { Api } from './client'

const Session = createContext<{ api: Api; disconnect: () => void; isActive: () => boolean } | null>(null)
export function useSession() {
  const value = useContext(Session)
  if (!value) throw new Error('Missing local session')
  return value
}

export function SessionGate({ children }: { children: ReactNode }) {
  const active = useRef<symbol | null>(null)
  const [session, setSession] = useState<{ api: Api; query: QueryClient; id: symbol } | null>(null)
  const [notice, setNotice] = useState('')
  const [connecting, setConnecting] = useState(false)

  async function connect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (connecting) return
    const form = event.currentTarget
    const input = form.elements.namedItem('token') as HTMLInputElement
    const token = input.value.trim()
    input.value = ''
    setConnecting(true)
    setNotice('')
    try {
      const id = Symbol()
      const query = new QueryClient({ defaultOptions: {
        queries: { retry: retryRead, staleTime: 2_000 }, mutations: { retry: false },
      } })
      const api = makeApi(token, () => {
        if (active.current !== id) return
        active.current = null
        api.close()
        void query.cancelQueries()
        query.clear()
        setSession(null)
        setNotice('Session expired. Enter the local API token again.')
      })
      await api.catalog()
      active.current = id
      setSession({ api, query, id })
    } catch (error) {
      setNotice(error instanceof ApiError ? error.message : 'Unable to connect to the local API.')
      input.focus()
    } finally {
      setConnecting(false)
    }
  }

  if (!session) return <main className="connect-shell">
    <section className="connect-card" aria-labelledby="connect-title">
      <div className="brand-mark" aria-hidden="true">+</div>
      <p className="eyebrow">Synthetic FHIR workspace</p>
      <h1 id="connect-title">Workflow Explorer</h1>
      <p>Connect to your local Healthcare Data Quality API.</p>
      <form onSubmit={connect} autoComplete="off">
        <label htmlFor="token">Local API token</label>
        <input autoComplete="off" id="token" name="token" type="password" required spellCheck={false} />
        <p className="hint">Use the workflow token from your local setup. Reconnect after refreshing this tab.</p>
        {notice && <p className="callout error" role="alert">{notice}</p>}
        <button className="primary" disabled={connecting}>{connecting ? 'Connecting…' : 'Connect'}</button>
      </form>
    </section>
  </main>

  function disconnect() {
    active.current = null
    session?.api.close()
    void session?.query.cancelQueries()
    session?.query.clear()
    setNotice('')
    setSession(null)
  }

  return <Session.Provider value={{ api: session.api, disconnect, isActive: () => active.current === session.id }}>
    <QueryClientProvider client={session.query}>{children}</QueryClientProvider>
  </Session.Provider>
}
