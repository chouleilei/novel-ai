const SAVED_USERNAME_KEY = 'novel-ai:saved-username'

export interface AuthCredentials {
  username: string
  password: string
}

const encodeBasicAuth = (value: string) => {
  const bytes = new TextEncoder().encode(value)
  let binary = ''
  for (const byte of bytes) {
    binary += String.fromCharCode(byte)
  }
  return window.btoa(binary)
}

export const getSavedUsername = () => {
  if (typeof window === 'undefined') {
    return ''
  }
  return window.localStorage.getItem(SAVED_USERNAME_KEY) || ''
}

export const saveUsername = (username: string) => {
  if (typeof window === 'undefined') {
    return
  }
  window.localStorage.setItem(SAVED_USERNAME_KEY, username)
}

export const checkAuthSession = async () => {
  const response = await fetch('/auth/session', {
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
    },
  })

  return response.ok
}

export const loginWithBasicAuth = async ({ username, password }: AuthCredentials) => {
  const response = await fetch('/auth/login', {
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
      Authorization: `Basic ${encodeBasicAuth(`${username}:${password}`)}`,
    },
  })

  if (!response.ok) {
    throw new Error(response.status === 401 ? '用户名或密码错误。' : '登录失败，请稍后重试。')
  }

  saveUsername(username)
}

export const logoutAuthSession = async () => {
  await fetch('/auth/logout', {
    method: 'POST',
    credentials: 'same-origin',
  })
}
