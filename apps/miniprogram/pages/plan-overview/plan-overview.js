const api = require('../../utils/api')
const { ICONS, examIcon } = require('../../utils/icon-map')
const { stepStatus } = require('../../utils/report')
const flowGuard = require('../../utils/flow-guard')
const app = getApp()

Page({
  data: { planID: '', plan: null, steps: [], completedSteps: 0, totalSteps: 0, progress: 0, activeTab: 'items' },

  onLoad(options) { this.setData({ planID: options.planID || '' }) },

  onShow() {
    if (!flowGuard.requireLogin(app)) return
    const planID = this.data.planID || (app.globalData.currentPlan && app.globalData.currentPlan.planID)
    if (!planID) return wx.redirectTo({ url: '/pages/plan/plan' })
    if (planID !== this.data.planID) this.setData({ planID })
    api.plans.get(planID).then(plan => this.applyPlan(plan)).catch(api.showError)
  },

  applyPlan(plan) {
    const source = Array.isArray(plan.steps) ? plan.steps : []
    const completedSteps = Number(plan.completedSteps || source.filter(step => step.status === 'done').length)
    const totalSteps = Number(plan.totalSteps || source.length)
    const progress = totalSteps ? Math.round(completedSteps / totalSteps * 100) : 0
    const steps = source.map(step => {
      const displayStatus = stepStatus(step)
      return {
        ...step,
        iconPath: step.status === 'done' ? ICONS.check : step.status === 'active' ? ICONS.direction : examIcon(step.title),
        statusText: displayStatus.text,
        statusTone: displayStatus.tone
      }
    })
    app.globalData.viewingPlanRecord = plan
    this.setData({ plan, steps, completedSteps, totalSteps, progress })
  },

  setTab(e) { this.setData({ activeTab: e.currentTarget.dataset.tab }) },

  openStep(e) {
    const detailID = e.currentTarget.dataset.id
    app.globalData.viewingPlanRecord = this.data.plan
    wx.navigateTo({ url: `/pages/exam-detail/exam-detail?planID=${this.data.planID}&detailID=${detailID}&mode=${this.data.activeTab}` })
  },

  goCurrent() { wx.redirectTo({ url: `/pages/plan/plan?planID=${this.data.planID}` }) },
  goBack() { wx.navigateBack({ delta: 1 }) }
})
