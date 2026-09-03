const api = require('../../utils/api')
const flowGuard = require('../../utils/flow-guard')
const app = getApp()

Page({
  data: { departments: [], selectedItemIDs: [] },
  async onLoad() {
    if (!flowGuard.requireHospital(app)) return
    try {
      const catalog = app.globalData.catalog || await api.hospitals.catalog(app.globalData.selectedHospitalId)
      app.globalData.catalog = catalog
      this.setData({ departments: catalog.departments })
    } catch (error) { api.showError(error) }
  },
  onCheckedChange(e) { this.setData({ selectedItemIDs: e.detail.value }) },
  submitSelection() {
    if (!this.data.selectedItemIDs.length) return wx.showToast({ title: '请至少选择一个项目', icon: 'none' })
    app.globalData.currentPackageId = null
    app.globalData.selectedItemIDs = this.data.selectedItemIDs
    wx.navigateTo({ url: '/pages/profile/profile' })
  }
})
