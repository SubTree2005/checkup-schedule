const api = require('../../utils/api')
const flowGuard = require('../../utils/flow-guard')
const { planReportSummary } = require('../../utils/report')
const app = getApp()

function pad(value) { return String(value).padStart(2, '0') }

Page({
  data: { groups: [], hasGroups: false, loading: true },

  onShow() {
    if (!flowGuard.requireLogin(app)) return
    api.plans.list().then(plans => this.applyPlans(plans)).catch(error => {
      this.setData({ loading: false })
      api.showError(error)
    })
  },

  applyPlans(plans) {
    const source = Array.isArray(plans) ? plans : []
    this._planByID = new Map(source.map(plan => [plan.planID || plan.id, plan]))
    const records = source
      .filter(item => item.finished || ['已完成', '已结束'].includes(item.planStatus))
      .map(item => this.normalizeRecord(item))
      .sort((left, right) => right.sortTime - left.sortTime)
    const byYear = new Map()
    records.forEach(record => {
      if (!byYear.has(record.year)) byYear.set(record.year, [])
      byYear.get(record.year).push(record)
    })
    const groups = Array.from(byYear, ([year, items]) => ({ year: `${year}年`, items }))
    this.setData({ groups, hasGroups: groups.length > 0, loading: false })
  },

  normalizeRecord(item) {
    const value = item.completedAt || item.generatedAt || item.appointmentAt
    const parsed = value ? new Date(value) : new Date()
    const date = Number.isNaN(parsed.getTime()) ? new Date() : parsed
    const originalStatus = item.planStatus || item.status || '已完成'
    const interrupted = originalStatus.includes('中断') || originalStatus === '已结束'
    const reports = planReportSummary(item)
    const status = interrupted ? '已中断' : reports.hasReport ? '已出报告' : '未出报告'
    return {
      id: item.planID || item.id,
      year: date.getFullYear(),
      sortTime: date.getTime(),
      dateLabel: `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
      hospitalName: item.hospitalName || '体检医院',
      hospitalImage: item.hospitalCoverUrl || item.coverImageUrl || '/addpicture/hospital-default.jpg',
      packageName: item.packageName || '自选项目',
      status,
      statusTone: reports.hasReport ? 'done' : interrupted ? 'interrupted' : 'neutral'
    }
  },

  goRecordDetail(e) {
    const id = e.currentTarget.dataset.id
    const selected = this.data.groups.reduce((found, group) => found || group.items.find(item => item.id === id), null)
    if (selected) app.globalData.viewingPlanRecord = this._planByID.get(id)
    wx.navigateTo({ url: `/pages/record-detail/record-detail?id=${id}` })
  },

  goBack() { wx.navigateBack({ delta: 1 }) }
})
