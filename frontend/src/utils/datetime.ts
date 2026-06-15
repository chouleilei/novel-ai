type DateTimeInput = string | number | Date | null | undefined

type FormatterParts = {
  year?: string
  month?: string
  day?: string
  hour?: string
  minute?: string
  second?: string
}

const SHANGHAI_TIME_ZONE = 'Asia/Shanghai'
const NAIVE_ISO_DATETIME_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/
const DATE_ONLY_PATTERN = /^\d{4}-\d{2}-\d{2}$/

const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: SHANGHAI_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

const dateTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: SHANGHAI_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
  hourCycle: 'h23',
})

const timeFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: SHANGHAI_TIME_ZONE,
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
  hourCycle: 'h23',
})

const parseDateTimeInput = (value: DateTimeInput): Date | null => {
  if (value == null || value === '') {
    return null
  }

  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value
  }

  if (typeof value === 'number') {
    const parsed = new Date(value)
    return Number.isNaN(parsed.getTime()) ? null : parsed
  }

  if (typeof value !== 'string') {
    return null
  }

  const raw = value.trim()
  if (!raw) {
    return null
  }

  let normalized = raw.includes(' ') && !raw.includes('T') ? raw.replace(' ', 'T') : raw
  if (NAIVE_ISO_DATETIME_PATTERN.test(normalized)) {
    normalized = `${normalized}Z`
  } else if (DATE_ONLY_PATTERN.test(normalized)) {
    normalized = `${normalized}T00:00:00Z`
  }

  const parsed = new Date(normalized)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

const getFormatterParts = (value: DateTimeInput, formatter: Intl.DateTimeFormat): FormatterParts | null => {
  const parsed = parseDateTimeInput(value)
  if (!parsed) {
    return null
  }

  const parts: FormatterParts = {}
  for (const part of formatter.formatToParts(parsed)) {
    if (part.type === 'literal') {
      continue
    }
    if (part.type in parts || ['year', 'month', 'day', 'hour', 'minute', 'second'].includes(part.type)) {
      parts[part.type as keyof FormatterParts] = part.value
    }
  }
  return parts
}

export const formatDate = (value: DateTimeInput, fallback = '暂无') => {
  const parts = getFormatterParts(value, dateFormatter)
  if (!parts?.year || !parts.month || !parts.day) {
    return fallback
  }
  return `${parts.year}-${parts.month}-${parts.day}`
}

export const formatDateTime = (value: DateTimeInput, fallback = '暂无') => {
  const parts = getFormatterParts(value, dateTimeFormatter)
  if (!parts?.year || !parts.month || !parts.day || !parts.hour || !parts.minute || !parts.second) {
    return fallback
  }
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`
}

export const formatTime = (value: DateTimeInput, fallback = '暂无') => {
  const parts = getFormatterParts(value, timeFormatter)
  if (!parts?.hour || !parts.minute || !parts.second) {
    return fallback
  }
  return `${parts.hour}:${parts.minute}:${parts.second}`
}
