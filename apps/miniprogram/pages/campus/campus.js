const app = getApp()

Page({
  data: { campuses: [] },
  onLoad() {
    const hospital = app.globalData.selectedHospital
    if (!hospital) return wx.redirectTo({ url: '/pages/hospital/hospital' })
    this.setData({ campuses: hospital.campuses || [] })
  },
  selectCampus(e) {
    if (!e.currentTarget.dataset.available) return wx.showToast({ title: '敬请期待', icon: 'none' })
    app.globalData.selectedCampusId = e.currentTarget.dataset.id
    wx.navigateTo({ url: '/pages/package/package' })
  }
})
