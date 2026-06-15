import axios from 'axios'

class AppError extends Error {
  code: string
  status?: number
  detail?: string
  retriable: boolean
  response?: { data?: { detail?: string } }

  constructor(options: { message: string; code: string; status?: number; detail?: string; retriable?: boolean }) {
    super(options.message)
    this.code = options.code
    this.status = options.status
    this.detail = options.detail
    this.retriable = options.retriable ?? false
    if (options.detail || options.status) {
      this.response = { data: { detail: options.detail } }
    }
  }
}

const api = axios.create({
  baseURL: '/api',
  headers: {
    'Content-Type': 'application/json',
  },
})

api.interceptors.response.use(
  (response) => response.data,
  (error) => {
    if (!error.response) {
      return Promise.reject(new AppError({
        message: '网络连接失败，请检查网络后重试',
        code: 'NETWORK_ERROR',
        retriable: true,
      }))
    }

    const status = error.response.status
    const detail = error.response.data?.detail

    if (status === 401) {
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('novel-ai:auth-required'))
      }
      return Promise.reject(new AppError({
        message: '认证已过期，请重新登录',
        code: 'AUTH_EXPIRED',
        status,
        detail: typeof detail === 'string' ? detail : undefined,
      }))
    }

    if (status === 403) {
      return Promise.reject(new AppError({
        message: '没有权限执行此操作',
        code: 'FORBIDDEN',
        status,
        detail: typeof detail === 'string' ? detail : undefined,
      }))
    }

    if (status >= 500) {
      return Promise.reject(new AppError({
        message: typeof detail === 'string' ? detail : '服务器内部错误，请稍后重试',
        code: 'SERVER_ERROR',
        status,
        detail: typeof detail === 'string' ? detail : undefined,
        retriable: true,
      }))
    }

    return Promise.reject(new AppError({
      message: typeof detail === 'string' ? detail : (error.message || '请求失败'),
      code: 'CLIENT_ERROR',
      status,
      detail: typeof detail === 'string' ? detail : undefined,
    }))
  }
)

export default api
export { AppError }
