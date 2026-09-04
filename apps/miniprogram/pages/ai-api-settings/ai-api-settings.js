const api = require('../../utils/api')
const agent = require('../../utils/ai-agent')

Page({
  data: {
    defaultModelName: agent.DEFAULT_MODEL_LABEL,
    configured: false,
    statusText: '检查中',
    mode: 'default',
    customModel: '',
    customApiKey: '',
    showApiKey: false
  },

  onLoad() {
    if (!wx.getStorageSync('patientToken')) return wx.redirectTo({ url: '/pages/login/login' })
    const preference = agent.getModelConfig()
    this.setData({
      mode: preference.mode,
      customModel: preference.model,
      customApiKey: preference.apiKey
    })
    api.agent.status().then(status => this.setData({
      defaultModelName: status.model || agent.DEFAULT_MODEL_LABEL,
      configured: !!status.configured,
      statusText: status.configured ? '已配置' : '待配置'
    })).catch(() => this.setData({ configured: false, statusText: '服务不可用' }))
  },

  chooseDefault() { this.setData({ mode: 'default' }) },
  chooseCustom() { this.setData({ mode: 'custom' }) },
  onModelInput(event) { this.setData({ customModel: event.detail.value }) },
  onApiKeyInput(event) { this.setData({ customApiKey: event.detail.value }) },
  toggleApiKey() { this.setData({ showApiKey: !this.data.showApiKey }) },

  save() {
    if (this.data.mode === 'default') {
      agent.restoreDefaultModel()
      wx.showToast({ title: '已恢复默认模型', icon: 'success' })
      wx.navigateBack({ delta: 1 })
      return
    }
    try {
      agent.saveModelConfig({ model: this.data.customModel, apiKey: this.data.customApiKey })
      wx.showToast({ title: '模型设置已保存', icon: 'success' })
      wx.navigateBack({ delta: 1 })
    } catch (error) {
      wx.showToast({ title: error.message || '请检查模型设置', icon: 'none' })
    }
  },

  goBack() { wx.navigateBack({ delta: 1 }) }
})
