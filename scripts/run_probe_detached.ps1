# Launch a diagnostic python script detached, so a long DLX call cannot hang the SSH
# session that started it (a dropped session leaves the child orphaned and still holding
# DLX, which is worse than the hang). Output goes to a file the caller reads separately.
param(
    [Parameter(Mandatory = $true)][string]$Script,
    [string[]]$Args = @(),
    [string]$Out = "C:\Users\madz\probe.txt"
)

$repo = "C:\Users\madz\Work\asingh\haver-chart\repo"
$py = "C:\Users\madz\envs\haver-chart\Scripts\python.exe"
$err = [System.IO.Path]::ChangeExtension($Out, ".err.txt")

Remove-Item $Out, $err -Force -ErrorAction SilentlyContinue

$argList = @("-u", $Script) + $Args
$p = Start-Process -FilePath $py -ArgumentList $argList -WorkingDirectory $repo `
    -RedirectStandardOutput $Out -RedirectStandardError $err -NoNewWindow -PassThru

Write-Output ("launched pid=" + $p.Id + " -> " + $Out)
