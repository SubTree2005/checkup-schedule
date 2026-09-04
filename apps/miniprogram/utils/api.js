const { requestTransport } = require('./runtime-config')

let initializedCloudEnv = ''
let authRedirectPending = false
let cacheGeneration = 0
let lastErrorToast = { message: '', shownAt: 0 }
const responseCache = new Map()
const pendingRequests = new Map()

function clearCache() {
  cacheGeneration += 1
  responseCache.clear()
  pendingRequests.clear()
}

function transportKey(transport) {
  return transport.type === 'cloud'
    ? `cloud:${transport.env}:${transport.service}`
    : `http:${transport.baseUrl}`
}

function pruneResponseCache(now) {
  if (responseCache.size < 50) return
  responseCache.forEach((entry, key) => {
    if (entry.expiresAt <= now) responseCache.delete(key)
  })
  while (responseCache.size > 100) {
    responseCache.delete(responseCache.keys().next().value)
  }
}

function ensureCloudInitialized(transport) {
  if (!wx.cloud || typeof wx.cloud.init !== 'function' || typeof wx.cloud.callContainer !== 'function') {
    throw new Error('当前微信版本不支持云托管，请升级微信后重试')
  }
  if (initializedCloudEnv === transport.env) return
  wx.cloud.init({ env: transport.env, traceUser: true })
  initializedCloudEnv = transport.env
}

function expireSession() {
  try {
    const app = getApp()
    if (app && typeof app.clearLoginState === 'function') app.clearLoginState()
    else wx.removeStorageSync('patientToken')
  } catch (error) {
    wx.removeStorageSync('patientToken')
  }
  const pages = typeof getCurrentPages === 'function' ? getCurrentPages() : []
  const route = pages.length ? pages[pages.length - 1].route : ''
  if (!pages.length || route === 'pages/login/login' || authRedirectPending) return
  authRedirectPending = true
  wx.reLaunch({
    url: '/pages/login/login',
    complete() { authRedirectPending = false }
  })
}

function handleResponse(response, resolve, reject, options) {
  if (response.statusCode >= 200 && response.statusCode < 300) {
    resolve(response.data)
    return
  }
  if (response.statusCode === 401 && !options.skipAuthExpiry) expireSession()
  const message = response.data && response.data.detail
  const error = new Error(typeof message === 'string' ? message : `请求失败（${response.statusCode}）`)
  error.statusCode = response.statusCode
  reject(error)
}

function networkError(error) {
  const detail = String(error && error.errMsg || '').trim()
  const generic = !detail || /^request:fail(?:\s|$)/i.test(detail) || /^cloud\.callContainer:fail(?:\s|$)/i.test(detail)
  const message = generic ? '暂时无法连接服务，请稍后重试' : detail
  const result = new Error(message)
  result.isNetworkError = true
  return result
}

function request(path, options = {}) {
  let transport
  try {
    transport = requestTransport()
  } catch (error) {
    return Promise.reject(error)
  }
  const token = wx.getStorageSync('patientToken')
  const method = String(options.method || 'GET').toUpperCase()
  const cacheMs = method === 'GET' ? Math.max(0, Number(options.cacheMs || 0)) : 0
  const invalidatesCache = method !== 'GET' && options.invalidateCache !== false
  const key = `${transportKey(transport)}|${token || 'anonymous'}|${method}|${path}`
  const now = Date.now()
  pruneResponseCache(now)
  if (cacheMs > 0) {
    const cached = responseCache.get(key)
    if (cached && cached.expiresAt > now) return Promise.resolve(cached.value)
    if (cached) responseCache.delete(key)
    const pending = pendingRequests.get(key)
    if (pending) return pending
  } else if (invalidatesCache) {
    clearCache()
  }
  const generation = cacheGeneration
  const operation = new Promise((resolve, reject) => {
    const header = {
      'content-type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {})
    }
    const callbacks = {
      success: response => handleResponse(response, resolve, reject, options),
      fail(error) {
        reject(networkError(error))
      }
    }

    if (transport.type === 'cloud') {
      try {
        ensureCloudInitialized(transport)
      } catch (error) {
        reject(error)
        return
      }
      wx.cloud.callContainer({
        config: { env: transport.env },
        path,
        header: { 'X-WX-SERVICE': transport.service, ...header },
        method,
        data: options.data === undefined ? '' : options.data,
        ...callbacks
      })
      return
    }

    wx.request({
      url: `${transport.baseUrl}${path}`,
      method,
      data: options.data,
      timeout: 15000,
      header,
      ...callbacks
    })
  })
  if (cacheMs <= 0) {
    if (!invalidatesCache) return operation
    return operation.then(value => {
      // A GET may finish while this mutation is in flight and cache the old
      // representation. Clear again after the server commits the mutation.
      clearCache()
      return value
    })
  }
  const shared = operation.then(value => {
    if (pendingRequests.get(key) === shared) pendingRequests.delete(key)
    if (generation === cacheGeneration) responseCache.set(key, { expiresAt: Date.now() + cacheMs, value })
    return value
  }, error => {
    if (pendingRequests.get(key) === shared) pendingRequests.delete(key)
    throw error
  })
  pendingRequests.set(key, shared)
  return shared
}

