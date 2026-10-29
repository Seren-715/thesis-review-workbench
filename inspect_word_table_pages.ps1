[CmdletBinding()]
param(
 [Parameter(Mandatory=$true)][string]$InputPath,
 [Parameter(Mandatory=$true)][string]$AuditPath,
 [Parameter(Mandatory=$true)][string]$PdfPath,
 [Parameter(Mandatory=$true)][string]$Caption,
 [ValidateSet(0,7)][int]$ExportItem=0
)
$ErrorActionPreference='Stop'
function Release-Com($value) {
 if ($null -ne $value -and [Runtime.InteropServices.Marshal]::IsComObject($value)) {
  [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($value)
 }
}
function File-Evidence([string]$path) {
 $file=Get-Item -LiteralPath $path
 $stream=$null;$hasher=$null
 try {
  $share=[IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
  $stream=[IO.File]::Open($file.FullName,[IO.FileMode]::Open,[IO.FileAccess]::Read,$share)
  $hasher=[Security.Cryptography.SHA256]::Create()
  $sizeBefore=[long]$stream.Length
  $hash=([BitConverter]::ToString($hasher.ComputeHash($stream))).Replace('-','').ToLowerInvariant()
  if([long]$stream.Length -ne $sizeBefore){throw 'File length changed while hashing'}
  return [ordered]@{path=$file.FullName;sha256=$hash;size_bytes=$sizeBefore}
 } finally {
  if($null -ne $hasher){$hasher.Dispose()}
  if($null -ne $stream){$stream.Dispose()}
 }
}
function Get-LayoutIdentitySnapshot($document) {
 $fields=$null;$tables=$null;$body=$null;$rows=@();$fieldRows=@()
 try {
  $fields=$document.Fields
  for($i=1;$i -le $fields.Count;$i++) {
   $field=$null;$code=$null;$result=$null
   try {
    $field=$fields.Item($i);$code=$field.Code;$result=$field.Result
    $fieldRows+= [ordered]@{code=[string]$code.Text;result=[string]$result.Text;locked=[bool]$field.Locked;start=[int]$result.Start}
   } finally {Release-Com $result;Release-Com $code;Release-Com $field}
  }
  $tables=$document.Tables;$body=$document.Content
  for($i=1;$i -le [Math]::Min(2,$tables.Count);$i++) {
   $table=$null;$range=$null;$prefix=$null;$start=$null;$end=$null
   try {
    $table=$tables.Item($i);$range=$table.Range;$prefix=$body.Duplicate
    $prefix.SetRange([Math]::Max([int]$body.Start,([int]$range.Start-160)),[int]$range.Start)
    $start=$range.Duplicate;$end=$range.Duplicate
    $start.SetRange([int]$range.Start,[int]$range.Start);$end.SetRange(([int]$range.End-1),([int]$range.End-1))
    $rows+= [ordered]@{index=$i;preceding_text=[string]$prefix.Text;table_text=[string]$range.Text;
     first_page=[int]$start.Information(3);last_page=[int]$end.Information(3)}
   } finally {Release-Com $end;Release-Com $start;Release-Com $prefix;Release-Com $range;Release-Com $table}
  }
  return [ordered]@{fields=$fieldRows;first_two_tables=$rows}
 } finally {Release-Com $body;Release-Com $tables;Release-Com $fields}
}
foreach($path in @($InputPath,$AuditPath,$PdfPath)) {
 if (-not [IO.Path]::IsPathRooted($path)) {throw 'Absolute paths required'}
}
$InputPath=[IO.Path]::GetFullPath($InputPath)
$AuditPath=[IO.Path]::GetFullPath($AuditPath)
$PdfPath=[IO.Path]::GetFullPath($PdfPath)
if (@(@($InputPath,$AuditPath,$PdfPath) | Sort-Object -Unique).Count -ne 3) {throw 'Paths must be distinct'}
foreach($path in @($AuditPath,$PdfPath)) {if(Test-Path -LiteralPath $path){throw 'Output already exists; no overwrite'}}
$audit=[ordered]@{status='started';stage='prepared';source=$null;source_after=$null;source_unchanged=$false;
 word_version=$null;word_build=$null;active_printer=$null;view_type=$null;show_revisions_and_comments=$null;revisions_view=$null;caption=$Caption;caption_matches=@();table=$null;
 page_texts=@();pdf=$null;layout_after_export=$null;layout_changed_during_export=$null;errors=@();cleanup_errors=@();layout_scope='Word current-machine readonly print-layout repagination; PDF exports document content without markup. No field update or source save.';
 visual_verified=$false;llm_requests=0;export_item=$ExportItem;export_mode=$(if($ExportItem -eq 7){'document_with_markup'}else{'document_content_without_markup'})}
$word=$null;$doc=$null;$documents=$null;$options=$null;$content=$null;$tables=$null;$oldLinks=$null
try {
 $audit.source=File-Evidence $InputPath
 $word=New-Object -ComObject Word.Application
 $word.Visible=$false;$word.DisplayAlerts=0;$word.AutomationSecurity=3
 $options=$word.Options;$oldLinks=$options.UpdateLinksAtOpen;$options.UpdateLinksAtOpen=$false
 $audit.word_version=[string]$word.Version;$audit.word_build=[string]$word.Build
 $audit.active_printer=[string]$word.ActivePrinter
 $documents=$word.Documents;$audit.stage='open_readonly'
 $doc=$documents.Open($InputPath,$false,$true,$false)
 $window=$null;$view=$null
 try {
  $window=$doc.ActiveWindow;$view=$window.View;$view.Type=3
  $view.ShowRevisionsAndComments=($ExportItem -eq 7)
  $audit.view_type=[int]$view.Type
  $audit.show_revisions_and_comments=[bool]$view.ShowRevisionsAndComments
  $audit.revisions_view=[int]$view.RevisionsView
 } finally {Release-Com $view;Release-Com $window}
 $audit.stage='repaginate'
 [void]$doc.Repaginate()
 $content=$doc.Content
 $findRange=$null;$find=$null
 try {
  $findRange=$content.Duplicate;$find=$findRange.Find
  $find.ClearFormatting();$find.Text=$Caption;$find.Forward=$true;$find.Wrap=0
  $find.MatchCase=$true;$find.MatchWildcards=$false;$find.MatchWholeWord=$false
  $lastEnd=[int]$content.End
  while($find.Execute()) {
   if($findRange.Tables.Count -gt 0){throw 'Caption is inside a table; manual binding required'}
   $audit.caption_matches+= [ordered]@{start=[int]$findRange.Start;end=[int]$findRange.End;text=[string]$findRange.Text}
   $nextStart=[int]$findRange.End
   if($audit.caption_matches.Count -gt 1 -or $nextStart -ge $lastEnd){break}
   $findRange.SetRange($nextStart,$lastEnd)
  }
 } finally {Release-Com $find;Release-Com $findRange}
 if($audit.caption_matches.Count -ne 1){throw 'Caption must match exactly once in main story; manual table binding needed'}
 $captionEnd=[int]$audit.caption_matches[0].end
 $tables=$doc.Tables;$tableIndex=$null;$tableRange=$null
 try {
  for($i=1;$i -le $tables.Count;$i++) {
   $item=$null;$candidateRange=$null
   try {
    $item=$tables.Item($i);$candidateRange=$item.Range
    if($item.NestingLevel -eq 1 -and [int]$candidateRange.Start -ge $captionEnd) {$tableIndex=$i;$tableRange=$candidateRange.Duplicate;break}
   } finally {Release-Com $candidateRange;Release-Com $item}
  }
  if($null -eq $tableRange){throw 'No table after caption'}
  $gap=$content.Duplicate
  try {
   $gap.SetRange($captionEnd,[int]$tableRange.Start)
   if(([string]$gap.Text -replace '[\s\x07]','').Length -ne 0){throw 'Nonempty text between caption and candidate table; ambiguous binding'}
  } finally {Release-Com $gap}
  $startProbe=$tableRange.Duplicate;$endProbe=$tableRange.Duplicate
  try {
   $startProbe.SetRange([int]$tableRange.Start,[int]$tableRange.Start)
   $endProbe.SetRange(([int]$tableRange.End-1),([int]$tableRange.End-1))
   $firstPage=[int]$startProbe.Information(3);$lastPage=[int]$endProbe.Information(3)
  } finally {Release-Com $startProbe;Release-Com $endProbe}
  if($firstPage -lt 1 -or $lastPage -lt $firstPage -or ($lastPage-$firstPage) -gt 10){throw 'Unexpected/oversized table page range'}
  $audit.table=[ordered]@{index=$tableIndex;range_start=[int]$tableRange.Start;range_end=[int]$tableRange.End;
   text=[string]$tableRange.Text;first_physical_page=$firstPage;last_physical_page=$lastPage;cross_page=($lastPage -gt $firstPage);
   continuation_marker_page_clues=@();result='not_applicable_single_page'}
  $pageCount=[int]$doc.ComputeStatistics(2)
  for($page=$firstPage;$page -le $lastPage;$page++) {
   $gotoWhat=[object][int]1;$gotoWhich=[object][int]1;$gotoCount=[object][int]$page
   $begin=$null;$next=$null;$pageRange=$null
   try {
    $begin=$doc.GoTo([ref]$gotoWhat,[ref]$gotoWhich,[ref]$gotoCount)
    if([int]$begin.Information(3) -ne $page){throw 'GoTo page does not match physical page'}
    $stop=[int]$content.End
    if($page -lt $pageCount) {
     $gotoCount=[object][int]($page+1)
     $next=$doc.GoTo([ref]$gotoWhat,[ref]$gotoWhich,[ref]$gotoCount);$stop=[int]$next.Start
    }
    if($stop -le [int]$begin.Start){throw 'Page boundaries are not increasing'}
    $pageRange=$content.Duplicate;$pageRange.SetRange([int]$begin.Start,$stop)
    $pageText=[string]$pageRange.Text
    $hasMarker=$pageText -match '续\s*表'
    $audit.page_texts+= [ordered]@{physical_page=$page;text=$pageText;contains_any_continuation_marker=$hasMarker}
    if($page -gt $firstPage -and $hasMarker){$audit.table.continuation_marker_page_clues+=$page}
   } finally {Release-Com $pageRange;Release-Com $next;Release-Com $begin}
  }
  if($audit.table.cross_page) {
   $audit.table.result='cross_page_no_continuation_text_clue'
   if($audit.table.continuation_marker_page_clues.Count -gt 0){$audit.table.result='cross_page_marker_present_needs_table_association_review'}
  }
 } finally {Release-Com $tableRange}
 $audit.identity_before_export=Get-LayoutIdentitySnapshot $doc
 $audit.stage='export_pdf'
 $audit.layout_scope='Word readonly print layout; export mode: '+$audit.export_mode+'. No explicit field update or source save; Word may evaluate fields during export.'
 [void]$doc.ExportAsFixedFormat($PdfPath,17,$false,0,0,1,1,$ExportItem)
 $audit.pdf=File-Evidence $PdfPath
 $audit.identity_after_export=Get-LayoutIdentitySnapshot $doc
 $audit.stage='verify_layout_after_pdf_export'
 $postTable=$null;$postRange=$null;$postStart=$null;$postEnd=$null
 try {
  $postTable=$doc.Tables.Item([int]$audit.table.index);$postRange=$postTable.Range
  $postStart=$postRange.Duplicate;$postEnd=$postRange.Duplicate
  $postStart.SetRange([int]$postRange.Start,[int]$postRange.Start)
  $postEnd.SetRange(([int]$postRange.End-1),([int]$postRange.End-1))
  $audit.layout_after_export=[ordered]@{
   first_physical_page=[int]$postStart.Information(3);last_physical_page=[int]$postEnd.Information(3);
   table_text=[string]$postRange.Text}
  $audit.layout_changed_during_export=(
   $audit.layout_after_export.first_physical_page -ne $audit.table.first_physical_page -or
   $audit.layout_after_export.last_physical_page -ne $audit.table.last_physical_page -or
   $audit.layout_after_export.table_text -cne $audit.table.text)
 } finally {Release-Com $postEnd;Release-Com $postStart;Release-Com $postRange;Release-Com $postTable}
 if($audit.layout_changed_during_export){throw 'Word table layout changed during PDF export; PDF cannot confirm pre-export pagination'}
 $audit.status='layout_evidence_collected_visual_pending'
} catch {$audit.errors+=($_.Exception.Message);$audit.status='failed'}
finally {
 if($null -ne $doc) {
  try {$saveChanges=[object][int]0;[void]$doc.Close([ref]$saveChanges)} catch {$audit.cleanup_errors+=('Document.Close: '+$_.Exception.Message)}
  try {Release-Com $doc} catch {$audit.cleanup_errors+=$_.Exception.Message}
 }
 try {Release-Com $tables;Release-Com $content} catch {$audit.cleanup_errors+=$_.Exception.Message}
 if($null -ne $options -and $null -ne $oldLinks){try{$options.UpdateLinksAtOpen=$oldLinks}catch{$audit.cleanup_errors+=$_.Exception.Message}}
 if($null -ne $word){try{$saveChanges=[object][int]0;[void]$word.Quit([ref]$saveChanges)}catch{$audit.cleanup_errors+=('Application.Quit: '+$_.Exception.Message)}}
 foreach($value in @($documents,$options,$word)){try{Release-Com $value}catch{$audit.cleanup_errors+=$_.Exception.Message}}
 try{$audit.source_after=File-Evidence $InputPath;$audit.source_unchanged=$audit.source.sha256 -ceq $audit.source_after.sha256}catch{$audit.errors+=$_.Exception.Message}
 if(-not $audit.source_unchanged -or $audit.cleanup_errors.Count -gt 0){$audit.status='failed'}
 $audit.finished_at=[DateTimeOffset]::Now.ToString('o')
 $json=ConvertTo-Json -InputObject $audit -Depth 20
 $stream=New-Object IO.FileStream($AuditPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
 $writer=New-Object IO.StreamWriter($stream,(New-Object Text.UTF8Encoding($true)))
 try{$writer.WriteLine($json)}finally{$writer.Dispose()}
}
Write-Output ('Layout status: '+$audit.status)
if($audit.status -eq 'failed'){exit 1}
exit 0
