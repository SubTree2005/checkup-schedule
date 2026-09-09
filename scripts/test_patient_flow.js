const assert = require('assert')
const fs = require('fs')
const path = require('path')

const app = {
  globalData: {},
  saveCurrentPlan(plan) { this.globalData.currentPlan = plan },
  saveProfile(profile) { this.globalData.profile = profile }
}
let definition
let lastNavigation
let lastRedirect
global.getApp = () => app
global.Page = value => { definition = value }
global.wx = {
  getStorageSync: () => 'test-token',
  getWindowInfo: () => ({ statusBarHeight: 44, windowWidth: 375 }),
  getMenuButtonBoundingClientRect: () => ({ top: 48, height: 32, left: 281, width: 87 }),
  navigateTo: value => { lastNavigation = value.url },
  redirectTo: value => { lastRedirect = value.url },
  showToast() {},
  showModal: options => options.success({ confirm: true })
}
const api = require('../apps/miniprogram/utils/api')
const report = require('../apps/miniprogram/utils/report')
const flow = require('../apps/miniprogram/utils/plan-flow')
const { navigationMetrics } = require('../apps/miniprogram/utils/layout')

function page(name) {
  const modulePath = require.resolve(`../apps/miniprogram/pages/${name}/${name}`)
  delete require.cache[modulePath]
  require(modulePath)
  return { ...definition, data: JSON.parse(JSON.stringify(definition.data)), setData(updates) { Object.assign(this.data, updates) } }
}

const plan = {
  planID: 'record-1', hospitalID: 'hospital-1', hospitalName: '原体检医院',
  finished: true, ended: true, planStatus: '已结束',
  steps: [
    { detailID: 'blood', itemID: 'blood-item', title: '血常规', status: 'done', completed: true,
      report: { items: [
        { id: 'r1', name: '白细胞', value: 0, unit: '10^9/L', status: 'low', referenceRange: '3.5–9.5' },
        { id: 'r2', name: '血红蛋白', value: 142, unit: 'g/L', status: 'normal' }
      ] } },
    { detailID: 'other', itemID: 'other-item', title: '另一项目', status: 'done', completed: true,
      report: { results: [{ id: 'r1', label: '另一个指标', value: '阴性' }] } },
    { detailID: 'ultrasound', itemID: 'ultrasound-item', title: '超声', department: '超声科', status: 'skipped', fasting: true, bladderRequired: true }
  ]
}

