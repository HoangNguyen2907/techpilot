import { ArrowUpRight } from 'lucide-react'
import { Card } from '@/components/ui/card'

const prompts = [
  'How has Bun evolved compared with Node.js on Hacker News?',
  'Why are developers moving from Redux to Zustand or Jotai?',
  'What do Hacker News users like and dislike about Tailwind CSS?',
]

export function EmptyState({ onPrompt }: { onPrompt: (prompt: string) => void }) {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-5 pb-8">
      <p className="text-sm font-medium text-cyan-300">TechPilot · Developer discussions</p>
      <h1 className="mt-3 text-3xl font-semibold tracking-tight text-slate-100 sm:text-4xl">What are developers talking about?</h1>
      <p className="mt-3 max-w-2xl leading-6 text-slate-400">Ask about technology trends and community opinions on Hacker News. Answers are grounded in the available discussions.</p>
      <div className="mt-8 grid gap-3 sm:grid-cols-3">
        {prompts.map((prompt) => (
          <Card className="border-white/10 bg-slate-900 text-slate-200 transition hover:border-cyan-300/40 hover:bg-slate-800" key={prompt}>
            <button className="flex h-full w-full items-start justify-between gap-3 p-4 text-left text-sm leading-5" onClick={() => onPrompt(prompt)} type="button">
              {prompt}<ArrowUpRight className="mt-0.5 h-4 w-4 shrink-0 text-cyan-300" />
            </button>
          </Card>
        ))}
      </div>
    </div>
  )
}
