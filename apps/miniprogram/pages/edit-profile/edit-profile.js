const api = require('../../utils/api')
const app = getApp()

Page({
  data: {
    form: { name: '', gender: '女', age: '', phone: '' },
    profile: { fasting: 'yes', bladder: 'normal', drinkingWater: 'adequate', medicalHistory: '-', allergens: '-' }
  },
  onLoad() {
    const userInfo = app.globalData.userInfo || wx.getStorageSync('userInfo') || {}
    const profile = app.globalData.profile || wx.getStorageSync('profile') || {}
    this.setData({ form: { ...this.data.form, ...userInfo }, profile: { ...this.data.profile, ...profile } })
  },
  onInput(e) { this.setData({ [`form.${e.currentTarget.dataset.key}`]: e.detail.value }) },
  onProfileInput(e) { this.setData({ [`profile.${e.currentTarget.dataset.key}`]: e.detail.value || '-' }) },
  setField(e) { this.setData({ [`form.${e.currentTarget.dataset.key}`]: e.currentTarget.dataset.value }) },
  setProfileField(e) { this.setData({ [`profile.${e.currentTarget.dataset.key}`]: e.currentTarget.dataset.value }) },
  async saveAll() {
    try {
      const payload = await api.profile.update({
        name: this.data.form.name,
        phone: this.data.form.phone,
        gender: this.data.form.gender,
        age: Number(this.data.form.age),
        fasting: this.data.profile.fasting,
        bladder: this.data.profile.bladder,
        drinkingWater: this.data.profile.drinkingWater,
        medicalHistory: this.data.profile.medicalHistory,
        allergens: this.data.profile.allergens
      })
      app.applyUser(payload)
      wx.showToast({ title: '保存成功', icon: 'success' })
      setTimeout(() => wx.navigateBack(), 300)
    } catch (error) { api.showError(error) }
  }
})
