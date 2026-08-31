const api = require('../../utils/api')

Page({
  data: { record: null, steps: [] },
  onLoad(options) {
    api.plans.get(options.id).then(record => this.setData({ record, steps: record.steps })).catch(api.showError)
  },
  goHome() { wx.switchTab({ url: '/pages/index/index' }) }
})
