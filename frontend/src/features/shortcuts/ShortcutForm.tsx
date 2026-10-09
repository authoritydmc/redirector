import { useState } from 'react'
import type { FormEvent } from 'react'
import { z } from 'zod'
import type { Shortcut, ShortcutInput } from './api'

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
  const [busy, setBusy] = useState(false)

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setState((prev) => ({ ...prev, [key]: value }))
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    const parsed = formSchema.safeParse(state)
    if (!parsed.success) {
      const fieldErrors: Partial<Record<keyof FormState, string>> = {}
      for (const issue of parsed.error.issues) {
        const key = issue.path[0] as keyof FormState | undefined
        if (key !== undefined && fieldErrors[key] === undefined) {
          fieldErrors[key] = issue.message
        }
      }
      setErrors(fieldErrors)
      return
    }
    setErrors({})
    setBusy(true)
    try {
      await onSubmit(toInput(parsed.data))
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
      </label>
      <label className={labelClass}>
        Target URL
        <input
          aria-label="Target URL"
          value={state.target}
          onChange={(event) => set('target', event.target.value)}
          placeholder="https://example.com/docs"
          className={inputClass}
        />
        {fieldError('target')}
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
