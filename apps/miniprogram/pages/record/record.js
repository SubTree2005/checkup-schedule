const api = require('../../utils/api')
const app = getApp()

Page({
  data: { currentPlan: null, records: [] },
  onShow() {
    api.plans.list().then(plans => {
      const currentPlan = plans.find(item => !item.finished) || null
      const records = plans.filter(item => item.finished)
      app.saveCurrentPlan(currentPlan)
      this.setData({ currentPlan, records })
    }).catch(api.showError)
  },
  goCurrentPlan() { if (this.data.currentPlan) wx.navigateTo({ url: '/pages/plan/plan' }) },
  goRecordDetail(e) { wx.navigateTo({ url: `/pages/record-detail/record-detail?id=${e.currentTarget.dataset.id}` }) }
})
