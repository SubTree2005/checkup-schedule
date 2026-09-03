function redirect(url) {
  wx.redirectTo({ url })
}

function requireLogin(app) {
  if (wx.getStorageSync('patientToken')) return true
  if (app && typeof app.clearLoginState === 'function') app.clearLoginState()
  redirect('/pages/login/login')
  return false
}

function requireHospital(app) {
  if (!requireLogin(app)) return false
  if (app.globalData.selectedHospitalId) return true
  redirect('/pages/hospital/hospital')
  return false
}

function hasSelection(app) {
  return !!app.globalData.currentPackageId
    || (Array.isArray(app.globalData.selectedItemIDs) && app.globalData.selectedItemIDs.length > 0)
}

function requireSelection(app) {
  if (!requireHospital(app)) return false
  if (hasSelection(app)) return true
  redirect('/pages/package/package')
  return false
}

function requireAppointment(app) {
  if (!requireSelection(app)) return false
  const draft = app.globalData.appointmentDraft
  if (draft && draft.appointmentAt) return true
  redirect('/pages/appointment-time/appointment-time')
  return false
}

module.exports = {
  hasSelection,
  requireAppointment,
  requireHospital,
  requireLogin,
  requireSelection
}
