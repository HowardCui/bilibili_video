param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Preview', 'Install', 'Status', 'Run', 'Enable', 'Disable', 'Uninstall')]
    [string]$Action,

    [string]$TaskName = 'Bilibili Ranking Collection',

    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),

    [string]$PythonPath
)

$ErrorActionPreference = 'Stop'

function Resolve-TaskPaths {
    $resolvedProject = (Resolve-Path -LiteralPath $ProjectRoot).Path
    $candidatePython = if ($PythonPath) {
        $PythonPath
    } else {
        Join-Path $resolvedProject '.venv\Scripts\python.exe'
    }
    $resolvedPython = (Resolve-Path -LiteralPath $candidatePython).Path
    return @{
        Project = $resolvedProject
        Python = $resolvedPython
    }
}

function New-RankingTaskXml {
    param(
        [Parameter(Mandatory = $true)][string]$ResolvedProject,
        [Parameter(Mandatory = $true)][string]$ResolvedPython
    )

    $userSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $xmlProject = [System.Security.SecurityElement]::Escape($ResolvedProject)
    $xmlPython = [System.Security.SecurityElement]::Escape($ResolvedPython)
    $xmlUserSid = [System.Security.SecurityElement]::Escape($userSid)
    $beijingNow = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId(
        [DateTimeOffset]::UtcNow,
        'China Standard Time'
    )
    $date = $beijingNow.ToString('yyyy-MM-dd')
    $triggers = foreach ($hour in 0, 6, 12, 18) {
        $time = '{0:D2}:00:00' -f $hour
        @"
    <CalendarTrigger>
      <StartBoundary>${date}T${time}+08:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
"@
    }

    return @"
<?xml version="1.0" encoding="UTF-8"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Run Bilibili ranking collection at 00, 06, 12 and 18 Asia/Shanghai.</Description>
  </RegistrationInfo>
  <Triggers>
$($triggers -join "`n")
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$xmlUserSid</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$xmlPython</Command>
      <Arguments>-m automation.ranking_once --json</Arguments>
      <WorkingDirectory>$xmlProject</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
}

if ($Action -in @('Preview', 'Install')) {
    $paths = Resolve-TaskPaths
    $taskXml = New-RankingTaskXml `
        -ResolvedProject $paths.Project `
        -ResolvedPython $paths.Python
}

switch ($Action) {
    'Preview' {
        $taskXml
    }
    'Install' {
        Register-ScheduledTask -TaskName $TaskName -Xml $taskXml -Force | Out-Null
        Get-ScheduledTask -TaskName $TaskName
    }
    'Status' {
        Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo
    }
    'Run' {
        Start-ScheduledTask -TaskName $TaskName
        Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo
    }
    'Enable' {
        Enable-ScheduledTask -TaskName $TaskName
    }
    'Disable' {
        Disable-ScheduledTask -TaskName $TaskName
    }
    'Uninstall' {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
}
