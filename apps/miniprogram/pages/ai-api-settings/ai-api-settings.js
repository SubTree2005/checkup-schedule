const api = require('../../utils/api')

Page({
  data: { modelName: 'DeepSeek V4 Flash', configured: false, statusText: '检查中' },

  onLoad() {
    if (!wx.getStorageSync('patientToken')) return wx.redirectTo({ url: '/pages/login/login' })
    api.agent.status().then(status => this.setData({
      modelName: status.model || 'DeepSeek V4 Flash',
      configured: !!status.configured,
      statusText: status.configured ? '已配置' : '待配置'
    })).catch(() => this.setData({ configured: false, statusText: '服务不可用' }))
  },

  goBack() { wx.navigateBack({ delta: 1 }) }
})
