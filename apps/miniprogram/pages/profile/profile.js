const api = require('../../utils/api')
const flowGuard = require('../../utils/flow-guard')
const app = getApp()

Page({
  data: {
    submitting: false,
    form: { fasting: 'yes', specialNeed: 'none', bladder: 'normal', drinkingWater: 'adequate', booked: 'yes' }
  },
  onLoad() {
    if (!flowGuard.requireSelection(app)) return
    const saved = app.globalData.profile || wx.getStorageSync('profile')
    const mode = app.globalData.selectedPlanMode
    this.setData({
      form: {
        ...this.data.form,
        ...(saved || {}),
        ...(mode ? { planMode: mode, booked: mode === 'appointment' ? 'yes' : 'no' } : {})
      }
    })
  },
  setField(e) { this.setData({ [`form.${e.currentTarget.dataset.key}`]: e.currentTarget.dataset.value }) },
  async submitProfile() {
    this.setData({ submitting: true })
    try {
      const profile = this.data.form
      const plan = await api.plans.create({
        hospitalID: app.globalData.selectedHospitalId,
        packageID: app.globalData.currentPackageId,
        selectedItemIDs: app.globalData.selectedItemIDs || [],
        profile
      })
      app.saveProfile(profile)
      app.saveCurrentPlan(plan)
      wx.navigateTo({ url: '/pages/plan/plan' })
    } catch (error) { api.showError(error) } finally { this.setData({ submitting: false }) }
  }
})
