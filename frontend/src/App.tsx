import { useState, type FormEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { api, type CurrentUser } from '@/lib/api'
import { supabase } from '@/lib/supabase'

function App() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setIsLoading(true)

    try {
      const { data, error: loginError } = await supabase.auth.signInWithPassword({
        email,
        password,
      })

      if (loginError) throw loginError
      if (!data.session) throw new Error('Supabase did not return a session.')

      const currentUser = await api.get<CurrentUser>('/api/auth/me', data.session.access_token)
      setUser(currentUser)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to sign in.')
    } finally {
      setIsLoading(false)
    }
  }

  async function handleLogout() {
    await supabase.auth.signOut()
    setUser(null)
    setPassword('')
    setError(null)
  }

  if (user) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-950 px-6 text-slate-100">
        <Card className="w-full max-w-lg border-white/10 bg-slate-900 text-slate-100 shadow-2xl">
          <CardHeader>
            <p className="text-sm font-medium uppercase tracking-[0.2em] text-cyan-300">Document Copilot</p>
            <h1 className="text-3xl font-semibold">You’re signed in</h1>
            <CardDescription className="text-slate-300">The API verified your account and synchronized your profile.</CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="space-y-4 rounded-xl bg-slate-950/70 p-5 text-sm">
              <div>
                <dt className="text-slate-400">Email</dt>
                <dd className="mt-1 break-all text-slate-100">{user.email ?? 'No email available'}</dd>
              </div>
              <div>
                <dt className="text-slate-400">User ID</dt>
                <dd className="mt-1 break-all font-mono text-xs text-slate-300">{user.id}</dd>
              </div>
            </dl>
            <Button
              className="mt-6 border-white/15 bg-transparent text-slate-100 hover:bg-white/10"
              onClick={handleLogout}
              type="button"
            >
              Sign out
            </Button>
          </CardContent>
        </Card>
      </main>
    )
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 px-6 py-12 text-slate-100">
      <section className="w-full max-w-md">
        <Card className="border-white/10 bg-slate-900 text-slate-100 shadow-2xl">
          <CardHeader>
            <p className="text-sm font-medium uppercase tracking-[0.2em] text-cyan-300">Driftwood Capital</p>
            <h1 className="text-4xl font-semibold tracking-tight">Document Copilot</h1>
            <CardDescription className="text-base leading-6 text-slate-400">Sign in with your work email to continue your research.</CardDescription>
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
                  placeholder="analyst@driftwood.com"
                  required
                  type="email"
                  value={email}
                />
              </div>
              <div className="space-y-2">
                <Label className="text-slate-200" htmlFor="password">Password</Label>
                <Input
                  autoComplete="current-password"
                  className="border-white/10 bg-slate-950 text-slate-100 placeholder:text-slate-600 focus-visible:border-cyan-300 focus-visible:ring-cyan-300/20"
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
              {isLoading && (
                <p className="text-sm text-slate-400" role="status">
                  Authenticating with Supabase and verifying your account…
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

export default App
