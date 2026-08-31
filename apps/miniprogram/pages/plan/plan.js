const api = require('../../utils/api')
const app = getApp()

Page({
  data: { plan: null, nextTitle: '', operating: false },
  onShow() {
    api.plans.current().then(plan => {
      if (plan) {
        app.saveCurrentPlan(plan)
        this.syncPlan(plan)
      } else if (app.globalData.currentPlan) {
        this.syncPlan(app.globalData.currentPlan)
      }
    }).catch(api.showError)
  },
  syncPlan(plan) {
    const nextStep = plan.steps.find(item => item.status !== 'done')
    this.setData({ plan, nextTitle: nextStep ? nextStep.title : '已完成全部体检' })
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
    } finally { this.setData({ operating: false }) }
  },
  onStartStep(e) {
    const step = this.data.plan.steps[Number(e.currentTarget.dataset.index)]
    this.runAction(() => api.plans.start(this.data.plan.planID, step.detailID))
  },
  async onCompleteStep(e) {
    const step = this.data.plan.steps[Number(e.currentTarget.dataset.index)]
    const updated = await this.runAction(() => api.plans.complete(this.data.plan.planID, step.detailID))
    if (updated && updated.finished) {
      wx.showToast({ title: '体检已完成', icon: 'success' })
      setTimeout(() => wx.redirectTo({ url: `/pages/record-detail/record-detail?id=${updated.planID}` }), 300)
    }
  },
  onReplan() { this.runAction(() => api.plans.replan(this.data.plan.planID)) },
  goNavigation(e) {
    wx.navigateTo({ url: `/pages/navigation/navigation?planID=${this.data.plan.planID}&detailID=${e.currentTarget.dataset.detailId}` })
  },
  goRecordTab() { wx.switchTab({ url: '/pages/record/record' }) },
  backHome() { wx.switchTab({ url: '/pages/index/index' }) }
})
