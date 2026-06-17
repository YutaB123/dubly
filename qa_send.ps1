param([string]$text, [int]$chat_id, [string]$attachJson="")
$base="https://canvas-study-assistant-xohy.onrender.com"
$H=@{"X-Chat-Key"="uwhusky"}
$bodyObj=@{ text=$text }
if($chat_id -gt 0){ $bodyObj.chat_id=$chat_id }
$body = $bodyObj | ConvertTo-Json -Compress -Depth 6
if($attachJson -ne ""){
  # attachJson is a full JSON object string to merge
  $body = $attachJson
}
function Try-Send($b){
  Invoke-RestMethod -Uri "$base/chat/send" -Method Post -Headers $H -TimeoutSec 150 -ContentType "application/json" -Body $b
}
try {
  $r = Try-Send $body
} catch {
  Start-Sleep -Seconds 2
  try { $r = Try-Send $body } catch {
    $resp=$_.Exception.Response
    $code = if($resp){[int]$resp.StatusCode}else{"?"}
    $rb=""
    if($resp){ try{ $sr=New-Object IO.StreamReader($resp.GetResponseStream()); $rb=$sr.ReadToEnd() }catch{} }
    Write-Output "SENDERR status=$code body=$rb msg=$($_.Exception.Message)"
    exit
  }
}
Write-Output "CHATID=$($r.chat_id)"
foreach($m in $r.messages){
  Write-Output "[$($m.role)] $($m.text)"
  if($m.media_url){ Write-Output "MEDIA=$($m.media_url)" }
}
