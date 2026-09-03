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

Page({
  data: {
    selectedPlanID: '',
    plan: null,
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
    const request = this.data.selectedPlanID ? api.plans.get(this.data.selectedPlanID) : api.plans.current()
    request.then(plan => {
      if (!plan) return this.syncPlan(app.globalData.currentPlan)
      app.saveCurrentPlan(plan)
      this.syncPlan(plan)
    }).catch(api.showError)
  },

  syncPlan(plan) {
    if (!plan) return this.setData({ plan: null, currentStep: null })
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
      plan,
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
    const plan = this.data.plan
    const step = this.data.currentStep
    if (!plan || !step) return
    if (plan.planStatus === '已中断') {
      const updated = await this.runAction(() => api.plans.resume(plan.planID))
      if (updated) this.openNavigation(updated)
      return
    }
    if (plan.planStatus === '待执行') {
      const updated = await this.runAction(async () => {
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

  openNavigation(plan = this.data.plan) {
    const step = (plan.steps || []).find(item => item.status === 'active') || (plan.steps || []).find(item => item.status === 'pending')
    if (!step) return
    wx.navigateTo({ url: `/pages/navigation/navigation?planID=${plan.planID}&detailID=${step.detailID}` })
  },

  onReplan() { this.runAction(() => api.plans.replan(this.data.plan.planID)) },

  goOverview() {
    wx.navigateTo({ url: `/pages/plan-overview/plan-overview?planID=${this.data.plan.planID}` })
  },

  async pausePlan() {
    const confirmed = await confirmAction('中断体检', '将保留当前进度，之后继续时会重新安排后续路线。', '确认中断')
    if (!confirmed) return
    const updated = await this.runAction(() => api.plans.pause(this.data.plan.planID))
    if (updated) wx.switchTab({ url: '/pages/index/index' })
  },

  async finishPlan() {
    const confirmed = await confirmAction('结束体检', '未完成的项目将保留为未完成，本次体检结束后不可继续。', '确认结束')
    if (!confirmed) return
    const updated = await this.runAction(() => api.plans.finish(this.data.plan.planID))
    if (updated) wx.redirectTo({ url: `/pages/plan-complete/plan-complete?id=${updated.planID}&ended=1` })
  },

  goBack() { wx.navigateBack({ delta: 1 }) },
  backHome() { wx.switchTab({ url: '/pages/index/index' }) }
})
