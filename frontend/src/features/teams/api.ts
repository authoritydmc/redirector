import { api } from '../../lib/client'

export interface TeamMember {
  email: string
  role: string
  created_at: string
}

export interface Team {
  id: number
  name: string
  created_by_email: string | null
  created_at: string
  members: TeamMember[]
}

export function listTeams(): Promise<Team[]> {
  return api<Team[]>('/api/v1/teams')
}

export function createTeam(name: string): Promise<Team> {
  return api<Team>('/api/v1/teams', { method: 'POST', body: { name } })
}

export function deleteTeam(id: number): Promise<Team> {
  return api<Team>(`/api/v1/teams/${id}`, { method: 'DELETE' })
}

export function addTeamMember(id: number, email: string, role: string): Promise<TeamMember> {
  return api<TeamMember>(`/api/v1/teams/${id}/members`, {
    method: 'POST',
    body: { email, role },
  })
}

export function removeTeamMember(id: number, email: string): Promise<TeamMember> {
  return api<TeamMember>(`/api/v1/teams/${id}/members/${encodeURIComponent(email)}`, {
    method: 'DELETE',
  })
}

export interface TeamUser {
  id: number
  email: string
  name: string | null
  disabled: boolean
  created_at: string
}

export function listUsers(): Promise<TeamUser[]> {
  return api<TeamUser[]>('/api/v1/auth/users')
}

export function setUserDisabled(email: string, disabled: boolean): Promise<TeamUser> {
  const action = disabled ? 'disable' : 'enable'
  return api<TeamUser>(`/api/v1/auth/users/${encodeURIComponent(email)}/${action}`, {
    method: 'POST',
  })
}
