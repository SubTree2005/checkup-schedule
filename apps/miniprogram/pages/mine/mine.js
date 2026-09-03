const api = require('../../utils/api')
const app = getApp()

function maskPhone(value) {
  const phone = String(value || '')
  return /^\d{11}$/.test(phone) ? `${phone.slice(0, 3)} **** ${phone.slice(-4)}` : phone
}

Page({
  data: { hasUserInfo: false, displayName: '未登录用户', avatarUrl: '../../addpicture/icons/icon-user.png', maskedPhone: '' },

  onShow() {
    const tabBar = typeof this.getTabBar === 'function' ? this.getTabBar() : null
    if (tabBar) tabBar.setData({ selected: 2 })
    if (!wx.getStorageSync('patientToken')) return this.renderUser(null)
    api.auth.me().then(payload => {
      app.applyUser(payload)
      this.renderUser(payload)
    }).catch(error => {
      if (error && error.statusCode === 401) {
        app.clearLoginState()
        this.renderUser(null)
        return
      }
      api.showError(error)
      this.renderUser(app.globalData.userInfo || wx.getStorageSync('userInfo') || null)
    })
  },

  renderUser(payload) {
    this.setData({
      hasUserInfo: !!payload,
      displayName: payload ? payload.name : '未登录用户',
      avatarUrl: payload && payload.avatarUrl ? payload.avatarUrl : '../../addpicture/icons/icon-user.png',
      maskedPhone: payload ? maskPhone(payload.phone) : ''
    })
  },

  requireLogin() {
    if (wx.getStorageSync('patientToken')) return true
    wx.navigateTo({ url: '/pages/login/login' })
    return false
  },

  openProfile() { if (this.requireLogin()) wx.navigateTo({ url: '/pages/edit-profile/edit-profile' }) },
  openAccount() { if (this.requireLogin()) wx.navigateTo({ url: '/pages/account-security/account-security' }) },
  openAiSettings() { if (this.requireLogin()) wx.navigateTo({ url: '/pages/ai-settings/ai-settings' }) }
})
