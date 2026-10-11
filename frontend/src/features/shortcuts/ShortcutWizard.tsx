import { useEffect, useState } from 'react'
import { debugResolve, hasDynamicPlaceholder, suggestPatternFor } from './api'
import type { ShortcutInput, ShortcutType } from './api'
import { toInput, validateForm } from './ShortcutForm'
import type { FormState } from './ShortcutForm'

const KINDS: Array<{ value: ShortcutType; title: string; blurb: string }> = [
  { value: 'static', title: 'Simple link', blurb: 'One name goes to one address. For bookmarks and go-links.' },
  { value: 'dynamic', title: 'Dynamic link', blurb: 'Placeholders like {ticket} fill in from the URL path.' },
  { value: 'user-dynamic', title: 'Guided params', blurb: 'Like dynamic, but every argument must match declared params.' },
]

type Availability = 'idle' | 'checking' | 'free' | 'taken' | 'unresolvable'

function useDebounced(value: string, delayMs: number): string {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}

function useAvailability(pattern: string): { status: Availability; takenTarget: string | null } {
  const debounced = useDebounced(pattern.trim().toLowerCase(), 400)
  const [status, setStatus] = useState<Availability>('idle')
  const [takenTarget, setTakenTarget] = useState<string | null>(null)
  useEffect(() => {
    if (debounced === '') {
      setStatus('idle')
      setTakenTarget(null)
      return
    }
    let live = true
    setStatus('checking')
    debugResolve(debounced).then(
      (res) => {
        if (!live) {
          return
        }
        if (res.outcome === 'redirect' || res.outcome === 'gone' || res.outcome === 'forbidden') {
          setStatus('taken')
          setTakenTarget(typeof res.target === 'string' ? res.target : null)
        } else if (res.outcome === 'not_found') {
          setStatus('free')
          setTakenTarget(null)
        } else {
          setStatus('unresolvable')
          setTakenTarget(null)
        }
      },
      () => {
        if (live) {
          setStatus('unresolvable')
          setTakenTarget(null)
        }
      },
    )
    return () => {
      live = false
    }
  }, [debounced])
  return { status, takenTarget }
}

interface Props {
  initialPattern?: string
  serverError: string | null
  onSubmit: (input: ShortcutInput) => Promise<void>
}

const inputClass = 'mt-1 w-full rounded border border-rd-line bg-rd-input px-3 py-1.5 text-sm text-rd-text'
const hintClass = 'mt-1 text-xs text-rd-muted'

