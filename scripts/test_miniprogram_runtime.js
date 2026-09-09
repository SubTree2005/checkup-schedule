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
  getDeviceInfo() { return { platform: 'devtools' } },
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

  let nativeTabCalls = 0
  const customTab = { setData(patch) { Object.assign(this, patch) } }
  wx.showTabBar = wx.hideTabBar = () => { nativeTabCalls += 1 }
  wx.hideKeyboard = () => {}
  wx.getWindowInfo = () => ({ windowHeight: 812, statusBarHeight: 44 })
  wx.getMenuButtonBoundingClientRect = () => ({ bottom: 84 })
  currentPages = [{ route: 'pages/index/index', getTabBar: () => customTab }]
  const chatUI = {
    ...registeredComponent.methods,
    data: { ...registeredComponent.data, withTabBar: true },
    setData(patch) { Object.assign(this.data, patch) }
  }
  chatUI.showSession({ title: '布局回归', messages: [] })
  assert.strictEqual(customTab.hidden, true)
  assert.strictEqual(chatUI.data.headerTop, 44, 'title returns to the native navigation row')
  assert(chatUI.data.headerRight >= 96, 'header must reserve capsule space')
  chatUI.onKeyboardHeight({ detail: { height: 300 } })
  assert.strictEqual(chatUI.data.viewportHeight - chatUI.data.keyboardHeight, 512)
  wx.getWindowInfo = () => ({ windowHeight: 512, statusBarHeight: 44 })
  chatUI.onKeyboardHeight({ detail: { height: 300 } })
  assert.strictEqual(chatUI.data.viewportHeight - chatUI.data.keyboardHeight, 512, 'a resized native viewport must not lift the composer twice')
  chatUI.onKeyboardHeight({ detail: { height: 9999 } })
  assert(chatUI.data.viewportHeight - chatUI.data.keyboardHeight >= chatUI.data.headerTop + 100)
  chatUI.onBlur()
  assert.strictEqual(chatUI.data.keyboardHeight, 0)
  chatUI.closeChat()
  assert.strictEqual(customTab.hidden, false)
  chatUI.onKeyboardHeight({ detail: { height: 300 } })
  assert.strictEqual(chatUI.data.keyboardHeight, 0, 'late keyboard events must not affect a closed chat')
  assert.strictEqual(nativeTabCalls, 0, 'custom navigation must never restore a second native tab bar')
  currentPages = []

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

  const canvasCalls = []
  const savedNextTick = wx.nextTick
  const savedCanvasContext = wx.createCanvasContext
  wx.nextTick = callback => callback()
  wx.createCanvasContext = id => new Proxy({}, { get: (_target, method) => (...args) => {
    canvasCalls.push([id, method, ...args])
    if (method === 'measureText') return { width: 60 }
  } })
  const multiFloorPage = {
    ...oversizedNavigationPage,
    data: { ...navigationDefinition.data },
    setData(patch, callback) { Object.assign(this.data, patch); if (callback) callback() }
  }
  const segments = ['1F', '2F', '3F'].map((floorKey, index) => ({
    floorKey, geojson: { features: [] }, routeCoordinates: [[0, 0], [10, 10]],
    fromPoint: { name: index ? '到达楼梯' : '起点', coordinates: [0, 0] },
    toPoint: { name: index < 2 ? '换层楼梯' : '终点', coordinates: [10, 10] },
    instruction: '沿走廊前进', transition: index < 2 ? '经楼梯上楼' : ''
  }))
  segments[0].waypoints = [{ name: '导诊台', order: 1, floorKey: '1F', coordinates: [5, 5] }]
  multiFloorPage.applyNavigation({ map: { ...segments[2], segments }, distanceMeters: 30, durationMinutes: 3 })
  assert.deepStrictEqual(multiFloorPage.data.mapSegments.map(s => s.floorKey), ['1F', '2F', '3F'])
  for (let index = 0; index < 3; index += 1) {
    canvasCalls.length = 0
    multiFloorPage.setFloor(index)
    if (index === 0) multiFloorPage.drawSelectedFloor()
    const calls = canvasCalls.filter(call => call[0] === 'indoorMap')
    assert(calls.some(call => call[1] === 'setStrokeStyle' && call[2] === '#1350BE'), 'every floor must paint its own blue path')
    assert(calls.some(call => call[1] === 'lineTo'), 'route coordinates must be drawn')
    assert(calls.some(call => call[1] === 'draw'), 'selected floor must flush its canvas')
    if (index === 0) assert(calls.some(call => call[1] === 'fillText' && String(call[2]).includes('途经1：导诊台')), 'guidance stops must be labelled on the continuous route')
  }
  multiFloorPage.startFloorSwipe({ touches: [{ clientX: 30, clientY: 30 }] })
  multiFloorPage.endFloorSwipe({ changedTouches: [{ clientX: 120, clientY: 32 }] })
  assert.strictEqual(multiFloorPage.data.activeFloorIndex, 1, 'horizontal swipe selects previous route segment')
  multiFloorPage.applyNavigation({ map: { ...segments[2], segments }, distanceMeters: 30, durationMinutes: 3 })
  assert.strictEqual(multiFloorPage.data.activeFloorIndex, 1, 'polling must preserve the selected floor')
  multiFloorPage._floorTrack = { left: 10, width: 300 }
  multiFloorPage.moveFloorDragTo(290)
  assert.strictEqual(multiFloorPage.data.activeFloorIndex, 2, 'dragging capsule selects the touched floor')
  multiFloorPage.startFloorSwipe({ touches: [{ clientX: 30, clientY: 30 }] })
  multiFloorPage.endFloorSwipe({ changedTouches: [{ clientX: 90, clientY: 200 }] })
  assert.strictEqual(multiFloorPage.data.activeFloorIndex, 2, 'vertical scroll must not switch floors')
  multiFloorPage.applyNavigation({ map: segments[0], distanceMeters: null, durationMinutes: null })
  assert.strictEqual(multiFloorPage.data.mapSegments.length, 1, 'legacy single-floor responses remain supported')
  multiFloorPage.setFloor(1)
  assert.strictEqual(multiFloorPage.data.activeFloorIndex, 0, 'single-floor capsule cannot move')
  canvasCalls.length = 0
  multiFloorPage.applyNavigation({ map: { ...segments[2], fromPoint: { floorKey: '1F', name: 'foreign-floor', coordinates: [5, 5] } }, distanceMeters: null, durationMinutes: null })
  assert(!canvasCalls.some(call => call[1] === 'fillText' && String(call[2]).includes('foreign-floor')), 'a marker from 1F must never appear on 3F')
  multiFloorPage.applyNavigation({ map: null, distanceMeters: null, durationMinutes: null })
  assert.strictEqual(multiFloorPage.data.hasMap, false)
  assert.deepStrictEqual(multiFloorPage.data.mapSegments, [], 'missing routes must clear stale floors')
  wx.nextTick = savedNextTick
  wx.createCanvasContext = savedCanvasContext

  // Preparation must survive a failed plan; near-term bookings ask before side effects.
  const savedGlobalData = appMock.globalData
  const savedSaveProfile = appMock.saveProfile
  const preparationCalls = []
  const originalCatalog = api.hospitals.catalog
  appMock.saveProfile = function (profile) { this.globalData.profile = profile }
  appMock.globalData = {
    selectedHospitalId: 'hospital-1', currentPackageId: 'package-1', selectedItemIDs: [],
    catalog: { packages: [{ id: 'package-1', items: [{ id: 'lab', fastingRequired: true }] }] },
    profile: { fasting: 'no' }, appointmentDraft: null
  }
  api.profile.update = async updates => { preparationCalls.push(['profile', updates]) }
  api.hospitals.catalog = async () => appMock.globalData.catalog
  api.plans.create = async payload => {
    preparationCalls.push(['plan', payload])
    throw new Error('无可行时段')
  }
  require('../apps/miniprogram/pages/preparation-confirm/preparation-confirm')
  const makePreparationPage = definition => ({
    ...definition,
    data: { ...definition.data, wechatPush: false, systemCalendar: false },
    setData(updates) { Object.assign(this.data, updates) }
  })
  const preparationPage = makePreparationPage(registeredPage)
  await preparationPage.onLoad()
  await preparationPage.confirmPrepared()
  assert.strictEqual(appMock.globalData.profile.fasting, 'yes', 'failed scheduling must retain independently saved preparation')
  assert.deepStrictEqual(preparationCalls.map(call => call[0]), ['profile', 'plan'])

  const referenceNow = Date.now()
  appMock.globalData.appointmentDraft = { appointmentAt: new Date(referenceNow + 45 * 60000).toISOString() }
  assert.strictEqual(planFlow.needsAppointmentFastingConfirmation(appMock, referenceNow), true)
  appMock.globalData.appointmentDraft.appointmentAt = new Date(referenceNow + 8 * 3600000).toISOString()
  assert.strictEqual(planFlow.needsAppointmentFastingConfirmation(appMock, referenceNow), false, 'exactly eight hours allows future preparation')
  appMock.globalData.appointmentDraft.appointmentAt = new Date(referenceNow + 45 * 60000).toISOString()
  appMock.globalData.selectedItemIDs = ['non-fasting']
  assert.strictEqual(planFlow.needsAppointmentFastingConfirmation(appMock, referenceNow), false, 'only selected fasting exams require confirmation')
  appMock.globalData.selectedItemIDs = []
  require('../apps/miniprogram/pages/preparation-reminder/preparation-reminder')
  const reminderDefinition = registeredPage
  preparationCalls.length = 0
  modalConfirm = false
  const declinedPage = makePreparationPage(reminderDefinition)
  await declinedPage.confirmAppointment()
  assert.deepStrictEqual(preparationCalls, [['profile', { fasting: 'no' }]], 'declining must clear stale fasting confirmation and must not book')
  assert.strictEqual(declinedPage.data.submitting, false)

  preparationCalls.length = 0
  modalConfirm = true
  await makePreparationPage(reminderDefinition).confirmAppointment()
  assert.deepStrictEqual(preparationCalls.map(call => call[0]), ['profile', 'plan'])
  assert.strictEqual(preparationCalls[1][1].profile.fasting, 'yes')
  assert.strictEqual(appMock.globalData.profile.fasting, 'yes', 'failed appointment must retain confirmation too')

  preparationCalls.length = 0
  api.profile.update = async () => { throw new Error('保存失败') }
  await makePreparationPage(reminderDefinition).confirmAppointment()
  assert.strictEqual(preparationCalls.length, 0, 'do not create a plan if preparation persistence fails')

  api.profile.update = originalProfileUpdate
  api.plans.create = originalCreate
  api.hospitals.catalog = originalCatalog
  appMock.globalData = savedGlobalData
  appMock.saveProfile = savedSaveProfile
  console.log('Mini-program runtime tests passed: navigation bounds, caching, account isolation, readiness flow and AI request serialization.')
}

main().catch(error => {
  console.error(error)
  process.exitCode = 1
})
