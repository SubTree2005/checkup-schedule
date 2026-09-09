const assert = require('assert')
const { requestTransport } = require('../apps/miniprogram/utils/runtime-config')

function transport(version, platform, override = '') {
  global.wx = {
    getAccountInfoSync: () => ({ miniProgram: { envVersion: version } }),
    getDeviceInfo: () => ({ platform }),
    getStorageSync: () => override
  }
  return requestTransport()
}

assert.strictEqual(transport('develop', 'devtools').baseUrl, 'http://127.0.0.1:8000')
assert.strictEqual(transport('develop', 'devtools', 'http://localhost:9000/').baseUrl, 'http://localhost:9000')
for (const platform of ['android', 'ios', 'devtools']) {
  for (const version of ['develop', 'trial', 'release']) {
    if (platform === 'devtools' && version === 'develop') continue
    const result = transport(version, platform, 'http://127.0.0.1:8000')
    assert.strictEqual(result.type, 'cloud', `${version}/${platform} must use cloud`)
    assert.strictEqual(result.service, 'checkup-schedule')
  }
}
global.wx = { getAccountInfoSync() { throw new Error('unavailable') } }
assert.strictEqual(requestTransport().type, 'cloud', 'unknown devices must never default to loopback')
global.wx.getSystemInfoSync = () => ({ platform: 'devtools' })
global.wx.getStorageSync = () => ''
assert.strictEqual(requestTransport().type, 'http', 'older IDE device API should work')
console.log('request transport tests passed')
