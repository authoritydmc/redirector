import { useEffect, useState } from 'react'

interface Health {
  status?: string
}

export default function App() {
  const [health, setHealth] = useState<string>('checking…')

  useEffect(() => {
    let cancelled = false
    fetch('/healthz')
      .then((res) => res.json())
      .then(
        (body: unknown) => {
          if (!cancelled) {
            const status = (body as Health).status ?? 'unknown'
            setHealth(String(status))
          }
        },
        () => {
          if (!cancelled) {
            setHealth('unreachable (is the API on :8123?)')
          }
        },
      )
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <main className="mx-auto max-w-xl p-8 font-sans dark:bg-[#0f1221] dark:text-white min-h-screen">
      <h1 className="text-2xl font-bold">Redirector v3</h1>
      <p className="mt-2 text-sm opacity-80">
        API status: <span className="font-mono">{health}</span>
      </p>
      <p className="mt-4 text-sm opacity-60">
        Scaffold only — dashboard, upstreams, and admin views land next (EPIC-02).
      </p>
    </main>
  )
}
