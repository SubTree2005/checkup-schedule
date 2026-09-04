const assert = require('assert')

const storage = new Map([
  ['patientToken', 'patient-a']
])
const requests = []
const toasts = []
let currentPages = []
let lastTabSwitch = null
let registeredComponent = null
let registeredPage = null
let lastNavigateBack = null
let lastNavigate = null
let lastRedirect = null
let modalConfirm = false
let lastModal = null
const appMock = {
  globalData: { activeTabIndex: 0 },
  clearLoginState() {},
  saveCurrentPlan(plan) { this.globalData.currentPlan = plan }
}
let registeredApp = null

global.wx = {
  getAccountInfoSync() {
    return { miniProgram: { envVersion: 'develop' } }
  },
  getStorageSync(key) {
    return storage.get(key)
  },
  setStorageSync(key, value) {
    storage.set(key, value)
  },
  removeStorageSync(key) {
    storage.delete(key)
  },
  request(options) {
    requests.push(options)
    return { abort() {} }
  },
  showToast(options) {
    toasts.push(options)
  },
  switchTab(options) {
    lastTabSwitch = options
  },
  navigateBack(options) {
    lastNavigateBack = options
  },
  navigateTo(options) {
    lastNavigate = options
  },
  redirectTo(options) {
    lastRedirect = options
  },
  showModal(options) {
    lastModal = options
    options.success({ confirm: modalConfirm })
  },
  reLaunch() {}
}

global.getCurrentPages = () => currentPages
global.getApp = () => appMock
global.Component = definition => { registeredComponent = definition }
global.Page = definition => { registeredPage = definition }
global.App = definition => { registeredApp = definition }

const api = require('../apps/miniprogram/utils/api')

function respond(index, data, statusCode = 200) {
  assert(requests[index], `missing request ${index}`)
  requests[index].success({ statusCode, data })
}

