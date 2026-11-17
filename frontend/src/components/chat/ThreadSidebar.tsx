import { useMemo, useState } from 'react'
import { LogOut, MessageSquarePlus, Pencil, Trash2, Check, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { ChatThread } from '@/lib/api'

type ThreadSidebarProps = {
  threads: ChatThread[]
  activeThreadId: string | null
  email: string
  error: string | null
  isLoading: boolean
  onNewChat: () => void
  onSelectThread: (threadId: string) => void
  onRename: (threadId: string, title: string) => Promise<void>
  onDelete: (threadId: string) => Promise<void>
  onSignOut: () => void
}

const dateFormatter = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })

function groupThreads(threads: ChatThread[]) {
  const groups = new Map<string, ChatThread[]>([
    ['Today', []],
    ['Previous 7 Days', []],
    ['Older', []],
  ])
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const weekStart = new Date(today)
  weekStart.setDate(weekStart.getDate() - 7)

  for (const thread of threads) {
    const updatedAt = new Date(thread.updated_at)
    const group = updatedAt >= today ? 'Today' : updatedAt >= weekStart ? 'Previous 7 Days' : 'Older'
    groups.get(group)?.push(thread)
  }
  return [...groups]
}

export function ThreadSidebar({
  threads,
  activeThreadId,
  email,
  error,
  isLoading,
  onNewChat,
  onSelectThread,
  onRename,
  onDelete,
  onSignOut,
}: ThreadSidebarProps) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draftTitle, setDraftTitle] = useState('')
  const groupedThreads = useMemo(() => groupThreads(threads), [threads])

  function startRename(thread: ChatThread) {
    setEditingId(thread.id)
    setDraftTitle(thread.title)
  }

  async function saveRename(threadId: string) {
    const title = draftTitle.trim()
    if (!title) return
    try {
      await onRename(threadId, title)
      setEditingId(null)
    } catch {
      // The parent displays the request error in the sidebar.
    }
  }

  async function confirmDelete(thread: ChatThread) {
    if (!window.confirm(`Delete “${thread.title}”? This cannot be undone.`)) return
    try {
      await onDelete(thread.id)
    } catch {
      // The parent displays the request error in the sidebar.
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950 text-slate-100">
      <div className="flex items-center justify-between px-4 pb-4 pt-5">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-300">TechPilot</p>
          <p className="mt-1 text-xs text-slate-500">Hacker News research</p>
        </div>
        <Button
          aria-label="Start a new chat"
          className="border-white/10 bg-slate-900 text-slate-200 hover:bg-slate-800"
          onClick={onNewChat}
          size="icon"
          title="New chat"
          variant="outline"
        >
          <MessageSquarePlus />
        </Button>
      </div>
      <Button
        className="mx-3 mb-4 justify-start bg-cyan-300 text-slate-950 hover:bg-cyan-200"
        onClick={onNewChat}
      >
        <MessageSquarePlus /> New chat
      </Button>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-4">
        {error && <p className="mb-3 rounded-md bg-rose-400/10 p-2 text-xs text-rose-200" role="alert">{error}</p>}
        {groupedThreads.map(([groupName, grouped]) => grouped.length > 0 && (
          <section className="mb-5" key={groupName}>
            <h2 className="px-2 pb-2 text-xs font-medium text-slate-500">{groupName}</h2>
            <ul className="space-y-1">
              {grouped.map((thread) => (
                <li className="group rounded-lg" key={thread.id}>
                  {editingId === thread.id ? (
                    <div className="flex items-center gap-1 p-1">
                      <Input
                        aria-label="Thread title"
                        autoFocus
                        className="h-8 border-white/10 bg-slate-900 text-sm text-slate-100"
                        maxLength={255}
                        onChange={(event) => setDraftTitle(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') void saveRename(thread.id)
                          if (event.key === 'Escape') setEditingId(null)
                        }}
                        value={draftTitle}
                      />
                      <Button aria-label="Save title" className="h-8 w-8" onClick={() => void saveRename(thread.id)} size="icon" variant="ghost">
                        <Check />
                      </Button>
                      <Button aria-label="Cancel rename" className="h-8 w-8" onClick={() => setEditingId(null)} size="icon" variant="ghost">
                        <X />
                      </Button>
                    </div>
                  ) : (
                    <div className={`flex items-center rounded-lg ${activeThreadId === thread.id ? 'bg-slate-800' : 'hover:bg-slate-900'}`}>
                      <button
                        aria-current={activeThreadId === thread.id ? 'page' : undefined}
                        className="min-w-0 flex-1 px-3 py-2 text-left"
                        onClick={() => onSelectThread(thread.id)}
                        type="button"
                      >
                        <span className="block truncate text-sm">{thread.title}</span>
                        <span className="mt-1 block text-xs text-slate-500">{dateFormatter.format(new Date(thread.updated_at))}</span>
                      </button>
                      <div className="flex pr-1 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100">
                        <Button aria-label={`Rename ${thread.title}`} className="h-8 w-8 text-slate-400 hover:text-slate-100" onClick={() => startRename(thread)} size="icon" variant="ghost">
                          <Pencil />
                        </Button>
                        <Button aria-label={`Delete ${thread.title}`} className="h-8 w-8 text-slate-400 hover:text-rose-300" onClick={() => void confirmDelete(thread)} size="icon" variant="ghost">
                          <Trash2 />
                        </Button>
                      </div>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </section>
        ))}
        {isLoading && <p className="px-2 text-sm text-slate-500">Loading conversations…</p>}
        {!isLoading && threads.length === 0 && <p className="px-2 text-sm text-slate-500">Your conversations will appear here.</p>}
      </div>

      <div className="border-t border-white/10 p-3">
        <p className="truncate px-2 pb-2 text-xs text-slate-400" title={email}>{email}</p>
        <Button className="w-full justify-start text-slate-300 hover:bg-slate-900 hover:text-white" onClick={onSignOut} variant="ghost">
          <LogOut /> Sign out
        </Button>
      </div>
    </div>
  )
}