export default function ShortcutWizard({ initialPattern = '', serverError, onSubmit }: Props) {
  const [step, setStep] = useState(0)
  const [busy, setBusy] = useState(false)
  const [state, setState] = useState<FormState>({
    pattern: initialPattern,
    target: '',
    type: 'static',
    visibility: 'public',
    tags: '',
    expires_at: '',
    owner_email: '',
  })
  const { status: availability, takenTarget } = useAvailability(state.pattern)
  const suggestion = suggestPatternFor(state.target)
  const errors = validateForm(state)

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setState((prev) => ({ ...prev, [key]: value }))
  }

  const steps = ['Kind', 'Target', 'Name', 'Review']
  const targetError = errors.target
  const patternError = errors.pattern
  const canNextTarget = targetError === undefined
  const canNextName =
    patternError === undefined &&
    state.pattern.trim() !== '' &&
    availability !== 'taken' &&
    availability !== 'checking'

  async function create() {
    const fieldErrors = validateForm(state)
    if (Object.keys(fieldErrors).length > 0 || availability === 'taken') {
      if (availability === 'taken') {
        setStep(2)
      } else if (fieldErrors.target !== undefined) {
        setStep(1)
      } else if (fieldErrors.pattern !== undefined) {
        setStep(2)
      }
      return
    }
    setBusy(true)
    try {
      await onSubmit(toInput(state))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div className="flex items-center gap-1" aria-label={`Wizard step ${step + 1} of ${steps.length}`}>
        {steps.map((label, i) => (
          <span key={label} title={label} aria-hidden="true"
            className={`h-1.5 flex-1 rounded-full ${i <= step ? 'bg-rd-accent' : 'bg-rd-line'}`}
          />
        ))}
      </div>

      {step === 0 && (
        <div className="mt-3 flex flex-col gap-2">
          <p className="text-sm text-rd-muted">What kind of link is this?</p>
          <div className="flex flex-col gap-2" role="radiogroup" aria-label="Link kind">
            {KINDS.map((kind) => (
              <button
                key={kind.value}
                type="button"
                role="radio"
                aria-checked={state.type === kind.value}
                onClick={() => set('type', kind.value)}
                className={`rounded border p-3 text-left ${
                  state.type === kind.value ? 'border-rd-accent bg-rd-surface' : 'border-rd-line'
                }`}
              >
                <span className="text-sm font-semibold">{kind.title}</span>
                <span className="mt-0.5 block text-xs text-rd-muted">{kind.blurb}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {step === 1 && (
        <div className="mt-3 flex flex-col gap-2">
          <p className="text-sm text-rd-muted">Where should the link go?</p>
          <label className="flex flex-col text-sm">
            Target URL
            <input
              aria-label="Wizard target URL"
              value={state.target}
              onChange={(event) => set('target', event.target.value)}
              placeholder="https://example.com/docs"
              className={inputClass}
            />
          </label>
          {targetError !== undefined && (
            <span role="alert" className="text-xs text-rd-danger">{targetError}</span>
          )}
          {targetError === undefined && state.target.trim() !== '' && (
            <span className={hintClass}>
              {hasDynamicPlaceholder(state.target)
                ? 'Placeholder detected — a dynamic kind fits best.'
                : 'Plain address — a simple link fits best.'}
              {suggestion !== '' && (
                <> Suggested name: <span className="font-mono">r/{suggestion}</span></>
              )}
            </span>
          )}
        </div>
      )}

      {step === 2 && (
        <div className="mt-3 flex flex-col gap-2">
          <p className="text-sm text-rd-muted">What should it be called?</p>
          <label className="flex flex-col text-sm">
            Pattern
            <input
              aria-label="Wizard pattern"
              value={state.pattern}
              onChange={(event) => set('pattern', event.target.value)}
              placeholder="my-link"
              className={inputClass}
            />
          </label>
          {patternError !== undefined && (
            <span role="alert" className="text-xs text-rd-danger">{patternError}</span>
          )}
          {patternError === undefined && state.pattern.trim() !== '' && (
            <span className={hintClass}>
              Link will be <span className="font-mono">r/{state.pattern.trim().toLowerCase()}</span>
              {' · '}
              {availability === 'checking' && <span>checking…</span>}
              {availability === 'free' && <span className="text-rd-accent">available ✓</span>}
              {availability === 'taken' && (
                <span className="text-rd-danger">
                  already taken{takenTarget !== null ? ` → ${takenTarget}` : ''} — pick another name
                </span>
              )}
              {availability === 'unresolvable' && <span>could not check</span>}
            </span>
          )}
          {suggestion !== '' && suggestion !== state.pattern.trim().toLowerCase() && (
            <span className={hintClass}>
              From your target:{' '}
              <button
                type="button"
                onClick={() => set('pattern', suggestion)}
                className="underline text-rd-accent"
              >
                use “{suggestion}”
              </button>
            </span>
          )}
        </div>
      )}

      {step === 3 && (
        <div className="mt-3 flex flex-col gap-2">
          <p className="text-sm text-rd-muted">Ready to create?</p>
          <dl className="rounded border border-rd-line text-sm">
            <div className="flex gap-2 border-b border-rd-line px-3 py-2">
              <dt className="text-rd-muted">Link</dt>
              <dd className="font-mono">r/{state.pattern.trim().toLowerCase()}</dd>
            </div>
            <div className="flex gap-2 border-b border-rd-line px-3 py-2">
              <dt className="text-rd-muted">Goes to</dt>
              <dd className="break-all">{state.target.trim()}</dd>
            </div>
            <div className="flex gap-2 px-3 py-2">
              <dt className="text-rd-muted">Kind</dt>
              <dd>{KINDS.find((k) => k.value === state.type)?.title ?? state.type}</dd>
            </div>
          </dl>
          <label className="flex items-center gap-2 text-sm">
            Visible to everyone
            <input
              type="checkbox"
              aria-label="Visible to everyone"
              checked={state.visibility === 'public'}
              onChange={(event) => set('visibility', event.target.checked ? 'public' : 'unlisted')}
            />
          </label>
          {serverError !== null && <p role="alert" className="text-sm text-rd-danger">{serverError}</p>}
        </div>
      )}

      <div className="mt-4 flex gap-2">
        {step > 0 && (
          <button
            type="button"
            onClick={() => setStep((s) => s - 1)}
            className="rounded border border-rd-line px-4 py-1.5 text-sm"
          >
            Back
          </button>
        )}
        {step < 3 && (
          <button
            type="button"
            onClick={() => setStep((s) => s + 1)}
            disabled={(step === 1 && !canNextTarget) || (step === 2 && !canNextName)}
            className="rounded bg-rd-accent px-4 py-1.5 text-sm text-rd-accent-ink disabled:opacity-50"
          >
            Next
          </button>
        )}
        {step === 3 && (
          <button
            type="button"
            onClick={() => void create()}
            disabled={busy}
            className="rounded bg-rd-accent px-4 py-1.5 text-sm text-rd-accent-ink disabled:opacity-50"
          >
            Create link
          </button>
        )}
      </div>
    </div>
  )
}
