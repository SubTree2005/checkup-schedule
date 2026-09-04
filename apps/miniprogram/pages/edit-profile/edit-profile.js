const api = require('../../utils/api')
const flowGuard = require('../../utils/flow-guard')
const app = getApp()

Page({
  data: {
    submitting: false,
    form: { name: '', gender: '女', age: '', phone: '', avatarUrl: '' },
    avatarPreview: '../../addpicture/icons/icon-user.png',
    profile: { medicalHistory: '-', allergens: '-' }
  },
  onLoad() {
    if (!flowGuard.requireLogin(app)) return
    const userInfo = app.globalData.userInfo || wx.getStorageSync('userInfo') || {}
    const profile = app.globalData.profile || wx.getStorageSync('profile') || {}
    this.applyProfile(userInfo, profile)
  },
  applyProfile(userInfo = {}, profile = {}) {
    this.setData({
      form: { ...this.data.form, ...userInfo },
      avatarPreview: userInfo.avatarUrl || '../../addpicture/icons/icon-user.png',
      profile: { ...this.data.profile, ...profile }
    })
  },
  onInput(e) { this.setData({ [`form.${e.currentTarget.dataset.key}`]: e.detail.value }) },
  onProfileInput(e) { this.setData({ [`profile.${e.currentTarget.dataset.key}`]: e.detail.value || '-' }) },
  setField(e) { this.setData({ [`form.${e.currentTarget.dataset.key}`]: e.currentTarget.dataset.value }) },
  chooseAvatar() {
    const choose = wx.chooseMedia
      ? callback => wx.chooseMedia({ count: 1, mediaType: ['image'], sourceType: ['album', 'camera'], sizeType: ['compressed'], success: callback })
      : callback => wx.chooseImage({ count: 1, sizeType: ['compressed'], sourceType: ['album', 'camera'], success: callback })
    choose(result => {
      const file = result.tempFiles && result.tempFiles[0]
      const path = (file && file.tempFilePath) || (result.tempFilePaths && result.tempFilePaths[0])
      if (!path) return
      const readAvatar = size => {
        if (Number(size || 0) > 1024 * 1024) return wx.showToast({ title: '头像不能超过 1 MB', icon: 'none' })
        wx.getFileSystemManager().readFile({
          filePath: path,
          encoding: 'base64',
          success: content => {
            const suffix = String(path).split('.').pop().toLowerCase()
            const mime = suffix === 'png' ? 'png' : suffix === 'webp' ? 'webp' : 'jpeg'
            this.setData({ avatarPreview: path, 'form.avatarUrl': `data:image/${mime};base64,${content.data}` })
          },
          fail: () => wx.showToast({ title: '头像读取失败', icon: 'none' })
        })
      }
      if (file && Number.isFinite(Number(file.size))) return readAvatar(file.size)
      wx.getFileInfo({ filePath: path, success: info => readAvatar(info.size), fail: () => readAvatar(0) })
    })
  },
  async saveAll() {
    if (this.data.submitting) return
    const name = String(this.data.form.name || '').trim()
    const phone = String(this.data.form.phone || '').trim()
    const age = Number(this.data.form.age)
    if (!name) return wx.showToast({ title: '请输入姓名', icon: 'none' })
    if (!Number.isInteger(age) || age < 1 || age > 120) return wx.showToast({ title: '请输入正确的年龄', icon: 'none' })
    if (!/^1\d{10}$/.test(phone)) return wx.showToast({ title: '请输入正确的手机号', icon: 'none' })
    this.setData({ submitting: true })
    try {
      const payload = await api.profile.update({
        name,
        phone,
        gender: this.data.form.gender,
        age,
        avatarUrl: this.data.form.avatarUrl || null,
        medicalHistory: this.data.profile.medicalHistory,
        allergens: this.data.profile.allergens
      })
      app.applyUser(payload)
      wx.showToast({ title: '保存成功', icon: 'success' })
      wx.navigateBack({ delta: 1 })
    } catch (error) {
      api.showError(error)
    } finally {
      this.setData({ submitting: false })
    }
  },
  goBack() { wx.navigateBack({ delta: 1 }) }
})
