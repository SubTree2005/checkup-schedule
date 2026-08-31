const api = require('../../utils/api')

Page({
  data: { fromName: '当前检查点', toName: '下一检查科室', distance: '暂无路线数据', duration: '', location: '' },
  onLoad(options) {
    if (!options.planID || !options.detailID) return
    api.plans.navigation(options.planID, options.detailID).then(data => this.setData({
      fromName: data.fromName,
      toName: data.toName,
      distance: data.distanceMeters === null ? '暂无路线数据' : `${data.distanceMeters} 米`,
      duration: data.durationMinutes === null ? '' : `约 ${data.durationMinutes} 分钟`,
      location: data.location || ''
    })).catch(api.showError)
  },
  goBackPlan() { wx.navigateBack() }
})
