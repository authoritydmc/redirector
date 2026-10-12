import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../../lib/client'
import { useAuth } from '../../lib/auth'
import {
  addTeamMember,
  createTeam,
  deleteTeam,
  listTeams,
  removeTeamMember,
} from './api'
import type { Team } from './api'

export default function TeamsPage() {
  const { role, email: myEmail } = useAuth()
  const [rows, setRows] = useState<Team[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [memberInputs, setMemberInputs] = useState<Record<number, string>>({})

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setRows(await listTeams())
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401
        ? 'Session expired — sign in again'
        : err instanceof Error ? err.message : 'Failed to load teams')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  async function create(event: React.FormEvent) {
    event.preventDefault()
    if (name.trim() === '') {
      return
    }
    setError(null)
    setNotice(null)
    try {
      const team = await createTeam(name.trim())
      setName('')
      setNotice(`Team ${team.name} created — you are its admin.`)
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Create failed')
    }
  }

  async function addMember(team: Team) {
    const email = (memberInputs[team.id] ?? '').trim()
    if (email === '') {
      return
    }
    setError(null)
    setNotice(null)
    try {
      await addTeamMember(team.id, email, 'member')
      setMemberInputs((prev) => ({ ...prev, [team.id]: '' }))
      setNotice(`${email} joined ${team.name}.`)
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Add failed')
    }
  }

  async function removeMember(team: Team, memberEmail: string) {
    setError(null)
    setNotice(null)
    try {
      await removeTeamMember(team.id, memberEmail)
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Remove failed')
    }
  }

  async function removeTeam(team: Team) {
    setError(null)
    setNotice(null)
    try {
      await deleteTeam(team.id)
      setNotice(`Team ${team.name} deleted.`)
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  function canManage(team: Team): boolean {
    if (role === 'admin') {
      return true
    }
    return team.members.some((m) => m.email === myEmail && m.role === 'admin')
  }

  return (
    <section>
      <h2 className="text-xl font-semibold">Teams</h2>
      <p className="mt-1 text-sm text-rd-muted">
        Team links resolve for members only. Found a team to join the fun — ask its admin to add your account email.
      </p>
      {error !== null && <p role="alert" className="mt-3 text-sm text-rd-danger">{error}</p>}
      {notice !== null && <p role="status" className="mt-3 text-sm text-rd-muted">{notice}</p>}

      <form onSubmit={create} className="mt-3 flex flex-wrap items-end gap-2">
        <label className="flex flex-col text-sm">
          New team name
          <input
            aria-label="New team name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="spaceships"
            className="mt-1 rounded border border-rd-line bg-rd-input px-3 py-1.5 text-sm text-rd-text"
          />
        </label>
        <button type="submit" className="rounded bg-rd-accent px-3 py-1.5 text-sm text-rd-accent-ink">
          Create team
        </button>
      </form>

      {loading ? (
        <p className="mt-4 text-sm text-rd-muted">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="mt-4 text-sm text-rd-muted">No teams yet — create the first one above.</p>
      ) : (
        <div className="mt-3 flex flex-col gap-3">
          {rows.map((team) => (
            <div key={team.id} className="rounded-xl border border-rd-line bg-rd-surface p-4">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="font-semibold">{team.name}</h3>
                {canManage(team) && (
                  <button
                    type="button"
                    onClick={() => void removeTeam(team)}
                    className="ml-auto underline text-rd-danger text-sm"
                  >
                    Delete team
                  </button>
                )}
              </div>
              <ul aria-label={`Members of ${team.name}`} className="mt-2 flex flex-col gap-1 text-sm">
                {team.members.map((member) => (
                  <li key={member.email} className="flex items-center gap-2">
                    <span className="font-mono">{member.email}</span>
                    <span className="text-xs text-rd-muted">{member.role}</span>
                    {canManage(team) && member.email !== myEmail && (
                      <button
                        type="button"
                        onClick={() => void removeMember(team, member.email)}
                        className="ml-auto underline text-rd-muted text-xs"
                      >
                        Remove
                      </button>
                    )}
                  </li>
                ))}
              </ul>
              {canManage(team) && (
                <div className="mt-2 flex flex-wrap items-end gap-2">
                  <label className="flex flex-col text-sm">
                    Add member by email
                    <input
                      aria-label={`Add member to ${team.name}`}
                      value={memberInputs[team.id] ?? ''}
                      onChange={(event) => setMemberInputs((prev) => ({
                        ...prev,
                        [team.id]: event.target.value,
                      }))}
                      placeholder="crew@example.com"
                      className="mt-1 rounded border border-rd-line bg-rd-input px-3 py-1.5 text-sm text-rd-text"
                    />
                  </label>
                  <button
                    type="button"
                    onClick={() => void addMember(team)}
                    className="rounded border border-rd-line px-3 py-1.5 text-sm"
                  >
                    Add
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