async function main() {
  const first = api.plans.list()
  const concurrent = api.plans.list()
  assert.strictEqual(first, concurrent, 'concurrent identical GETs should share one promise')
  assert.strictEqual(requests.length, 1, 'concurrent identical GETs should send one request')
  const planRows = [{ planID: 'plan-1' }]
  respond(0, planRows)
  assert.deepStrictEqual(await first, planRows)

  const cached = await api.plans.list()
  assert.strictEqual(cached, planRows, 'fresh GET cache should reuse the response value')
  assert.strictEqual(requests.length, 1, 'fresh GET cache should avoid another request')

  storage.set('patientToken', 'patient-b')
  const otherPatient = api.plans.list()
  assert.strictEqual(requests.length, 2, 'cache entries must be isolated by patient token')
  respond(1, [])
  await otherPatient

  const pause = api.plans.pause('plan-1')
  assert.strictEqual(requests.length, 3)
  respond(2, { planID: 'plan-1', planStatus: '已中断' })
  await pause
  const afterMutation = api.plans.list()
  assert.strictEqual(requests.length, 4, 'plan mutations should invalidate cached GETs')
  respond(3, [{ planID: 'plan-1', planStatus: '已中断' }])
  await afterMutation

  const chat = api.agent.chat({ messages: [] })
  assert.strictEqual(requests.length, 5)
  respond(4, { reply: 'ok' })
  await chat
  await api.plans.list()
  assert.strictEqual(requests.length, 5, 'AI chat should not invalidate unrelated patient data')

  api.clearCache()
  const afterClear = api.plans.list()
  assert.strictEqual(requests.length, 6, 'explicit cache clearing should force a fresh request')
  respond(5, [])
  await afterClear

  api.clearCache()
  const staleRequest = api.plans.list()
  assert.strictEqual(requests.length, 7)
  api.clearCache()
  const freshRequest = api.plans.list()
  assert.strictEqual(requests.length, 8)
  respond(6, [{ planID: 'stale' }])
  await staleRequest
  const concurrentFresh = api.plans.list()
  assert.strictEqual(concurrentFresh, freshRequest, 'a stale completion must not discard a newer in-flight GET')
  assert.strictEqual(requests.length, 8)
  respond(7, [{ planID: 'fresh' }])
  assert.deepStrictEqual(await freshRequest, [{ planID: 'fresh' }])

  const agent = require('../apps/miniprogram/utils/ai-agent')
  storage.set('userInfo', { userID: 'user-a' })
  agent.saveModelConfig({ model: 'model-a', apiKey: 'secret-a' })
  storage.set('userInfo', { userID: 'user-b' })
  assert.strictEqual(agent.getModelConfig().mode, 'default', 'AI settings must not leak between patient accounts')
  agent.saveModelConfig({ model: 'model-b' })
  storage.set('userInfo', { userID: 'user-a' })
  assert.strictEqual(agent.getModelConfig().model, 'model-a')

  const manyMessages = Array.from({ length: 80 }, (_, index) => ({ id: `m-${index}`, role: 'user', content: String(index) }))
  agent.saveSession({ id: 'long-session', title: 'long', messages: manyMessages })
  assert.strictEqual(storage.get('aiAgentSession:user-a').messages.length, 20, 'saved chat sessions should stay bounded')

  let selectedRecordTab = ''
  currentPages = [{
    route: 'pages/record/record',
    selectRecordTab(tab) { selectedRecordTab = tab }
  }]
  agent.localAction('历史体检').run()
  assert.strictEqual(selectedRecordTab, 'history', 'record-page AI actions should work without switching to the same tab')

  require('../apps/miniprogram/custom-tab-bar/index')
  assert(registeredComponent, 'custom tab component should register')
  const tab = {
    data: { ...registeredComponent.data },
    setData(patch) { Object.assign(this.data, patch) }
  }
  registeredComponent.methods.switchTab.call(tab, { currentTarget: { dataset: { index: 1 } } })
  assert.strictEqual(lastTabSwitch.url, '/pages/record/record', 'tab navigation should start immediately')
  assert.strictEqual(tab.data.selected, 0, 'the outgoing tab should not animate to the destination state')
  lastTabSwitch.complete()
  assert.strictEqual(tab._switching, false, 'the rapid-tap guard should reset after navigation')

  const { backToRoute } = require('../apps/miniprogram/utils/navigation')
  currentPages = [{ route: 'pages/plan/plan' }, { route: 'pages/navigation/navigation' }, { route: 'pages/plan-overview/plan-overview' }]
  backToRoute('pages/plan/plan', '/pages/plan/plan?planID=plan-1')
  assert.strictEqual(lastNavigateBack.delta, 2, 'returning to a plan should reuse the existing page')
  currentPages = [{ route: 'pages/record-detail/record-detail' }, { route: 'pages/navigation/navigation' }]
  backToRoute('pages/plan/plan', '/pages/plan/plan?planID=plan-1')
  assert.strictEqual(lastRedirect.url, '/pages/plan/plan?planID=plan-1', 'a direct entry should retain a safe fallback')

  require('../apps/miniprogram/app')
  assert(registeredApp, 'application should register')
  storage.set('userInfo', { userID: 'deleted-user' })
  storage.set('aiAgentHistory:deleted-user', [{ id: 'private' }])
  const deletingApp = { ...registeredApp, globalData: { ...registeredApp.globalData, userInfo: { userID: 'deleted-user' } } }
  registeredApp.clearLoginState.call(deletingApp, { clearPrivateData: true })
  assert.strictEqual(storage.has('aiAgentHistory:deleted-user'), false, 'account deletion should remove local private AI history')

  api.clearCache()
  storage.set('patientToken', 'launch-token-old')
  storage.set('userInfo', { userID: 'launch-user-old' })
  const launchApp = { ...registeredApp, globalData: { ...registeredApp.globalData } }
  registeredApp.onLaunch.call(launchApp)
  const staleMeIndex = requests.length - 1
  registeredApp.setAuthenticated.call(launchApp, {
    token: 'launch-token-new',
    user: { userID: 'launch-user-new', name: 'new user', profile: {} }
  })
  respond(staleMeIndex, { userID: 'launch-user-old', name: 'old user', profile: {} })
  await Promise.resolve()
  await Promise.resolve()
  assert.strictEqual(launchApp.globalData.userInfo.userID, 'launch-user-new', 'a stale identity response must not overwrite a newer login')

  const planFlow = require('../apps/miniprogram/utils/plan-flow')
  const originalCreate = api.plans.create
  const createdSelections = []
  api.plans.create = async payload => {
    createdSelections.push(payload.selectedItemIDs)
    return { planID: 'safe-plan', steps: [] }
  }
  const flowApp = {
    globalData: {
      catalog: {
        packages: [{ id: 'package-1', items: [
          { id: 'ready-exam', name: '常规检查' },
          { id: 'fasting-exam', name: '空腹抽血', fastingRequired: true }
        ] }]
      },
      currentPackageId: 'package-1',
      selectedItemIDs: [],
      selectedHospitalId: 'hospital-1',
      profile: null,
      appointmentDraft: null
    },
    saveProfile() {},
    saveCurrentPlan() {}
  }
  await planFlow.createSameDayPlan(flowApp, { preparationDecision: 'continue-today' })
  assert.deepStrictEqual(createdSelections[0], ['ready-exam'], 'continuing today must exclude exams whose preparation is unmet')
  flowApp.globalData.catalog.packages[0].items = [{ id: 'fasting-only', name: '空腹检查', fastingRequired: true }]
  await assert.rejects(
    planFlow.createSameDayPlan(flowApp, { preparationDecision: 'continue-today' }),
    /均需完成检前准备/,
    'an all-preparation selection must not create an unsafe same-day plan'
  )
  assert.strictEqual(createdSelections.length, 1)
  api.plans.create = originalCreate

  api.clearCache()
  const overlappingMutation = api.plans.pause('plan-race')
  const staleDuringMutation = api.plans.list()
  const mutationIndex = requests.length - 2
  const staleDuringMutationIndex = requests.length - 1
  respond(staleDuringMutationIndex, [{ planID: 'stale-during-mutation' }])
  await staleDuringMutation
  respond(mutationIndex, { planID: 'plan-race', planStatus: '已中断' })
  await overlappingMutation
  const afterOverlappingMutation = api.plans.list()
  assert.strictEqual(requests.length - 1, staleDuringMutationIndex + 1, 'a successful mutation must discard GET data cached while it was in flight')
  respond(requests.length - 1, [{ planID: 'fresh-after-mutation' }])
  await afterOverlappingMutation

  require('../apps/miniprogram/components/ai-agent/ai-agent')
  const aiComponent = { data: { ...registeredComponent.data, thinking: true, draft: '重复发送' } }
  const requestCountBeforeBlockedSend = requests.length
  await registeredComponent.methods.sendMessage.call(aiComponent)
  assert.strictEqual(requests.length, requestCountBeforeBlockedSend, 'confirming the composer while thinking must not start a second request')

  require('../apps/miniprogram/pages/plan/plan')
  assert(registeredPage, 'plan page should register')
  const planDefinition = registeredPage
  const makeScheduledPlanPage = () => ({
    ...planDefinition,
    data: {
      ...planDefinition.data,
      currentStep: { detailID: 'detail-before-replan', status: 'pending', title: '预约项目' }
    },
    _plan: {
      planID: 'scheduled-plan',
      planStatus: '待执行',
      steps: [{ detailID: 'detail-before-replan', status: 'pending', title: '预约项目' }]
    },
    setData(patch, callback) {
      Object.assign(this.data, patch)
      if (callback) callback()
    }
  })
  const originalProfileUpdate = api.profile.update
  const originalReplan = api.plans.replan
  const originalStart = api.plans.start
  const originalResume = api.plans.resume
  const readinessCalls = []
  api.profile.update = async payload => {
    readinessCalls.push(['profile', payload])
    return payload
  }
  api.plans.replan = async planID => {
    readinessCalls.push(['replan', planID])
    return { planID, planStatus: '待执行', steps: [{ detailID: 'detail-after-replan', status: 'pending', title: '预约项目' }] }
  }
  api.plans.start = async (planID, detailID) => {
    readinessCalls.push(['start', planID, detailID])
    return { planID, planStatus: '进行中', steps: [{ detailID, status: 'active', title: '预约项目' }] }
  }
  api.plans.resume = async planID => {
    readinessCalls.push(['resume', planID])
    return { planID, planStatus: '进行中', steps: [{ detailID: 'detail-after-resume', status: 'active', title: '恢复项目' }] }
  }
  modalConfirm = false
  await planDefinition.handleMainAction.call(makeScheduledPlanPage())
  assert.deepStrictEqual(readinessCalls, [], 'cancelling readiness confirmation must not send any request')
  assert.match(lastModal.content, /按本计划要求完成空腹准备/)
  assert.doesNotMatch(lastModal.content, /8\s*小时/, 'readiness copy must not assume a fixed fasting duration')

  modalConfirm = true
  const preparedPlanPage = makeScheduledPlanPage()
  await planDefinition.handleMainAction.call(preparedPlanPage)
  assert.deepStrictEqual(readinessCalls, [
    ['profile', { fasting: 'yes', bladder: 'normal', drinkingWater: 'adequate' }],
    ['replan', 'scheduled-plan'],
    ['start', 'scheduled-plan', 'detail-after-replan']
  ], 'confirmed readiness must be persisted before replan and start')
  assert.match(lastNavigate.url, /detailID=detail-after-replan/)

  const makePausedPlanPage = () => ({
    ...planDefinition,
    data: {
      ...planDefinition.data,
      currentStep: { detailID: 'detail-before-resume', status: 'pending', title: '恢复项目' }
    },
    _plan: {
      planID: 'paused-plan',
      planStatus: '已中断',
      steps: [{ detailID: 'detail-before-resume', status: 'pending', title: '恢复项目' }]
    },
    setData(patch, callback) {
      Object.assign(this.data, patch)
      if (callback) callback()
    }
  })
  readinessCalls.length = 0
  modalConfirm = false
  await planDefinition.handleMainAction.call(makePausedPlanPage())
  assert.deepStrictEqual(readinessCalls, [], 'cancelling readiness confirmation before resume must not send any request')

  modalConfirm = true
  await planDefinition.handleMainAction.call(makePausedPlanPage())
  assert.deepStrictEqual(readinessCalls, [
    ['profile', { fasting: 'yes', bladder: 'normal', drinkingWater: 'adequate' }],
    ['resume', 'paused-plan']
  ], 'resuming a paused plan must persist confirmed readiness before resume')
  assert.match(lastNavigate.url, /detailID=detail-after-resume/)
  api.profile.update = originalProfileUpdate
  api.plans.replan = originalReplan
  api.plans.start = originalStart
  api.plans.resume = originalResume

  require('../apps/miniprogram/pages/navigation/navigation')
  const navigationDefinition = registeredPage
  let mapCoordinates = Array.from({ length: 60000 }, (_, index) => [index, index])
  for (let depth = 0; depth < 12000; depth += 1) mapCoordinates = [mapCoordinates]
  const oversizedNavigationPage = {
    ...navigationDefinition,
    data: { ...navigationDefinition.data, hasMap: true },
    _map: {
      geojson: { features: [{ geometry: { type: 'LineString', coordinates: mapCoordinates } }] },
      routeCoordinates: Array.from({ length: 40001 }, (_, index) => [index, index])
    },
    setData(patch) { Object.assign(this.data, patch) },
    createSelectorQuery() {
      return {
        select() { return this },
        boundingClientRect(callback) { callback({ width: 300, height: 200 }); return this },
        exec() { return this }
      }
    }
  }
  navigationDefinition.drawIndoorMap.call(oversizedNavigationPage)
  assert.strictEqual(oversizedNavigationPage.data.hasMap, false, 'oversized or deeply nested maps must not be drawn')
  assert.match(oversizedNavigationPage.data.location, /地图数据过大/)

  console.log('Mini-program runtime tests passed: navigation bounds, caching, account isolation, readiness flow and AI request serialization.')
}

main().catch(error => {
  console.error(error)
  process.exitCode = 1
})
