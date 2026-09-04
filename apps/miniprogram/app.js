const api = require('./utils/api')
const LEGACY_AI_STORAGE_KEYS = ['aiAgentSession', 'aiAgentHistory', 'aiAgentModelConfig', 'aiAgentConfig']

App({
  globalData: {
    brandName: '检畅',
    currentPlan: null,
    profile: null,
    currentPackageId: null,
    selectedItemIDs: [],
    selectedHospitalId: null,
    selectedHospital: null,
    selectedCampusId: null,
    selectedCampus: null,
    selectedPlanMode: null,
    appointmentDraft: null,
    preparationDecision: null,
    splitPlanDraft: null,
    catalog: null,
    viewingPlanRecord: null,
    activeTabIndex: 0,
    userInfo: null,
    userRole: 'user',
    isLoggedIn: false
  },

  onLaunch() {
    this.globalData.currentPlan = wx.getStorageSync('currentPlan') || null
    this.globalData.profile = wx.getStorageSync('profile') || null
    this.globalData.userInfo = wx.getStorageSync('userInfo') || null
    const token = wx.getStorageSync('patientToken')
    this.globalData.isLoggedIn = !!token
    if (this.globalData.isLoggedIn) {
      api.auth.me().then(payload => {
        if (wx.getStorageSync('patientToken') === token) this.applyUser(payload)
      }).catch(error => {
        if (wx.getStorageSync('patientToken') !== token) return
        if (error && error.statusCode === 401) this.clearLoginState()
      })
    }
  },

  applyUser(payload) {
    const userInfo = {
      userID: payload.userID,
      name: payload.name,
      gender: payload.gender,
      age: payload.age,
      phone: payload.phone,
      avatarUrl: payload.avatarUrl || ''
    }
    this.globalData.userInfo = userInfo
    this.globalData.profile = payload.profile || this.globalData.profile
    this.globalData.isLoggedIn = true
    wx.setStorageSync('userInfo', userInfo)
    wx.setStorageSync('profile', this.globalData.profile)
    return payload
  },

  setAuthenticated(payload) {
    api.clearCache()
    wx.setStorageSync('patientToken', payload.token)
    this.applyUser(payload.user)
  },

  clearLoginState(options = {}) {
    api.clearCache()
    const storedUser = this.globalData.userInfo || wx.getStorageSync('userInfo') || {}
    const userID = String(storedUser.userID || storedUser.id || '')
    this.globalData.isLoggedIn = false
    this.globalData.userInfo = null
    this.globalData.profile = null
    this.globalData.currentPlan = null
    this.globalData.currentPackageId = null
    this.globalData.selectedItemIDs = []
    this.globalData.selectedHospitalId = null
    this.globalData.selectedHospital = null
    this.globalData.selectedCampusId = null
    this.globalData.selectedCampus = null
    this.globalData.selectedPlanMode = null
    this.globalData.appointmentDraft = null
    this.globalData.preparationDecision = null
    this.globalData.splitPlanDraft = null
    this.globalData.catalog = null
    this.globalData.viewingPlanRecord = null
    wx.removeStorageSync('patientToken')
    wx.removeStorageSync('userInfo')
    wx.removeStorageSync('profile')
    wx.removeStorageSync('currentPlan')
    LEGACY_AI_STORAGE_KEYS.forEach(key => wx.removeStorageSync(key))
    if (options.clearPrivateData && userID) {
      LEGACY_AI_STORAGE_KEYS.forEach(key => wx.removeStorageSync(`${key}:${userID}`))
    }
  },

  saveCurrentPlan(plan) {
    this.globalData.currentPlan = plan
    if (plan) wx.setStorageSync('currentPlan', plan)
    else wx.removeStorageSync('currentPlan')
  },

  saveProfile(profile) {
    this.globalData.profile = profile
    wx.setStorageSync('profile', profile)
  },

  saveUserInfo(userInfo) {
    this.globalData.userInfo = userInfo
    wx.setStorageSync('userInfo', userInfo)
  }
})
