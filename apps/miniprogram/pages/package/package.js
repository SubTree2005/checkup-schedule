const api = require('../../utils/api')
const { examIcon } = require('../../utils/icon-map')
const flowGuard = require('../../utils/flow-guard')
const app = getApp()

Page({
  data: {
    loading: true,
    activeTab: 'package',
    selectedHospitalName: '',
    packages: [],
    hasPackages: false,
    departments: [],
    selectedPackageId: '',
    selectedItemIDs: []
  },

  onLoad() {
    if (flowGuard.requireHospital(app)) this.loadCatalog()
  },

  async loadCatalog() {
    try {
      const catalog = await api.hospitals.catalog(app.globalData.selectedHospitalId)
      this.applyCatalog(catalog)
    } catch (error) {
      api.showError(error)
    } finally {
      this.setData({ loading: false })
    }
  },

  applyCatalog(catalog) {
    const source = catalog || {}
    app.globalData.catalog = source
    const packages = (source.packages || []).map(pkg => this.normalizePackage(pkg))
    const departments = (source.departments || []).map(department => ({
      ...department,
      projects: (department.projects || []).map(project => ({ ...project, selected: false }))
    }))
    const selectedHospital = app.globalData.selectedHospital || source.hospital || {}
    this.setData({
      packages,
      hasPackages: packages.length > 0,
      departments,
      selectedHospitalName: selectedHospital.name || selectedHospital.hospitalName || '',
      selectedPackageId: packages[0] ? packages[0].id : '',
      loading: false
    })
  },

  normalizePackage(pkg) {
    const groupedItems = (pkg.groups || []).reduce((all, group) => all.concat(group.items || []), [])
    const items = pkg.items && pkg.items.length ? pkg.items : groupedItems
    const duration = items.reduce((total, item) => total + Number(item.duration || 0), 0)
    const itemCount = items.length || (pkg.checkIds || []).length
    return {
      ...pkg,
      id: pkg.id || pkg.packageID,
      durationLabel: this.formatDuration(duration),
      itemCount,
      hasPreviewItems: items.length > 0,
      previewItems: items.slice(0, 5).map(item => {
        const name = item.name || item.itemName || '检查'
        return { id: item.id || item.itemID, name, iconPath: examIcon(name) }
      })
    }
  },

  formatDuration(minutes) {
    const value = Math.max(0, Number(minutes || 0))
    if (!value) return '时长待确认'
    if (value < 60) return `约 ${value} 分钟`
    const hours = Math.floor(value / 60)
    const rest = value % 60
    return rest ? `约 ${hours} 小时 ${rest} 分钟` : `约 ${hours} 小时`
  },

  switchTab(e) { this.setData({ activeTab: e.currentTarget.dataset.tab }) },
  selectPackage(e) { this.setData({ selectedPackageId: e.currentTarget.dataset.id }) },

  onCheckedChange(e) {
    const selectedItemIDs = e.detail.value
    const selected = new Set(selectedItemIDs)
    const departments = this.data.departments.map(department => ({
      ...department,
      projects: department.projects.map(project => ({ ...project, selected: selected.has(project.id) }))
    }))
    this.setData({ selectedItemIDs, departments })
  },

  continueSelection() {
    if (this.data.activeTab === 'package') {
      if (!this.data.selectedPackageId) return wx.showToast({ title: '请选择一个套餐', icon: 'none' })
      app.globalData.currentPackageId = this.data.selectedPackageId
      app.globalData.selectedItemIDs = []
    } else {
      if (!this.data.selectedItemIDs.length) return wx.showToast({ title: '请至少选择一个项目', icon: 'none' })
      app.globalData.currentPackageId = null
      app.globalData.selectedItemIDs = this.data.selectedItemIDs
    }
    wx.navigateTo({ url: '/pages/package-detail/package-detail' })
  },

  changeHospital() { wx.navigateBack({ delta: 1 }) },
  goBack() { wx.navigateBack({ delta: 1 }) }
})
