const api = require('../../utils/api')
const flowGuard = require('../../utils/flow-guard')
const app = getApp()

function pad(value) { return String(value).padStart(2, '0') }

function localDateKey(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function dateTitle(key) {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const target = new Date(`${key}T00:00:00`)
  const offset = Math.round((target.getTime() - today.getTime()) / 86400000)
  if (offset === 0) return '今天'
  if (offset === 1) return '明天'
  if (offset === 2) return '后天'
  return ''
}

function normalizeDate(row) {
  const date = new Date(`${row.date || row.key}T00:00:00`)
  return {
    ...row,
    key: row.key || row.date,
    title: dateTitle(row.key || row.date),
    shortDate: `${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    weekday: `周${'日一二三四五六'[date.getDay()]}`,
    availabilityLabel: row.availabilityLabel || (row.available ? '尚有余号' : '号源已满'),
    slots: Array.isArray(row.slots) ? row.slots : []
  }
}

Page({
  data: {
    dates: [],
    slots: [],
    hasDates: false,
    hasBookableSlot: false,
    selectedDate: '',
    selectedSlot: '',
    loading: true
  },

  async onLoad() {
    if (!flowGuard.requireSelection(app)) return
    try {
      const payload = await api.hospitals.appointmentSlots(app.globalData.selectedHospitalId)
      this.applyAvailability(payload)
    } catch (error) {
      api.showError(error)
      this.setData({ loading: false })
    }
  },

  applyAvailability(payload) {
    const today = localDateKey(new Date())
    const dates = (payload.dates || []).map(normalizeDate).filter(item => item.key >= today)
    const firstDate = dates.find(item => item.available && item.slots.some(slot => slot.available))
    const slots = firstDate ? firstDate.slots : []
    const firstSlot = slots.find(slot => slot.available)
    this.setData({
      dates,
      slots,
      hasDates: dates.length > 0,
      hasBookableSlot: !!firstSlot,
      selectedDate: firstDate ? firstDate.key : '',
      selectedSlot: firstSlot ? firstSlot.key : '',
      loading: false
    })
  },

  selectDate(e) {
    const item = this.data.dates.find(date => date.key === e.currentTarget.dataset.key)
    if (!item || !item.available) return
    const firstSlot = item.slots.find(slot => slot.available)
    this.setData({
      selectedDate: item.key,
      slots: item.slots,
      hasBookableSlot: !!firstSlot,
      selectedSlot: firstSlot ? firstSlot.key : ''
    })
  },

  selectSlot(e) {
    const slot = this.data.slots.find(item => item.key === e.currentTarget.dataset.key)
    if (slot && slot.available) this.setData({ selectedSlot: slot.key })
  },

  nextStep() {
    const date = this.data.dates.find(item => item.key === this.data.selectedDate)
    const slot = this.data.slots.find(item => item.key === this.data.selectedSlot)
    if (!date || !slot || !slot.available) return wx.showToast({ title: '请选择有余号的日期和时间段', icon: 'none' })
    app.globalData.appointmentDraft = {
      appointmentAt: slot.appointmentAt,
      dateLabel: `${date.shortDate} ${date.weekday}`,
      timeLabel: `${slot.start}–${slot.end}`,
      splitAcrossDays: app.globalData.preparationDecision === 'split'
    }
    wx.navigateTo({ url: '/pages/preparation-reminder/preparation-reminder' })
  },

  goBack() {
    const split = app.globalData.splitPlanDraft
    if (split) {
      app.globalData.currentPackageId = split.packageID
      app.globalData.selectedItemIDs = split.originalItemIDs || []
      app.globalData.splitPlanDraft = null
      app.globalData.preparationDecision = null
    }
    wx.navigateBack({ delta: 1 })
  }
})
