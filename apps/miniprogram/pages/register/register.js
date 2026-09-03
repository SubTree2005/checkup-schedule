const api = require('../../utils/api')
const app = getApp()
const PRIVACY_POLICY_VERSION = 'v0.3.1-2026-08-31'

Page({
  data: {
    submitting: false,
    acceptedPolicies: false,
    form: { name: '', gender: '女', age: '', phone: '', password: '', confirmPassword: '', medicalHistory: '', allergens: '' }
  },
  setGender(e) { this.setData({ 'form.gender': e.currentTarget.dataset.value }) },
  onInput(e) { this.setData({ [`form.${e.currentTarget.dataset.key}`]: e.detail.value }) },
  togglePolicies() { this.setData({ acceptedPolicies: !this.data.acceptedPolicies }) },
  openTerms() { wx.navigateTo({ url: '/pages/legal/legal?type=terms' }) },
  openPrivacy() { wx.navigateTo({ url: '/pages/legal/legal?type=privacy' }) },

  async submitRegister() {
    const name = String(this.data.form.name || '').trim()
    const gender = this.data.form.gender
    const age = Number(this.data.form.age)
    const phone = String(this.data.form.phone || '').trim()
    const password = String(this.data.form.password || '')
    const confirmPassword = String(this.data.form.confirmPassword || '')
    const medicalHistory = String(this.data.form.medicalHistory || '').trim()
    const allergens = String(this.data.form.allergens || '').trim()
    if (!name || !age || !phone || !password || !confirmPassword || !medicalHistory || !allergens) return wx.showToast({ title: '请完整填写注册信息', icon: 'none' })
    if (!Number.isInteger(age) || age < 1 || age > 120) return wx.showToast({ title: '请输入正确的年龄', icon: 'none' })
    if (!/^1\d{10}$/.test(phone)) return wx.showToast({ title: '请输入正确的手机号', icon: 'none' })
    if (password.length < 8) return wx.showToast({ title: '密码至少需要 8 位', icon: 'none' })
    if (password !== confirmPassword) return wx.showToast({ title: '两次输入的密码不一致', icon: 'none' })
    if (!this.data.acceptedPolicies) return wx.showToast({ title: '请先阅读并同意协议', icon: 'none' })
    this.setData({ submitting: true })
    try {
      const payload = await api.auth.register({
        name,
        gender,
        age: Number(age),
        phone,
        password,
        medicalHistory,
        allergens,
        privacyConsent: true,
        privacyConsentVersion: PRIVACY_POLICY_VERSION
      })
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
