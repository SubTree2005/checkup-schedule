param(
  [string]$InputDirectory = "artifacts\miniprogram-pages"
)

Add-Type -AssemblyName System.Drawing

$root = (Resolve-Path $InputDirectory).Path
$files = Get-ChildItem -LiteralPath $root -Filter "*.png" |
  Where-Object { $_.Name -notlike "contact-sheet-*" } |
  Sort-Object Name

$labels = @{
  "01-login" = "登录"
  "02-register" = "注册"
  "03-terms" = "用户协议"
  "04-privacy" = "隐私政策"
  "05-home" = "首页·暂无体检"
  "06-hospitals" = "选择医院与院区"
  "07-staff" = "工作人员入口"
  "08-mine" = "我的"
  "09-profile-edit" = "个人资料"
  "10-account-security" = "账号与隐私"
  "11-history-empty" = "历史体检·空态"
  "12-record-empty" = "体检·空态"
  "13-campus" = "选择院区"
  "14-packages" = "选择套餐与项目"
  "15-package-detail" = "确认已选项目"
  "16-select-mode" = "选择体检方式"
  "17-appointment-time" = "选择日期与时间"
  "18-preparation-reminder" = "准备与提醒"
  "19-preparation-confirm" = "准备条件确认"
  "20-preparation-arrangement" = "准备条件未满足"
  "21-department-projects" = "按科室自选项目"
  "22-health-profile" = "健康状态信息"
  "23-home-active" = "首页·体检进行中"
  "24-record-multiple" = "体检·多个预约"
  "25-live-plan" = "排队与体检"
  "26-plan-overview" = "体检总览"
  "27-navigation" = "体检导航"
  "28-plan-complete" = "体检完成"
  "29-history" = "历史体检"
  "30-record-detail" = "体检详情"
  "31-exam-detail-empty-report" = "项目详情·暂无报告"
  "32-exam-detail-with-report" = "项目详情·已出报告"
  "33-home-report" = "首页·报告提醒"
  "34-record-history-tab" = "体检·历史体检"
  "35-ai-chat" = "AI 助手·初始会话"
  "36-ai-chat-thinking" = "AI 助手·思考中"
  "37-ai-settings" = "AI 助手设置"
  "38-ai-api-settings" = "AI 助手·API 配置"
}

$columns = 3
$rows = 2
$perSheet = $columns * $rows
$thumbWidth = 300
$thumbHeight = 648
$labelHeight = 58
$gap = 16
$sheetWidth = $gap + $columns * ($thumbWidth + $gap)
$sheetHeight = $gap + $rows * ($labelHeight + $thumbHeight + $gap)
$font = New-Object System.Drawing.Font("Microsoft YaHei UI", 12, [System.Drawing.FontStyle]::Regular)
$labelFont = New-Object System.Drawing.Font("Microsoft YaHei UI", 14, [System.Drawing.FontStyle]::Regular)
$brush = [System.Drawing.Brushes]::Black
$borderPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(218, 224, 232), 2)

for ($sheetIndex = 0; $sheetIndex -lt [math]::Ceiling($files.Count / $perSheet); $sheetIndex++) {
  $bitmap = New-Object System.Drawing.Bitmap($sheetWidth, $sheetHeight)
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  $graphics.Clear([System.Drawing.Color]::White)
  $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
  $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::ClearTypeGridFit

  for ($slot = 0; $slot -lt $perSheet; $slot++) {
    $fileIndex = $sheetIndex * $perSheet + $slot
    if ($fileIndex -ge $files.Count) { break }
    $file = $files[$fileIndex]
    $column = $slot % $columns
    $row = [math]::Floor($slot / $columns)
    $x = $gap + $column * ($thumbWidth + $gap)
    $y = $gap + $row * ($labelHeight + $thumbHeight + $gap)
    $baseName = [System.IO.Path]::GetFileNameWithoutExtension($file.Name)
    $label = if ($labels.ContainsKey($baseName)) { $labels[$baseName] } else { $baseName }
    $graphics.DrawString($baseName, $font, $brush, $x, $y + 2)
    $graphics.DrawString($label, $labelFont, $brush, $x, $y + 25)

    $image = [System.Drawing.Image]::FromFile($file.FullName)
    try {
      $imageY = $y + $labelHeight
      $graphics.DrawImage($image, $x, $imageY, $thumbWidth, $thumbHeight)
      $graphics.DrawRectangle($borderPen, $x, $imageY, $thumbWidth, $thumbHeight)
    }
    finally {
      $image.Dispose()
    }
  }

  $output = Join-Path $root ("contact-sheet-{0:D2}.png" -f ($sheetIndex + 1))
  $bitmap.Save($output, [System.Drawing.Imaging.ImageFormat]::Png)
  $graphics.Dispose()
  $bitmap.Dispose()
}

$font.Dispose()
$labelFont.Dispose()
$borderPen.Dispose()
