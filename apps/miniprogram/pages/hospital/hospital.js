const api = require('../../utils/api')
const app = getApp()

Page({
  data: { hospitals: [], loading: true },
  onLoad() {
    api.hospitals.list().then(hospitals => this.setData({ hospitals })).catch(api.showError).finally(() => this.setData({ loading: false }))
  },
  selectHospital(e) {
    const hospital = this.data.hospitals.find(item => item.id === e.currentTarget.dataset.id)
    app.globalData.selectedHospitalId = hospital.id
    app.globalData.selectedHospital = hospital
    app.globalData.catalog = null
    wx.navigateTo({ url: '/pages/campus/campus' })
  }
})
