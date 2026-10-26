param([string]$Pptx, [string]$OutDir, [string]$Pdf = "")
# Render a .pptx with PowerPoint itself: one PNG per slide, and optionally a PDF.
$ErrorActionPreference = "Stop"
$Pptx = (Resolve-Path $Pptx).Path
New-Item -ItemType Directory -Force $OutDir | Out-Null
$OutDir = (Resolve-Path $OutDir).Path
$app = New-Object -ComObject PowerPoint.Application
try {
  $pres = $app.Presentations.Open($Pptx, -1, 0, 0)   # read-only, no window
  $n = $pres.Slides.Count
  for ($i = 1; $i -le $n; $i++) {
    $pres.Slides.Item($i).Export((Join-Path $OutDir ("slide{0}.png" -f $i)), "PNG", 1920, 1080)
  }
  if ($Pdf) { $pres.SaveAs($Pdf, 32) }                 # ppSaveAsPDF
  $pres.Close()
  "rendered $n slides"
} finally {
  $app.Quit()
  [System.Runtime.InteropServices.Marshal]::ReleaseComObject($app) | Out-Null
}
