import type { ReactNode } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import Layout from './components/Layout'
import { AuthProvider, useAuth } from './lib/auth'
import AdminPage from './routes/AdminPage'
import GuidePage from './routes/GuidePage'
import JobsPage from './routes/JobsPage'
import Login from './routes/Login'
import MetricsPage from './routes/MetricsPage'
import NotFound from './routes/NotFound'
import TeamsPage from './features/teams/TeamsPage'
import ShortcutsPage from './features/shortcuts/ShortcutsPage'
import UpstreamsPage from './features/upstreams/UpstreamsPage'

function RequireAuth({ children }: { children: ReactNode }) {
  const { token } = useAuth()
  const location = useLocation()
  if (token === null) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  return children
}

function RequireAdmin({ children }: { children: ReactNode }) {
  const { role } = useAuth()
  if (role !== 'admin') {
    return <Navigate to="/teams" replace />
  }
  return children
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<Layout />}>
        <Route index element={<ShortcutsPage />} />
        <Route path="upstreams" element={<UpstreamsPage />} />
        <Route path="metrics" element={<MetricsPage />} />
        <Route path="guide" element={<GuidePage />} />
        <Route
          path="teams"
          element={
            <RequireAuth>
              <TeamsPage />
            </RequireAuth>
          }
        />
        <Route
          path="jobs"
          element={
            <RequireAuth>
              <RequireAdmin>
                <JobsPage />
              </RequireAdmin>
            </RequireAuth>
          }
        />
        <Route
          path="admin"
          element={
            <RequireAuth>
              <RequireAdmin>
                <AdminPage />
              </RequireAdmin>
            </RequireAuth>
          }
        />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  )
}
