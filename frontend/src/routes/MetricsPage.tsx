import { useCallback, useEffect, useState } from 'react'
import { getKpi, getLive, type Kpi, type Live } from '../features/admin/api'

function Card({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-rd-line bg-rd-surface p-4 shadow-xl">
      <p className="text-xs font-bold uppercase tracking-widest text-rd-muted">{label}</p>
      <p className="mt-1 text-2xl font-bold">{value}</p>
    </div>
  )
}

function num(value: number | null | undefined, suffix = ''): string {
  return value === null || value === undefined ? '—' : `${value}${suffix}`
}

export default function MetricsPage() {
  const [kpi, setKpi] = useState<Kpi | null>(null)
  const [live, setLive] = useState<Live | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    try {
      const [k, l] = await Promise.all([getKpi(), getLive()])
      setKpi(k)
      setLive(l)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load metrics')
    }
  }, [])

  useEffect(() => {
    void reload()
    const timer = setInterval(reload, 10000)
    return () => clearInterval(timer)
  }, [reload])

  if (error !== null) {
    return (
      <section>
        <h2 className="text-xl font-semibold">Metrics</h2>
        <p role="alert" className="mt-3 text-sm text-rd-danger">{error}</p>
      </section>
    )
  }
  if (kpi === null || live === null) {
    return (
      <section>
        <h2 className="text-xl font-semibold">Metrics</h2>
        <p className="mt-4 text-sm text-rd-muted">Loading…</p>
      </section>
    )
  }

  return (
    <section>
      <h2 className="text-xl font-semibold">Metrics</h2>
      <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Card label="Shortcuts" value={String(kpi.overview.total_shortcuts)} />
        <Card label="Total hits" value={String(kpi.overview.total_hits)} />
        <Card label="Avg hits" value={String(kpi.overview.avg_hits)} />
        <Card label="Zero-hit" value={String(kpi.overview.zero_hit_count)} />
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Card label="Most popular" value={kpi.overview.most_popular?.pattern ?? '—'} />
        <Card
          label="Cache hit rate"
          value={live.cache.hit_rate === null || live.cache.hit_rate === undefined ? '—' : `${live.cache.hit_rate}%`}
        />
        <Card label="CPU" value={num(live.process.cpu_percent, '%')} />
        <Card label="Memory" value={num(live.process.memory_mb, ' MB')} />
      </div>
      <p className="mt-3 text-sm text-rd-muted">
        Status: {live.status} · {live.process.platform} · {live.process.python}
      </p>
    </section>
  )
}