function showError(error) {
  const message = error && error.message ? error.message : '操作失败'
  const now = Date.now()
  if (message === lastErrorToast.message && now - lastErrorToast.shownAt < 1500) return
  lastErrorToast = { message, shownAt: now }
  wx.showToast({ title: message, icon: 'none', duration: 2500 })
}

module.exports = {
  clearCache,
  showError,
  auth: {
    register: data => request('/api/patient/auth/register', { method: 'POST', data }),
    login: data => request('/api/patient/auth/login', { method: 'POST', data, skipAuthExpiry: true }),
    me: () => request('/api/patient/auth/me', { cacheMs: 30000 }),
    logout: () => request('/api/patient/auth/logout', { method: 'POST' }),
    deleteAccount: password => request('/api/patient/account', { method: 'DELETE', data: { password } })
  },
  profile: {
    update: data => request('/api/patient/profile', { method: 'PATCH', data })
  },
  hospitals: {
    list: () => request('/api/patient/hospitals', { cacheMs: 120000 }),
    catalog: hospitalID => request(`/api/patient/hospitals/${encodeURIComponent(hospitalID)}/catalog`, { cacheMs: 120000 }),
    appointmentSlots: hospitalID => request(`/api/patient/hospitals/${encodeURIComponent(hospitalID)}/appointment-slots`, { cacheMs: 15000 })
  },
  plans: {
    create: data => request('/api/patient/plans', { method: 'POST', data }),
    current: () => request('/api/patient/plans/current', { cacheMs: 3000 }),
    list: () => request('/api/patient/plans', { cacheMs: 5000 }),
    get: planID => request(`/api/patient/plans/${encodeURIComponent(planID)}`, { cacheMs: 3000 }),
    start: (planID, detailID) => request(`/api/patient/plans/${encodeURIComponent(planID)}/steps/${encodeURIComponent(detailID)}/start`, { method: 'POST' }),
    complete: (planID, detailID) => request(`/api/patient/plans/${encodeURIComponent(planID)}/steps/${encodeURIComponent(detailID)}/complete`, { method: 'POST' }),
    pause: planID => request(`/api/patient/plans/${encodeURIComponent(planID)}/pause`, { method: 'POST' }),
    resume: planID => request(`/api/patient/plans/${encodeURIComponent(planID)}/resume`, { method: 'POST' }),
    finish: planID => request(`/api/patient/plans/${encodeURIComponent(planID)}/finish`, { method: 'POST' }),
    replan: planID => request(`/api/patient/plans/${encodeURIComponent(planID)}/replan`, { method: 'POST' }),
    navigation: (planID, detailID) => request(`/api/patient/plans/${encodeURIComponent(planID)}/navigation?detailID=${encodeURIComponent(detailID)}`)
  },
  reminders: {
    config: () => request('/api/patient/reminders/config', { cacheMs: 60000 }),
    list: () => request('/api/patient/reminders', { cacheMs: 10000 })
  },
  agent: {
    status: () => request('/api/patient/agent/status', { cacheMs: 30000 }),
    chat: data => request('/api/patient/agent/chat', { method: 'POST', data, invalidateCache: false })
  }
}
