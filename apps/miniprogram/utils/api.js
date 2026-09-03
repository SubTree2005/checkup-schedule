const { requestTransport } = require('./runtime-config')

let initializedCloudEnv = ''
let authRedirectPending = false

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
  return new Promise((resolve, reject) => {
    let transport
    try {
      transport = requestTransport()
    } catch (error) {
      reject(error)
      return
    }
    const token = wx.getStorageSync('patientToken')
    const method = options.method || 'GET'
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
}

function showError(error) {
  wx.showToast({ title: error.message || '操作失败', icon: 'none', duration: 2500 })
}

module.exports = {
  showError,
  auth: {
    register: data => request('/api/patient/auth/register', { method: 'POST', data }),
    login: data => request('/api/patient/auth/login', { method: 'POST', data, skipAuthExpiry: true }),
    me: () => request('/api/patient/auth/me'),
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
    current: () => request('/api/patient/plans/current'),
    list: () => request('/api/patient/plans'),
    get: planID => request(`/api/patient/plans/${encodeURIComponent(planID)}`),
    start: (planID, detailID) => request(`/api/patient/plans/${encodeURIComponent(planID)}/steps/${encodeURIComponent(detailID)}/start`, { method: 'POST' }),
    complete: (planID, detailID) => request(`/api/patient/plans/${encodeURIComponent(planID)}/steps/${encodeURIComponent(detailID)}/complete`, { method: 'POST' }),
    pause: planID => request(`/api/patient/plans/${encodeURIComponent(planID)}/pause`, { method: 'POST' }),
    resume: planID => request(`/api/patient/plans/${encodeURIComponent(planID)}/resume`, { method: 'POST' }),
    finish: planID => request(`/api/patient/plans/${encodeURIComponent(planID)}/finish`, { method: 'POST' }),
    replan: planID => request(`/api/patient/plans/${encodeURIComponent(planID)}/replan`, { method: 'POST' }),
    navigation: (planID, detailID) => request(`/api/patient/plans/${encodeURIComponent(planID)}/navigation?detailID=${encodeURIComponent(detailID)}`)
  },
  reminders: {
    config: () => request('/api/patient/reminders/config'),
    list: () => request('/api/patient/reminders')
  },
  agent: {
    status: () => request('/api/patient/agent/status'),
    chat: data => request('/api/patient/agent/chat', { method: 'POST', data })
  }
}
