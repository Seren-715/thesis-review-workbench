<# Read-only Word body snapshot: original DOC versus already-derived DOCX.
No Save/SaveAs, field updates, embedded activation or process killing. #>
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$SourcePath,
      [Parameter(Mandatory=$true)][string]$DerivedPath,
      [Parameter(Mandatory=$true)][string]$ResultPath)
$ErrorActionPreference = 'Stop'
function Release-Com([object]$item) {
    if ($null -ne $item -and [Runtime.InteropServices.Marshal]::IsComObject($item)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($item)
    }
}
function Get-Digest([string]$path) {
    $stream=$null; $hash=$null
    try {
        $stream=[IO.File]::OpenRead($path);$hash=[Security.Cryptography.SHA256]::Create()
        return ([BitConverter]::ToString($hash.ComputeHash($stream))).Replace('-','').ToLowerInvariant()
    } finally {if ($null -ne $stream) {$stream.Dispose()};if ($null -ne $hash) {$hash.Dispose()}}
}
function Get-TextDigest([string]$text) {
    $hash=[Security.Cryptography.SHA256]::Create()
    try {return ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($text)))).Replace('-','').ToLowerInvariant()}
    finally {$hash.Dispose()}
}
function Read-Snapshot([object]$documents,[string]$path,[bool]$collectParagraphs) {
    $doc=$null;$content=$null;$collection=$null;$footnotes=$null;$endnotes=$null
    try {
        $doc=$documents.Open($path,$false,$true,$false)
        $content=$doc.Content
        $body=[string]$content.Text
        $rows=New-Object 'System.Collections.Generic.List[object]'
        if ($collectParagraphs) {
            $collection=$content.Paragraphs
            if ($collection.Count -gt 20000) {throw 'Paragraph count exceeds bounded snapshot limit.'}
            for ($index=1;$index -le $collection.Count;$index++) {
                $paragraph=$null;$range=$null;$style=$null
                try {
                    $paragraph=$collection.Item($index);$range=$paragraph.Range;$style=$range.Style
                    $level=[int]$paragraph.OutlineLevel
                    $heading=$null;if ($level -ge 1 -and $level -le 9) {$heading=$level}
                    $rows.Add([ordered]@{index=$index-1;raw_text=[string]$range.Text;
                        range_start=[int]$range.Start;range_end=[int]$range.End;
                        heading_level=$heading;style_name=[string]$style.NameLocal;
                        in_table=[bool]$range.Information(12)})
                } finally {Release-Com $style;Release-Com $range;Release-Com $paragraph}
            }
        }
        $noteRows=New-Object 'System.Collections.Generic.List[object]'
        $footnotes=$doc.Footnotes;$endnotes=$doc.Endnotes
        foreach ($noteKind in @('footnote','endnote')) {
            $noteCollection=$footnotes;if ($noteKind -eq 'endnote') {$noteCollection=$endnotes}
            for ($noteIndex=1;$noteIndex -le $noteCollection.Count;$noteIndex++) {
                $note=$null;$noteRange=$null;$referenceRange=$null
                try {
                    $note=$noteCollection.Item($noteIndex);$noteRange=$note.Range;$referenceRange=$note.Reference
                    $noteRows.Add([ordered]@{kind=$noteKind;index=$noteIndex;
                        reference_text=[string]$referenceRange.Text;reference_start=[int]$referenceRange.Start;
                        reference_end=[int]$referenceRange.End;note_text=[string]$noteRange.Text})
                } finally {Release-Com $referenceRange;Release-Com $noteRange;Release-Com $note}
            }
        }
        return [ordered]@{path=$path;sha256=(Get-Digest $path);raw_body_text=$body;
            body_utf8_sha256=(Get-TextDigest $body);paragraphs=$rows.ToArray();
            footnote_count=[int]$footnotes.Count;endnote_count=[int]$endnotes.Count;notes=$noteRows.ToArray();
            comment_count=[int]$doc.Comments.Count;read_only=[bool]$doc.ReadOnly}
    } finally {
        Release-Com $endnotes;Release-Com $footnotes;Release-Com $collection;Release-Com $content
        if ($null -ne $doc) {try {$noSave=[object][int]0;[void]$doc.Close([ref]$noSave)} finally {Release-Com $doc}}
    }
}
foreach ($path in @($SourcePath,$DerivedPath,$ResultPath)) {
    if (-not [IO.Path]::IsPathRooted($path)) {throw 'Snapshot paths must be absolute.'}
}
if (Test-Path -LiteralPath $ResultPath) {throw 'Result exists; no audit overwrite.'}
$sourceBefore=Get-Digest $SourcePath;$derivedBefore=Get-Digest $DerivedPath
$report=[ordered]@{schema_version=1;status='started';source=$null;derived=$null;source_sha256_unchanged=$false;
    derived_sha256_unchanged=$false;errors=@();cleanup_errors=@();word_launches=0;llm_requests=0;svn_requests=0;
    extraction_policy='Word Content.Range.Text read only; paragraph OutlineLevel and range evidence, without field updates or layout assertions.'}
$word=$null;$documents=$null;$options=$null;$oldLinks=$null;$oldSecurity=$null
try {
    $word=New-Object -ComObject Word.Application;$report.word_launches=1
    $word.Visible=$false;$word.DisplayAlerts=0;$oldSecurity=$word.AutomationSecurity;$word.AutomationSecurity=3
    $options=$word.Options;$oldLinks=$options.UpdateLinksAtOpen;$options.UpdateLinksAtOpen=$false
    $documents=$word.Documents
    $report.source=Read-Snapshot $documents $SourcePath $true
    $report.derived=Read-Snapshot $documents $DerivedPath $false
    $report.status='read_only_snapshot_collected'
} catch {$report.errors += $_.Exception.Message;$report.status='failed'}
finally {
    if ($null -ne $options -and $null -ne $oldLinks) {try {$options.UpdateLinksAtOpen=$oldLinks} catch {$report.cleanup_errors += $_.Exception.Message}}
    if ($null -ne $word) {
        try {if ($null -ne $oldSecurity) {$word.AutomationSecurity=$oldSecurity};$noSave=[object][int]0;[void]$word.Quit([ref]$noSave)}
        catch {$report.cleanup_errors += $_.Exception.Message}
    }
    foreach ($item in @($documents,$options,$word)) {try {Release-Com $item} catch {$report.cleanup_errors += $_.Exception.Message}}
    $report.source_sha256_unchanged=$sourceBefore -ceq (Get-Digest $SourcePath)
    $report.derived_sha256_unchanged=$derivedBefore -ceq (Get-Digest $DerivedPath)
    if (-not $report.source_sha256_unchanged -or -not $report.derived_sha256_unchanged -or $report.cleanup_errors.Count) {$report.status='failed'}
    $json=ConvertTo-Json -InputObject $report -Depth 12
    $stream=New-Object IO.FileStream($ResultPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
    $writer=New-Object IO.StreamWriter($stream,(New-Object Text.UTF8Encoding($true)))
    try {$writer.WriteLine($json)} finally {$writer.Dispose()}
}
Write-Output $report.status
if ($report.status -ne 'read_only_snapshot_collected') {exit 1}
