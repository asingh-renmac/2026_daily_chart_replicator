# List python processes WITH owner, full command line, parent and start time.
#
# Six python.exe run on this host and three of them are the live servers, so "kill
# python" is never a safe instruction -- kill by PID, having read this. The full command
# line is printed untruncated because WHICH FOLDER each server was started from is the
# thing you actually need, and that is exactly the part a truncated listing cuts off.
#
# Each server shows up twice: a launcher and the interpreter it re-execs. The Parent
# column is what tells them apart -- stopping the parent takes the child with it.
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Sort-Object ParentProcessId, ProcessId |
    ForEach-Object {
        $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwner
        [PSCustomObject]@{
            PID     = $_.ProcessId
            Parent  = $_.ParentProcessId
            Owner   = "$($owner.Domain)\$($owner.User)"
            Started = $_.CreationDate
            Command = $_.CommandLine
        }
    } | Format-List
