const app = getApp()
const planFlow = require('../../utils/plan-flow')
const flowGuard = require('../../utils/flow-guard')

Page({
  data: { requirements: [], submitting: false },

  onLoad() {
    if (!flowGuard.requireSelection(app)) return
    this.applyRequirements(planFlow.preparationRequirements(app, true))
  },

  applyRequirements(requirements) { this.setData({ requirements }) },

  async confirmPrepared() {
    if (this.data.submitting) return
    this.setData({ submitting: true })
    try {
      const plan = await planFlow.createPlan(app, {
        planMode: 'realtime', booked: 'no', fasting: 'yes', bladder: 'normal', drinkingWater: 'adequate', preparationDecision: 'ready'
      })
      app.globalData.preparationDecision = 'ready'
      app.saveCurrentPlan(plan)
      wx.redirectTo({ url: '/pages/plan/plan' })
    } catch (error) {
      require('../../utils/api').showError(error)
    } finally {
      this.setData({ submitting: false })
    }
  },

  notPrepared() { wx.navigateTo({ url: '/pages/preparation-arrangement/preparation-arrangement' }) },
  goBack() { wx.navigateBack({ delta: 1 }) }
})
