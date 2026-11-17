import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { api, type CurrentUser } from '@/lib/api'
import { useAuth } from '@/lib/use-auth'
import { supabase } from '@/lib/supabase'

type LoginLocationState = {
  from?: string
}

export function LoginPage() {
  const { session, isLoading: isRestoringSession } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [hasSubmitted, setHasSubmitted] = useState(false)
  const from = (location.state as LoginLocationState | null)?.from ?? '/'

  if (isRestoringSession) {
    return <main className="grid min-h-screen place-items-center bg-slate-950 text-sm text-slate-400">Restoring session…</main>
  }
  if (session && !hasSubmitted) return <Navigate to="/" replace />

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setHasSubmitted(true)
    setError(null)
    setIsLoading(true)

    try {
      const { error: loginError } = await supabase.auth.signInWithPassword({ email, password })
      if (loginError) throw loginError
      await api.get<CurrentUser>('/api/auth/me')
      navigate(from, { replace: true })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to sign in.')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 px-6 py-12 text-slate-100">
      <section className="w-full max-w-md">
        <Card className="border-white/10 bg-slate-900 text-slate-100 shadow-2xl">
          <CardHeader>
            <p className="text-sm font-medium uppercase tracking-[0.2em] text-cyan-300">TechPilot</p>
            <h1 className="text-4xl font-semibold tracking-tight">Your HN research copilot</h1>
            <CardDescription className="text-base leading-6 text-slate-400">
              Sign in with your email to explore developer discussions.
            </CardDescription>
          </CardHeader>
          <form onSubmit={handleLogin}>
            <CardContent className="space-y-5">
              <div className="space-y-2">
                <Label className="text-slate-200" htmlFor="email">Email</Label>
                <Input
                  autoComplete="email"
                  className="border-white/10 bg-slate-950 text-slate-100 placeholder:text-slate-600 focus-visible:border-cyan-300 focus-visible:ring-cyan-300/20"
                  id="email"
                  onChange={(event) => setEmail(event.target.value)}
                  required
                  type="email"
                  value={email}
                />
              </div>
              <div className="space-y-2">
                <Label className="text-slate-200" htmlFor="password">Password</Label>
                <Input
                  autoComplete="current-password"
                  className="border-white/10 bg-slate-950 text-slate-100 focus-visible:border-cyan-300 focus-visible:ring-cyan-300/20"
                  id="password"
                  onChange={(event) => setPassword(event.target.value)}
                  required
                  type="password"
                  value={password}
                />
              </div>
              {error && (
                <p className="rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-200" role="alert">
                  {error}
                </p>
              )}
              <Button
                className="h-11 w-full bg-cyan-300 text-base font-semibold text-slate-950 hover:bg-cyan-200"
                disabled={isLoading}
                type="submit"
              >
                {isLoading ? 'Signing in…' : 'Sign in'}
              </Button>
            </CardContent>
          </form>
        </Card>
      </section>
    </main>
  )
}
