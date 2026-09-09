const { navigationMetrics } = require('../../utils/layout')
const app = getApp()
const planFlow = require('../../utils/plan-flow')
const flowGuard = require('../../utils/flow-guard')

Page({
  data: {
    ...navigationMetrics(), requirements: [], submitting: false, loading: true, demoUnrestricted: false },

  async onLoad() {
    if (!flowGuard.requireSelection(app)) return
    try {
      await planFlow.refreshRestrictions(app)
      this.applyRequirements(planFlow.preparationRequirements(app, true))
      this.setData({ demoUnrestricted: planFlow.demoUnrestricted(app) })
    } catch (error) { require('../../utils/api').showError(error) }
    finally { this.setData({ loading: false }) }
  },

  applyRequirements(requirements) { this.setData({ requirements }) },

  async confirmPrepared() {
    if (this.data.submitting || this.data.loading) return
    this.setData({ submitting: true })
    try {
      await planFlow.refreshRestrictions(app)
      const unrestricted = planFlow.demoUnrestricted(app)
      const restored = this.data.demoUnrestricted && !unrestricted
      this.setData({ demoUnrestricted: unrestricted, requirements: planFlow.preparationRequirements(app, true) })
      if (restored) {
        wx.showToast({ title: '演示已结束，请确认检前准备', icon: 'none' })
        return
      }
      const preparation = unrestricted ? {} : { fasting: 'yes', bladder: 'normal', drinkingWater: 'adequate' }
      if (!unrestricted) await planFlow.savePreparation(app, preparation)
      const plan = await planFlow.createPlan(app, {
        planMode: 'realtime', booked: 'no', ...preparation, preparationDecision: 'ready'
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
