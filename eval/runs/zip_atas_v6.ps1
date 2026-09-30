param([switch]$Markdown)
$ErrorActionPreference = 'Stop'
$taskRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$taskRaw = [System.IO.Path]::GetFullPath((Join-Path $taskRoot 'data/raw/v6'))
$taskDownloads = 'C:\Users\REVY\Downloads'
$taskStem = if ($Markdown) { 'atas_conversacional-v6-md' } else { 'atas_conversacional-v6' }
$taskDestination = Join-Path $taskDownloads ($taskStem + '.zip')
$taskTemporary = Join-Path $taskDownloads ($taskStem + '.partial-' + (Get-Date -Format 'yyyy-MM-dd_HH-mm-ss') + '.zip')
foreach ($taskTarget in @($taskDestination, $taskTemporary)) {
    if (-not [System.IO.Path]::GetFullPath($taskTarget).StartsWith($taskDownloads + '\')) { throw 'ZIP outside Downloads refused' }
    if (Test-Path -LiteralPath $taskTarget) { throw ('Existing delivery will not be overwritten: ' + $taskTarget) }
}
$taskInventory = Get-Content -LiteralPath (Join-Path $taskRoot 'data/inventory/v6.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$taskRecords = @($taskInventory.arquivos | Where-Object { $_.status -eq 'ok' -and $_.base_id -eq 'atas_conversacional' -and $_.tipo -eq 'pdf' })
if ($Markdown) {
    $taskExport = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'atas-v6-md-export.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($taskExport.markdown_files -ne $taskRecords.Count) { throw 'Markdown count differs from PDF ZIP' }
    $taskRecords = @($taskExport.records)
    $taskRaw = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'atas-v6-md'))
}
if ($taskRecords.Count -eq 0) { throw 'No ata PDFs found in inventory' }
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$taskExpected = @{}
$taskArchive = [System.IO.Compression.ZipFile]::Open($taskTemporary, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($taskRecord in $taskRecords) {
        $taskSource = [System.IO.Path]::GetFullPath((Join-Path $taskRoot $taskRecord.source_file))
        if (-not $taskSource.StartsWith($taskRaw + '\')) { throw ('Source outside v6: ' + $taskSource) }
        $taskEntryName = $taskSource.Substring($taskRaw.Length + 1).Replace('\', '/')
        if ($taskExpected.ContainsKey($taskEntryName)) { throw ('Duplicate ZIP entry: ' + $taskEntryName) }
        if ((Get-FileHash -LiteralPath $taskSource -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskRecord.sha256) { throw ('Inventory hash mismatch: ' + $taskEntryName) }
        $taskExpected[$taskEntryName] = $taskRecord.sha256
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($taskArchive, $taskSource, $taskEntryName, [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
    }
} finally { $taskArchive.Dispose() }
$taskArchive = [System.IO.Compression.ZipFile]::OpenRead($taskTemporary)
try {
    if ($taskArchive.Entries.Count -ne $taskRecords.Count) { throw 'ZIP entry count mismatch' }
    foreach ($taskEntry in $taskArchive.Entries) {
        $taskSuffix = if ($Markdown) { '.md' } else { '.pdf' }
        if (-not $taskExpected.ContainsKey($taskEntry.FullName) -or -not $taskEntry.FullName.EndsWith($taskSuffix, [System.StringComparison]::OrdinalIgnoreCase)) { throw ('Unexpected ZIP entry: ' + $taskEntry.FullName) }
        $taskStream = $taskEntry.Open()
        $taskHasher = [System.Security.Cryptography.SHA256]::Create()
        try { $taskDigest = [System.BitConverter]::ToString($taskHasher.ComputeHash($taskStream)).Replace('-', '').ToLowerInvariant() }
        finally { $taskStream.Dispose(); $taskHasher.Dispose() }
        if ($taskDigest -ne $taskExpected[$taskEntry.FullName]) { throw ('ZIP integrity mismatch: ' + $taskEntry.FullName) }
    }
} finally { $taskArchive.Dispose() }
Move-Item -LiteralPath $taskTemporary -Destination $taskDestination
$taskReport = [ordered]@{ version = 'v6'; archive = $taskDestination; entries = $taskRecords.Count; format = $(if ($Markdown) { 'md' } else { 'pdf' }); relative_paths_preserved = $true; inventory_hashes_validated = $true; zip_integrity_validated = $true; sha256 = (Get-FileHash -LiteralPath $taskDestination -Algorithm SHA256).Hash.ToLowerInvariant(); bytes = (Get-Item -LiteralPath $taskDestination).Length; compression = 'Windows native System.IO.Compression' }
if (-not $Markdown) { $taskReport['pdf_entries'] = $taskRecords.Count; $taskReport['only_ata_pdfs'] = $true }
$taskReportName = if ($Markdown) { 'zip-atas-v6-md.json' } else { 'zip-atas-v6.json' }
$taskReport | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot $taskReportName) -Encoding UTF8
$taskReport | ConvertTo-Json
