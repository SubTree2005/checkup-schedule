const agent = require('../../utils/ai-agent')
const api = require('../../utils/api')

function timeText(value) {
  const date = new Date(value || Date.now())
  const pad = number => String(number).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

Page({
  data: {
    modelName: agent.DEFAULT_MODEL_LABEL,
    history: [],
    hasHistory: false
  },

  onLoad() {
    if (!wx.getStorageSync('patientToken')) return wx.redirectTo({ url: '/pages/login/login' })
  },

  onShow() { if (wx.getStorageSync('patientToken')) this.refresh() },

  refresh() {
    const active = agent.ensureSession()
    const archived = agent.getHistory()
    const sessions = (active.messages || []).length > 1 ? [{ ...active, active: true }].concat(archived) : archived
    this.setData({
      history: sessions.map(item => ({
        id: item.id,
        title: item.title || '未命名会话',
        active: item.active === true,
        timeText: timeText(item.updatedAt || item.archivedAt),
        preview: ((item.messages || []).find(message => message.role === 'user') || {}).content || '暂无用户消息'
      })),
      hasHistory: sessions.length > 0
    })
    api.agent.status().then(status => {
      const preference = agent.getModelConfig()
      this.setData({ modelName: preference.mode === 'custom' ? preference.model : (status.model || agent.DEFAULT_MODEL_LABEL) })
    }).catch(() => {
      const preference = agent.getModelConfig()
      this.setData({ modelName: preference.mode === 'custom' ? preference.model : agent.DEFAULT_MODEL_LABEL })
    })
  },

  openApiSettings() { wx.navigateTo({ url: '/pages/ai-api-settings/ai-api-settings' }) },

  openHistory(event) {
    const component = this.selectComponent('#aiAgent')
    if (component) component.openSessionById(event.currentTarget.dataset.id)
  },

  goBack() { wx.navigateBack({ delta: 1 }) }
})
