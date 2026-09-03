const LOCAL_API_BASE_URL = 'http://127.0.0.1:8000'

const PRODUCTION_CLOUD_CONTAINER = {
  env: 'prod-d3gt6bqwxd07c2857',
  service: 'checkup-schedule'
}

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

function requestTransport() {
  const environment = environmentVersion()
  const developmentOverride = environment === 'develop' ? wx.getStorageSync('apiBaseUrl') : ''
  if (environment === 'develop') {
    return {
      type: 'http',
      baseUrl: normalizeBaseUrl(developmentOverride || LOCAL_API_BASE_URL)
    }
  }

  if (!PRODUCTION_CLOUD_CONTAINER.env || !PRODUCTION_CLOUD_CONTAINER.service) {
    throw new Error('尚未配置生产云托管服务，请联系小程序管理员')
  }
  return { type: 'cloud', ...PRODUCTION_CLOUD_CONTAINER }
}

module.exports = {
  environmentVersion,
  requestTransport
}
