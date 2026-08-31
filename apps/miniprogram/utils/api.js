const { requestTransport } = require('./runtime-config')

function handleResponse(response, resolve, reject) {
  if (response.statusCode >= 200 && response.statusCode < 300) {
    resolve(response.data)
    return
  }
  if (response.statusCode === 401) wx.removeStorageSync('patientToken')
  const message = response.data && response.data.detail
  reject(new Error(typeof message === 'string' ? message : `请求失败（${response.statusCode}）`))
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
      success: response => handleResponse(response, resolve, reject),
      fail(error) {
        reject(new Error(error.errMsg || '无法连接服务器，请检查云托管服务状态'))
      }
    }

    if (transport.type === 'cloud') {
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
    login: data => request('/api/patient/auth/login', { method: 'POST', data }),
    me: () => request('/api/patient/auth/me'),
    logout: () => request('/api/patient/auth/logout', { method: 'POST' }),
    deleteAccount: password => request('/api/patient/account', { method: 'DELETE', data: { password } })
  },
  profile: {
    update: data => request('/api/patient/profile', { method: 'PATCH', data })
  },
  hospitals: {
    list: () => request('/api/patient/hospitals'),
    catalog: hospitalID => request(`/api/patient/hospitals/${encodeURIComponent(hospitalID)}/catalog`)
  },
  plans: {
    create: data => request('/api/patient/plans', { method: 'POST', data }),
    current: () => request('/api/patient/plans/current'),
    list: () => request('/api/patient/plans'),
    get: planID => request(`/api/patient/plans/${encodeURIComponent(planID)}`),
    start: (planID, detailID) => request(`/api/patient/plans/${encodeURIComponent(planID)}/steps/${encodeURIComponent(detailID)}/start`, { method: 'POST' }),
    complete: (planID, detailID) => request(`/api/patient/plans/${encodeURIComponent(planID)}/steps/${encodeURIComponent(detailID)}/complete`, { method: 'POST' }),
    replan: planID => request(`/api/patient/plans/${encodeURIComponent(planID)}/replan`, { method: 'POST' }),
    navigation: (planID, detailID) => request(`/api/patient/plans/${encodeURIComponent(planID)}/navigation?detailID=${encodeURIComponent(detailID)}`)
  }
}
