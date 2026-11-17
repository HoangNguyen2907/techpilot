import { env } from '@/lib/env'
import { supabase } from '@/lib/supabase'

export type CurrentUser = {
  id: string
  email: string | null
}

export type ChatThread = {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export type ChatMessage = {
  id: string
  thread_id: string
  role: 'user' | 'assistant'
  content: string
  parts: unknown
  created_at: string
}

export type ChatThreadDetail = ChatThread & {
  messages: ChatMessage[]
}

export class ApiError extends Error {
  readonly status: number
  readonly isNetworkError: boolean

  constructor(
    message: string,
    status: number,
    isNetworkError = false,
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.isNetworkError = isNetworkError
  }
}

type RequestOptions = {
  signal?: AbortSignal
}

async function accessToken(): Promise<string> {
  const { data, error } = await supabase.auth.getSession()
  if (error) throw new ApiError(error.message, 0)
  if (!data.session) throw new ApiError('Your session has expired. Please sign in again.', 401)
  return data.session.access_token
}

async function responseError(response: Response): Promise<ApiError> {
  let message = `Request failed (${response.status}).`
  try {
    const body: unknown = await response.json()
    if (typeof body === 'object' && body !== null && 'detail' in body) {
      const detail = body.detail
      if (typeof detail === 'string') message = detail
    }
  } catch {
    // Keep the status-based message when the response has no JSON body.
  }
  return new ApiError(message, response.status)
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  options?: RequestOptions,
): Promise<T> {
  const token = await accessToken()
  const headers = new Headers({ Authorization: `Bearer ${token}` })
  if (body !== undefined) headers.set('Content-Type', 'application/json')

  let response: Response
  try {
    response = await fetch(`${env.apiBaseUrl.replace(/\/$/, '')}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: options?.signal,
    })
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    throw new ApiError('Could not connect to the API. Check your connection and try again.', 0, true)
  }

  if (!response.ok) throw await responseError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

async function stream(
  path: string,
  body: unknown,
  signal: AbortSignal,
): Promise<Response> {
  const token = await accessToken()
  let response: Response
  try {
    response = await fetch(`${env.apiBaseUrl.replace(/\/$/, '')}${path}`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body: JSON.stringify(body),
      signal,
    })
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    throw new ApiError('Could not connect to the API. Check your connection and try again.', 0, true)
  }

  if (!response.ok) throw await responseError(response)
  if (!response.body) throw new ApiError('The API returned an empty response stream.', response.status)
  return response
}

export const api = {
  get<T>(path: string, options?: RequestOptions) {
    return request<T>('GET', path, undefined, options)
  },
  post<T>(path: string, body: unknown, options?: RequestOptions) {
    return request<T>('POST', path, body, options)
  },
  patch<T>(path: string, body: unknown, options?: RequestOptions) {
    return request<T>('PATCH', path, body, options)
  },
  delete<T>(path: string, options?: RequestOptions) {
    return request<T>('DELETE', path, undefined, options)
  },
  stream,
}
