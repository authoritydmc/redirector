import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from '../lib/auth'
import { THEMES, useTheme } from '../lib/theme'

export default function Layout() {
  const { logout } = useAuth()
  const navigate = useNavigate()
  const [theme, setTheme] = useTheme()

  function signOut() {
    logout()
    navigate('/login', { replace: true })
  }

  const link = ({ isActive }: { isActive: boolean }) =>
    `rounded px-2 py-1 text-sm hover:bg-rd-surface${isActive ? ' bg-rd-surface font-semibold' : ''}`
  const control = 'rounded border border-rd-line bg-rd-input px-2 py-1 text-sm text-rd-text'
  return (
    <div className="min-h-screen bg-rd-bg font-sans text-rd-text">
      <header className="flex flex-wrap items-center gap-2 border-b border-rd-line px-4 py-2">
        <span className="font-bold">Redirector</span>
        <nav className="ml-4 flex flex-wrap gap-1" aria-label="Primary">
          <NavLink className={link} to="/" end>Shortcuts</NavLink>
          <NavLink className={link} to="/upstreams">Upstreams</NavLink>
          <NavLink className={link} to="/jobs">Jobs</NavLink>
          <NavLink className={link} to="/admin">Admin</NavLink>
        </nav>
        <span className="ml-auto flex gap-2">
          <select
            aria-label="Theme"
            value={theme}
            onChange={(event) => setTheme(event.target.value)}
            className={control}
          >
            {THEMES.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
          <button type="button" onClick={signOut} className="rounded px-2 py-1 text-sm hover:bg-rd-surface">
            Sign out
          </button>
        </span>
      </header>
      <main className="mx-auto max-w-4xl p-4">
        <Outlet />
      </main>
    </div>
  )
}
