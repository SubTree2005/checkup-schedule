const api = require('../../utils/api')
const app = getApp()

Page({
  data: {
    user: { avatar: '/addpicture/logo.png', userInfo: {}, profile: {}, role: 'user' },
    hasUserInfo: false,
    displayName: '未登录用户',
    menus: [
      { key: 'profile', label: '个人信息' },
      { key: 'record', label: '体检记录' },
      { key: 'package', label: '开始新的体检' },
      { key: 'account', label: '账号与隐私' },
      { key: 'logout', label: '退出登录' }
    ]
  },
  onShow() {
    if (!wx.getStorageSync('patientToken')) return this.renderUser(null)
    api.auth.me().then(payload => {
      app.applyUser(payload)
      this.renderUser(payload)
    }).catch(error => { api.showError(error); this.renderUser(null) })
  },
  renderUser(payload) {
    const userInfo = payload ? {
      name: payload.name, gender: payload.gender, age: payload.age, phone: payload.phone
    } : {}
    this.setData({
      hasUserInfo: !!payload,
      displayName: payload ? payload.name : '未登录用户',
      user: { avatar: '/addpicture/logo.png', userInfo, profile: payload ? payload.profile : {}, role: 'user' }
    })
  },
  async onMenuTap(e) {
    const key = e.currentTarget.dataset.key
    if (!wx.getStorageSync('patientToken')) return wx.navigateTo({ url: '/pages/login/login' })
    if (key === 'profile') return wx.navigateTo({ url: '/pages/edit-profile/edit-profile' })
    if (key === 'record') return wx.switchTab({ url: '/pages/record/record' })
    if (key === 'package') return wx.navigateTo({ url: '/pages/hospital/hospital' })
    if (key === 'account') return wx.navigateTo({ url: '/pages/account-security/account-security' })
    if (key === 'logout') {
      try { await api.auth.logout() } catch (_error) {}
      app.clearLoginState()
      this.renderUser(null)
      wx.showToast({ title: '已退出登录', icon: 'success' })
    }
  }
})
