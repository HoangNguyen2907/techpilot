import { useCallback, useEffect, useState } from 'react'
import { Menu } from 'lucide-react'
import { Navigate, useLocation, useMatch, useNavigate } from 'react-router-dom'
import { ChatContainer } from '@/components/chat/ChatContainer'
import { ThreadSidebar } from '@/components/chat/ThreadSidebar'
import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet'
import { api, type ChatThread } from '@/lib/api'
import { useAuth } from '@/lib/use-auth'
import { supabase } from '@/lib/supabase'

let threadListRequest: Promise<ChatThread[]> | null = null

function fetchThreadList() {
  if (threadListRequest) return threadListRequest

  const request = api.get<ChatThread[]>('/api/chat/threads')
  threadListRequest = request
  const clearRequest = () => {
    if (threadListRequest === request) threadListRequest = null
  }
  void request.then(clearRequest, clearRequest)
  return request
}

export function ChatShell() {
  const { session } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const threadMatch = useMatch('/chat/:threadId')
  const activeThreadId = threadMatch?.params.threadId ?? null
  const isRoot = location.pathname === '/'
  const [threads, setThreads] = useState<ChatThread[]>([])
  const [sidebarError, setSidebarError] = useState<string | null>(null)
  const [isLoadingThreads, setIsLoadingThreads] = useState(true)
  const [mobileSidebarPath, setMobileSidebarPath] = useState<string | null>(null)
  const isMobileSidebarOpen = mobileSidebarPath === location.pathname

  const fetchThreads = useCallback(() => fetchThreadList(), [])

  useEffect(() => {
    let isMounted = true
    void fetchThreads()
      .then((result) => {
        if (!isMounted) return
        setThreads(result)
        setSidebarError(null)
      })
      .catch((cause: unknown) => {
        if (isMounted) setSidebarError(cause instanceof Error ? cause.message : 'Unable to load conversations.')
      })
      .finally(() => {
        if (isMounted) setIsLoadingThreads(false)
      })
    return () => {
      isMounted = false
    }
  }, [fetchThreads])

  if (!isRoot && !activeThreadId) return <Navigate to="/" replace />

  async function refreshThreads() {
    try {
      setThreads(await fetchThreads())
      setSidebarError(null)
    } catch (cause) {
      setSidebarError(cause instanceof Error ? cause.message : 'Unable to load conversations.')
    }
  }

  function newChat() {
    setMobileSidebarPath(null)
    navigate('/')
  }

  function selectThread(threadId: string) {
    setMobileSidebarPath(null)
    navigate(`/chat/${encodeURIComponent(threadId)}`)
  }

  async function renameThread(threadId: string, title: string) {
    setSidebarError(null)
    try {
      const updated = await api.patch<ChatThread>(`/api/chat/threads/${encodeURIComponent(threadId)}`, { title })
      setThreads((current) => current.map((thread) => thread.id === threadId ? updated : thread))
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Unable to rename conversation.'
      setSidebarError(message)
      throw cause
    }
  }

  async function deleteThread(threadId: string) {
    setSidebarError(null)
    try {
      await api.delete<void>(`/api/chat/threads/${encodeURIComponent(threadId)}`)
      setThreads((current) => current.filter((thread) => thread.id !== threadId))
      if (threadId === activeThreadId) navigate('/', { replace: true })
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Unable to delete conversation.'
      setSidebarError(message)
      throw cause
    }
  }

  async function signOut() {
    const { error } = await supabase.auth.signOut()
    if (error) setSidebarError(error.message)
  }

  function handleThreadCreated(thread: ChatThread) {
    setThreads((current) => [thread, ...current.filter((item) => item.id !== thread.id)])
    setMobileSidebarPath(null)
    navigate(`/chat/${encodeURIComponent(thread.id)}`, {
      replace: true,
      state: { createdThreadId: thread.id },
    })
    void refreshThreads()
  }

  const sidebar = (
    <ThreadSidebar
      activeThreadId={activeThreadId}
      email={session?.user.email ?? ''}
      error={sidebarError}
      isLoading={isLoadingThreads}
      onDelete={deleteThread}
      onNewChat={newChat}
      onRename={renameThread}
      onSelectThread={selectThread}
      onSignOut={() => void signOut()}
      threads={threads}
    />
  )

  return (
    <main className="flex h-dvh min-h-[480px] overflow-hidden bg-slate-950">
      <aside className="hidden w-72 shrink-0 border-r border-white/10 md:block">{sidebar}</aside>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-3 border-b border-white/10 px-3 py-2 md:hidden">
          <Sheet
            open={isMobileSidebarOpen}
            onOpenChange={(open) => setMobileSidebarPath(open ? location.pathname : null)}
          >
            <SheetTrigger asChild>
              <Button aria-label="Open conversations" className="text-slate-300" size="icon" variant="ghost">
                <Menu />
              </Button>
            </SheetTrigger>
            <SheetContent className="w-[300px] border-white/10 bg-slate-950 p-0 text-slate-100" side="left">
              <SheetHeader className="sr-only">
                <SheetTitle>Conversations</SheetTitle>
                <SheetDescription>Browse and manage your chat threads.</SheetDescription>
              </SheetHeader>
              {sidebar}
            </SheetContent>
          </Sheet>
          <span className="text-sm font-medium text-slate-300">TechPilot</span>
        </div>
        <ChatContainer onThreadCreated={handleThreadCreated} threadId={activeThreadId} />
      </div>
    </main>
  )
}
