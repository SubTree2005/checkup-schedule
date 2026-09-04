const api = require('../../utils/api')
const flowGuard = require('../../utils/flow-guard')
const app = getApp()

Page({
  data: { password: '', deleting: false },
  onLoad() { flowGuard.requireLogin(app) },
  goBack() { wx.navigateBack({ delta: 1 }) },
  onPasswordInput(e) { this.setData({ password: e.detail.value }) },
  async deleteAccount() {
    if (this.data.password.length < 8) return wx.showToast({ title: '请输入当前账号密码', icon: 'none' })
    const confirmed = await new Promise(resolve => {
      wx.showModal({
        title: '确认永久注销账号？',
        content: '账号及全部体检数据将被删除，且无法恢复。',
        confirmText: '确认注销',
        confirmColor: '#C62828',
        success: result => resolve(result.confirm),
        fail: () => resolve(false)
      })
    })
    if (!confirmed) return
    this.setData({ deleting: true })
    try {
      await api.auth.deleteAccount(this.data.password)
      app.clearLoginState({ clearPrivateData: true })
      wx.showToast({ title: '账号已注销', icon: 'success' })
      wx.reLaunch({ url: '/pages/login/login' })
    } catch (error) {
      api.showError(error)
    } finally {
      this.setData({ deleting: false })
    }
  }
})
