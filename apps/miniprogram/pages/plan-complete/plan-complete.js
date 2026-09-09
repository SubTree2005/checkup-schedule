const { navigationMetrics } = require('../../utils/layout')
const flowGuard = require('../../utils/flow-guard')
const api = require('../../utils/api')
const { prepareFollowUpAppointment } = require('../../utils/plan-flow')
const app = getApp()

Page({
  data: {
    ...navigationMetrics(), planID: '', ended: false, unfinishedItems: [], unfinishedCount: 0, loading: true },

  onLoad(options) {
    if (!flowGuard.requireLogin(app)) return
    if (!options.id) return wx.switchTab({ url: '/pages/record/record' })
    this.setData({ planID: options.id, ended: options.ended === '1' })
    const cached = app.globalData.viewingPlanRecord
    if (cached && (cached.planID || cached.id) === options.id) this.applyPlan(cached)
    api.plans.get(options.id).then(plan => this.applyPlan(plan)).catch(error => {
      api.showError(error)
      this.setData({ loading: false })
    })
  },

  applyPlan(plan) {
    this._plan = plan
    const unfinishedItems = (plan.steps || []).filter(step => step.status !== 'done' && !step.completed)
    this.setData({ ended: !!plan.ended || unfinishedItems.length > 0, unfinishedItems, unfinishedCount: unfinishedItems.length, loading: false })
  },

  bookUnfinished() {
    try {
      prepareFollowUpAppointment(app, this._plan || {})
      wx.navigateTo({ url: '/pages/appointment-time/appointment-time' })
    } catch (error) { api.showError(error) }
  },

  viewRecord() {
    if (this.data.planID) wx.redirectTo({ url: `/pages/record-detail/record-detail?id=${this.data.planID}` })
  },

  goHome() { wx.switchTab({ url: '/pages/index/index' }) },
  goBack() { wx.navigateBack({ delta: 1 }) }
})
