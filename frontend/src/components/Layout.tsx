import { useEffect, useState } from 'react'
import { Link, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from '../lib/auth'

function useDarkMode(): [boolean, () => void] {
  const [dark, setDark] = useState<boolean>(() => localStorage.getItem('redirector.theme') === 'dark')
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    localStorage.setItem('redirector.theme', dark ? 'dark' : 'light')
  }, [dark])
  return [dark, () => setDark((value) => !value)]
}

export default function Layout() {
  const { logout } = useAuth()
  const navigate = useNavigate()
  const [dark, toggleDark] = useDarkMode()

  function signOut() {
    logout()
    navigate('/login', { replace: true })
  }

  const link = 'rounded px-2 py-1 text-sm hover:bg-black/5 dark:hover:bg-white/10'
  return (
    <div className="min-h-screen font-sans dark:bg-[#0f1221] dark:text-white">
      <header className="flex items-center gap-2 border-b px-4 py-2 dark:border-white/10">
        <span className="font-bold">Redirector v3</span>
        <nav className="ml-4 flex gap-1">
          <Link className={link} to="/">Dashboard</Link>
          <Link className={link} to="/upstreams">Upstreams</Link>
          <Link className={link} to="/jobs">Jobs</Link>
          <Link className={link} to="/admin">Admin</Link>
        </nav>
        <span className="ml-auto flex gap-2">
          <button type="button" onClick={toggleDark} className={link} aria-label="Toggle dark mode">
            {dark ? 'Light' : 'Dark'}
          </button>
          <button type="button" onClick={signOut} className={link}>
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
