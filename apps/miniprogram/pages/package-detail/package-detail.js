const api = require('../../utils/api')
const flowGuard = require('../../utils/flow-guard')
const app = getApp()

Page({
  data: {
    loading: true,
    selectionName: '',
    totalItems: 0,
    durationLabel: '',
    summaryGroups: [],
    selectedItems: [],
    preparationItems: [],
    detailOpen: false
  },

  onLoad() {
    if (flowGuard.requireSelection(app)) this.loadSelection()
  },

  async loadSelection() {
    try {
      const catalog = app.globalData.catalog || await api.hospitals.catalog(app.globalData.selectedHospitalId)
      app.globalData.catalog = catalog
      this.applySelection(catalog, app.globalData.currentPackageId, app.globalData.selectedItemIDs || [])
    } catch (error) {
      this.setData({ loading: false })
      api.showError(error)
    }
  },

  applySelection(catalog, packageId, selectedItemIDs) {
    const source = catalog || {}
    const pkg = (source.packages || []).find(item => (item.id || item.packageID) === packageId)
    const groupedItems = pkg
      ? (pkg.groups || []).reduce((all, group) => all.concat(group.items || []), [])
      : []
    const packageItems = pkg && pkg.items && pkg.items.length ? pkg.items : groupedItems
    const catalogItems = (source.departments || []).reduce((all, department) => {
      return all.concat((department.projects || []).map(project => ({
        ...project,
        department: project.department || department.name || ''
      })))
    }, source.exams || [])
    const selectedSet = new Set(selectedItemIDs || [])
    const selectedItems = pkg
      ? packageItems
      : catalogItems.filter(item => selectedSet.has(item.id || item.itemID))
    const totalDuration = selectedItems.reduce((total, item) => total + Number(item.duration || 0), 0)

    this.setData({
      selectionName: pkg ? (pkg.name || pkg.packageName) : '自选项目',
      totalItems: selectedItems.length,
      durationLabel: this.formatDuration(totalDuration),
      summaryGroups: this.summarize(selectedItems),
      selectedItems: selectedItems.map(item => ({
        id: item.id || item.itemID,
        name: item.name || item.itemName,
        department: item.department || ''
      })),
      preparationItems: this.preparation(selectedItems, pkg),
      loading: false
    })
  },

  summarize(items) {
    const groups = [
      { key: 'laboratory', label: '检验检查', matcher: /血|尿|便|检验|化验|生化|肝功能|肾功能/ },
      { key: 'imaging', label: '影像检查', matcher: /超声|DR|CT|核磁|放射|影像|胸片/ },
      { key: 'functional', label: '功能检查', matcher: /心电|肺功能|骨密度|听力|眼底|功能/ }
    ]
    const counts = groups.map(group => ({ key: group.key, label: group.label, count: 0 }))
    let otherCount = 0
    items.forEach(item => {
      const text = `${item.name || item.itemName || ''}${item.department || ''}`
      const index = groups.findIndex(group => group.matcher.test(text))
      if (index === -1) otherCount += 1
      else counts[index].count += 1
    })
    return [...counts, { key: 'other', label: '其他项目', count: otherCount }]
      .filter(group => group.count > 0)
  },

  preparation(items, pkg) {
    const rows = []
    if (items.some(item => item.fastingRequired)) rows.push({ key: 'fasting', label: '空腹', text: '检查前保持空腹 8–12 小时' })
    const notices = (pkg && Array.isArray(pkg.notice) ? pkg.notice : [])
      .filter(text => text && !String(text).includes('路线会结合'))
      .slice(0, 2)
    notices.forEach((text, index) => rows.push({ key: `notice-${index}`, label: '注意', text }))
    if (!rows.length) rows.push({ key: 'normal', label: '准备', text: '本次项目暂无特殊准备要求' })
    return rows
  },

  formatDuration(minutes) {
    const value = Math.max(0, Number(minutes || 0))
    if (!value) return '待现场确认'
    if (value < 60) return `约 ${value} 分钟`
    const hours = Math.floor(value / 60)
    const rest = value % 60
    return rest ? `约 ${hours} 小时 ${rest} 分钟` : `约 ${hours} 小时`
  },

  toggleDetails() { this.setData({ detailOpen: !this.data.detailOpen }) },
  goNext() { wx.navigateTo({ url: '/pages/select-mode/select-mode' }) },
  goBack() { wx.navigateBack({ delta: 1 }) }
})
