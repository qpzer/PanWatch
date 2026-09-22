const API_BASE = '/api'
const DEFAULT_TIMEOUT_MS = 20000

interface ApiResponse<T> {
  code: number
  success?: boolean
  data: T
  message: string
}

export function getToken(): string | null {
  return localStorage.getItem('token')
}

export function logout() {
  localStorage.removeItem('token')
  localStorage.removeItem('token_expires')
  window.location.href = '/login'
}

export function isAuthenticated(): boolean {
  const token = getToken()
  if (!token) return false

  const expires = localStorage.getItem('token_expires')
  if (expires && new Date(expires) < new Date()) {
    logout()
    return false
  }
  return true
}

export interface ApiRequestOptions extends RequestInit {
  timeoutMs?: number
}

export async function fetchAPI<T>(path: string, options?: ApiRequestOptions): Promise<T> {
  const headers: Record<string, string> = {}

  const token = getToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  if (options?.body) {
    headers['Content-Type'] = 'application/json'
  }

  // 外部 signal 与超时共存：任一端触发都会中止请求，避免传入 signal 后超时兜底失效
  const timeoutController = new AbortController()
  const timeoutMs = typeof options?.timeoutMs === 'number' && options.timeoutMs > 0
    ? options.timeoutMs
    : DEFAULT_TIMEOUT_MS
  const timeoutId = window.setTimeout(() => timeoutController.abort(), timeoutMs)
  const externalSignal = options?.signal
  const relayExternalAbort = () => timeoutController.abort()
  if (externalSignal) {
    if (externalSignal.aborted) {
      timeoutController.abort()
    } else {
      externalSignal.addEventListener('abort', relayExternalAbort, { once: true })
    }
  }

  let res: Response
  try {
    const { timeoutMs: _timeoutMs, ...requestOptions } = options || {}
    res = await fetch(`${API_BASE}${path}`, {
      ...requestOptions,
      headers: {
        ...headers,
        ...(requestOptions.headers as Record<string, string> | undefined),
      },
      signal: timeoutController.signal,
    })
  } catch (error: any) {
    if (error?.name === 'AbortError') {
      // 外部主动取消（如页面卸载）原样抛出，仅超时中止改写为超时提示
      if (externalSignal?.aborted) throw error
      throw new Error('请求超时，请稍后重试')
    }
    throw error
  } finally {
    window.clearTimeout(timeoutId)
    externalSignal?.removeEventListener('abort', relayExternalAbort)
  }

  if (res.status === 401) {
    logout()
    throw new Error('登录已过期')
  }

  const body: ApiResponse<T> = await res.json().catch(() => ({
    code: res.status,
    data: null as T,
    message: `HTTP ${res.status}`,
  }))
  if (body.code !== 0 || body.success === false) {
    throw new Error(body.message || `HTTP ${res.status}`)
  }
  return body.data
}

export const apiClient = {
  request: fetchAPI,
}
