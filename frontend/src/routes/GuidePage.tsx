import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { debugResolve, hasDynamicPlaceholder, suggestPatternFor } from '../features/shortcuts/api'

const DEMO_TYPE_MS = 28
const DEMO_START_DELAY_MS = 450

function usePrefersReducedMotion(): boolean {
  const [reduced] = useState(
    () =>
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  )
  return reduced
}

/** Auto-types `sample` into an empty field, like a live demo.
 *
 * Starts on mount (or replay) while the field is still empty. The first
 * user edit takes over: keystrokes typed at the end of a running demo
 * replace the demo text, any other edit is kept verbatim, and the demo
 * never resumes until Replay. Honors prefers-reduced-motion (fills
 * instantly, no timers).
 */
function useDemoTyping(sample: string, initialValue: string, setValue: (v: string) => void) {
  const reduced = usePrefersReducedMotion()
  const liveRef = useRef(false)
  const shownRef = useRef('')
  // `initialValue` is read once: remounting on a preserved edit must not replay.
  // (`value` itself stays out of the effect deps so demo ticks don't retrigger it.)
  const armedRef = useRef(initialValue === '')
  const [runId, setRunId] = useState(0)
  const [typing, setTyping] = useState(false)

  useEffect(() => {
    if (!armedRef.current || sample === '') {
      return
    }
    if (reduced) {
      shownRef.current = sample
      setValue(sample)
      return
    }
    liveRef.current = true
    shownRef.current = ''
    setTyping(true)
    let i = 0
    let timer: ReturnType<typeof setTimeout>
    const tick = () => {
      if (!liveRef.current) {
        return
      }
      i += 1
      shownRef.current = sample.slice(0, i)
      setValue(shownRef.current)
      if (i < sample.length) {
        timer = setTimeout(tick, DEMO_TYPE_MS)
      } else {
        liveRef.current = false
        setTyping(false)
      }
    }
    timer = setTimeout(tick, DEMO_START_DELAY_MS)
    return () => {
      liveRef.current = false
      clearTimeout(timer)
      setTyping(false)
    }
  }, [runId, sample, reduced, setValue])

  return {
    typing,
    takeOver: (next: string) => {
      const wasRunning = liveRef.current
      liveRef.current = false
      armedRef.current = false
      setTyping(false)
      const shown = shownRef.current
      shownRef.current = ''
      setValue(wasRunning && shown !== '' && next.startsWith(shown) ? next.slice(shown.length) : next)
    },
    replay: () => {
      liveRef.current = false
      shownRef.current = ''
      armedRef.current = true
      setValue('')
      setRunId((n) => n + 1)
    },
  }
}

function DemoBar({ typing, onReplay }: { typing: boolean; onReplay: () => void }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span
        role="status"
        className="inline-flex items-center gap-1.5 rounded-full border border-rd-line px-2 py-0.5 text-rd-muted"
      >
        <span
          aria-hidden="true"
          className={`inline-block h-3 w-[2px] bg-rd-accent ${typing ? 'animate-rd-caret' : ''}`}
        />
        {typing ? 'Demo typing…' : 'Auto demo'}
      </span>
      <button type="button" onClick={onReplay} className="underline text-rd-muted">
        Replay
      </button>
    </div>
  )
}

const inputClass =
  'mt-1 w-full rounded border border-rd-line bg-rd-input px-3 py-1.5 text-sm text-rd-text'

function useDebounced(value: string, delayMs: number): string {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}

function StepShell({
  step,
  total,
  title,
  children,
  onBack,
  onNext,
  nextLabel = 'Next',
}: {
  step: number
  total: number
  title: string
  children: React.ReactNode
  onBack: (() => void) | null
  onNext: (() => void) | null
  nextLabel?: string
}) {
  return (
    <div key={step} className="animate-rd-fade-up">
      <div className="flex items-center gap-1" aria-label={`Step ${step + 1} of ${total}`}>
        {Array.from({ length: total }, (_, i) => (
          <span
            key={i}
            aria-hidden="true"
            className={`h-1.5 flex-1 rounded-full ${i <= step ? 'bg-rd-accent' : 'bg-rd-line'}`}
          />
        ))}
      </div>
      <h3 className="mt-4 text-lg font-semibold">{title}</h3>
      <div className="mt-3 flex flex-col gap-3">{children}</div>
      <div className="mt-4 flex gap-2">
        {onBack !== null && (
          <button
            type="button"
            onClick={onBack}
            className="rounded border border-rd-line px-4 py-1.5 text-sm"
          >
            Back
          </button>
        )}
        {onNext !== null && (
          <button
            type="button"
            onClick={onNext}
            className="rounded bg-rd-accent px-4 py-1.5 text-sm text-rd-accent-ink"
          >
            {nextLabel}
          </button>
        )}
      </div>
    </div>
  )
}

