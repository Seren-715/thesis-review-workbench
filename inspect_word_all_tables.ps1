[CmdletBinding()]
param(
 [Parameter(Mandatory=$true)][string]$InputPath,
 [Parameter(Mandatory=$true)][string]$AuditPath,
 [Parameter(Mandatory=$true)][string]$PdfPath
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

function Table-Snapshot($document) {
 $collection=$null;$content=$null;$items=@()
 try {
  $collection=$document.Tables;$content=$document.Content
  if($collection.Count -gt 100){throw 'Native table count exceeds pilot limit100'}
  for($i=1;$i -le $collection.Count;$i++) {
   $table=$null;$range=$null;$start=$null;$end=$null;$prefix=$null
   try {
    $table=$collection.Item($i);$range=$table.Range
    $start=$range.Duplicate;$end=$range.Duplicate;$prefix=$content.Duplicate
    $start.SetRange([int]$range.Start,[int]$range.Start)
    $end.SetRange(([int]$range.End-1),([int]$range.End-1))
    $prefix.SetRange([Math]::Max([int]$content.Start,([int]$range.Start-180)),[int]$range.Start)
    $first=[int]$start.Information(3);$last=[int]$end.Information(3)
    if($first -lt 1 -or $last -lt $first -or ($last-$first) -gt 20){throw 'Unexpected table physical page span'}
    $items+= [ordered]@{index=$i;nesting_level=[int]$table.NestingLevel;text=[string]$range.Text;
     range_start=[int]$range.Start;range_end=[int]$range.End;preceding_text=[string]$prefix.Text;
     first_physical_page=$first;last_physical_page=$last;cross_page=($last -gt $first)}
   } finally {Release-Com $prefix;Release-Com $end;Release-Com $start;Release-Com $range;Release-Com $table}
  }
  return $items
 } finally {Release-Com $content;Release-Com $collection}
}
foreach($path in @($InputPath,$AuditPath,$PdfPath)){if(-not [IO.Path]::IsPathRooted($path)){throw 'Absolute paths required'}}
$InputPath=[IO.Path]::GetFullPath($InputPath);$AuditPath=[IO.Path]::GetFullPath($AuditPath);$PdfPath=[IO.Path]::GetFullPath($PdfPath)
if(@(@($InputPath,$AuditPath,$PdfPath) | Sort-Object -Unique).Count -ne 3){throw 'Paths must be distinct'}
foreach($path in @($AuditPath,$PdfPath)){if(Test-Path -LiteralPath $path){throw 'Output exists; no overwrite'}}
$audit=[ordered]@{status='started';stage='prepared';source=$null;source_after=$null;source_unchanged=$false;
 tables_before=@();tables_after=@();table_results=@();page_texts=@();pdf=$null;errors=@();cleanup_errors=@();
 export_item=7;visual_verified=$false;llm_requests=0;layout_changed_during_export=$null;
 limits='Native main-story Word tables only. Pictures, nested/textbox tables, repeated headers and continuation-marker association require visual review. Markup presentation; no explicit field update or source save.'}
$word=$null;$doc=$null;$documents=$null;$options=$null;$content=$null;$oldLinks=$null
try {
 $audit.source=File-Evidence $InputPath
 $word=New-Object -ComObject Word.Application;$word.Visible=$false;$word.DisplayAlerts=0;$word.AutomationSecurity=3
 $options=$word.Options;$oldLinks=$options.UpdateLinksAtOpen;$options.UpdateLinksAtOpen=$false
 $audit.word_version=[string]$word.Version;$audit.word_build=[string]$word.Build;$audit.active_printer=[string]$word.ActivePrinter
 $documents=$word.Documents;$audit.stage='open_readonly';$doc=$documents.Open($InputPath,$false,$true,$false)
 $window=$null;$view=$null
 try {$window=$doc.ActiveWindow;$view=$window.View;$view.Type=3;$view.ShowRevisionsAndComments=$true;
  $audit.view_type=[int]$view.Type;$audit.revisions_view=[int]$view.RevisionsView;$audit.show_revisions_and_comments=[bool]$view.ShowRevisionsAndComments
 } finally {Release-Com $view;Release-Com $window}
 $audit.stage='repaginate';[void]$doc.Repaginate();$content=$doc.Content
 $audit.tables_before=@(Table-Snapshot $doc)
 $audit.word_page_count=[int]$doc.ComputeStatistics(2)
 $needed=@($audit.tables_before | ForEach-Object {for($p=$_.first_physical_page;$p -le $_.last_physical_page;$p++){$p}} | Sort-Object -Unique)
 foreach($page in $needed) {
  $begin=$null;$next=$null;$range=$null
  try {
   $what=[object][int]1;$which=[object][int]1;$count=[object][int]$page
   $begin=$doc.GoTo([ref]$what,[ref]$which,[ref]$count)
   if([int]$begin.Information(3) -ne $page){throw 'GoTo physical page mapping mismatch'}
   $stop=[int]$content.End
   if($page -lt $audit.word_page_count){$count=[object][int]($page+1);$next=$doc.GoTo([ref]$what,[ref]$which,[ref]$count);$stop=[int]$next.Start}
   if($stop -le [int]$begin.Start){throw 'Page boundaries not increasing'}
   $range=$content.Duplicate;$range.SetRange([int]$begin.Start,$stop);$text=[string]$range.Text
   $matches=@([regex]::Matches($text,'续\s*表') | ForEach-Object {[ordered]@{offset=$_.Index;text=$_.Value}})
   $audit.page_texts+= [ordered]@{physical_page=[int]$page;text=$text;continuation_marker_matches=$matches}
  } finally {Release-Com $range;Release-Com $next;Release-Com $begin}
 }
 foreach($table in $audit.tables_before) {
  $markerPages=@($audit.page_texts | Where-Object {$_.physical_page -gt $table.first_physical_page -and $_.physical_page -le $table.last_physical_page -and $_.continuation_marker_matches.Count -gt 0} | ForEach-Object {$_.physical_page})
  $result='not_applicable_single_page'
  if($table.cross_page){$result='cross_page_no_continuation_text_clue';if($markerPages.Count -gt 0){$result='cross_page_marker_needs_table_association_review'}}
  $audit.table_results+= [ordered]@{index=$table.index;first_physical_page=$table.first_physical_page;last_physical_page=$table.last_physical_page;
    result=$result;continuation_marker_page_clues=$markerPages;visual_verified=$false}
 }
 $audit.stage='export_pdf_with_markup';[void]$doc.ExportAsFixedFormat($PdfPath,17,$false,0,0,1,1,7);$audit.pdf=File-Evidence $PdfPath
 $audit.stage='verify_layout_after_export';$audit.tables_after=@(Table-Snapshot $doc)
 $audit.layout_changed_during_export=$audit.tables_before.Count -ne $audit.tables_after.Count
 if(-not $audit.layout_changed_during_export){
  for($i=0;$i -lt $audit.tables_before.Count;$i++) {
   $before=$audit.tables_before[$i];$after=$audit.tables_after[$i]
   foreach($key in @('index','nesting_level','text','preceding_text','first_physical_page','last_physical_page')){
    if($before[$key] -cne $after[$key]){$audit.layout_changed_during_export=$true}
   }
  }
 }
 if($audit.layout_changed_during_export){throw 'Table identity or layout changed during PDF export'}
 $audit.status='native_table_layout_collected_visual_pending'
} catch {$audit.errors+=$_.Exception.Message;$audit.status='failed'}
finally {
 if($null -ne $doc){try{$saveChanges=[object][int]0;[void]$doc.Close([ref]$saveChanges)}catch{$audit.cleanup_errors+=('Document.Close: '+$_.Exception.Message)};try{Release-Com $doc}catch{$audit.cleanup_errors+=$_.Exception.Message}}
 try{Release-Com $content}catch{$audit.cleanup_errors+=$_.Exception.Message}
 if($null -ne $options -and $null -ne $oldLinks){try{$options.UpdateLinksAtOpen=$oldLinks}catch{$audit.cleanup_errors+=$_.Exception.Message}}
 if($null -ne $word){try{$saveChanges=[object][int]0;[void]$word.Quit([ref]$saveChanges)}catch{$audit.cleanup_errors+=('Application.Quit: '+$_.Exception.Message)}}
 foreach($value in @($documents,$options,$word)){try{Release-Com $value}catch{$audit.cleanup_errors+=$_.Exception.Message}}
 try{$audit.source_after=File-Evidence $InputPath;$audit.source_unchanged=$audit.source.sha256 -ceq $audit.source_after.sha256}catch{$audit.errors+=$_.Exception.Message}
 if(-not $audit.source_unchanged -or $audit.cleanup_errors.Count -gt 0){$audit.status='failed'}
 $audit.finished_at=[DateTimeOffset]::Now.ToString('o')
 $stream=New-Object IO.FileStream($AuditPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
 $writer=New-Object IO.StreamWriter($stream,(New-Object Text.UTF8Encoding($true)))
 try{$writer.WriteLine((ConvertTo-Json -InputObject $audit -Depth 25))}finally{$writer.Dispose()}
}
Write-Output ('Layout status: '+$audit.status)
if($audit.status -eq 'failed'){exit 1}
exit 0
