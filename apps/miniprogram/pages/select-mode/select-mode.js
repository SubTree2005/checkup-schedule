const app = getApp()
const flowGuard = require('../../utils/flow-guard')

Page({
  onLoad() { flowGuard.requireSelection(app) },

  chooseMode(e) {
    const mode = e.currentTarget.dataset.mode
    app.globalData.selectedPlanMode = mode
    app.globalData.appointmentDraft = null
    app.globalData.preparationDecision = null
    app.globalData.splitPlanDraft = null
    app.globalData.profile = {
      ...(app.globalData.profile || {}),
      booked: mode === 'appointment' ? 'yes' : 'no',
      planMode: mode
    }
    const url = mode === 'appointment'
      ? '/pages/appointment-time/appointment-time'
      : '/pages/preparation-confirm/preparation-confirm'
    wx.navigateTo({ url })
  },

  goBack() { wx.navigateBack({ delta: 1 }) }
})
