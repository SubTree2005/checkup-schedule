function rawReport(step = {}) {
  return step.report || step.examReport || step.reportData || null
}

function reportIsReady(step = {}) {
  const report = rawReport(step)
  const status = String(step.reportStatus || (report && report.status) || '').toLowerCase()
  const readyStatus = ['ready', 'issued', 'published', 'completed', '已出报告', '已发布']
  if (step.reportAvailable === true || readyStatus.includes(status)) return true
  if (!report || typeof report !== 'object') return false
  return !!(
    report.conclusion || report.summary || report.result || report.reportTime || report.reportedAt
    || (Array.isArray(report.items) && report.items.length)
    || (Array.isArray(report.results) && report.results.length)
  )
}

function resultRows(report) {
  const rows = report && (report.items || report.results)
  if (!Array.isArray(rows)) return []
  return rows.map((item, index) => ({
    id: item.id || item.itemID || `result-${index}`,
    label: item.label || item.name || item.itemName || '检查结果',
    value: item.value === undefined || item.value === null ? '' : String(item.value),
    unit: item.unit || '',
    reference: item.referenceRange || item.reference || '',
    status: item.status || item.resultStatus || '',
    ...resultStatus(item.status || item.resultStatus || '')
  }))
}

function resultStatus(status) {
  const value = String(status).toLowerCase()
  const labels = { normal: '正常', high: '偏高', h: '偏高', low: '偏低', l: '偏低', abnormal: '异常', positive: '阳性', negative: '阴性' }
  return {
    statusText: labels[value] || status,
    statusTone: ['high', 'h', 'low', 'l', 'abnormal', 'positive', '偏高', '偏低', '异常', '阳性', '↑', '↓'].includes(value) ? 'abnormal' : 'neutral'
  }
}

function planReportIndicators(plan = {}) {
  return (plan.steps || []).reduce((all, step) => {
    const report = normalizeReport(step)
    if (!report.available) return all
    return all.concat(report.rows.map((row, resultIndex) => ({
      ...row,
      key: `${step.detailID}:${resultIndex}`,
      detailID: step.detailID,
      projectTitle: step.title,
      resultIndex
    })))
  }, [])
}

function normalizeReport(step = {}) {
  const report = rawReport(step) || {}
  const available = reportIsReady(step)
  return {
    available,
    conclusion: report.conclusion || report.summary || report.result || step.reportSummary || '',
    reportedAt: report.reportedAt || report.reportTime || step.reportedAt || '',
    rows: resultRows(report)
  }
}

function stepStatus(step = {}) {
  if (reportIsReady(step)) return { text: '已出报告', tone: 'reported' }
  if (step.status === 'done' || step.completed) return { text: '未出报告', tone: 'done' }
  if (step.status === 'active') return { text: '当前', tone: 'active' }
  if (step.status === 'skipped') return { text: '未完成', tone: 'pending' }
  return { text: '未完成', tone: 'pending' }
}

function planReportSummary(plan = {}) {
  const steps = Array.isArray(plan.steps) ? plan.steps : []
  const available = steps.filter(reportIsReady)
  const timestamps = available
    .map(step => normalizeReport(step).reportedAt)
    .filter(Boolean)
    .map(value => new Date(value).getTime())
    .filter(Number.isFinite)
  return {
    count: available.length,
    total: steps.length,
    hasReport: available.length > 0,
    latestAt: timestamps.length ? Math.max.apply(null, timestamps) : 0
  }
}

module.exports = { normalizeReport, planReportIndicators, planReportSummary, reportIsReady, stepStatus }
