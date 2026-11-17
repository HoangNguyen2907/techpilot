import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { Send, Square } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'

type ChatInputProps = {
  isStreaming: boolean
  isDisabled: boolean
  onSubmit: (content: string) => void
  onStop: () => void
}

export function ChatInput({ isStreaming, isDisabled, onSubmit, onStop }: ChatInputProps) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`
  }, [value])

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const content = value.trim()
    if (!content || isStreaming || isDisabled) return
    setValue('')
    onSubmit(content)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      event.currentTarget.form?.requestSubmit()
    }
  }

  return (
    <form className="mx-auto flex w-full max-w-4xl items-end gap-2 rounded-2xl border border-white/10 bg-slate-900 p-2 shadow-xl" onSubmit={submit}>
      <Textarea
        aria-label="Your message"
        className="max-h-[180px] min-h-11 resize-none border-0 bg-transparent px-3 py-3 text-slate-100 shadow-none placeholder:text-slate-500 focus-visible:ring-0"
        disabled={isStreaming || isDisabled}
        maxLength={20_000}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Ask about developer trends or discussions…"
        ref={textareaRef}
        rows={1}
        value={value}
      />
      {isStreaming ? (
        <Button aria-label="Stop response" className="mb-0.5 shrink-0 bg-slate-700 text-white hover:bg-slate-600" onClick={onStop} size="icon" type="button">
          <Square />
        </Button>
      ) : (
        <Button aria-label="Send message" className="mb-0.5 shrink-0 bg-cyan-300 text-slate-950 hover:bg-cyan-200" disabled={!value.trim() || isDisabled} size="icon" type="submit">
          <Send />
        </Button>
      )}
    </form>
  )
}
