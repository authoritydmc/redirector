import type { ReactNode } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import Layout from './components/Layout'
import { AuthProvider, useAuth } from './lib/auth'
import Login from './routes/Login'
import Placeholder from './routes/Placeholder'
import ShortcutsPage from './features/shortcuts/ShortcutsPage'

function RequireAuth({ children }: { children: ReactNode }) {
  const { token } = useAuth()
  const location = useLocation()
  if (token === null) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  return children
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/*"
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<ShortcutsPage />} />
        <Route path="upstreams" element={<Placeholder name="Upstreams" />} />
        <Route path="jobs" element={<Placeholder name="Jobs" />} />
        <Route path="admin" element={<Placeholder name="Admin" />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}

export default function App() {
  return (
    <BrowserRouter basename="/app">
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  )
}
