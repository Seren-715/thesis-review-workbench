<#
Read-only legacy DOC -> derived DOCX with comment fidelity audit.
Only a newly created Word automation instance is closed. No macro execution,
revision acceptance, comment deletion, OLE activation or process killing.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InputPath,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [Parameter(Mandatory = $true)][string]$AuditPath
)

$ErrorActionPreference = 'Stop'

function Release-OwnCom([object]$Value) {
    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}

function Get-FileEvidence([string]$LiteralPath) {
    $info = Get-Item -LiteralPath $LiteralPath
    # A Windows PowerShell subprocess can inherit PowerShell 7's PSModulePath.
    # Get-FileHash is then unavailable despite SHA-256 being supported by .NET.
    # Hash the exact file bytes through a disposed stream, with no cmdlet/module
    # dependency and without weakening the before/after provenance audit.
    $fileStream = $null; $hasher = $null
    try {
        $fileStream = [IO.File]::OpenRead($info.FullName)
        $hasher = [Security.Cryptography.SHA256]::Create()
        $sourceDigest = ([BitConverter]::ToString($hasher.ComputeHash($fileStream))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        if ($null -ne $fileStream) { $fileStream.Dispose() }
        if ($null -ne $hasher) { $hasher.Dispose() }
    }
    return [ordered]@{
        path = $info.FullName
        sha256 = $sourceDigest
        size_bytes = [long]$info.Length
    }
}

function Get-NormalizedCommentText([string]$Value) {
    # Comparison only: raw strings remain intact in the audit.
    $valueNormalized = $Value.Replace("`r`n", "`n").Replace("`r", "`n")
    # Word appends one terminal paragraph marker; additional blank paragraphs
    # remain significant evidence and must not all be stripped.
    if ($valueNormalized.EndsWith("`n", [StringComparison]::Ordinal)) {
        return $valueNormalized.Substring(0, $valueNormalized.Length - 1)
    }
    return $valueNormalized
}

function Read-CommentEvidence([object]$Document) {
    $collection = $null
    $rows = New-Object 'System.Collections.Generic.List[object]'
    try {
        $collection = $Document.Comments
        for ($index = 1; $index -le $collection.Count; $index++) {
            $comment = $null; $commentRange = $null; $scopeRange = $null
            try {
                $comment = $collection.Item($index)
                $commentRange = $comment.Range
                $scopeRange = $comment.Scope
                $rows.Add([ordered]@{
                    word_index = $index
                    author = [string]$comment.Author
                    initials = [string]$comment.Initial
                    date = ([datetime]$comment.Date).ToString('o')
                    range_text = [string]$commentRange.Text
                    scope_text = [string]$scopeRange.Text
                    range_start = [int]$commentRange.Start
                    range_end = [int]$commentRange.End
                    scope_start = [int]$scopeRange.Start
                    scope_end = [int]$scopeRange.End
                })
            }
            finally {
                Release-OwnCom $scopeRange
                Release-OwnCom $commentRange
                Release-OwnCom $comment
            }
        }
    }
    finally { Release-OwnCom $collection }
    return $rows.ToArray()
}

function Get-CommentMultiset([object[]]$Rows) {
    $result = New-Object 'System.Collections.Generic.Dictionary[string,int]' ([StringComparer]::Ordinal)
    foreach ($row in $Rows) {
        $key = ConvertTo-Json -InputObject @(
            [string]$row.author,
            (Get-NormalizedCommentText ([string]$row.range_text)),
            (Get-NormalizedCommentText ([string]$row.scope_text))
        ) -Compress
        if ($result.ContainsKey($key)) { $result[$key]++ }
        else { $result.Add($key, 1) }
    }
    return ,$result
}

function Test-CommentMultiset([object[]]$Before, [object[]]$After) {
    if ($Before.Count -ne $After.Count) { return $false }
    $left = Get-CommentMultiset $Before
    $right = Get-CommentMultiset $After
    if ($left.Count -ne $right.Count) { return $false }
    foreach ($key in $left.Keys) {
        if (-not $right.ContainsKey($key) -or $left[$key] -ne $right[$key]) { return $false }
    }
    return $true
}

function Test-CommentSequence([object[]]$Before, [object[]]$After) {
    if ($Before.Count -ne $After.Count) { return $false }
    for ($index = 0; $index -lt $Before.Count; $index++) {
        foreach ($property in @('author', 'initials', 'date')) {
            if ([string]$Before[$index][$property] -cne [string]$After[$index][$property]) { return $false }
        }
        foreach ($property in @('range_text', 'scope_text')) {
            $beforeText = Get-NormalizedCommentText ([string]$Before[$index][$property])
            $afterText = Get-NormalizedCommentText ([string]$After[$index][$property])
            if ($beforeText -cne $afterText) { return $false }
        }
    }
    return $true
}

function Get-BodyTextHash([object]$Document) {
    $contentRange = $null; $hasher = $null
    try {
        $contentRange = $Document.Content
        $bytes = [Text.Encoding]::UTF8.GetBytes([string]$contentRange.Text)
        $hasher = [Security.Cryptography.SHA256]::Create()
        return ([BitConverter]::ToString($hasher.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        if ($null -ne $hasher) { $hasher.Dispose() }
        Release-OwnCom $contentRange
    }
}

# Validate destinations before producing anything; never overwrite an audit.
foreach ($namedPath in @($InputPath, $OutputPath, $AuditPath)) {
    if ($namedPath -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+\\)') {
        throw 'InputPath, OutputPath and AuditPath must be absolute filesystem paths.'
    }
}
$InputPath = [IO.Path]::GetFullPath($InputPath)
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
$AuditPath = [IO.Path]::GetFullPath($AuditPath)
if ([StringComparer]::OrdinalIgnoreCase.Equals($InputPath, $OutputPath) -or
    [StringComparer]::OrdinalIgnoreCase.Equals($InputPath, $AuditPath) -or
    [StringComparer]::OrdinalIgnoreCase.Equals($OutputPath, $AuditPath)) {
    throw 'Source, derived DOCX and audit paths must be different.'
}
if (-not (Test-Path -LiteralPath $InputPath -PathType Leaf)) { throw 'Source file does not exist.' }
if ([IO.Path]::GetExtension($InputPath) -ine '.doc' -or [IO.Path]::GetExtension($OutputPath) -ine '.docx') {
    throw 'Expected a preserved .doc source and a new .docx destination.'
}
foreach ($destination in @($OutputPath, $AuditPath)) {
    if (Test-Path -LiteralPath $destination) { throw 'Output/audit already exists; choose new filenames.' }
    if (-not (Test-Path -LiteralPath ([IO.Path]::GetDirectoryName($destination)) -PathType Container)) {
        throw 'Output and audit parent directories must already exist.'
    }
}
$previewPath = [IO.Path]::ChangeExtension($OutputPath, '.preview.pdf')
if ([StringComparer]::OrdinalIgnoreCase.Equals($AuditPath, $previewPath)) {
    throw 'AuditPath must differ from the derived preview PDF path.'
}

$audit = [ordered]@{
    schema_version = 1
    status = 'started'
    current_stage = 'prepared'
    created_at = [DateTimeOffset]::Now.ToString('o')
    source = $null
    source_after = $null
    source_sha256_unchanged = $false
    derived = $null
    word_version = $null
    word_build = $null
    comments_before = @()
    comments_after = @()
    comments_before_count = $null
    comments_after_count = $null
    comment_fidelity_verified = $false
    comment_metadata_sequence_verified = $false
    comparison_policy = 'Ordinal multiset of author + comment text + scope text; CRLF/CR normalize to LF, at most one terminal LF paragraph marker removed; raw evidence retained.'
    original_body_text_utf8_sha256 = $null
    derived_body_text_utf8_sha256 = $null
    body_text_hash_equal = $null
    visual_verification = 'not_verified'
    preview = [ordered]@{status = 'not_rendered'; path = $previewPath; sha256 = $null; size_bytes = $null}
    metadata_comparison_scope = 'Author, initials, date, normalized comment/scope text and Word collection order must match. Numeric offsets are recorded but may shift and are not equality criteria. Identical comments with identical metadata cannot be distinguished solely by this check; images, replies, resolution state and formatting remain unverified.'
    limitations = 'Text/comment preservation audit only. Rendering, pagination, font fidelity and image/object semantics remain unverified. No embedded object is activated; original DOC must be retained.'
    errors = @()
    cleanup_errors = @()
    llm_requests = 0
}
$word = $null; $documents = $null; $document = $null; $options = $null
$oldUpdateLinks = $null; $oldAutomationSecurity = $null
$conversionSucceeded = $false
try {
    $audit.source = Get-FileEvidence $InputPath
    # Create a new automation instance; never attach via GetActiveObject.
    $word = New-Object -ComObject Word.Application
    $audit.word_version = [string]$word.Version
    $audit.word_build = [string]$word.Build
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $oldAutomationSecurity = $word.AutomationSecurity
    $word.AutomationSecurity = 3
    $options = $word.Options
    $oldUpdateLinks = $options.UpdateLinksAtOpen
    $options.UpdateLinksAtOpen = $false
    $documents = $word.Documents
    $audit.current_stage = 'open_original'
    $document = $documents.Open($InputPath, $false, $true, $false)
    $audit.comments_before = @(Read-CommentEvidence $document)
    $audit.comments_before_count = $audit.comments_before.Count
    $audit.original_body_text_utf8_sha256 = Get-BodyTextHash $document
    # SaveAs2 to a distinct derived file, without accepting tracked changes.
    $audit.current_stage = 'save_derived'
    [void]$document.SaveAs2($OutputPath, 16)
    $audit.current_stage = 'close_saved_document'
    $saveChanges = [object][int]0
    [void]$document.Close([ref]$saveChanges)
    Release-OwnCom $document
    $document = $null
    $audit.current_stage = 'reopen_derived'
    $document = $documents.Open($OutputPath, $false, $true, $false)
    $audit.current_stage = 'verify_derived'
    $audit.comments_after = @(Read-CommentEvidence $document)
    $audit.comments_after_count = $audit.comments_after.Count
    $audit.derived_body_text_utf8_sha256 = Get-BodyTextHash $document
    $audit.body_text_hash_equal = $audit.original_body_text_utf8_sha256 -ceq $audit.derived_body_text_utf8_sha256
    $audit.comment_fidelity_verified = Test-CommentMultiset $audit.comments_before $audit.comments_after
    if (-not $audit.comment_fidelity_verified) {
        throw 'Comment count, author, full comment text or scope text changed; derived DOCX is retained but must not be enabled.'
    }
    $audit.comment_metadata_sequence_verified = Test-CommentSequence $audit.comments_before $audit.comments_after
    if (-not $audit.comment_metadata_sequence_verified) {
        throw 'Comment metadata or Word collection order changed; derived DOCX is retained but must not be enabled.'
    }
    $conversionSucceeded = $true
    # PDF is optional evidence. Failure here never replaces comment-audit status.
    try {
        if (Test-Path -LiteralPath $previewPath) { throw 'Preview PDF already exists; it was not overwritten.' }
        [void]$document.ExportAsFixedFormat($previewPath, 17)
        $pdfEvidence = Get-FileEvidence $previewPath
        $audit.preview.status = 'rendered_not_visually_verified'
        $audit.preview.sha256 = $pdfEvidence.sha256
        $audit.preview.size_bytes = $pdfEvidence.size_bytes
    }
    catch {
        $audit.preview.status = 'failed_or_skipped'
        $audit.preview['error'] = $_.Exception.Message
    }
}
catch {
    $audit.errors += $_.Exception.Message
}
finally {
    if ($null -ne $document) {
        try {
            $saveChanges = [object][int]0
            [void]$document.Close([ref]$saveChanges)
        }
        catch { $audit.cleanup_errors += ('Document.Close: ' + $_.Exception.Message) }
        try { Release-OwnCom $document }
        catch { $audit.cleanup_errors += $_.Exception.Message }
    }
    if ($null -ne $options) {
        try { if ($null -ne $oldUpdateLinks) { $options.UpdateLinksAtOpen = $oldUpdateLinks } }
        catch { $audit.cleanup_errors += $_.Exception.Message }
    }
    if ($null -ne $word) {
        try { if ($null -ne $oldAutomationSecurity) { $word.AutomationSecurity = $oldAutomationSecurity } }
        catch { $audit.cleanup_errors += $_.Exception.Message }
        try {
            $saveChanges = [object][int]0
            [void]$word.Quit([ref]$saveChanges)
        }
        catch { $audit.cleanup_errors += ('Application.Quit: ' + $_.Exception.Message) }
    }
    foreach ($ownedCom in @($documents, $options, $word)) {
        try { Release-OwnCom $ownedCom }
        catch { $audit.cleanup_errors += $_.Exception.Message }
    }
    try {
        $audit.source_after = Get-FileEvidence $InputPath
        $audit.source_sha256_unchanged = ($null -ne $audit.source -and
            $audit.source.sha256 -ceq $audit.source_after.sha256)
        if (-not $audit.source_sha256_unchanged) { $audit.errors += 'Original source SHA-256 changed or could not be verified.' }
    }
    catch { $audit.errors += $_.Exception.Message }
    if (Test-Path -LiteralPath $OutputPath -PathType Leaf) {
        try { $audit.derived = Get-FileEvidence $OutputPath }
        catch { $audit.errors += $_.Exception.Message }
    }
    $audit.status = 'failed'
    if ($conversionSucceeded -and $audit.source_sha256_unchanged -and $audit.errors.Count -eq 0 -and $audit.cleanup_errors.Count -eq 0) {
        if ($audit.body_text_hash_equal) {
            $audit.status = 'comment_text_fidelity_verified_visual_pending'
        }
        else {
            $audit.status = 'review_required_body_text_changed'
            $audit['review_reason'] = 'Original and derived Word body text hashes differ; inspect before enabling derived DOCX.'
        }
    }
    $audit['finished_at'] = [DateTimeOffset]::Now.ToString('o')
    $auditJson = ConvertTo-Json -InputObject $audit -Depth 20
    # CreateNew enforces no overwrite, including a path created during conversion.
    $auditStream = New-Object IO.FileStream($AuditPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    $auditWriter = $null
    try {
        $auditWriter = New-Object IO.StreamWriter($auditStream, (New-Object Text.UTF8Encoding($true)))
        $auditWriter.WriteLine($auditJson)
    }
    finally {
        if ($null -ne $auditWriter) { $auditWriter.Dispose() }
        else { $auditStream.Dispose() }
    }
}
Write-Output ('Comment audit status: ' + $audit.status)
Write-Output ('Before/after comments: ' + $audit.comments_before_count + '/' + $audit.comments_after_count)
Write-Output ('Audit: ' + $AuditPath)
Write-Output 'Layout and visual fidelity remain unverified; retain the original DOC.'
if ($audit.status -ne 'comment_text_fidelity_verified_visual_pending') { exit 1 }
exit 0
