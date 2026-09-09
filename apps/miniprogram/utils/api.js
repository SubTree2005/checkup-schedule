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
  const detail = String(error && (error.errMsg || error.message) || '').trim()
  const reason = detail.replace(/^(?:request|cloud\.callContainer|callContainer):fail\s*/i, '').trim()
  const code = error && error.errCode !== undefined ? String(error.errCode) : ''
  const timedOut = /timeout|timed out|超时/i.test(detail)
  const summary = timedOut ? '连接超时，请检查网络后重试' : '暂时无法连接服务'
  const message = `${summary}${code ? `（${code}）` : ''}${reason ? `：${reason}` : '，请稍后重试'}`
  const result = new Error(message)
  result.errCode = code
  result.errMsg = detail
  result.isNetworkError = true
  result.isTimeout = timedOut
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
    let settled = false
    const finish = callback => value => {
      if (settled) return
      settled = true
      clearTimeout(deadline)
      callback(value)
    }
    const succeed = finish(resolve)
    const fail = finish(reject)
    const deadline = setTimeout(() => {
      fail(networkError({ errMsg: 'request:fail timeout', errCode: 'CLIENT_TIMEOUT' }))
    }, 16000)
    const header = {
      'content-type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {})
    }
    const callbacks = {
      success: response => {
        if (!settled) handleResponse(response, succeed, fail, options)
      },
      fail(error) {
        fail(networkError(error))
      }
    }

    if (transport.type === 'cloud') {
      try {
        ensureCloudInitialized(transport)
      } catch (error) {
        fail(error)
        return
      }
      wx.cloud.callContainer({
        config: { env: transport.env },
        path,
        header: { 'X-WX-SERVICE': transport.service, ...header },
        method,
        timeout: 15000,
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
  const repeatDelay = error && error.isNetworkError ? 60000 : 1500
  if (message === lastErrorToast.message && now - lastErrorToast.shownAt < repeatDelay) return
  lastErrorToast = { message, shownAt: now }
  wx.showToast({ title: message, icon: 'none', duration: error && error.isNetworkError ? 5000 : 2500 })
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
    list: () => request('/api/patient/hospitals'),
    catalog: hospitalID => request(`/api/patient/hospitals/${encodeURIComponent(hospitalID)}/catalog`),
    appointmentSlots: hospitalID => request(`/api/patient/hospitals/${encodeURIComponent(hospitalID)}/appointment-slots`)
  },
  plans: {
    create: data => request('/api/patient/plans', { method: 'POST', data }),
    current: () => request('/api/patient/plans/current', { cacheMs: 3000 }),
    list: () => request('/api/patient/plans', { cacheMs: 5000 }),
    get: planID => request(`/api/patient/plans/${encodeURIComponent(planID)}`, { cacheMs: 3000 }),
    start: (planID, detailID) => request(`/api/patient/plans/${encodeURIComponent(planID)}/steps/${encodeURIComponent(detailID)}/start`, { method: 'POST' }),
    complete: (planID, detailID) => request(`/api/patient/plans/${encodeURIComponent(planID)}/steps/${encodeURIComponent(detailID)}/complete`, { method: 'POST' }),
    skip: (planID, detailID) => request(`/api/patient/plans/${encodeURIComponent(planID)}/steps/${encodeURIComponent(detailID)}/skip`, { method: 'POST' }),
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
    createJob: data => request('/api/patient/agent/jobs', { method: 'POST', data, invalidateCache: false }),
    job: id => request(`/api/patient/agent/jobs/${encodeURIComponent(id)}`),
    cancelJob: id => request(`/api/patient/agent/jobs/${encodeURIComponent(id)}`, { method: 'DELETE', invalidateCache: false }),
    chat: data => request('/api/patient/agent/chat', { method: 'POST', data, invalidateCache: false })
  }
}
