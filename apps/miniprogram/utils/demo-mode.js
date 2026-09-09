function isDemoUnrestricted(source, now = Date.now()) {
  return !!source && source.demoUnrestricted === true && Date.parse(source.demoUnrestrictedUntil) > now
}

module.exports = { isDemoUnrestricted }
