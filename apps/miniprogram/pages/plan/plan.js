const { navigationMetrics } = require('../../utils/layout')
const { isDemoUnrestricted } = require('../../utils/demo-mode')
const api = require('../../utils/api')
const { ICONS, examIcon } = require('../../utils/icon-map')
const flowGuard = require('../../utils/flow-guard')
const app = getApp()

function confirmAction(title, content, confirmText) {
  return new Promise(resolve => {
    wx.showModal({
      title,
      content,
      confirmText,
      confirmColor: '#C43D4D',
      success: result => resolve(result.confirm),
      fail: () => resolve(false)
    })
  })
}

function confirmPreparationReady() {
  return new Promise(resolve => {
    wx.showModal({
      title: '确认当前检前准备',
      content: '请确认：已按本计划要求完成空腹准备（如需），且已完成饮水憋尿准备（如需）。若尚未完成，请取消并在准备完成后再开始。',
      confirmText: '已准备好',
      confirmColor: '#1350BE',
      success: result => resolve(result.confirm),
      fail: () => resolve(false)
    })
  })
}

Page({
  data: {
    ...navigationMetrics(),
    selectedPlanID: '',
    hasPlan: false,
    currentStep: null,
    currentStepNumber: 0,
    totalSteps: 0,
    queueAhead: 0,
    mainActionText: '完成本项',
    reminder: null,
    replanNotice: '',
    operating: false
  },

  onLoad(options) { this.setData({ selectedPlanID: options.planID || '' }) },

  onShow() {
    if (!flowGuard.requireLogin(app)) return
    this._visible = true
    const cached = app.globalData.currentPlan
    const cachedID = cached && (cached.planID || cached.id)
    if (cached && (!this.data.selectedPlanID || cachedID === this.data.selectedPlanID) && cached !== this._plan) {
      this.syncPlan(cached)
    }
    this.refreshPlan()
  },

  onHide() { this._visible = false; clearTimeout(this._refreshTimer) },
  onUnload() { this.onHide() },

  async refreshPlan() {
    clearTimeout(this._refreshTimer)
    const revision = this._actionRevision || 0
    try {
      if (this.data.operating) return
      const plan = await (this.data.selectedPlanID ? api.plans.get(this.data.selectedPlanID) : api.plans.current())
      if (!this._visible || this.data.operating || revision !== (this._actionRevision || 0)) return
      if (!plan) {
        if (!this.data.selectedPlanID) app.saveCurrentPlan(null)
        this.syncPlan(null)
        return
      }
      if (plan.finished) return this.showCompletion(plan)
      app.saveCurrentPlan(plan)
      if (!this.data.selectedPlanID) this.setData({ selectedPlanID: plan.planID })
      if (plan !== this._plan) this.syncPlan(plan)
    } catch (error) {
      if (!this._plan) api.showError(error)
    } finally {
      if (this._visible && !this._completionShown) this._refreshTimer = setTimeout(() => this.refreshPlan(), 15000)
    }
  },

  syncPlan(plan) {
    this._plan = plan || null
    if (!plan) return this.setData({ hasPlan: false, currentStep: null })
    const steps = Array.isArray(plan.steps) ? plan.steps : []
    const sourceStep = steps.find(step => step.status === 'active') || steps.find(step => step.status === 'pending') || null
    const rawLocation = sourceStep && sourceStep.navigationTarget && sourceStep.navigationTarget.locationText
    const locationHint = rawLocation && !['请查看院内指引', '位置以现场标识为准'].includes(rawLocation)
      ? rawLocation
      : ''
    const currentStep = sourceStep ? { ...sourceStep, locationHint, iconPath: examIcon(sourceStep.title) } : null
    const currentStepIndex = Math.max(0, steps.findIndex(step => currentStep && step.detailID === currentStep.detailID))
    const paused = plan.planStatus === '已中断'
    const scheduled = plan.planStatus === '待执行'
    this.setData({
      hasPlan: true,
      currentStep,
      currentStepNumber: currentStep ? currentStepIndex + 1 : steps.length,
      totalSteps: Number(plan.totalSteps || steps.length),
      queueAhead: Math.max(0, Number((currentStep && currentStep.queueAhead) || 0)),
      mainActionText: paused ? '继续体检' : scheduled ? '开始体检' : currentStep && currentStep.status === 'pending' ? '前往本项' : '完成本项',
      canSkip: plan.planStatus === '进行中',
      replanNotice: plan.replanNotice || this.data.replanNotice,
      reminder: this.resolveReminder(currentStep)
    })
  },

  resolveReminder(step) {
    if (!step || isDemoUnrestricted(this._plan)) return null
    const text = `${step.title || ''} ${step.department || ''} ${step.note || ''}`
    if (step.bladderRequired || /膀胱|泌尿|前列腺|憋尿/.test(text)) {
      return { iconPath: ICONS.water, title: '请开始饮水并保持憋尿', detail: '请尽快饮水 500–800ml，完成后请勿排尿，以确保检查结果准确。' }
    }
    if (step.fasting) return { iconPath: ICONS.stomach, title: '请继续保持空腹', detail: '本项完成前请勿进食；如有不适，请及时告知工作人员。' }
    return null
  },

  async confirmCurrentPreparation() {
    if (isDemoUnrestricted(this._plan)) return true
    if (this._readinessConfirming) return false
    this._readinessConfirming = true
    try {
      return await confirmPreparationReady()
    } finally {
      this._readinessConfirming = false
    }
  },

  async runAction(action) {
    if (this.data.operating) return null
    this.setData({ operating: true })
    this._actionRevision = (this._actionRevision || 0) + 1
    try {
      const updated = await action()
      app.saveCurrentPlan(updated.finished ? null : updated)
      this.syncPlan(updated)
      if (updated.finished) this.showCompletion(updated)
      return updated
    } catch (error) {
      api.showError(error)
      return null
    } finally {
      this.setData({ operating: false })
    }
  },

  showCompletion(plan) {
    if (this._completionShown) return
    this._completionShown = true
    clearTimeout(this._refreshTimer)
    app.saveCurrentPlan(null)
    app.globalData.viewingPlanRecord = plan
    wx.redirectTo({ url: `/pages/plan-complete/plan-complete?id=${plan.planID}${plan.ended ? '&ended=1' : ''}` })
  },

  async handleMainAction() {
    const plan = this._plan
    const step = this.data.currentStep
    if (!plan || !step) return
    const unrestricted = isDemoUnrestricted(plan)
    if (plan.planStatus === '已中断') {
      const prepared = await this.confirmCurrentPreparation()
      if (!prepared) return
      const updated = await this.runAction(async () => {
        if (!unrestricted) await api.profile.update({ fasting: 'yes', bladder: 'normal', drinkingWater: 'adequate' })
        return api.plans.resume(plan.planID)
      })
      if (updated) this.openNavigation(updated)
      return
    }
    if (plan.planStatus === '待执行') {
      const prepared = await this.confirmCurrentPreparation()
      if (!prepared) return
      const updated = await this.runAction(async () => {
        if (!unrestricted) await api.profile.update({ fasting: 'yes', bladder: 'normal', drinkingWater: 'adequate' })
        const replanned = await api.plans.replan(plan.planID)
        if (replanned.finished) return replanned
        const first = (replanned.steps || []).find(item => item.status === 'pending')
        if (!first) throw new Error('当前没有可开始的体检项目')
        return api.plans.start(plan.planID, first.detailID)
      })
      if (updated) this.openNavigation(updated)
      return
    }
    if (step.status === 'pending') {
      this.openNavigation(plan)
      return
    }
    const updated = await this.runAction(() => api.plans.complete(plan.planID, step.detailID))
    if (!updated) return
    if (updated.finished) {
      return
    }
    this.openNavigation(updated)
  },

  openNavigation(plan) {
    const currentPlan = plan || this._plan
    if (!currentPlan || currentPlan.finished) return
    const step = (currentPlan.steps || []).find(item => item.status === 'active') || (currentPlan.steps || []).find(item => item.status === 'pending')
    if (!step) return
    wx.navigateTo({ url: `/pages/navigation/navigation?planID=${currentPlan.planID}&detailID=${step.detailID}&followRoute=1` })
  },

  async onReplan() {
    if (!this._plan) return
    const updated = await this.runAction(() => api.plans.replan(this._plan.planID))
    if (updated) this.openNavigation(updated)
  },

  async skipCurrent() {
    if (!this._plan || !this.data.currentStep || this.data.operating) return
    const detailID = this.data.currentStep.detailID
    const confirmed = await confirmAction('跳过此项', '此项将保留为未完成，并重新安排后续路线。结束体检后可预约未完成项目。', '确认跳过')
    if (!confirmed) return
    const updated = await this.runAction(() => api.plans.skip(this._plan.planID, detailID))
    if (updated) this.openNavigation(updated)
  },

  goOverview() {
    if (this._plan) wx.navigateTo({ url: `/pages/plan-overview/plan-overview?planID=${this._plan.planID}` })
  },

  async pausePlan() {
    const confirmed = await confirmAction('中断体检', '将保留当前进度，之后继续时会重新安排后续路线。', '确认中断')
    if (!confirmed) return
    const updated = await this.runAction(() => api.plans.pause(this._plan.planID))
    if (updated) wx.switchTab({ url: '/pages/index/index' })
  },

  async finishPlan() {
    const confirmed = await confirmAction('结束体检', '将保留当前记录，未完成项目可在结束后另行预约。', '确认结束')
    if (!confirmed) return
    await this.runAction(() => api.plans.finish(this._plan.planID))
  },

  goBack() { wx.navigateBack({ delta: 1 }) },
  backHome() { wx.switchTab({ url: '/pages/index/index' }) }
})
