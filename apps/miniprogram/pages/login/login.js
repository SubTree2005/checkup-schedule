const api = require('../../utils/api')
const app = getApp()

Page({
  data: { activeRole: 'user', submitting: false, form: { account: '', password: '' } },
  chooseRole(e) { this.setData({ activeRole: e.currentTarget.dataset.role }) },
  onInput(e) { this.setData({ [`form.${e.currentTarget.dataset.key}`]: e.detail.value }) },
  goRegister() { wx.navigateTo({ url: '/pages/register/register' }) },

  async submitLogin() {
    if (this.data.activeRole === 'manager') {
      wx.showToast({ title: '医院管理员请使用 Web 管理端', icon: 'none' })
      return
    }
    const { account, password } = this.data.form
    if (!account || !password) return wx.showToast({ title: '请输入手机号和密码', icon: 'none' })
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
