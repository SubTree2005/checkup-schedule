const api = require('../../utils/api')
const app = getApp()

Page({
  data: { submitting: false, form: { name: '', gender: '女', age: '', phone: '', password: '' } },
  setGender(e) { this.setData({ 'form.gender': e.currentTarget.dataset.value }) },
  onInput(e) { this.setData({ [`form.${e.currentTarget.dataset.key}`]: e.detail.value }) },

  async submitRegister() {
    const { name, gender, age, phone, password } = this.data.form
    if (!name || !age || !phone || !password) return wx.showToast({ title: '请完整填写注册信息', icon: 'none' })
    if (password.length < 8) return wx.showToast({ title: '密码至少需要 8 位', icon: 'none' })
    this.setData({ submitting: true })
    try {
      const payload = await api.auth.register({ name, gender, age: Number(age), phone, password })
      app.setAuthenticated(payload)
      wx.showToast({ title: '注册成功', icon: 'success' })
      setTimeout(() => wx.switchTab({ url: '/pages/mine/mine' }), 300)
    } catch (error) {
      api.showError(error)
    } finally {
      this.setData({ submitting: false })
    }
  }
})
