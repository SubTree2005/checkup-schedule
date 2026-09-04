const api = require('../../utils/api')
const { planReportSummary } = require('../../utils/report')
const { navigationMetrics } = require('../../utils/layout')
const app = getApp()
const navigation = navigationMetrics()

function pad(value) { return String(value).padStart(2, '0') }

Page({
  data: {
    ...navigation,
    currentPlan: null,
    isLoggedIn: false,
    hasHomeContent: false,
    feedCards: [],
    planState: 'active',
    homeTitle: '',
    homeSubtitle: '',
    completedSteps: 0,
    totalSteps: 0,
    progress: 0,
    timelineSteps: [],
    timelineScrollLeft: 0,
    currentMessage: '',
    primaryActionText: '继续体检'
  },

  onShow() {
    const tabBar = typeof this.getTabBar === 'function' ? this.getTabBar() : null
    if (tabBar && typeof tabBar.select === 'function') tabBar.select(0)
    const isLoggedIn = !!wx.getStorageSync('patientToken')
    if (this.data.isLoggedIn !== isLoggedIn) this.setData({ isLoggedIn })
    if (!isLoggedIn) {
      this._sourcePlans = null
      if (this.data.feedCards.length || this.data.currentPlan || this.data.hasHomeContent) {
        this.setData({ feedCards: [], hasHomeContent: false })
        this.clearPlanDisplay()
      }
      return
    }
    const cached = app.globalData.currentPlan
    if (cached && ['active', 'paused'].includes(this.resolvePlanState(cached))) {
      if (this._renderedPlan !== cached) this.syncPlan(cached)
    } else if (this.data.currentPlan) {
      this.syncPlan(null)
    }
    api.plans.list().then(plans => {
      if (plans === this._sourcePlans) return
      this._sourcePlans = plans
      this.syncHome(plans)
    }).catch(api.showError)
  },

  syncHome(rows) {
    const plans = Array.isArray(rows) ? rows : []
    this._planByID = new Map(plans.map(plan => [plan.planID || plan.id, plan]))
    const active = plans.find(plan => this.resolvePlanState(plan) === 'active') || plans.find(plan => this.resolvePlanState(plan) === 'paused') || null
    const now = Date.now()
    const day = 24 * 60 * 60 * 1000
    const month = 30 * day
    const scheduled = plans
      .filter(plan => this.resolvePlanState(plan) === 'scheduled')
      .map(plan => ({ plan, time: this.planTime(plan) }))
      .filter(item => item.time && item.time.getTime() >= now && item.time.getTime() - now <= day)
      .sort((a, b) => a.time.getTime() - b.time.getTime())
      .map(item => this.feedCard(item.plan, 'scheduled', item.time))
    const reports = plans
      .filter(plan => plan.finished || ['已完成', '已结束'].includes(plan.planStatus))
      .map(plan => ({ plan, summary: planReportSummary(plan), time: this.completionTime(plan) }))
      .filter(item => item.summary.hasReport && item.time && now - item.time.getTime() >= 0 && now - item.time.getTime() <= month)
      .sort((a, b) => b.time.getTime() - a.time.getTime())
      .map(item => this.feedCard(item.plan, 'report', item.time, item.summary))
    const feedCards = scheduled.concat(reports).map((card, index) => ({ ...card, highlighted: !active && index === 0 }))

    if (active) {
      app.saveCurrentPlan(active)
      this.syncPlan(active)
      const reportCopy = reports.length ? `，另有 ${reports.length} 份报告可查看` : ''
      this.setData({ feedCards, hasHomeContent: true, homeSubtitle: `${this.data.homeSubtitle}${reportCopy}` })
      return
    }

    const next = scheduled.length ? this._planByID.get(scheduled[0].id) : null
    if (next) app.saveCurrentPlan(next)
    else if (!plans.some(plan => !plan.finished)) app.saveCurrentPlan(null)
    this.clearPlanDisplay()
    this.setData({
      feedCards,
      hasHomeContent: feedCards.length > 0,
      homeTitle: scheduled.length ? '近期有预约' : reports.length ? '有体检报告请查收' : '',
      homeSubtitle: scheduled.length
        ? (reports.length ? `请按时到院，另有 ${reports.length} 份报告可查看` : '请按预约时间到院')
        : reports.length ? `${reports.length} 份报告可查看` : ''
    })
  },

  feedCard(plan, type, time, reportSummary = {}) {
    return {
      id: plan.planID || plan.id,
      type,
      hospitalName: plan.hospitalName || '体检医院',
      packageName: plan.packageName || '自选项目',
      timeLabel: type === 'scheduled' ? this.formatAppointment(time) : this.formatDate(time),
      eyebrow: type === 'scheduled' ? '预约体检' : '体检报告',
      statusText: type === 'scheduled' ? '待开始' : '已出报告',
      reportText: type === 'report' ? `${reportSummary.count || 0} / ${reportSummary.total || 0} 项已出报告` : '',
      actionText: type === 'scheduled' ? '查看预约' : '查看报告'
    }
  },

  syncPlan(plan) {
    if (!plan) {
      this._renderedPlan = null
      this.clearPlanDisplay()
      this.setData({ hasHomeContent: this.data.feedCards.length > 0 })
      return
    }
    this._renderedPlan = plan
    const steps = Array.isArray(plan.steps) ? plan.steps : []
    const totalSteps = Number(plan.totalSteps || steps.length || 0)
    const completedSteps = Number(plan.completedSteps || steps.filter(step => step.status === 'done').length)
    const progress = Math.max(0, Math.min(100, Number(plan.progress || (totalSteps ? Math.round(completedSteps / totalSteps * 100) : 0))))
    const inferredCurrentStepIndex = steps.findIndex(step => step.status !== 'done')
    const parsedCurrentStepIndex = Number(plan.currentStepIndex)
    const currentStepIndex = Math.max(0, Number.isFinite(parsedCurrentStepIndex) ? parsedCurrentStepIndex : inferredCurrentStepIndex)
    const currentStep = steps[currentStepIndex] || steps.find(step => step.status !== 'done') || null
    const planState = this.resolvePlanState(plan)
    const copy = this.planCopy(planState, plan, completedSteps, totalSteps)
    const timelineSteps = steps.map(step => ({
      detailID: step.detailID,
      title: step.title,
      state: step.status === 'done' ? 'done' : step.status === 'active' ? 'active' : 'pending',
      time: this.formatClock(step.estimatedStart)
    }))
    const windowWidth = wx.getSystemInfoSync().windowWidth || 375
    this.setData({
      currentPlan: {
        id: plan.id || plan.planID,
        planID: plan.planID || plan.id,
        hospitalName: plan.hospitalName || '体检医院',
        packageName: plan.packageName || '自选项目'
      },
      hasHomeContent: true,
      planState,
      homeTitle: copy.title,
      homeSubtitle: copy.subtitle,
      completedSteps,
      totalSteps,
      progress,
      timelineSteps,
      timelineScrollLeft: steps.length > 3 ? Math.max(0, (currentStepIndex - 1) * 190 * windowWidth / 750) : 0,
      currentMessage: this.currentMessage(planState, currentStep),
      primaryActionText: copy.action
    })
  },

  clearPlanDisplay() {
    this._renderedPlan = null
    this.setData({ currentPlan: null, completedSteps: 0, totalSteps: 0, progress: 0, timelineSteps: [], currentMessage: '' })
  },

  resolvePlanState(plan) {
    if (plan.finished || ['已完成', '已结束'].includes(plan.planStatus)) return 'complete'
    const status = String(plan.planStatus || plan.status || '')
    if (status.includes('中断') || status.includes('暂停')) return 'paused'
    if (status.includes('预约') || status.includes('待执行')) return 'scheduled'
    return 'active'
  },

  planCopy(planState, plan, completedSteps, totalSteps) {
    if (planState === 'paused') return { title: '体检已中断', subtitle: `已完成 ${completedSteps} / ${totalSteps} 项`, action: '继续体检' }
    return { title: '正在进行中', subtitle: `预计还需 ${this.formatDuration(plan.remainingDuration)}`, action: '继续体检' }
  },

  currentMessage(planState, step) {
    if (!step || planState === 'complete') return ''
    if (step.fasting) return `${step.title}前请保持空腹。`
    if (planState === 'paused' || planState === 'scheduled') return ''
    if (Number(step.queueWait) > 0) return `${step.department}预计排队 ${step.queueWait} 分钟。`
    return ''
  },

  planTime(plan) {
    const steps = Array.isArray(plan.steps) ? plan.steps : []
    const value = plan.appointmentAt || steps.map(step => step.estimatedStart).find(Boolean)
    if (!value) return null
    const date = new Date(value)
    return Number.isNaN(date.getTime()) ? null : date
  },

  completionTime(plan) {
    const value = plan.completedAt || plan.generatedAt
    if (!value) return null
    const date = new Date(value)
    return Number.isNaN(date.getTime()) ? null : date
  },

  formatAppointment(date) {
    if (!date) return '时间待确认'
    const clock = `${pad(date.getHours())}:${pad(date.getMinutes())}`
    const now = new Date()
    const sameDay = date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate()
    return sameDay ? `今天 ${clock}` : `${date.getMonth() + 1}月${date.getDate()}日 ${clock}`
  },

  formatDate(date) { return date ? `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` : '' },
  formatClock(value) {
    if (!value) return ''
    const date = new Date(value)
    return Number.isNaN(date.getTime()) ? '' : `${pad(date.getHours())}:${pad(date.getMinutes())}`
  },
  formatDuration(minutes) {
    const value = Math.max(0, Number(minutes || 0))
    if (value < 60) return `${value} 分钟`
    const hours = Math.floor(value / 60)
    const rest = value % 60
    return rest ? `${hours} 小时 ${rest} 分钟` : `${hours} 小时`
  },

  goStart() {
    if (!this.data.isLoggedIn) return wx.navigateTo({ url: '/pages/login/login' })
    wx.navigateTo({ url: '/pages/hospital/hospital' })
  },

  handlePrimary() {
    if (!this.data.currentPlan) return this.goStart()
    wx.navigateTo({ url: `/pages/plan/plan?planID=${this.data.currentPlan.planID || this.data.currentPlan.id}` })
  },

  openFeedCard(e) {
    const card = this.data.feedCards.find(item => item.id === e.currentTarget.dataset.id)
    if (!card) return
    const plan = this._planByID.get(card.id)
    if (!plan) return
    app.globalData.viewingPlanRecord = plan
    if (card.type === 'report') return wx.navigateTo({ url: `/pages/record-detail/record-detail?id=${card.id}&mode=reports` })
    app.saveCurrentPlan(plan)
    wx.navigateTo({ url: `/pages/record-detail/record-detail?id=${card.id}` })
  },

  goOverview() {
    if (this.data.currentPlan) wx.navigateTo({ url: `/pages/plan-overview/plan-overview?planID=${this.data.currentPlan.planID || this.data.currentPlan.id}` })
  }
})
