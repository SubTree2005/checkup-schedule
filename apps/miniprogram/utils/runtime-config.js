const LOCAL_API_BASE_URL = 'http://127.0.0.1:8000'

// 上线前替换为已加入微信公众平台 request 合法域名的 HTTPS 地址。
const PRODUCTION_API_BASE_URL = 'https://api.example.com'

function environmentVersion() {
  try {
    return wx.getAccountInfoSync().miniProgram.envVersion || 'develop'
  } catch (_error) {
    return 'develop'
  }
}

function normalizeBaseUrl(value) {
  return String(value || '').trim().replace(/\/$/, '')
}

function apiBaseUrl() {
  const environment = environmentVersion()
  const developmentOverride = environment === 'develop' ? wx.getStorageSync('apiBaseUrl') : ''
  const configured = normalizeBaseUrl(
    developmentOverride || (environment === 'develop' ? LOCAL_API_BASE_URL : PRODUCTION_API_BASE_URL)
  )
  if (environment !== 'develop' && (!configured.startsWith('https://') || configured.includes('example.com'))) {
    throw new Error('尚未配置生产 HTTPS API 地址，请联系小程序管理员')
  }
  return configured
}

module.exports = { apiBaseUrl, environmentVersion }