function StepTarget({ target, setTarget }: { target: string; setTarget: (v: string) => void }) {
  const demo = useDemoTyping('https://x.example/docs/getting-started', target, setTarget)
  const suggestion = suggestPatternFor(target)
  const dynamic = hasDynamicPlaceholder(target)
  return (
    <>
      <p className="text-sm text-rd-muted">
        Type where the link should go. The app reads the target and suggests the rest.
      </p>
      <DemoBar typing={demo.typing} onReplay={demo.replay} />
      <label className="flex flex-col text-sm">
        Target URL — watch the demo, or type your own
        <input
          aria-label="Tutorial target"
          value={target}
          onChange={(event) => demo.takeOver(event.target.value)}
          placeholder="https://example.com/docs"
          className={inputClass}
        />
      </label>
      {suggestion !== '' && (
        <p className="text-sm">
          Suggested pattern: <span className="font-mono font-bold">r/{suggestion}</span>
        </p>
      )}
      {dynamic && (
        <p className="text-sm">
          Detected a placeholder — this wants the{' '}
          <strong>dynamic</strong> type below.
        </p>
      )}
      {!dynamic && target.trim() !== '' && (
        <p className="text-sm text-rd-muted">No placeholders — a plain static link.</p>
      )}
    </>
  )
}

function StepAvailability({ pattern, setPattern }: { pattern: string; setPattern: (v: string) => void }) {
  const demo = useDemoTyping('docs', pattern, setPattern)
  const debounced = useDebounced(pattern.trim().toLowerCase(), 400)
  const [state, setState] = useState<'idle' | 'checking' | 'free' | 'taken' | 'error'>('idle')
  const [takenTarget, setTakenTarget] = useState<string | null>(null)

  useEffect(() => {
    if (debounced === '') {
      setState('idle')
      return
    }
    let live = true
    setState('checking')
    debugResolve(debounced).then(
      (res) => {
        if (!live) {
          return
        }
        if (res.outcome === 'not_found') {
          setState('free')
          setTakenTarget(null)
        } else if (res.outcome === 'redirect') {
          setState('taken')
          setTakenTarget(typeof res.target === 'string' ? res.target : null)
        } else {
          setState('error')
          setTakenTarget(null)
        }
      },
      () => {
        if (live) {
          setState('error')
        }
      },
    )
    return () => {
      live = false
    }
  }, [debounced])

  return (
    <>
      <p className="text-sm text-rd-muted">
        Type a pattern — the app checks it live against shortcuts and upstreams.
      </p>
      <DemoBar typing={demo.typing} onReplay={demo.replay} />
      <label className="flex flex-col text-sm">
        Pattern — the demo claims <span className="font-mono">docs</span>; type your own to check it
        <input
          aria-label="Tutorial pattern"
          value={pattern}
          onChange={(event) => demo.takeOver(event.target.value)}
          placeholder="my-link"
          className={inputClass}
        />
      </label>
      {state === 'checking' && <p className="text-sm text-rd-muted">checking…</p>}
      {state === 'free' && pattern.trim() !== '' && (
        <p className="text-sm">
          <span className="font-mono">r/{debounced}</span> is <strong className="text-rd-accent">available ✓</strong>
        </p>
      )}
      {state === 'taken' && (
        <p className="text-sm">
          <span className="font-mono">r/{debounced}</span> is taken
          {takenTarget !== null && (
            <> — it goes to <span className="font-mono">{takenTarget}</span></>
          )}
          . Pick another one.
        </p>
      )}
      {state === 'error' && (
        <p className="text-sm text-rd-muted">Could not check that pattern right now.</p>
      )}
    </>
  )
}

