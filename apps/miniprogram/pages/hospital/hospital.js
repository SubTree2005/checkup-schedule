const api = require('../../utils/api')
const flowGuard = require('../../utils/flow-guard')
const app = getApp()

Page({
  data: {
    hospitals: [],
    visibleHospitals: [],
    hasVisibleHospitals: false,
    query: '',
    loading: true
  },

  async onLoad() {
    if (!flowGuard.requireLogin(app)) return
    try {
      const rows = await api.hospitals.list()
      this.applyHospitals(rows)
    } catch (error) {
      api.showError(error)
    } finally {
      this.setData({ loading: false })
    }
  },

  applyHospitals(rows) {
    const hospitals = (Array.isArray(rows) ? rows : []).map(row => this.normalizeHospital(row))
    this.setData({ hospitals, visibleHospitals: hospitals, hasVisibleHospitals: hospitals.length > 0, loading: false })
  },

  normalizeHospital(row) {
    const fullName = row.name || row.hospitalName || '体检医院'
    const match = fullName.match(/^(.*?)[（(]([^）)]+)[）)]$/)
    const hospitalName = match ? match[1] : fullName
    const defaultCampus = match ? match[2] : '本院区'
    const campuses = (Array.isArray(row.campuses) && row.campuses.length ? row.campuses : [{ id: row.id, name: defaultCampus, available: true }])
      .map(campus => ({
        ...campus,
        displayName: campus.name === fullName ? defaultCampus : campus.name,
        available: campus.available !== false
      }))
    return {
      ...row,
      raw: row,
      id: row.id || row.hospitalID,
      displayName: hospitalName,
      coverUrl: row.coverUrl || row.imageUrl || '../../addpicture/hospital-default.jpg',
      hospitalLevel: row.hospitalLevel || '未定级',
      positioning: row.positioning || '综合医疗机构',
      campuses
    }
  },

  onSearch(e) {
    const query = String(e.detail.value || '').trim().toLowerCase()
    const visibleHospitals = this.data.hospitals.filter(hospital => {
      const names = [hospital.displayName, ...hospital.campuses.map(campus => campus.displayName)]
      return names.some(name => String(name || '').toLowerCase().includes(query))
    })
    this.setData({ query, visibleHospitals, hasVisibleHospitals: visibleHospitals.length > 0 })
  },

  selectCampus(e) {
    const hospitalId = e.currentTarget.dataset.hospitalId
    const campusId = e.currentTarget.dataset.campusId
    const hospital = this.data.hospitals.find(item => item.id === hospitalId)
    const campus = hospital && hospital.campuses.find(item => item.id === campusId)
    if (!hospital || !campus) return
    if (!campus.available) return wx.showToast({ title: '该院区暂未开放', icon: 'none' })

    app.globalData.selectedHospitalId = campus.hospitalID || campus.id || hospital.id
    app.globalData.selectedHospital = hospital.raw
    app.globalData.selectedCampusId = campus.id
    app.globalData.selectedCampus = campus
    app.globalData.catalog = null
    app.globalData.currentPackageId = null
    app.globalData.selectedItemIDs = []
    wx.navigateTo({ url: '/pages/package/package' })
  },

  goBack() { wx.navigateBack({ delta: 1 }) }
})
