const api = require('../../utils/api')
const app = getApp()

Page({
  data: { pkg: null, groupViews: [] },
  onLoad(options) { this.loadPackage(options.id) },
  async loadPackage(id) {
    try {
      const catalog = app.globalData.catalog || await api.hospitals.catalog(app.globalData.selectedHospitalId)
      app.globalData.catalog = catalog
      const pkg = catalog.packages.find(item => item.id === id)
      if (!pkg) throw new Error('未找到该体检套餐')
      app.globalData.currentPackageId = pkg.id
      this.setData({
        pkg,
        groupViews: pkg.groups.map(group => ({ ...group, open: false, items: group.items.map(item => item.name) }))
      })
    } catch (error) { api.showError(error) }
  },
  toggleGroup(e) {
    const index = Number(e.currentTarget.dataset.index)
    this.setData({ groupViews: this.data.groupViews.map((item, idx) => idx === index ? { ...item, open: !item.open } : item) })
  },
  goProfile() { wx.navigateTo({ url: '/pages/profile/profile' }) }
})