function StepDynamic({ template, setTemplate }: { template: string; setTemplate: (v: string) => void }) {
  const demo = useDemoTyping('https://j.example/{ticket}', template, setTemplate)
  const [sample, setSample] = useState('ABC-123')
  const preview = template
    .replace(/\{[^}]+\}/g, sample.toLowerCase())
    .replace(/\[arg\]/gi, sample.toLowerCase())
  return (
    <>
      <p className="text-sm text-rd-muted">
        One link, infinite destinations. Put a placeholder in the target, then watch a
        sample value flow through it.
      </p>
      <DemoBar typing={demo.typing} onReplay={demo.replay} />
      <label className="flex flex-col text-sm">
        Template — the demo types one with a <span className="font-mono">{'{ticket}'}</span> placeholder
        <input
          aria-label="Tutorial template"
          value={template}
          onChange={(event) => demo.takeOver(event.target.value)}
          placeholder="https://j.example/{ticket}"
          className={inputClass}
        />
      </label>
      <label className="flex flex-col text-sm">
        Sample value
        <input
          aria-label="Tutorial sample value"
          value={sample}
          onChange={(event) => setSample(event.target.value)}
          className={inputClass}
        />
      </label>
      {template.trim() !== '' && (
        <p className="rounded border border-rd-line bg-rd-input p-3 font-mono text-sm break-all">
          → {preview}
        </p>
      )}
    </>
  )
}

const VISIBILITY: Array<{ value: string; who: string; note: string }> = [
  { value: 'public', who: 'Everyone', note: 'Resolves for anyone, listed everywhere.' },
  { value: 'unlisted', who: 'Everyone with the link', note: 'Resolves, but hidden from lists.' },
  { value: 'private', who: 'Only you + admins', note: 'Others get a 403.' },
  { value: 'team', who: 'Owner + admins', note: 'Tied to the owner email.' },
]

function StepVisibility() {
  const [picked, setPicked] = useState('public')
  const current = VISIBILITY.find((v) => v.value === picked) ?? VISIBILITY[0]
  return (
    <>
      <p className="text-sm text-rd-muted">Pick who may open the link:</p>
      <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Visibility">
        {VISIBILITY.map((v) => (
          <button
            key={v.value}
            type="button"
            role="radio"
            aria-checked={picked === v.value}
            onClick={() => setPicked(v.value)}
            className={`rounded border px-3 py-1.5 text-sm ${
              picked === v.value
                ? 'border-rd-accent bg-rd-accent text-rd-accent-ink'
                : 'border-rd-line'
            }`}
          >
            {v.value}
          </button>
        ))}
      </div>
      <p className="rounded border border-rd-line bg-rd-input p-3 text-sm">
        <strong>{current.who}</strong> — {current.note}
      </p>
    </>
  )
}

export default function GuidePage() {
  const [step, setStep] = useState(0)
  const [target, setTarget] = useState('')
  const [pattern, setPattern] = useState('')
  const [template, setTemplate] = useState('')
  const total = 5

  return (
    <div>
      <h2 className="text-xl font-semibold">Guide</h2>
      <p className="mt-1 text-sm text-rd-muted">Learn by doing — every box below is live.</p>
      <div className="mt-3 rounded-xl border border-rd-line bg-rd-surface p-4 shadow-xl md:p-6">
        {step === 0 && (
          <StepShell step={0} total={total} title="1 · Point it somewhere" onBack={null} onNext={() => setStep(1)}>
            <StepTarget target={target} setTarget={setTarget} />
          </StepShell>
        )}
        {step === 1 && (
          <StepShell step={1} total={total} title="2 · Claim a name" onBack={() => setStep(0)} onNext={() => setStep(2)}>
            <StepAvailability pattern={pattern} setPattern={setPattern} />
          </StepShell>
        )}
        {step === 2 && (
          <StepShell step={2} total={total} title="3 · Go dynamic" onBack={() => setStep(1)} onNext={() => setStep(3)}>
            <StepDynamic template={template} setTemplate={setTemplate} />
          </StepShell>
        )}
        {step === 3 && (
          <StepShell step={3} total={total} title="4 · Choose who sees it" onBack={() => setStep(2)} onNext={() => setStep(4)}>
            <StepVisibility />
          </StepShell>
        )}
        {step === 4 && (
          <StepShell
            step={4}
            total={total}
            title="5 · Share it"
            onBack={() => setStep(3)}
            onNext={null}
            nextLabel="Done"
          >
            <p className="text-sm text-rd-muted">
              Create the real thing on the Shortcuts page — every shortcut gets a
              QR code, hit counters, and an API at <span className="font-mono">/api/v1</span>.
            </p>
            <div>
              <Link
                to="/"
                className="inline-block rounded bg-rd-accent px-4 py-2 text-sm text-rd-accent-ink"
              >
                Create your first link
              </Link>
            </div>
          </StepShell>
        )}
      </div>
    </div>
  )
}
