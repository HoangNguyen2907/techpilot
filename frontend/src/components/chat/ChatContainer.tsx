import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, Sparkles } from 'lucide-react'
import ReactMarkdown, { type Components } from 'react-markdown'
import { Button } from '@/components/ui/button'
import { ChatInput } from '@/components/chat/ChatInput'
import { EmptyState } from '@/components/chat/EmptyState'
import { api, type ChatMessage, type ChatThread, type ChatThreadDetail } from '@/lib/api'

type ChatContainerProps = {
  threadId: string | null
  onThreadCreated: (thread: ChatThread) => void
}

type ServerEvent = {
  event: string
  data: Record<string, unknown>
}

type ConversationState = {
  threadId: string | null
  messages: ChatMessage[]
  error: string | null
  isLoaded: boolean
}

const markdownComponents: Components = {
  p: ({ children }) => <p className="mb-3 last:mb-0 leading-7">{children}</p>,
  a: ({ href, children }) => (
    <a className="text-cyan-300 underline decoration-cyan-300/40 underline-offset-4 hover:text-cyan-200" href={href} rel="noreferrer" target="_blank">
      {children}
    </a>
  ),
  ul: ({ children }) => <ul className="mb-3 list-disc space-y-1 pl-6 last:mb-0">{children}</ul>,
  ol: ({ children }) => <ol className="mb-3 list-decimal space-y-1 pl-6 last:mb-0">{children}</ol>,
  blockquote: ({ children }) => <blockquote className="my-3 border-l-2 border-cyan-300/50 pl-4 text-slate-300">{children}</blockquote>,
  pre: ({ children }) => <pre className="my-3 overflow-x-auto rounded-lg bg-slate-950 p-4 text-sm">{children}</pre>,
  code: ({ children, className }) => (
    <code className={`${className ?? ''} rounded bg-slate-950/80 px-1 py-0.5 font-mono text-[0.9em] text-cyan-100`}>{children}</code>
  ),
  h1: ({ children }) => <h2 className="mb-2 mt-5 text-xl font-semibold first:mt-0">{children}</h2>,
  h2: ({ children }) => <h2 className="mb-2 mt-5 text-lg font-semibold first:mt-0">{children}</h2>,
  h3: ({ children }) => <h3 className="mb-2 mt-4 font-semibold first:mt-0">{children}</h3>,
  table: ({ children }) => <div className="my-3 overflow-x-auto"><table className="w-full border-collapse text-left text-sm">{children}</table></div>,
  th: ({ children }) => <th className="border-b border-white/15 px-3 py-2 font-semibold">{children}</th>,
  td: ({ children }) => <td className="border-b border-white/10 px-3 py-2 align-top">{children}</td>,
}

function parseEventData(value: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value)
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new Error('The API sent an invalid streaming event.')
  }
  return parsed as Record<string, unknown>
}

async function readSseStream(
  response: Response,
  onEvent: (event: ServerEvent) => void,
  signal: AbortSignal,
) {
  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  function dispatch(block: string) {
    let event = 'message'
    const data: string[] = []
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
    }
    if (data.length > 0) onEvent({ event, data: parseEventData(data.join('\n')) })
  }

  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      const blocks = buffer.split(/\r?\n\r?\n/)
      buffer = blocks.pop() ?? ''
      for (const block of blocks) dispatch(block)
      if (done) break
    }
    if (!signal.aborted && buffer.trim()) dispatch(buffer)
  } finally {
    reader.releaseLock()
  }
}

function newLocalMessage(role: ChatMessage['role'], content: string): ChatMessage {
  return {
    id: crypto.randomUUID(),
    thread_id: '',
    role,
    content,
    parts: null,
    created_at: new Date().toISOString(),
  }
}

