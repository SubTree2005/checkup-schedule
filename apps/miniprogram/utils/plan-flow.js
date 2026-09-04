const api = require('./api')
const { ICONS } = require('./icon-map')

function packageItems(catalog, packageId) {
  const pkg = (catalog.packages || []).find(item => (item.id || item.packageID) === packageId)
  if (!pkg) return []
  if (Array.isArray(pkg.items) && pkg.items.length) return pkg.items
  return (pkg.groups || []).reduce((all, group) => all.concat(group.items || []), [])
}

function customItems(catalog, itemIds) {
  const selected = new Set(itemIds || [])
  const items = (catalog.departments || []).reduce((all, department) => {
    return all.concat((department.projects || []).map(item => ({ ...item, department: item.department || department.name })))
  }, catalog.exams || [])
  return items.filter(item => selected.has(item.id || item.itemID))
}

function selectedItems(app) {
  const catalog = app.globalData.catalog || {}
  if (!app.globalData.currentPackageId) return customItems(catalog, app.globalData.selectedItemIDs)
  const items = packageItems(catalog, app.globalData.currentPackageId)
  const selected = new Set(app.globalData.selectedItemIDs || [])
  return selected.size ? items.filter(item => selected.has(item.id || item.itemID)) : items
}

function itemNeedsPreparation(item) {
  const text = `${item.name || item.itemName || ''} ${item.department || ''} ${item.note || ''}`
  return !!item.fastingRequired || /空腹|膀胱|泌尿|前列腺|憋尿/.test(text)
}

function splitSelectedItems(app) {
  const readyItemIDs = []
  const deferredItemIDs = []
  selectedItems(app).forEach(item => {
    const id = item.id || item.itemID
    if (!id) return
    if (itemNeedsPreparation(item)) deferredItemIDs.push(id)
    else readyItemIDs.push(id)
  })
  return { readyItemIDs, deferredItemIDs }
}

function packageNotice(app) {
  const catalog = app.globalData.catalog || {}
  const pkg = (catalog.packages || []).find(item => (item.id || item.packageID) === app.globalData.currentPackageId)
  return pkg && Array.isArray(pkg.notice) ? pkg.notice.join('。') : ''
}

function preparationRequirements(app, compact = false) {
  const items = selectedItems(app)
  const text = `${items.map(item => `${item.name || item.itemName || ''}${item.department || ''}`).join(' ')} ${packageNotice(app)}`
  const rows = []
  if (items.some(item => item.fastingRequired) || /空腹/.test(text)) {
    rows.push({ key: 'fasting', iconPath: ICONS.stomachColor, title: '空腹', detail: '体检前 8–12 小时禁止进食，可少量饮水。' })
  }
  rows.push({ key: 'identity', iconPath: ICONS.identityColor, title: '携带身份证件', detail: '体检当日请携带本人有效身份证件。' })
  if (/膀胱|泌尿|前列腺|憋尿/.test(text)) {
    rows.push({ key: 'water', iconPath: ICONS.waterColor, title: '请先饮水并保持憋尿', detail: '体检前 1 小时请饮水约 500ml，并憋尿。' })
  }
  if (!compact && /药|服用|停药/.test(text)) {
    rows.push({ key: 'medicine', iconPath: ICONS.pillColor, title: '停药', detail: '如正在服用相关药物，请遵医嘱确认。' })
  }
  if (!compact) rows.push({ key: 'clothes', iconPath: ICONS.shirtColor, title: '着装', detail: '建议穿着宽松衣物，便于检查。' })
  return rows
}

function selectedPackageName(app) {
  const catalog = app.globalData.catalog || {}
  const pkg = (catalog.packages || []).find(item => (item.id || item.packageID) === app.globalData.currentPackageId)
  return pkg ? (pkg.name || pkg.packageName) : '自选项目'
}

function selectedHospitalName(app) {
  const campus = app.globalData.selectedCampus || {}
  const hospital = app.globalData.selectedHospital || {}
  return campus.fullName || hospital.name || hospital.hospitalName || '体检医院'
}

function profileForPlan(app, updates, includeAppointmentDraft = true) {
  const profile = { ...(app.globalData.profile || {}), ...(updates || {}) }
  if (includeAppointmentDraft && app.globalData.appointmentDraft) {
    profile.appointmentAt = app.globalData.appointmentDraft.appointmentAt
    profile.appointmentDateLabel = app.globalData.appointmentDraft.dateLabel
    profile.appointmentTimeLabel = app.globalData.appointmentDraft.timeLabel
  } else if (!includeAppointmentDraft) {
    delete profile.appointmentAt
    delete profile.appointmentDateLabel
    delete profile.appointmentTimeLabel
  }
  return profile
}

async function createPlanWithSelection(app, updates = {}, selection = null) {
  const profileUpdates = { ...updates }
  const reminderSubscription = profileUpdates.reminderSubscription || null
  delete profileUpdates.reminderSubscription
  const includeAppointmentDraft = !selection || selection.includeAppointmentDraft !== false
  const profile = profileForPlan(app, profileUpdates, includeAppointmentDraft)
  const plan = await api.plans.create({
    hospitalID: app.globalData.selectedHospitalId,
    packageID: selection ? selection.packageID : app.globalData.currentPackageId,
    selectedItemIDs: selection ? selection.selectedItemIDs : (app.globalData.selectedItemIDs || []),
    appointmentAt: includeAppointmentDraft ? (profile.appointmentAt || null) : null,
    reminderSubscription,
    profile
  })
  app.saveProfile(profile)
  if (!selection || selection.saveCurrent !== false) app.saveCurrentPlan(plan)
  return plan
}

function createPlan(app, updates = {}) {
  return createPlanWithSelection(app, updates)
}

function createSameDayPlan(app, updates = {}) {
  const split = splitSelectedItems(app)
  if (!split.deferredItemIDs.length) return createPlan(app, updates)
  if (!split.readyItemIDs.length) {
    return Promise.reject(new Error('所选项目均需完成检前准备，请改为预约后再体检'))
  }
  return createPlanForItems(app, split.readyItemIDs, updates)
}

function createPlanForItems(app, itemIDs, updates = {}, options = {}) {
  return createPlanWithSelection(app, updates, {
    packageID: options.packageID === undefined ? app.globalData.currentPackageId : options.packageID,
    selectedItemIDs: itemIDs,
    includeAppointmentDraft: options.includeAppointmentDraft !== false,
    saveCurrent: options.saveCurrent !== false
  })
}

function requestWeChatPush(ids) {
  if (!ids.length || typeof wx.requestSubscribeMessage !== 'function') return Promise.resolve({ skipped: true })
  return new Promise(resolve => wx.requestSubscribeMessage({ tmplIds: ids, complete: resolve }))
}

function addSystemCalendar(app) {
  const draft = app.globalData.appointmentDraft
  if (!draft || !draft.appointmentAt || typeof wx.addPhoneCalendar !== 'function') return Promise.resolve({ skipped: true })
  const start = Math.floor(new Date(draft.appointmentAt).getTime() / 1000)
  return new Promise(resolve => wx.addPhoneCalendar({
    title: `${selectedPackageName(app)}体检`,
    startTime: start,
    description: `${selectedHospitalName(app)}，${draft.dateLabel} ${draft.timeLabel}`,
    complete: resolve
  }))
}

module.exports = {
  addSystemCalendar,
  createPlan,
  createPlanForItems,
  createSameDayPlan,
  preparationRequirements,
  requestWeChatPush,
  selectedHospitalName,
  selectedPackageName,
  splitSelectedItems
}
