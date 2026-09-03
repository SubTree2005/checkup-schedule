const api = require('../../utils/api')
const app = getApp()

Page({
  data: {
    submitting: false,
    agreed: false,
    showPassword: false,
    form: { account: '', password: '' }
  },

  onInput(e) {
    this.setData({ [`form.${e.currentTarget.dataset.key}`]: e.detail.value })
  },

  toggleAgreement() {
    this.setData({ agreed: !this.data.agreed })
  },

  togglePassword() {
    this.setData({ showPassword: !this.data.showPassword })
  },

  openTerms() {
    wx.navigateTo({ url: '/pages/legal/legal?type=terms' })
  },

  openPrivacy() {
    wx.navigateTo({ url: '/pages/legal/legal?type=privacy' })
  },

  goRegister() {
    wx.navigateTo({ url: '/pages/register/register' })
  },

  async submitLogin() {
    const account = String(this.data.form.account || '').trim()
    const password = String(this.data.form.password || '')
    if (!account || !password) {
      wx.showToast({ title: '请输入手机号和密码', icon: 'none' })
      return
    }
    if (!/^1\d{10}$/.test(account)) {
      wx.showToast({ title: '请输入正确的手机号', icon: 'none' })
      return
    }
    if (!this.data.agreed) {
      wx.showToast({ title: '请先阅读并同意协议', icon: 'none' })
      return
    }
    this.setData({ submitting: true })
    try {
      const payload = await api.auth.login({ phone: account, password })
      app.setAuthenticated(payload)
      wx.showToast({ title: '登录成功', icon: 'success' })
      setTimeout(() => wx.switchTab({ url: '/pages/index/index' }), 300)
    } catch (error) {
      api.showError(error)
    } finally {
      this.setData({ submitting: false })
    }
  }
})
