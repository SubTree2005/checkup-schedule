const flowGuard = require('../../utils/flow-guard')
const app = getApp()

Page({
  data: { planID: '', ended: false },

  onLoad(options) {
    if (!flowGuard.requireLogin(app)) return
    if (!options.id) return wx.switchTab({ url: '/pages/record/record' })
    this.setData({ planID: options.id, ended: options.ended === '1' })
  },

  viewRecord() {
    if (this.data.planID) wx.redirectTo({ url: `/pages/record-detail/record-detail?id=${this.data.planID}` })
  },

  goHome() { wx.switchTab({ url: '/pages/index/index' }) },
  goBack() { wx.navigateBack({ delta: 1 }) }
})
