const ICONS = Object.freeze({
  lab: '/addpicture/icons/icon-lab.png',
  home: '/addpicture/icons/icon-home.png',
  record: '/addpicture/icons/icon-record.png',
  user: '/addpicture/icons/icon-user.png',
  phone: '/addpicture/icons/iconfont/login-phone.svg',
  lock: '/addpicture/icons/iconfont/login-password.svg',
  search: '/addpicture/icons/icon-search.png',
  hospital: '/addpicture/icons/icon-hospital.png',
  calendar: '/addpicture/icons/iconfont/calendar.svg',
  clock: '/addpicture/icons/iconfont/clock.svg',
  location: '/addpicture/icons/iconfont/location.svg',
  route: '/addpicture/icons/iconfont/reroute.svg',
  queue: '/addpicture/icons/iconfont/queue-people.svg',
  info: '/addpicture/icons/icon-info.png',
  bell: '/addpicture/icons/iconfont/bell.svg',
  bellAlert: '/addpicture/icons/iconfont/bell-alert.svg',
  wechat: '/addpicture/icons/iconfont/wechat.svg',
  check: '/addpicture/icons/icon-check.png',
  plus: '/addpicture/icons/iconfont/add-exam.svg',
  direction: '/addpicture/icons/iconfont/direction.svg',
  directionStraight: '/addpicture/icons/iconfont/direction-straight.svg',
  directionLeft: '/addpicture/icons/iconfont/direction-left.svg',
  directionRight: '/addpicture/icons/iconfont/direction-right.svg',
  directionUturn: '/addpicture/icons/iconfont/direction-uturn.svg',
  imaging: '/addpicture/icons/icon-imaging.png',
  ultrasound: '/addpicture/icons/icon-ultrasound.png',
  ecg: '/addpicture/icons/icon-ecg.png',
  consultation: '/addpicture/icons/icon-consultation.png',
  eye: '/addpicture/icons/icon-eye.png',
  tooth: '/addpicture/icons/icon-tooth.png',
  stomach: '/addpicture/icons/iconfont/stomach.svg',
  identity: '/addpicture/icons/iconfont/id-card.svg',
  water: '/addpicture/icons/iconfont/water-cup.svg',
  pill: '/addpicture/icons/iconfont/capsule.svg',
  shirt: '/addpicture/icons/iconfont/loose-shirt.svg',
  urine: '/addpicture/icons/icon-urine.png',
  clockArt: '/addpicture/icons/iconfont/clock-art.svg',
  calendarColor: '/addpicture/icons/iconfont/calendar.svg',
  clockColor: '/addpicture/icons/iconfont/clock.svg',
  stomachColor: '/addpicture/icons/iconfont/stomach.svg',
  identityColor: '/addpicture/icons/iconfont/id-card.svg',
  waterColor: '/addpicture/icons/iconfont/water-cup.svg',
  pillColor: '/addpicture/icons/iconfont/capsule.svg',
  shirtColor: '/addpicture/icons/iconfont/loose-shirt.svg',
  wechatColor: '/addpicture/icons/iconfont/wechat.svg',
  bellColor: '/addpicture/icons/iconfont/bell.svg',
  calendarPlus: '/addpicture/icons/iconfont/calendar-plus.svg',
  loginVisible: '/addpicture/icons/iconfont/login-visible.svg',
  loginHidden: '/addpicture/icons/iconfont/login-hidden.svg'
})

function examIcon(name = '') {
  const text = String(name)
  if (/超声|B超|彩超/.test(text)) return ICONS.ultrasound
  if (/心电|心率|动态心电/.test(text)) return ICONS.ecg
  if (/CT|DR|胸片|放射|影像|核磁|X线/.test(text)) return ICONS.imaging
  if (/眼|视力|眼底/.test(text)) return ICONS.eye
  if (/牙|口腔/.test(text)) return ICONS.tooth
  if (/尿|尿液/.test(text)) return ICONS.urine
  if (/血|检验|生化|肝功能|肾功能|便常规/.test(text)) return ICONS.lab
  if (/问诊|内科|外科|一般检查|身高|体重|血压/.test(text)) return ICONS.consultation
  return ICONS.lab
}

module.exports = { ICONS, examIcon }
