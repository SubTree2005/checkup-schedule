const api = require('../../utils/api')
const flowGuard = require('../../utils/flow-guard')
const app = getApp()

Page({
  data: {},
  onLoad() { flowGuard.requireLogin(app) },
  goBack() { wx.navigateBack({ delta: 1 }) },
  openTerms() { wx.navigateTo({ url: '/pages/legal/legal?type=terms' }) },
  openPrivacy() { wx.navigateTo({ url: '/pages/legal/legal?type=privacy' }) },
  openDeleteAccount() { wx.navigateTo({ url: '/pages/delete-account/delete-account' }) },
  async logout() {
    const confirmed = await new Promise(resolve => {
      wx.showModal({
        title: '确认退出登录？',
        content: '退出后需重新登录才能查看体检信息。',
        confirmText: '退出登录',
        confirmColor: '#D83A3A',
        success: result => resolve(result.confirm),
        fail: () => resolve(false)
      })
    })
    if (!confirmed) return
    try {
      await api.auth.logout()
    } catch (error) {
      // 无论服务端会话是否仍有效，都清除本地登录状态。
    }
    app.clearLoginState()
    wx.reLaunch({ url: '/pages/login/login' })
  }
})
