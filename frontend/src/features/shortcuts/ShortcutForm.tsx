import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { z } from 'zod'
import type { Shortcut, ShortcutInput } from './api'
import { debugResolve, hasDynamicPlaceholder, suggestPatternFor } from './api'

const patternRule = z
  .string()
  .trim()
  .min(1, 'Pattern is required')
  .regex(/^[a-z0-9\-_./]+$/i, 'Letters, digits, and - _ . / only')

const targetRule = z
  .string()
  .trim()
  .min(1, 'Target is required')
  .refine((value) => /^https?:\/\//i.test(value), 'Target must start with http:// or https://')

const formSchema = z.object({
  pattern: patternRule,
  target: targetRule,
  type: z.enum(['static', 'dynamic', 'user-dynamic']),
  visibility: z.enum(['public', 'unlisted', 'private', 'team']),
  tags: z.string(),
  expires_at: z.string(),
  owner_email: z.string(),
})

export type FormState = z.infer<typeof formSchema>

function validateForm(state: FormState): Partial<Record<keyof FormState, string>> {
  const parsed = formSchema.safeParse(state)
  if (parsed.success) {
    return {}
  }
  const fieldErrors: Partial<Record<keyof FormState, string>> = {}
  for (const issue of parsed.error.issues) {
    const key = issue.path[0] as keyof FormState | undefined
    if (key !== undefined && fieldErrors[key] === undefined) {
      fieldErrors[key] = issue.message
    }
  }
  return fieldErrors
}

export function toInput(state: FormState): ShortcutInput {
  return {
    pattern: state.pattern.trim().toLowerCase().replace(/^\/+|\/+$/g, ''),
    target: state.target.trim(),
    type: state.type,
    visibility: state.visibility,
    tags: state.tags.split(',').map((tag) => tag.trim()).filter((tag) => tag.length > 0),
    expires_at: state.expires_at === '' ? null : new Date(state.expires_at).toISOString(),
    owner_email: state.owner_email.trim() === '' ? null : state.owner_email.trim(),
  }
}

interface Props {
  initial?: Partial<Shortcut>
  fixedPattern?: boolean
  submitLabel: string
  serverError: string | null
  onSubmit: (input: ShortcutInput) => Promise<void>
  onCancel: () => void
}

const inputClass = 'mt-1 w-full rounded border border-rd-line bg-rd-input px-3 py-1.5 text-sm text-rd-text'
const labelClass = 'flex flex-col text-sm'
const hintClass = 'mt-1 text-xs text-rd-muted'

function useDebounced(value: string, delayMs: number): string {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}

type Availability = 'idle' | 'checking' | 'free' | 'taken' | 'unresolvable'

function expiryInputValue(days: number): string {
  const date = new Date(Date.now() + days * 86400000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export default function ShortcutForm({ initial, fixedPattern, submitLabel, serverError, onSubmit, onCancel }: Props) {
  const [state, setState] = useState<FormState>({
    pattern: initial?.pattern ?? '',
    target: initial?.target ?? '',
    type: initial?.type ?? 'static',
    visibility: initial?.visibility ?? 'public',
    tags: (initial?.tags ?? []).join(', '),
    expires_at: '',
    owner_email: initial?.owner_email ?? '',
  })
  const [errors, setErrors] = useState<Partial<Record<keyof FormState, string>>>({})
  const [touched, setTouched] = useState(false)
  const [busy, setBusy] = useState(false)
  const [availability, setAvailability] = useState<Availability>('idle')
  const [takenTarget, setTakenTarget] = useState<string | null>(null)

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setTouched(true)
    setState((prev) => ({ ...prev, [key]: value }))
  }

  const debouncedPattern = useDebounced(state.pattern.trim().toLowerCase(), 400)

  useEffect(() => {
    if (fixedPattern === true || debouncedPattern === '') {
      setAvailability('idle')
      setTakenTarget(null)
      return
    }
    let live = true
    setAvailability('checking')
    debugResolve(debouncedPattern).then(
      (res) => {
        if (!live) {
          return
        }
        if (res.outcome === 'redirect' || res.outcome === 'gone' || res.outcome === 'forbidden') {
          setAvailability('taken')
          setTakenTarget(typeof res.target === 'string' ? res.target : null)
        } else if (res.outcome === 'not_found') {
          setAvailability('free')
          setTakenTarget(null)
        } else {
          setAvailability('unresolvable')
          setTakenTarget(null)
        }
      },
      () => {
        if (live) {
          setAvailability('unresolvable')
          setTakenTarget(null)
        }
      },
    )
    return () => {
      live = false
    }
  }, [debouncedPattern, fixedPattern])

  const suggestion = suggestPatternFor(state.target)
  const showSuggestion = !fixedPattern && suggestion !== '' && suggestion !== state.pattern.trim().toLowerCase()
  const wantsDynamic = hasDynamicPlaceholder(state.target) && state.type === 'static'

  useEffect(() => {
    if (touched) {
      setErrors(validateForm(state))
    }
  }, [state, touched])

  async function submit(event: FormEvent) {
    event.preventDefault()
    const fieldErrors = validateForm(state)
    if (Object.keys(fieldErrors).length > 0) {
      setErrors(fieldErrors)
      setTouched(true)
      return
    }
    setErrors({})
    setBusy(true)
    try {
      await onSubmit(toInput(state))
    } finally {
      setBusy(false)
    }
  }

  function fieldError(key: keyof FormState) {
    return errors[key] !== undefined ? (
      <span role="alert" className="text-xs text-rd-danger">{errors[key]}</span>
    ) : null
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <label className={labelClass}>
        Pattern
        <input
          aria-label="Pattern"
          value={state.pattern}
          disabled={fixedPattern === true}
          onChange={(event) => set('pattern', event.target.value)}
          className={inputClass}
        />
        {fieldError('pattern')}
        {!fixedPattern && state.pattern.trim() !== '' && (
          <span className={hintClass}>
            Link will be <span className="font-mono">r/{state.pattern.trim().toLowerCase()}</span>
            {' · '}
            {availability === 'checking' && <span>checking…</span>}
            {availability === 'free' && <span className="text-rd-accent">available ✓</span>}
            {availability === 'taken' && (
              <span>already taken{takenTarget !== null ? ` → ${takenTarget}` : ''}</span>
            )}
            {availability === 'unresolvable' && <span>could not check</span>}
          </span>
        )}
        {showSuggestion && (
          <span className={hintClass}>
            Suggestion from target:{' '}
            <button
              type="button"
              onClick={() => set('pattern', suggestion)}
              className="underline text-rd-accent"
            >
              use “{suggestion}”
            </button>
          </span>
        )}
      </label>
      <label className={labelClass}>
        Target URL
        <input
          aria-label="Target URL"
          value={state.target}
          onChange={(event) => set('target', event.target.value)}
          onBlur={() => {
            if (!fixedPattern && state.pattern.trim() === '' && suggestion !== '') {
              set('pattern', suggestion)
            }
          }}
          placeholder="https://example.com/docs"
          className={inputClass}
        />
        {fieldError('target')}
        {wantsDynamic && (
          <span className={hintClass}>
            Target has a placeholder like <span className="font-mono">{'{name}'}</span> —{' '}
            <button
              type="button"
              onClick={() => set('type', 'dynamic')}
              className="underline text-rd-accent"
            >
              switch to dynamic
            </button>
          </span>
        )}
      </label>
      <div className="flex gap-3">
        <label className={labelClass}>
          Type
          <select aria-label="Type" value={state.type} onChange={(event) => set('type', event.target.value as FormState['type'])} className={inputClass}>
            <option value="static">static</option>
            <option value="dynamic">dynamic</option>
            <option value="user-dynamic">user-dynamic</option>
          </select>
        </label>
        <label className={labelClass}>
          Visibility
          <select aria-label="Visibility" value={state.visibility} onChange={(event) => set('visibility', event.target.value as FormState['visibility'])} className={inputClass}>
            <option value="public">public</option>
            <option value="unlisted">unlisted</option>
            <option value="private">private</option>
            <option value="team">team</option>
          </select>
        </label>
      </div>
      <p className={hintClass}>
        {state.type === 'static' && 'Static: one pattern, one target.'}
        {state.type === 'dynamic' && 'Dynamic: target placeholders like {ticket} fill from the URL path.'}
        {state.type === 'user-dynamic' && 'User-dynamic: like dynamic, but arguments must match declared params.'}
        {' '}
        {state.visibility === 'public' && 'Public: anyone can resolve it.'}
        {state.visibility === 'unlisted' && 'Unlisted: resolves, but hidden from lists.'}
        {state.visibility === 'private' && 'Private: only you or an admin can resolve it.'}
        {state.visibility === 'team' && 'Team: only the owner or an admin can resolve it.'}
      </p>
      <label className={labelClass}>
        Tags (comma-separated)
        <input
          aria-label="Tags"
          value={state.tags}
          onChange={(event) => set('tags', event.target.value)}
          className={inputClass}
        />
      </label>
      <div className="flex gap-3">
        <label className={labelClass}>
          Expires at (optional)
          <input
            aria-label="Expires at"
            type="datetime-local"
            value={state.expires_at}
            onChange={(event) => set('expires_at', event.target.value)}
            className={inputClass}
          />
        </label>
        <span className="flex items-end gap-1 pb-0.5">
          <button type="button" onClick={() => set('expires_at', '')} className="rounded border border-rd-line px-2 py-1 text-xs">
            Never
          </button>
          <button type="button" onClick={() => set('expires_at', expiryInputValue(7))} className="rounded border border-rd-line px-2 py-1 text-xs">
            +7d
          </button>
          <button type="button" onClick={() => set('expires_at', expiryInputValue(30))} className="rounded border border-rd-line px-2 py-1 text-xs">
            +30d
          </button>
        </span>
        <label className={labelClass}>
          Owner email (optional)
          <input
            aria-label="Owner email"
            value={state.owner_email}
            onChange={(event) => set('owner_email', event.target.value)}
            className={inputClass}
          />
        </label>
      </div>
      {serverError !== null && <p role="alert" className="text-sm text-rd-danger">{serverError}</p>}
      <div className="flex gap-2">
        <button type="submit" disabled={busy} className="rounded bg-rd-accent px-3 py-1.5 text-sm text-rd-accent-ink disabled:opacity-50">
          {submitLabel}
        </button>
        <button type="button" onClick={onCancel} className="rounded border border-rd-line px-3 py-1.5 text-sm">
          Cancel
        </button>
      </div>
    </form>
  )
}
