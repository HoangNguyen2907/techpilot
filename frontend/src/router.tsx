import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '@/lib/use-auth'

export function ProtectedRoute() {
  const { session, isLoading } = useAuth()
  const location = useLocation()

  if (isLoading) {
    return <main className="grid min-h-screen place-items-center bg-slate-950 text-sm text-slate-400">Restoring session…</main>
  }

  if (!session) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  return <Outlet />
}
