const api = require('../../utils/api')
const { ICONS } = require('../../utils/icon-map')
const { planReportSummary } = require('../../utils/report')
const app = getApp()

function pad(value) { return String(value).padStart(2, '0') }

Page({
  data: {
    loading: true,
    activeTab: 'appointments',
    hasPlans: false,
    hasHistory: false,
    contentEmpty: false,
    highlightPlan: null,
    otherPlans: [],
    historyGroups: [],
    showOtherHeading: false
  },

  onShow() {
    const tabBar = typeof this.getTabBar === 'function' ? this.getTabBar() : null
    if (tabBar) tabBar.setData({ selected: 1 })
    const requestedTab = wx.getStorageSync('recordInitialTab')
    if (requestedTab === 'appointments' || requestedTab === 'history') {
      wx.removeStorageSync('recordInitialTab')
      this.setData({ activeTab: requestedTab })
    }
    if (!wx.getStorageSync('patientToken')) {
      this.setData({ loading: false, hasPlans: false, hasHistory: false, contentEmpty: true, highlightPlan: null, otherPlans: [], historyGroups: [] })
      return
    }
    this.loadPlans()
  },

  async loadPlans() {
    this.setData({ loading: true })
    try { this.applyPlans(await api.plans.list()) }
    catch (error) { this.setData({ loading: false }); api.showError(error) }
  },

  applyPlans(rows) {
    const source = Array.isArray(rows) ? rows : []
    this._planByID = new Map(source.map(plan => [plan.planID || plan.id, plan]))
    const plans = source.filter(plan => !plan.finished && plan.planStatus !== '已完成').map(plan => this.normalizePlan(plan)).sort((a, b) => a.sortTime - b.sortTime)
    const history = source.filter(plan => plan.finished || ['已完成', '已结束'].includes(plan.planStatus)).map(plan => this.normalizeHistory(plan)).sort((a, b) => b.sortTime - a.sortTime)
    const byYear = new Map()
    history.forEach(record => {
      if (!byYear.has(record.year)) byYear.set(record.year, [])
      byYear.get(record.year).push(record)
    })
    const highlightPlan = this.pickHighlight(plans)
    const otherPlans = highlightPlan ? plans.filter(plan => plan.id !== highlightPlan.id) : plans
    if (highlightPlan) app.saveCurrentPlan(this._planByID.get(highlightPlan.id))
    const historyGroups = Array.from(byYear, ([year, items]) => ({ year: `${year}年`, items }))
    const hasPlans = plans.length > 0
    const hasHistory = history.length > 0
    this.setData({
      loading: false,
      hasPlans,
      hasHistory,
      contentEmpty: this.data.activeTab === 'appointments' ? !hasPlans : !hasHistory,
      highlightPlan,
      otherPlans,
      historyGroups,
      showOtherHeading: !!highlightPlan && otherPlans.length > 0
    })
  },

  setTab(e) {
    const activeTab = e.currentTarget.dataset.tab
    this.setData({ activeTab, contentEmpty: activeTab === 'appointments' ? !this.data.hasPlans : !this.data.hasHistory })
  },

  normalizePlan(plan) {
    const appointmentAt = this.planTime(plan)
    const state = this.planState(plan)
    const total = Number(plan.totalSteps || 0)
    const completed = Number(plan.completedSteps || 0)
    return {
      id: plan.planID || plan.id,
      hospitalName: plan.hospitalName || '体检医院',
      examType: plan.packageName || '自选项目',
      appointmentLabel: this.formatAppointment(appointmentAt),
      sortTime: appointmentAt ? appointmentAt.getTime() : Number.MAX_SAFE_INTEGER,
      isToday: this.isToday(appointmentAt),
      state,
      stateIconPath: state === 'paused' ? ICONS.bellAlert : state === 'active' ? ICONS.direction : ICONS.clock,
      stateLabel: this.stateLabel(state),
      progressText: total ? `已完成 ${completed} / ${total} 项` : '',
      actionText: state === 'scheduled' ? '开始体检' : '继续体检',
      showPrimaryAction: state === 'paused' || state === 'active' || this.isToday(appointmentAt)
    }
  },

  normalizeHistory(plan) {
    const value = plan.completedAt || plan.generatedAt || plan.appointmentAt
    const parsed = value ? new Date(value) : new Date()
    const date = Number.isNaN(parsed.getTime()) ? new Date() : parsed
    const interrupted = plan.planStatus === '已结束' || String(plan.planStatus || plan.status || '').includes('中断')
    const reports = planReportSummary(plan)
    const status = interrupted ? '已中断' : reports.hasReport ? '已出报告' : '未出报告'
    return {
      id: plan.planID || plan.id,
      year: date.getFullYear(),
      sortTime: date.getTime(),
      dateLabel: `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
      hospitalName: plan.hospitalName || '体检医院',
      hospitalImage: plan.hospitalCoverUrl || plan.coverImageUrl || '/addpicture/hospital-default.jpg',
      packageName: plan.packageName || '自选项目',
      status,
      statusTone: reports.hasReport ? 'reported' : interrupted ? 'interrupted' : 'pending'
    }
  },

  planTime(plan) {
    const steps = Array.isArray(plan.steps) ? plan.steps : []
    const value = steps.map(step => step.estimatedStart).find(Boolean) || plan.appointmentAt || plan.generatedAt
    if (!value) return null
    const parsed = new Date(value)
    return Number.isNaN(parsed.getTime()) ? null : parsed
  },

  planState(plan) {
    const status = String(plan.planStatus || plan.status || '')
    if (status.includes('中断') || status.includes('暂停')) return 'paused'
    if (status.includes('进行')) return 'active'
    return 'scheduled'
  },

  stateLabel(state) {
    if (state === 'paused') return '体检已中断'
    if (state === 'active') return '体检进行中'
    return '待开始'
  },

  pickHighlight(plans) {
    const running = plans.find(plan => plan.state === 'active') || plans.find(plan => plan.state === 'paused')
    if (running) return running
    const today = plans.filter(plan => plan.isToday)
    if (!today.length) return null
    const now = Date.now()
    return today.sort((a, b) => Math.abs(a.sortTime - now) - Math.abs(b.sortTime - now))[0]
  },

  isToday(date) {
    if (!date) return false
    const now = new Date()
    return date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate()
  },

  formatAppointment(date) {
    if (!date) return '时间待确认'
    const clock = `${pad(date.getHours())}:${pad(date.getMinutes())}`
    if (this.isToday(date)) return `今天 ${clock}`
    return `${date.getMonth() + 1}月${date.getDate()}日 ${clock}`
  },

  findAppointment(id) { return [this.data.highlightPlan, ...this.data.otherPlans].find(plan => plan && plan.id === id) },

  openPlan(e) {
    const selected = this.findAppointment(e.currentTarget.dataset.id)
    if (!selected) return
    app.saveCurrentPlan(this._planByID.get(selected.id))
    wx.navigateTo({ url: `/pages/plan/plan?planID=${selected.id}` })
  },

  viewDetail(e) {
    const id = e.currentTarget.dataset.id
    const selected = this.findAppointment(id)
    if (selected) app.globalData.viewingPlanRecord = this._planByID.get(id)
    wx.navigateTo({ url: `/pages/record-detail/record-detail?id=${id}` })
  },

  viewHistoryDetail(e) {
    const id = e.currentTarget.dataset.id
    const selected = this.data.historyGroups.reduce((found, group) => found || group.items.find(item => item.id === id), null)
    if (selected) app.globalData.viewingPlanRecord = this._planByID.get(id)
    wx.navigateTo({ url: `/pages/record-detail/record-detail?id=${id}` })
  },

  createPlan() {
    if (!wx.getStorageSync('patientToken')) return wx.navigateTo({ url: '/pages/login/login' })
    wx.navigateTo({ url: '/pages/hospital/hospital' })
  }
})
