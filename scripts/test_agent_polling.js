'use strict'
const assert = require('node:assert/strict')
const originalSetTimeout = global.setTimeout
const originalNow = Date.now
let token = 'patient-a'
global.wx = {
  getStorageSync(key) { return key === 'patientToken' ? token : undefined },
  setStorageSync() {}, removeStorageSync() {}
}
const api = require('../apps/miniprogram/utils/api')
const agent = require('../apps/miniprogram/utils/ai-agent')
const session = { messages: [{ role: 'user', content: '你好' }] }
global.setTimeout = fn => originalSetTimeout(fn, 0)

async function main() {
  let submits = 0
  let polls = 0
  let clock = originalNow()
  Date.now = () => clock
  api.agent.createJob = async () => { submits += 1; return { jobID: 'job-1', status: 'pending' } }
  api.agent.job = async () => {
    polls += 1
    clock += 5000
    return polls < 5 ? { status: 'pending' } : { status: 'completed', reply: '25 秒后完成' }
  }
  api.agent.cancelJob = async () => {}
  assert.equal(await agent.startRequest(session, '').promise, '25 秒后完成')
  assert.equal(submits, 1)
  assert.equal(polls, 5)

  const requestIDs = []
  api.agent.createJob = async data => {
    requestIDs.push(data.requestID)
    if (requestIDs.length === 1) throw Object.assign(new Error('timeout'), { isNetworkError: true })
    return { jobID: 'job-2', status: 'completed', reply: '重试成功' }
  }
  assert.equal(await agent.startRequest(session, '').promise, '重试成功')
  assert.equal(requestIDs.length, 2)
  assert.equal(requestIDs[0], requestIDs[1], 'submission retries must keep their idempotency key')

  api.agent.createJob = async () => ({ jobID: 'job-3', status: 'pending' })
  polls = 0
  api.agent.job = async () => {
    polls += 1
    if (polls < 3) throw Object.assign(new Error('network'), { isNetworkError: true })
    return { status: 'completed', reply: '网络恢复后取回回答' }
  }
  assert.equal(await agent.startRequest(session, '').promise, '网络恢复后取回回答')
  api.agent.job = async () => ({ status: 'failed', error: 'AI 服务请求失败（429）' })
  await assert.rejects(agent.startRequest(session, '').promise, /429/)

  let finishSubmit
  const cancellations = []
  api.agent.createJob = () => new Promise(resolve => { finishSubmit = resolve })
  api.agent.cancelJob = async id => { cancellations.push(id) }
  const stopped = agent.startRequest(session, '')
  stopped.abort()
  finishSubmit({ jobID: 'late-submit', status: 'pending' })
  await assert.rejects(stopped.promise, /已停止生成/)
  assert.deepEqual(cancellations, ['late-submit'])

  api.agent.createJob = async () => ({ jobID: 'switch-account', status: 'pending' })
  api.agent.job = async () => { token = 'patient-b'; return { status: 'completed', reply: '私有回答' } }
  await assert.rejects(agent.startRequest(session, '').promise, /已停止生成/)
  token = 'patient-a'

  api.agent.createJob = async () => ({ jobID: 'stalled-job', status: 'pending' })
  api.agent.job = async () => { clock += 61000; return { status: 'pending' } }
  await assert.rejects(agent.startRequest(session, '').promise, /等待超时/)
  assert(cancellations.includes('stalled-job'))

  api.agent.createJob = async () => { throw Object.assign(new Error('not found'), { statusCode: 404 }) }
  await assert.rejects(agent.startRequest(session, '').promise, /新版后端/)
  console.log('Agent polling tests passed: slow replies, idempotent retry, transient failures, cancellation, account isolation and deadline.')
}

main().catch(error => { console.error(error); process.exitCode = 1 }).finally(() => {
  global.setTimeout = originalSetTimeout
  Date.now = originalNow
})