export function ChatContainer({ threadId, onThreadCreated }: ChatContainerProps) {
  const [conversation, setConversation] = useState<ConversationState>({
    threadId: null,
    messages: [],
    error: null,
    isLoaded: true,
  })
  const [isStreaming, setIsStreaming] = useState(false)
  const [showJumpToLatest, setShowJumpToLatest] = useState(false)
  const historyRef = useRef<HTMLDivElement>(null)
  const shouldAutoScroll = useRef(true)
  const streamController = useRef<AbortController | null>(null)
  const skipHistoryForThread = useRef<string | null>(null)
  const preserveStreamOnThreadChange = useRef(false)
  const messages = useMemo(
    () => conversation.threadId === threadId ? conversation.messages : [],
    [conversation.messages, conversation.threadId, threadId],
  )
  const error = conversation.threadId === threadId ? conversation.error : null
  const isLoadingHistory = threadId !== null && (
    conversation.threadId !== threadId || !conversation.isLoaded
  )

  useEffect(() => {
    if (!threadId || skipHistoryForThread.current === threadId) {
      if (skipHistoryForThread.current === threadId) skipHistoryForThread.current = null
      return
    }

    const controller = new AbortController()
    void api.get<ChatThreadDetail>(`/api/chat/threads/${encodeURIComponent(threadId)}`, { signal: controller.signal })
      .then((thread) => setConversation({ threadId, messages: thread.messages, error: null, isLoaded: true }))
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) {
          setConversation({
            threadId,
            messages: [],
            error: cause instanceof Error ? cause.message : 'Unable to load this conversation.',
            isLoaded: true,
          })
        }
      })

    return () => controller.abort()
  }, [threadId])

  useEffect(() => () => {
    if (preserveStreamOnThreadChange.current) {
      preserveStreamOnThreadChange.current = false
      return
    }
    streamController.current?.abort()
  }, [threadId])

  useEffect(() => {
    const history = historyRef.current
    if (history && shouldAutoScroll.current) history.scrollTop = history.scrollHeight
  }, [messages])

  function handleScroll() {
    const history = historyRef.current
    if (!history) return
    shouldAutoScroll.current = history.scrollHeight - history.scrollTop - history.clientHeight < 96
    setShowJumpToLatest(!shouldAutoScroll.current)
  }

  function jumpToLatest() {
    const history = historyRef.current
    if (!history) return
    history.scrollTo({ top: history.scrollHeight, behavior: 'smooth' })
    shouldAutoScroll.current = true
    setShowJumpToLatest(false)
  }

  async function submitMessage(content: string) {
    if (isStreaming || isLoadingHistory) return
    setIsStreaming(true)
    shouldAutoScroll.current = true
    const userMessage = newLocalMessage('user', content)
    const assistantMessage = newLocalMessage('assistant', '')
    setConversation((current) => ({
      threadId,
      messages: [...(current.threadId === threadId ? current.messages : []), userMessage, assistantMessage],
      error: null,
      isLoaded: true,
    }))

    const controller = new AbortController()
    streamController.current = controller
    let activeThreadId = threadId
    let receivedDone = false
    try {
      const response = await api.stream('/api/chat/stream', { threadId, content }, controller.signal)
      await readSseStream(response, (serverEvent) => {
        if (serverEvent.event === 'thread_created') {
          const { threadId: createdId, title } = serverEvent.data
          if (typeof createdId !== 'string' || typeof title !== 'string') {
            throw new Error('The API sent an invalid new thread event.')
          }
          activeThreadId = createdId
          const now = new Date().toISOString()
          const createdThread = { id: createdId, title, created_at: now, updated_at: now }
          skipHistoryForThread.current = createdId
          preserveStreamOnThreadChange.current = true
          setConversation((current) => ({ ...current, threadId: createdId, isLoaded: true }))
          onThreadCreated(createdThread)
        } else if (serverEvent.event === 'delta') {
          const text = serverEvent.data.text
          if (typeof text === 'string' && text) {
            setConversation((current) => current.threadId === activeThreadId
              ? {
                ...current,
                messages: current.messages.map((message) => message.id === assistantMessage.id
                  ? { ...message, content: message.content + text }
                  : message),
              }
              : current)
          }
        } else if (serverEvent.event === 'done') {
          receivedDone = true
        } else if (serverEvent.event === 'error') {
          const detail = serverEvent.data.error
          throw new Error(typeof detail === 'string' ? detail : 'The response could not be completed.')
        }
      }, controller.signal)

      if (!controller.signal.aborted && !receivedDone) {
        throw new Error('The response stream ended before completion.')
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        const message = cause instanceof Error ? cause.message : 'Unable to send your message.'
        controller.abort()
        setConversation((current) => current.threadId === activeThreadId ? { ...current, error: message } : current)
      }
    } finally {
      if (streamController.current === controller) {
        streamController.current = null
        setIsStreaming(false)
      }
    }
  }

  function stopResponse() {
    streamController.current?.abort()
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col bg-slate-950 text-slate-100">
      <div className="flex items-center justify-between border-b border-white/10 px-5 py-3 md:px-8">
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <Sparkles className="h-4 w-4 text-cyan-300" />
          <span>{threadId ? 'Conversation' : 'New conversation'}</span>
        </div>
        {isLoadingHistory && <span className="text-xs text-slate-500">Loading history…</span>}
      </div>

      <div className="relative flex min-h-0 flex-1 flex-col">
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto" onScroll={handleScroll} ref={historyRef}>
          {messages.length === 0 && !isLoadingHistory ? (
            <EmptyState onPrompt={(prompt) => void submitMessage(prompt)} />
          ) : (
            <div className="mx-auto w-full max-w-4xl flex-1 space-y-6 px-5 py-8">
              {messages.map((message) => (
                <article className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`} key={message.id}>
                  <div className={message.role === 'user'
                    ? 'max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-slate-800 px-4 py-3 text-slate-100 sm:max-w-[75%]'
                    : 'max-w-full min-w-0 text-slate-200 sm:max-w-[90%]'}>
                    {message.role === 'assistant' ? (
                      message.content ? (
                        <div className="break-words text-sm leading-7">
                          <ReactMarkdown components={markdownComponents}>{message.content}</ReactMarkdown>
                        </div>
                      ) : isStreaming ? (
                        <span aria-label="Assistant is responding" className="inline-block h-5 w-1 animate-pulse rounded bg-cyan-300" />
                      ) : null
                    ) : message.content}
                  </div>
                </article>
              ))}
              {error && <p className="rounded-lg border border-rose-400/20 bg-rose-400/10 px-4 py-3 text-sm text-rose-200" role="alert">{error}</p>}
            </div>
          )}
        </div>
        {showJumpToLatest && (
          <Button aria-label="Jump to latest message" className="absolute bottom-4 left-1/2 -translate-x-1/2 rounded-full border-white/15 bg-slate-800 text-white hover:bg-slate-700" onClick={jumpToLatest} size="icon" variant="outline">
            <ArrowDown />
          </Button>
        )}
      </div>

      <div className="px-4 pb-4 pt-2 sm:px-6 sm:pb-6">
        {error && messages.length === 0 && <p className="mx-auto mb-2 max-w-4xl text-sm text-rose-200" role="alert">{error}</p>}
        <ChatInput isDisabled={isLoadingHistory} isStreaming={isStreaming} onSubmit={(content) => void submitMessage(content)} onStop={stopResponse} />
        <p className="mx-auto mt-2 max-w-4xl text-center text-[11px] text-slate-600">Responses reflect discussions found on Hacker News.</p>
      </div>
    </section>
  )
}
