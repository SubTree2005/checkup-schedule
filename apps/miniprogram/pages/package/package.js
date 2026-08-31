const api = require('../../utils/api')
const app = getApp()

Page({
  data: { packages: [], loading: true },
  onLoad() { this.loadCatalog() },
  async loadCatalog() {
    try {
      const catalog = await api.hospitals.catalog(app.globalData.selectedHospitalId)
      app.globalData.catalog = catalog
      this.setData({ packages: catalog.packages })
      if (!catalog.packages.length) wx.showToast({ title: '该医院尚未配置套餐', icon: 'none' })
    } catch (error) { api.showError(error) } finally { this.setData({ loading: false }) }
  },
  viewDetail(e) { wx.navigateTo({ url: `/pages/package-detail/package-detail?id=${e.currentTarget.dataset.id}` }) },
  selectPackage(e) {
    app.globalData.currentPackageId = e.currentTarget.dataset.id
    app.globalData.selectedItemIDs = []
    wx.navigateTo({ url: '/pages/profile/profile' })
  }
})
