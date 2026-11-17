import { env } from '@/lib/env'

export type CurrentUser = {
  id: string
  email: string | null
}

export const api = {
  async get<T>(path: string, accessToken: string): Promise<T> {
    const response = await fetch(`${env.apiBaseUrl.replace(/\/$/, '')}${path}`, {
      headers: {
        Authorization: `Bearer ${accessToken}`,
      },
    })

    if (!response.ok) {
      throw new Error(`API request failed (${response.status}).`)
    }

    return (await response.json()) as T
  },
}
