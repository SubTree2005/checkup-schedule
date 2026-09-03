const api = require('../../utils/api')
const flowGuard = require('../../utils/flow-guard')
const { planReportSummary, stepStatus } = require('../../utils/report')
const app = getApp()

function formatDate(value, fallback) {
  const parsed = value ? new Date(value) : null
  if (!parsed || Number.isNaN(parsed.getTime())) return fallback || ''
  return `${parsed.getFullYear()}-${String(parsed.getMonth() + 1).padStart(2, '0')}-${String(parsed.getDate()).padStart(2, '0')}`
}

Page({
  data: { record: null, steps: [], activeTab: 'items', recordID: '' },

  onLoad(options) {
    if (!flowGuard.requireLogin(app)) return
    const recordID = options.id || ''
    if (!recordID) return wx.switchTab({ url: '/pages/record/record' })
    this.setData({ recordID, activeTab: options.mode === 'reports' ? 'reports' : 'items' })
    const cached = app.globalData.viewingPlanRecord
    if (cached && (cached.planID || cached.id) === recordID) this.applyRecord(cached)
    api.plans.get(recordID).then(record => this.applyRecord(record)).catch(error => {
      if (!this.data.record) api.showError(error)
    })
  },

  applyRecord(record) {
    const steps = (record.steps || []).map(step => {
      const displayStatus = stepStatus(step)
      return { ...step, statusText: displayStatus.text, statusTone: displayStatus.tone }
    })
    const reports = planReportSummary(record)
    const rawStatus = String(record.status || record.planStatus || '')
    const interrupted = rawStatus === '已结束' || rawStatus.includes('中断')
    const finished = !!record.finished || ['已完成', '已结束'].includes(rawStatus)
    const displayStatus = reports.hasReport
      ? '已出报告'
      : interrupted ? '已中断'
        : finished ? '未出报告'
          : rawStatus.includes('进行') ? '进行中' : '待开始'
    const statusTone = reports.hasReport ? 'reported' : interrupted ? 'interrupted' : finished ? 'pending' : rawStatus.includes('进行') ? 'active' : 'scheduled'
    const normalized = {
      ...record,
      hospitalImage: record.hospitalCoverUrl || record.coverImageUrl || '/addpicture/hospital-default.jpg',
      packageName: record.packageName || '自选项目',
      completionDate: formatDate(record.completedAt, record.date),
      displayStatus,
      statusTone,
      reportCount: reports.count
    }
    app.globalData.viewingPlanRecord = record
    this.setData({ record: normalized, steps })
  },

  setTab(e) { this.setData({ activeTab: e.currentTarget.dataset.tab }) },

  openStep(e) {
    wx.navigateTo({ url: `/pages/exam-detail/exam-detail?planID=${this.data.recordID}&detailID=${e.currentTarget.dataset.id}&mode=${this.data.activeTab}` })
  },

  goBack() { wx.navigateBack({ delta: 1 }) }
})
