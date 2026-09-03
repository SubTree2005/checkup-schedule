param(
    [string]$SourceRoot = (Join-Path $PSScriptRoot "..\..\iconfont-medical-icons"),
    [string]$OutputRoot = (Join-Path $PSScriptRoot "..\apps\miniprogram\addpicture\icons\iconfont")
)

$assets = [ordered]@{
    "location.svg"           = @{ Source = "01-location.svg"; Color = "#2F69EC" }
    "reroute.svg"            = @{ Source = "02-reroute.svg"; Color = "#2F69EC" }
    "queue-people.svg"       = @{ Source = "03-queue-people.svg"; Color = "#2F69EC" }
    "clock.svg"              = @{ Source = "04-clock.svg"; Color = "#2F69EC" }
    "clock-art.svg"          = @{ Source = "04-clock.svg"; Color = "#7EA6F6" }
    "direction.svg"          = @{ Source = "05-navigation-turn.svg"; Color = "#2F69EC" }
    "direction-straight.svg" = @{ Source = "05a-navigation-straight.svg"; Color = "#2F69EC" }
    "direction-left.svg"     = @{ Source = "05b-navigation-left.svg"; Color = "#2F69EC" }
    "direction-right.svg"    = @{ Source = "05c-navigation-right.svg"; Color = "#2F69EC" }
    "direction-uturn.svg"    = @{ Source = "05d-navigation-uturn.svg"; Color = "#2F69EC" }
    "bell.svg"               = @{ Source = "06-bell.svg"; Color = "#2F69EC" }
    "bell-alert.svg"         = @{ Source = "06-bell.svg"; Color = "#D94C58" }
    "calendar.svg"           = @{ Source = "07-calendar.svg"; Color = "#26A269" }
    "stomach.svg"            = @{ Source = "08-stomach.svg"; Color = "#E79516" }
    "id-card.svg"            = @{ Source = "09-id-card.svg"; Color = "#2F69EC" }
    "water-cup.svg"          = @{ Source = "10-water-cup.svg"; Color = "#2388E8" }
    "capsule.svg"            = @{ Source = "11-capsule.svg"; Color = "#2AA568" }
    "loose-shirt.svg"        = @{ Source = "12-loose-shirt.svg"; Color = "#7659D6" }
    "wechat.svg"             = @{ Source = "13-wechat.svg"; Color = "#20A760" }
    "calendar-plus.svg"      = @{ Source = "14-calendar-plus.svg"; Color = "#6B62D9" }
    "add-exam.svg"           = @{ Source = "14-calendar-plus.svg"; Color = "#FFFFFF" }
    "login-phone.svg"        = @{ Source = "15-phone.svg"; Color = "#526178" }
    "login-password.svg"     = @{ Source = "16-password-lock.svg"; Color = "#526178" }
    "login-visible.svg"      = @{ Source = "17-password-visible.svg"; Color = "#526178" }
    "login-hidden.svg"       = @{ Source = "18-password-hidden.svg"; Color = "#526178" }
}

$sourceDirectory = [System.IO.Path]::GetFullPath($SourceRoot)
$outputDirectory = [System.IO.Path]::GetFullPath($OutputRoot)
[System.IO.Directory]::CreateDirectory($outputDirectory) | Out-Null

foreach ($asset in $assets.GetEnumerator()) {
    $sourcePath = Join-Path $sourceDirectory $asset.Value.Source
    if (-not (Test-Path -LiteralPath $sourcePath)) {
        throw "Missing source icon: $sourcePath"
    }

    $svg = [System.IO.File]::ReadAllText($sourcePath)
    if (-not $svg.Contains("currentColor")) {
        throw "Source icon is not color-normalized: $sourcePath"
    }

    $svg = $svg.Replace("currentColor", $asset.Value.Color)
    [System.IO.File]::WriteAllText(
        (Join-Path $outputDirectory $asset.Key),
        $svg,
        [System.Text.UTF8Encoding]::new($false)
    )
}

Write-Output ("Prepared {0} SVG assets in {1}" -f $assets.Count, $outputDirectory)
