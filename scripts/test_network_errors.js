const assert = require('assert')
const notices = []
let failure = { errCode: -501000, errMsg: 'cloud.callContainer:fail permission denied' }
global.wx = {
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'trial' } }),
  getStorageSync: () => '',
  cloud: { init() {}, callContainer(options) { options.fail(failure) } },
  showToast: options => notices.push(options)
}
const api = require('../apps/miniprogram/utils/api')
async function main() {
  const error = await api.plans.list().catch(e => e)
  assert(error.isNetworkError)
  assert(error.message.includes('-501000'))
  assert(error.message.includes('permission denied'))
  api.showError(error)
  api.showError(error)
  assert.strictEqual(notices.length, 1, 'repeated page failures should not spam the same notice')
  failure = { errMsg: 'request:fail timeout' }
  const timeout = await api.plans.list().catch(e => e)
  assert(timeout.isTimeout)
  assert(timeout.message.includes('连接超时'))
  api.showError(timeout)
  assert.strictEqual(notices.length, 2, 'different errors should still be visible')
  const originalSetTimeout = global.setTimeout
  const originalClearTimeout = global.clearTimeout
  let deadline
  let lateRequest
  global.setTimeout = callback => { deadline = callback; return 1 }
  global.clearTimeout = () => {}
  wx.cloud.callContainer = options => { lateRequest = options }
  try {
    const pending = api.auth.login({ phone: '18888888888', password: 'test-only' }).catch(e => e)
    deadline()
    const timedOut = await pending
    assert(timedOut.isTimeout, 'missing SDK callbacks must still finish login')
    lateRequest.success({ statusCode: 200, data: { token: 'late' } })
    assert.strictEqual(await pending, timedOut, 'late login success cannot replace timeout')
  } finally {
    global.setTimeout = originalSetTimeout
    global.clearTimeout = originalClearTimeout
  }
  console.log('Network error diagnostics tests passed')
}
main().catch(error => { console.error(error); process.exitCode = 1 })
