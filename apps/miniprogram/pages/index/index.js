const api = require('../../utils/api')
const app = getApp()

Page({
  data: {
    currentPlan: null,
    isLoggedIn: false,
    tips: '空腹检查请保持空腹 8–12 小时；如需泌尿系或腹部超声，请按提示关注喝水与膀胱充盈状态。',
    values: [
      { colorClass: 'mark-blue', title: '节省时间', desc: '减少等待总时长' },
      { colorClass: 'mark-green', title: '高效路线', desc: '智能安排更顺路的检查顺序' },
      { colorClass: 'mark-blue', title: '科学顺序', desc: '考虑空腹、抽血与超声等前后关系' },
      { colorClass: 'mark-green', title: '动态调整', desc: '实时应对排队变化' }
    ],
    steps: ['登录/注册', '选择医院与院区', '选择项目方式', '选择套餐或项目', '填写身体状况', '按计划完成体检']
  },

  onShow() {
    const isLoggedIn = !!wx.getStorageSync('patientToken')
    this.setData({ isLoggedIn, currentPlan: app.globalData.currentPlan })
    if (isLoggedIn) {
      api.plans.current().then(plan => {
        app.saveCurrentPlan(plan)
        this.setData({ currentPlan: plan })
      }).catch(api.showError)
    }
  },

  goStart() {
    if (!this.data.isLoggedIn) return wx.navigateTo({ url: '/pages/login/login' })
    wx.navigateTo({ url: '/pages/hospital/hospital' })
  },

  goLogin() { wx.navigateTo({ url: '/pages/login/login' }) },
  goRecord() { wx.switchTab({ url: '/pages/record/record' }) },
  goStaff() { wx.showToast({ title: '管理员请使用 Web 管理端', icon: 'none' }) },

  continuePlan() {
    if (this.data.currentPlan) return wx.navigateTo({ url: '/pages/plan/plan' })
    wx.showToast({ title: '当前没有进行中的体检', icon: 'none' })
  }
})
