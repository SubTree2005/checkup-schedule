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
    selectedPlanID: '',
    hasPlan: false,
    currentStep: null,
    currentStepNumber: 0,
    totalSteps: 0,
    queueAhead: 0,
    mainActionText: '完成本项',
    reminder: null,
    operating: false
  },

  onLoad(options) { this.setData({ selectedPlanID: options.planID || '' }) },

  onShow() {
    if (!flowGuard.requireLogin(app)) return
    const cached = app.globalData.currentPlan
    const cachedID = cached && (cached.planID || cached.id)
    if (cached && (!this.data.selectedPlanID || cachedID === this.data.selectedPlanID) && cached !== this._plan) {
      this.syncPlan(cached)
    }
    const request = this.data.selectedPlanID ? api.plans.get(this.data.selectedPlanID) : api.plans.current()
    request.then(plan => {
      if (!plan) {
        if (!this.data.selectedPlanID) app.saveCurrentPlan(null)
        this.syncPlan(null)
        return
      }
      app.saveCurrentPlan(plan)
      if (plan !== this._plan) this.syncPlan(plan)
    }).catch(error => {
      if (!this._plan) api.showError(error)
    })
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
      mainActionText: paused ? '继续体检' : scheduled ? '开始体检' : '完成本项',
      reminder: this.resolveReminder(currentStep)
    })
  },

  resolveReminder(step) {
    if (!step) return null
    const text = `${step.title || ''} ${step.department || ''} ${step.note || ''}`
    if (step.bladderRequired || /膀胱|泌尿|前列腺|憋尿/.test(text)) {
      return { iconPath: ICONS.water, title: '请开始饮水并保持憋尿', detail: '请尽快饮水 500–800ml，完成后请勿排尿，以确保检查结果准确。' }
    }
    if (step.fasting) return { iconPath: ICONS.stomach, title: '请继续保持空腹', detail: '本项完成前请勿进食；如有不适，请及时告知工作人员。' }
    return null
  },

  async confirmCurrentPreparation() {
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
    try {
      const updated = await action()
      app.saveCurrentPlan(updated.finished ? null : updated)
      this.syncPlan(updated)
      return updated
    } catch (error) {
      api.showError(error)
      return null
    } finally {
      this.setData({ operating: false })
    }
  },

  async handleMainAction() {
    const plan = this._plan
    const step = this.data.currentStep
    if (!plan || !step) return
    if (plan.planStatus === '已中断') {
      const prepared = await this.confirmCurrentPreparation()
      if (!prepared) return
      const updated = await this.runAction(async () => {
        await api.profile.update({ fasting: 'yes', bladder: 'normal', drinkingWater: 'adequate' })
        return api.plans.resume(plan.planID)
      })
      if (updated) this.openNavigation(updated)
      return
    }
    if (plan.planStatus === '待执行') {
      const prepared = await this.confirmCurrentPreparation()
      if (!prepared) return
      const updated = await this.runAction(async () => {
        await api.profile.update({ fasting: 'yes', bladder: 'normal', drinkingWater: 'adequate' })
        const replanned = await api.plans.replan(plan.planID)
        const first = (replanned.steps || []).find(item => item.status === 'pending')
        if (!first) throw new Error('当前没有可开始的体检项目')
        return api.plans.start(plan.planID, first.detailID)
      })
      if (updated) this.openNavigation(updated)
      return
    }
    if (step.status === 'pending') {
      const updated = await this.runAction(() => api.plans.start(plan.planID, step.detailID))
      if (updated) this.openNavigation(updated)
      return
    }
    const updated = await this.runAction(() => api.plans.complete(plan.planID, step.detailID))
    if (!updated) return
    if (updated.finished) {
      wx.redirectTo({ url: `/pages/plan-complete/plan-complete?id=${updated.planID}` })
      return
    }
    this.openNavigation(updated)
  },

  openNavigation(plan) {
    const currentPlan = plan || this._plan
    if (!currentPlan) return
    const step = (currentPlan.steps || []).find(item => item.status === 'active') || (currentPlan.steps || []).find(item => item.status === 'pending')
    if (!step) return
    wx.navigateTo({ url: `/pages/navigation/navigation?planID=${currentPlan.planID}&detailID=${step.detailID}` })
  },

  onReplan() { if (this._plan) this.runAction(() => api.plans.replan(this._plan.planID)) },

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
    const confirmed = await confirmAction('结束体检', '未完成的项目将保留为未完成，本次体检结束后不可继续。', '确认结束')
    if (!confirmed) return
    const updated = await this.runAction(() => api.plans.finish(this._plan.planID))
    if (updated) wx.redirectTo({ url: `/pages/plan-complete/plan-complete?id=${updated.planID}&ended=1` })
  },

  goBack() { wx.navigateBack({ delta: 1 }) },
  backHome() { wx.switchTab({ url: '/pages/index/index' }) }
})