async function main() {
  const metrics = navigationMetrics()
  assert.strictEqual(metrics.statusBarHeight + metrics.navigationBarHeight / 2, 48 + 32 / 2, 'title must share the capsule vertical center even when the bar is under 44px')
  assert(metrics.navigationSideWidth >= 375 - 281, 'title must leave room for the capsule')
  const realMenu = wx.getMenuButtonBoundingClientRect
  wx.getMenuButtonBoundingClientRect = () => { throw new Error('unavailable') }
  assert(Number.isFinite(navigationMetrics().navigationBarHeight))
  wx.getMenuButtonBoundingClientRect = realMenu

  const indicators = report.planReportIndicators(plan)
  assert.strictEqual(indicators.length, 3, 'report tab lists indicators, not projects or unfinished exams')
  assert.strictEqual(new Set(indicators.map(item => item.key)).size, 3, 'repeated result IDs across reports stay distinct')
  assert.strictEqual(indicators[0].value, '0', 'zero is a real report value')
  assert.strictEqual(indicators[0].statusText, '偏低')
  assert.strictEqual(indicators[0].statusTone, 'abnormal')
  assert.deepStrictEqual(report.planReportIndicators({ steps: [{ title: '未出报告', status: 'done' }] }), [])

  const recordPage = page('record-detail')
  recordPage.setData({ recordID: plan.planID, activeTab: 'reports' })
  recordPage.applyRecord(plan)
  assert.strictEqual(recordPage.data.steps.length, 3)
  assert.strictEqual(recordPage.data.indicators.length, 3)
  recordPage.openIndicator({ currentTarget: { dataset: { id: 'blood', index: 1 } } })
  assert(lastNavigation.endsWith('detailID=blood&mode=reports&resultIndex=1'))
  const detailPage = page('exam-detail')
  detailPage.setData({ detailID: 'blood', reportFirst: true, resultIndex: 1 })
  detailPage.applyPlan(plan)
  assert.strictEqual(detailPage.data.indicator.label, '血红蛋白')
  assert.strictEqual(detailPage.data.report.rows.length, 1)
  assert.strictEqual(detailPage.data.step.title, '血常规')

  app.globalData = { selectedHospitalId: 'stale-hospital', selectedCampus: { fullName: '错误院区' }, currentPackageId: 'whole-package',
    catalog: { packages: [] }, appointmentDraft: { appointmentAt: 'old' }, splitPlanDraft: { old: true } }
  flow.prepareFollowUpAppointment(app, plan)
  assert.strictEqual(app.globalData.selectedHospitalId, 'hospital-1')
  assert.strictEqual(app.globalData.currentPackageId, null)
  assert.deepStrictEqual(app.globalData.selectedItemIDs, ['ultrasound-item'])
  assert.strictEqual(app.globalData.appointmentDraft, null)
  assert.strictEqual(app.globalData.splitPlanDraft, null)
  assert.strictEqual(flow.selectedHospitalName(app), '原体检医院')
  const requirements = flow.preparationRequirements(app)
  assert(requirements.some(item => item.key === 'fasting'))
  assert(requirements.some(item => item.key === 'water'), 'preparation must use the carried items even with a stale catalog')
  app.globalData.appointmentDraft = { appointmentAt: '2026-09-10T00:00:00Z' }
  let booking
  api.plans.create = async payload => { booking = payload; return { planID: 'follow-up' } }
  await flow.createPlan(app)
  assert.deepStrictEqual(booking.selectedItemIDs, ['ultrasound-item'])
  assert.strictEqual(booking.followUpPlanID, 'record-1')
  assert.strictEqual(booking.packageID, null)
  const completePage = page('plan-complete')
  completePage.applyPlan(plan)
  assert.strictEqual(completePage.data.unfinishedItems.length, 1)
  assert.strictEqual(completePage.data.unfinishedCount, 1, 'finished page must expose the booking section to the renderer')
  completePage.bookUnfinished()
  assert.strictEqual(lastNavigation, '/pages/appointment-time/appointment-time')
  api.hospitals.appointmentSlots = async () => ({ dates: [] })
  const appointmentPage = page('appointment-time')
  await appointmentPage.onLoad()
  assert.strictEqual(appointmentPage.data.followUpCount, 1)
  assert.strictEqual(appointmentPage.data.followUpItems[0].name, '超声')

  const normalData = { ...app.globalData }
  const demoHospital = { hospitalID: 'hospital-1', demoUnrestricted: true, demoUnrestrictedUntil: new Date(Date.now() + 7200000).toISOString() }
  app.globalData.demoRestrictionHospital = demoHospital
  assert.deepStrictEqual(flow.splitSelectedItems(app).deferredItemIDs, [], 'demo ignores fasting and name-based bladder preparation')
  assert.deepStrictEqual(flow.preparationRequirements(app), [])
  app.globalData.appointmentDraft = { appointmentAt: new Date(Date.now() + 1800000).toISOString() }
  assert.equal(flow.needsAppointmentFastingConfirmation(app), false)
  app.globalData.demoRestrictionHospital = { ...demoHospital, hospitalID: 'another-hospital' }
  assert.equal(flow.demoUnrestricted(app), false, 'demo setting must match the selected hospital')
  app.globalData.demoRestrictionHospital = { ...demoHospital, demoUnrestrictedUntil: new Date(Date.now() - 1).toISOString() }
  assert.deepStrictEqual(flow.splitSelectedItems(app).deferredItemIDs, ['ultrasound-item'], 'expiry restores original exam preparation')
  assert.equal(flow.needsAppointmentFastingConfirmation(app), true)
  app.globalData = normalData

  const demoRoutePage = page('plan')
  demoRoutePage.syncPlan({ ...plan, ...demoHospital })
  assert.equal(demoRoutePage.resolveReminder({ title: '膀胱超声', bladderRequired: true }), null)
  const modal = wx.showModal
  wx.showModal = () => { throw new Error('demo must not ask for preparation confirmation') }
  assert.equal(await demoRoutePage.confirmCurrentPreparation(), true)
  wx.showModal = modal

  const demoPrepPage = page('preparation-confirm')
  api.hospitals.catalog = async () => ({ hospital: demoHospital })
  let profileWrites = 0
  const updateProfile = api.profile.update
  api.profile.update = async () => { profileWrites += 1 }
  const demoScheduled = { ...plan, ...demoHospital, finished: false, planStatus: '待执行', steps: [
    { detailID: 'demo-step', status: 'pending', title: '膀胱检查', bladderRequired: true }
  ] }
  const savedReplan = api.plans.replan
  const savedStart = api.plans.start
  const savedResume = api.plans.resume
  api.plans.replan = async () => demoScheduled
  api.plans.start = async () => ({ ...demoScheduled, planStatus: '进行中' })
  api.plans.resume = async () => ({ ...demoScheduled, planStatus: '进行中' })
  demoRoutePage.syncPlan(demoScheduled)
  await demoRoutePage.handleMainAction()
  demoRoutePage.syncPlan({ ...demoScheduled, planStatus: '已中断' })
  await demoRoutePage.handleMainAction()
  assert.equal(profileWrites, 0, 'starting and resuming demo plans must preserve actual preparation')
  api.plans.replan = savedReplan
  api.plans.start = savedStart
  api.plans.resume = savedResume
  await demoPrepPage.onLoad()
  assert.equal(demoPrepPage.data.demoUnrestricted, true)
  await demoPrepPage.confirmPrepared()
  assert.equal(profileWrites, 0, 'demo must not falsify the patient preparation profile')
  api.hospitals.catalog = async () => ({ hospital: { ...demoHospital, demoUnrestricted: false } })
  await demoPrepPage.confirmPrepared()
  assert.equal(demoPrepPage.data.demoUnrestricted, false)
  assert.equal(profileWrites, 0, 'restored restrictions require a fresh real preparation confirmation')
  api.profile.update = updateProfile
  app.globalData = normalData

  const live = { planID: 'live', planStatus: '进行中', steps: [
    { detailID: 'closed', itemID: 'closed-item', title: '关闭科室项目', status: 'skipped' },
    { detailID: 'next', itemID: 'next-item', title: '可执行项目', status: 'pending' }
  ] }
  const routePage = page('plan')
  routePage.syncPlan(live)
  assert.strictEqual(routePage.data.currentStep.detailID, 'next')
  assert.strictEqual(routePage.data.mainActionText, '开始本项')
  let skipCalls = 0
  api.plans.skip = async (planID, detailID) => {
    skipCalls += 1
    assert.strictEqual(planID, 'live')
    assert.strictEqual(detailID, 'next')
    return { ...live, finished: true, ended: true, planStatus: '已结束', steps: live.steps.map(step => ({ ...step, status: 'skipped' })) }
  }
  await routePage.skipCurrent()
  assert.strictEqual(skipCalls, 1)
  assert.strictEqual(app.globalData.currentPlan, null)
  assert(lastRedirect.includes('/plan-complete/plan-complete?id=live&ended=1'))

  const refreshedPage = page('plan')
  refreshedPage._visible = true
  refreshedPage.setData({ selectedPlanID: 'live' })
  api.plans.get = async () => live
  await refreshedPage.refreshPlan()
  assert.strictEqual(refreshedPage.data.currentStep.detailID, 'next')
  assert(refreshedPage._refreshTimer, 'visible route schedules another availability refresh')
  refreshedPage.onHide()
  assert.strictEqual(refreshedPage._visible, false)

  let release
  const stalePage = page('plan')
  stalePage._visible = true
  stalePage.setData({ selectedPlanID: 'live' })
  api.plans.get = () => new Promise(resolve => { release = resolve })
  const polling = stalePage.refreshPlan()
  stalePage._actionRevision = 1
  release(live)
  await polling
  assert.strictEqual(stalePage.data.currentStep, null, 'a pre-action poll must not overwrite a newer mutation')
  stalePage.onHide()

  const navigationPage = page('navigation')
  navigationPage._visible = true
  navigationPage._followingRoute = true
  navigationPage.setData({ planID: 'live', detailID: 'closed', canSkip: true, toName: '旧检查点' })
  const nextPlan = { ...live, replanNotice: '已跳过旧检查点' }
  api.plans.skip = async () => nextPlan
  const navigationCalls = []
  api.plans.navigation = async (planID, detailID) => {
    navigationCalls.push(detailID)
    return { fromName: '原起点', toName: '下一检查科室', distanceMeters: 80, durationMinutes: 2, map: null }
  }
  lastRedirect = ''
  await navigationPage.skipCurrent()
  assert.deepStrictEqual(navigationCalls, ['next'], 'navigation skip must immediately fetch the next destination route')
  assert.strictEqual(navigationPage.data.detailID, 'next')
  assert.strictEqual(navigationPage.data.toName, '下一检查科室')
  assert.strictEqual(lastRedirect, '', 'continue on the navigation page when another item remains')
  assert.strictEqual(app.globalData.currentPlan, nextPlan)
  navigationPage.onHide()

  const finalNavigation = page('navigation')
  finalNavigation._visible = true
  finalNavigation.setData({ planID: 'live', detailID: 'next', canSkip: true })
  api.plans.skip = async () => ({ ...nextPlan, finished: true, ended: true, steps: nextPlan.steps.map(step => ({ ...step, status: 'skipped' })) })
  await finalNavigation.skipCurrent()
  assert(lastRedirect.includes('/plan-complete/plan-complete?id=live&ended=1'), 'last navigation skip must offer unfinished-item booking')
  assert.strictEqual(navigationCalls.length, 1, 'no map request after the last item is skipped')
  finalNavigation.onHide()

  const directNavigation = page('navigation')
  directNavigation._visible = true
  directNavigation._needsPlanCheck = true
  directNavigation.setData({ planID: 'live', detailID: 'next' })
  api.plans.get = async () => nextPlan
  await directNavigation.refreshNavigation()
  assert.strictEqual(directNavigation.data.canSkip, true, 'a direct navigation entry must discover the live plan even without a cached current plan')
  directNavigation.onHide()

  const config = require('../apps/miniprogram/app.json')
  let customHeaders = 0
  for (const route of config.pages) {
    const base = path.join(__dirname, '../apps/miniprogram', route)
    const wxml = fs.readFileSync(base + '.wxml', 'utf8')
    if (!/class="(?:flow-nav|settings-nav)"/.test(wxml)) continue
    customHeaders += 1
    assert(wxml.includes('{{navigationStyle}}'), `${route} must supply real capsule metrics`)
    assert(!/\.(?:flow-nav|settings-nav)\s*\{\s*height:\s*\d+rpx/.test(fs.readFileSync(base + '.wxss', 'utf8')), `${route} must not hard-code header height`)
  }
  console.log(`Patient flow regressions passed: route refresh/skip, navigation skip to next point, unfinished booking, indicator drilldown, ${customHeaders} custom headers.`)
}

main().catch(error => { console.error(error); process.exitCode = 1 })
