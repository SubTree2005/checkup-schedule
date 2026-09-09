const { navigationMetrics } = require('../../utils/layout')
const api = require('../../utils/api')
const flowGuard = require('../../utils/flow-guard')
const { examIcon } = require('../../utils/icon-map')
const { normalizeReport, stepStatus } = require('../../utils/report')
const app = getApp()

function formatDateTime(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  const pad = number => String(number).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

Page({
  data: {
    ...navigationMetrics(), planID: '', detailID: '', step: null, report: null, reportFirst: false, loading: true },

  onLoad(options) {
    if (!flowGuard.requireLogin(app)) return
    const planID = options.planID || ''
    const detailID = options.detailID || ''
    const resultIndex = /^\d+$/.test(options.resultIndex || '') ? Number(options.resultIndex) : null
    this.setData({ planID, detailID, reportFirst: options.mode === 'reports', resultIndex })
    const cached = app.globalData.viewingPlanRecord || app.globalData.currentPlan
    if (cached && (cached.planID || cached.id) === planID) this.applyPlan(cached)
    if (!planID || !detailID) return
    api.plans.get(planID).then(plan => this.applyPlan(plan)).catch(error => {
      if (!this.data.step) api.showError(error)
    })
  },

  applyPlan(plan) {
    const step = (plan.steps || []).find(item => item.detailID === this.data.detailID)
    if (!step) return
    const displayStatus = stepStatus(step)
    const report = normalizeReport(step)
    const indicator = this.data.resultIndex !== null && this.data.resultIndex !== undefined ? report.rows[this.data.resultIndex] : null
    const completed = step.status === 'done' || step.completed === true || ['done', 'reported'].includes(displayStatus.tone)
    this.setData({
      loading: false,
      indicator: indicator || null,
      step: {
        detailID: step.detailID,
        title: step.title || '检查项目',
        department: step.department || '检查科室',
        iconPath: examIcon(step.title),
        location: (step.navigationTarget && step.navigationTarget.locationText) || '请查看院内指引',
        durationText: step.duration ? `约 ${step.duration} 分钟` : '时长以现场安排为准',
        showDuration: !completed,
        statusText: displayStatus.text,
        statusTone: displayStatus.tone
      },
      report: { ...report, rows: this.data.resultIndex === null || this.data.resultIndex === undefined ? report.rows : indicator ? [indicator] : [], reportedAtText: formatDateTime(report.reportedAt) }
    })
  },

  goNavigation() {
    wx.navigateTo({ url: `/pages/navigation/navigation?planID=${this.data.planID}&detailID=${this.data.detailID}` })
  },

  goBack() { wx.navigateBack({ delta: 1 }) }
})
