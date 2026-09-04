const app = getApp()
const api = require('../../utils/api')
const planFlow = require('../../utils/plan-flow')
const flowGuard = require('../../utils/flow-guard')

Page({
  data: { selected: 'today', submitting: false },

  onLoad() { flowGuard.requireSelection(app) },

  selectOption(e) { this.setData({ selected: e.currentTarget.dataset.value }) },

  async confirmChoice() {
    if (this.data.selected === 'split') {
      const split = planFlow.splitSelectedItems(app)
      app.globalData.preparationDecision = 'split'
      app.globalData.selectedPlanMode = 'appointment'
      if (split.readyItemIDs.length && split.deferredItemIDs.length) {
        app.globalData.splitPlanDraft = {
          packageID: app.globalData.currentPackageId,
          originalItemIDs: app.globalData.selectedItemIDs || [],
          readyItemIDs: split.readyItemIDs,
          deferredItemIDs: split.deferredItemIDs,
          activePlan: null
        }
        app.globalData.selectedItemIDs = split.deferredItemIDs
      } else {
        app.globalData.splitPlanDraft = null
      }
      wx.navigateTo({ url: '/pages/appointment-time/appointment-time' })
      return
    }
    await this.continueToday()
  },

  async continueToday() {
    if (this.data.submitting) return
    this.setData({ submitting: true })
    try {
      const plan = await planFlow.createSameDayPlan(app, {
        planMode: 'realtime', booked: 'no', preparationDecision: 'continue-today'
      })
      app.globalData.preparationDecision = 'continue-today'
      wx.redirectTo({ url: '/pages/plan/plan' })
      return plan
    } catch (error) {
      api.showError(error)
      return null
    } finally {
      this.setData({ submitting: false })
    }
  },

  goBack() { wx.navigateBack({ delta: 1 }) }
})
