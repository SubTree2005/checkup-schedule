const api = require('../../utils/api')
const app = getApp()

Page({
  data: { password: '', deleting: false },
  onPasswordInput(e) { this.setData({ password: e.detail.value }) },
  openTerms() { wx.navigateTo({ url: '/pages/legal/legal?type=terms' }) },
  openPrivacy() { wx.navigateTo({ url: '/pages/legal/legal?type=privacy' }) },
  async deleteAccount() {
    if (this.data.password.length < 8) return wx.showToast({ title: '请输入当前账号密码', icon: 'none' })
    const confirmed = await new Promise(resolve => {
      wx.showModal({
        title: '确认永久注销账号？',
        content: '账号、健康状态、体检计划和执行记录将被删除，且无法恢复。',
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
      app.clearLoginState()
      wx.showToast({ title: '账号已注销', icon: 'success' })
      setTimeout(() => wx.reLaunch({ url: '/pages/login/login' }), 500)
    } catch (error) {
      api.showError(error)
    } finally {
      this.setData({ deleting: false })
    }
  }
})
