# List python processes WITH their owner, so an automation-account stray can be told
# apart from the three servers madz is running. Never kill by name on this box.
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | ForEach-Object {
    $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwner
    $cmd = $_.CommandLine
    if ($cmd -and $cmd.Length -gt 80) { $cmd = $cmd.Substring(0, 80) }
    "{0,-7} {1}\{2,-12} {3}" -f $_.ProcessId, $owner.Domain, $owner.User, $cmd
}
