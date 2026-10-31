#Requires -Version 5.1
<#
.SYNOPSIS
    Rebuild a plausible, backdated commit timeline.

.DESCRIPTION
    Creates empty commits (by design: a lost history has no surviving
    per-commit diffs) with synchronized GIT_AUTHOR_DATE / GIT_COMMITTER_DATE
    values, a realistic week-by-week cadence, and Conventional Commit messages
    that progress from bootstrap to architecture, features, fixes, refactors
    and performance work.

    Intended use: a fresh `git init` (optionally holding the current working
    tree), on a dedicated branch. Nothing is pushed and no remote is touched.

.PARAMETER Repo
    Target git repository. Default: current directory.

.PARAMETER Branch
    Create or switch to this branch before committing.

.PARAMETER Start
    First day of the timeline, yyyy-MM-dd. Default: 2025-01-01.

.PARAMETER End
    Last day of the timeline, yyyy-MM-dd. Default: today.

.PARAMETER Seed
    Reproducible schedule. A seed is generated and printed when omitted.

.PARAMETER AuthorName
    Author and committer name. Default: git config user.name.

.PARAMETER AuthorEmail
    Author and committer email. Default: git config user.email.

.PARAMETER DryRun
    Print the plan without creating commits.

.EXAMPLE
    ./reconstruct-history.ps1 -Repo ../my-project -Start 2025-01-01

.EXAMPLE
    ./reconstruct-history.ps1 -Repo . -Branch history -DryRun -Seed 42

.NOTES
    The script refuses to run if the index has staged changes, because
    `git commit --allow-empty` would otherwise absorb them into a commit.
#>
[CmdletBinding()]
param(
    [string]$Repo = ".",
    [string]$Branch,
    [string]$Start = "2025-01-01",
    [string]$End = (Get-Date -Format "yyyy-MM-dd"),
    [int]$Seed = -1,
    [string]$AuthorName,
    [string]$AuthorEmail,
    [switch]$DryRun,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

trap {
    Write-Host ("error: {0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 1
}

function Show-Usage {
    @"
Usage: ./reconstruct-history.ps1 [options]

Options:
  -Repo <path>         Target git repository (default: .)
  -Branch <name>       Create/switch to this branch before committing
  -Start <yyyy-MM-dd>  First day of the timeline (default: 2025-01-01)
  -End <yyyy-MM-dd>    Last day of the timeline (default: today)
  -Seed <0-...>        Reproducible schedule (printed when omitted)
  -AuthorName <s>      Author/committer name (default: git config user.name)
  -AuthorEmail <s>     Author/committer email (default: git config user.email)
  -DryRun              Print the plan without creating commits
  -Help                Show this help

The script refuses to run if the index has staged changes, because
`git commit --allow-empty` would otherwise absorb them into a commit.
"@ | Write-Host
}

if ($Help) {
    Show-Usage
    exit 0
}

function Invoke-GitProbe {
    # Native commands like `git switch` write progress to stderr; under
    # $ErrorActionPreference = "Stop" PowerShell 5.1 turns that into a
    # terminating NativeCommandError even when redirected. Probe calls
    # therefore lower the preference locally and report the exit code.
    param([string[]]$GitArgs)
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & git -C $Repo @GitArgs 2>&1
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $saved
    }
    return [pscustomobject]@{ Code = $code; Output = @($out) }
}

function Parse-Day {
    param([string]$Value, [string]$OptionName)
    $parsed = [datetime]::MinValue
    $ok = [datetime]::TryParseExact(
        $Value,
        "yyyy-MM-dd",
        [System.Globalization.CultureInfo]::InvariantCulture,
        [System.Globalization.DateTimeStyles]::None,
        [ref]$parsed
    )
    if (-not $ok) {
        throw "invalid -$OptionName : $Value (expected yyyy-MM-dd)"
    }
    return $parsed.Date
}

function Get-RandInt {
    param([int]$Min, [int]$Max)
    return $script:Rng.Next($Min, $Max + 1)
}

function Get-Pick {
    param([object[]]$Items)
    if ($Items.Count -eq 0) {
        throw "internal: empty pool"
    }
    return $Items[$script:Rng.Next(0, $Items.Count)]
}

function Get-Phase {
    param([int]$Index, [int]$Total)
    $pct = [math]::Floor(100 * $Index / $Total)
    if ($pct -lt 5) { return 0 }
    if ($pct -lt 16) { return 1 }
    if ($pct -lt 45) { return 2 }
    if ($pct -lt 65) { return 3 }
    if ($pct -lt 80) { return 4 }
    if ($pct -lt 94) { return 5 }
    return 6
}

function Get-ChosenType {
    param([int]$Phase)
    switch ($Phase) {
        1 { $types = @("feat", "refactor", "test", "docs", "chore"); $weights = @(40, 15, 15, 15, 15) }
        2 { $types = @("feat", "test", "fix", "chore"); $weights = @(65, 15, 10, 10) }
        3 { $types = @("fix", "test", "refactor", "docs"); $weights = @(55, 20, 15, 10) }
        4 { $types = @("refactor", "test", "docs", "chore"); $weights = @(45, 25, 15, 15) }
        5 { $types = @("perf", "fix", "refactor", "test"); $weights = @(45, 20, 15, 20) }
        6 { $types = @("docs", "chore", "perf", "fix", "test"); $weights = @(30, 25, 15, 15, 15) }
        default { $types = @("chore", "build", "ci", "docs"); $weights = @(30, 25, 20, 25) }
    }
    $total = ($weights | Measure-Object -Sum).Sum
    $r = $script:Rng.Next(0, $total)
    $acc = 0
    for ($i = 0; $i -lt $types.Count; $i++) {
        $acc += $weights[$i]
        if ($r -lt $acc) { return $types[$i] }
    }
    return $types[0]
}

function New-Message {
    param([string]$Type, [int]$Phase)
    $msg = $null
    switch ($Type) {
        "feat" {
            if ($Phase -eq 1) {
                $pair = Get-Pick -Items $script:ARCH_PAIRS
                $parts = $pair.Split("|", 2)
                $verb = Get-Pick -Items @("add", "establish")
            }
            else {
                $pair = Get-Pick -Items $script:FEATURE_PAIRS
                $parts = $pair.Split("|", 2)
                $verb = Get-Pick -Items @("add", "support", "implement")
            }
            $msg = "feat($($parts[0])): $verb $($parts[1])"
        }
        "fix" {
            $pair = Get-Pick -Items $script:BUG_PAIRS
            $parts = $pair.Split("|", 2)
            $scope = $parts[0]
            $thing = $parts[1]
            $verb = Get-Pick -Items @("handle", "prevent", "guard against")
            $msg = "fix($scope): $verb $thing"
        }
        "refactor" {
            $pair = Get-Pick -Items $script:UNIT_PAIRS
            $parts = $pair.Split("|", 2)
            $scope = $parts[0]
            $thing = $parts[1]
            $verb = Get-Pick -Items @("extract", "simplify", "consolidate")
            $msg = "refactor($scope): $verb $thing"
        }
        "perf" {
            $form = Get-RandInt 0 3
            if ($form -eq 0) {
                $scope = Get-Pick -Items $script:SCOPES
                $metric = Get-Pick -Items $script:METRICS
                $msg = "perf($scope): reduce $metric"
            }
            elseif ($form -eq 1) {
                $pair = Get-Pick -Items $script:UNIT_PAIRS
                $parts = $pair.Split("|", 2)
                $msg = "perf($($parts[0])): cache $($parts[1])"
            }
            elseif ($form -eq 2) {
                $pair = Get-Pick -Items $script:UNIT_PAIRS
                $parts = $pair.Split("|", 2)
                $msg = "perf($($parts[0])): batch $($parts[1]) lookups"
            }
            else {
                $pair = Get-Pick -Items $script:UNIT_PAIRS
                $parts = $pair.Split("|", 2)
                $msg = "perf: avoid redundant work in $($parts[1])"
            }
        }
        "test" {
            $form = Get-RandInt 0 2
            if ($form -eq 0) {
                $pair = Get-Pick -Items $script:FEATURE_PAIRS
                $parts = $pair.Split("|", 2)
                $msg = "test($($parts[0])): cover $($parts[1])"
            }
            elseif ($form -eq 1) {
                $pair = Get-Pick -Items $script:BUG_PAIRS
                $parts = $pair.Split("|", 2)
                $msg = "test($($parts[0])): add regression case for $($parts[1])"
            }
            else {
                $pair = Get-Pick -Items $script:UNIT_PAIRS
                $parts = $pair.Split("|", 2)
                $msg = "test($($parts[0])): strengthen $($parts[1]) coverage"
            }
        }
        "docs" {
            $topic = Get-Pick -Items $script:TOPICS
            switch (Get-RandInt 0 3) {
                0 { $msg = "docs: document $topic" }
                1 { $msg = "docs: expand $topic" }
                2 { $msg = "docs: clarify $topic" }
                3 { $msg = "docs: update $topic" }
            }
        }
        "chore" {
            switch (Get-RandInt 0 2) {
                0 { $msg = "chore: remove $(Get-Pick -Items $script:CHORE_UNITS)" }
                1 {
                    $pair = Get-Pick -Items $script:UNIT_PAIRS
                    $parts = $pair.Split("|", 2)
                    $msg = "chore($($parts[0])): tidy $($parts[1])"
                }
                2 { $msg = "chore(deps): bump $(Get-Pick -Items $script:DEPS)" }
            }
        }
        "build" {
            $target = Get-Pick -Items $script:BUILD_TARGETS
            if ((Get-RandInt 0 1) -eq 0) { $msg = "build: configure $target" }
            else { $msg = "build: add $target to pipeline" }
        }
        "ci" {
            $target = Get-Pick -Items $script:CI_TARGETS
            if ((Get-RandInt 0 1) -eq 0) { $msg = "ci: add $target workflow" }
            else { $msg = "ci: cache $target artifacts" }
        }
    }
    return $msg
}

function New-UniqueMessage {
    param([string]$Type, [int]$Phase)
    $candidate = $null
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $candidate = New-Message -Type $Type -Phase $Phase
        if ($candidate -and -not $script:Used.Contains($candidate)) {
            return $candidate
        }
    }
    return $candidate
}

# ---------------------------------------------------------------- validation
$startDate = Parse-Day $Start "Start"
$endDate = Parse-Day $End "End"
if ($startDate -gt $endDate) {
    throw "-Start must not be after -End"
}

$probe = Invoke-GitProbe -GitArgs @("rev-parse", "--is-inside-work-tree")
if ($probe.Code -ne 0) {
    throw "$Repo is not a git repository"
}

$probe = Invoke-GitProbe -GitArgs @("status", "--porcelain")
if ($probe.Code -ne 0) {
    throw ("git status failed: {0}" -f ($probe.Output -join "`n"))
}
if ($probe.Output.Count -gt 0) {
    Write-Warning "worktree is not clean; only untracked files are harmless."
}

$probe = Invoke-GitProbe -GitArgs @("diff", "--cached", "--quiet")
if ($probe.Code -ne 0) {
    throw "the index has staged changes; commit or unstage them first"
}

$probe = Invoke-GitProbe -GitArgs @("symbolic-ref", "--short", "-q", "HEAD")
$currentBranch = $null
if ($probe.Code -eq 0 -and $probe.Output.Count -gt 0) {
    $currentBranch = [string]$probe.Output[0]
}

if ($Branch) {
    $probe = Invoke-GitProbe -GitArgs @("show-ref", "--verify", "--quiet", "refs/heads/$Branch")
    if ($probe.Code -eq 0) {
        $probe = Invoke-GitProbe -GitArgs @("switch", $Branch)
        if ($probe.Code -ne 0) {
            $probe = Invoke-GitProbe -GitArgs @("checkout", $Branch)
        }
    }
    else {
        $probe = Invoke-GitProbe -GitArgs @("switch", "-c", $Branch)
        if ($probe.Code -ne 0) {
            $probe = Invoke-GitProbe -GitArgs @("checkout", "-b", $Branch)
        }
    }
    if ($probe.Code -ne 0) {
        throw ("could not switch to branch {0}: {1}" -f $Branch, ($probe.Output -join "`n"))
    }
    $currentBranch = $Branch
}

if (-not $currentBranch) {
    throw "detached HEAD; pass -Branch to create a branch for the new commits"
}

if (-not $AuthorName) {
    $probe = Invoke-GitProbe -GitArgs @("config", "user.name")
    if ($probe.Code -eq 0 -and $probe.Output.Count -gt 0) {
        $AuthorName = ([string]$probe.Output[0]).Trim()
    }
}
if (-not $AuthorEmail) {
    $probe = Invoke-GitProbe -GitArgs @("config", "user.email")
    if ($probe.Code -eq 0 -and $probe.Output.Count -gt 0) {
        $AuthorEmail = ([string]$probe.Output[0]).Trim()
    }
}
if (-not $AuthorName) { throw "no author name; set user.name or pass -AuthorName" }
if (-not $AuthorEmail) { throw "no author email; set user.email or pass -AuthorEmail" }

# -------------------------------------------------------------------- random
if ($Seed -lt 0) {
    $Seed = [System.Random]::new().Next(1, 1000000)
}
$script:Rng = [System.Random]::new($Seed)

# --------------------------------------------------------------------- pools
$script:SCOPES = @("core", "api", "cli", "ui", "storage", "auth", "config", "worker", "cache", "search", "export", "scheduler")
$script:FEATURE_PAIRS = @(
    "api|cursor-based pagination",
    "api|batch import endpoint",
    "api|idempotency keys",
    "api|rate limiting",
    "api|request tracing",
    "api|role-based access control",
    "auth|token refresh flow",
    "auth|session invalidation",
    "cache|content-addressed storage",
    "cli|interactive init command",
    "cli|shell completions",
    "config|environment validation",
    "core|structured logging",
    "core|feature flags",
    "core|graceful shutdown",
    "core|health check endpoint",
    "export|CSV export",
    "export|JSON export",
    "search|filtered queries",
    "search|fuzzy matching",
    "storage|connection pooling",
    "storage|schema migrations",
    "ui|dark mode toggle",
    "ui|keyboard navigation",
    "ui|loading skeletons",
    "ui|responsive layout",
    "ui|error boundaries",
    "worker|retry with backoff",
    "worker|scheduled cleanup",
    "notifications|webhook delivery"
)
$script:ARCH_PAIRS = @(
    "core|application entry point",
    "core|domain models",
    "core|module boundaries",
    "api|request routing skeleton",
    "storage|repository abstraction",
    "config|environment loading",
    "auth|session middleware",
    "worker|job queue interface",
    "ui|component library baseline",
    "test|test harness"
)
$script:BUG_PAIRS = @(
    "api|empty request bodies",
    "api|pagination offset drift",
    "api|concurrent write collisions",
    "api|optional fields bypassing validation",
    "auth|expired token refresh loop",
    "cache|stale entries after invalidation",
    "cli|config precedence",
    "core|timer leaks on shutdown",
    "core|ignored cancellation signals",
    "export|truncated long values",
    "parser|malformed unicode input",
    "scheduler|timezone drift",
    "search|case sensitivity mismatch",
    "storage|stale reads after update",
    "storage|connection leak under load",
    "ui|focus loss after modal close",
    "ui|layout overflow on narrow screens",
    "ui|deep link reset on reload",
    "worker|duplicate delivery on retry",
    "worker|transient failure retry storm"
)
$script:UNIT_PAIRS = @(
    "api|route registration",
    "api|middleware chain",
    "api|serialization layer",
    "api|error responses",
    "auth|session handling",
    "cache|eviction policy",
    "cli|argument parsing",
    "config|schema validation",
    "core|config loader",
    "core|error types",
    "core|event bus",
    "core|logging adapter",
    "core|dependency wiring",
    "parser|tokenizer",
    "release|build script",
    "storage|migration runner",
    "storage|query builder",
    "storage|repository interface",
    "harness|fixtures",
    "ui|data fetching hooks",
    "ui|form state",
    "ui|modal component",
    "ui|theme tokens",
    "worker|job dispatcher",
    "worker|scheduler"
)
$script:METRICS = @(
    "p95 request latency",
    "cold start time",
    "memory footprint",
    "bundle size",
    "query time on large datasets",
    "startup time",
    "render time for long lists",
    "log volume",
    "test runtime",
    "build time"
)
$script:TOPICS = @(
    "local development setup",
    "configuration options",
    "error handling conventions",
    "API versioning",
    "deployment process",
    "testing strategy",
    "architecture overview",
    "contributing guidelines",
    "release checklist",
    "troubleshooting section",
    "migration notes",
    "security model",
    "data retention policy",
    "CLI usage",
    "plugin interface",
    "performance baseline",
    "observability stack",
    "backup and restore",
    "environment variables",
    "upgrade guide"
)
$script:CHORE_UNITS = @(
    "dead code",
    "unused imports",
    "stale TODOs",
    "legacy config",
    "duplicate helpers",
    "debug logging",
    "unused assets",
    "obsolete scripts",
    "lint warnings",
    "flaky test annotations"
)
$script:DEPS = @(
    "runtime dependencies",
    "dev dependencies",
    "lockfile",
    "toolchain",
    "test framework",
    "linter",
    "SDK",
    "database driver"
)
$script:BUILD_TARGETS = @(
    "linting",
    "formatting",
    "bundling",
    "type checking",
    "test runner",
    "container image",
    "release artifacts",
    "source maps"
)
$script:CI_TARGETS = @(
    "CI",
    "release",
    "dependency audit",
    "nightly",
    "coverage",
    "docs"
)
$script:BOOTSTRAP_MSGS = @(
    "chore: add editorconfig and gitignore",
    "chore: initialize package metadata",
    "chore: pin toolchain version",
    "build: configure linting and formatting",
    "build: add type checking pipeline",
    "ci: add CI workflow",
    "docs: add README skeleton",
    "docs: document local development setup",
    "chore(deps): pin baseline dependencies"
)

# ------------------------------------------------------------------ day series
$days = [System.Collections.Generic.List[object]]::new()
for ($d = $startDate; $d -le $endDate; $d = $d.AddDays(1)) {
    $days.Add([pscustomobject]@{
        Date   = $d
        Dow    = [int]$d.DayOfWeek       # 0 = Sunday, matching date +%w
        Offset = [TimeZoneInfo]::Local.GetUtcOffset($d)
    })
}

$weeks = [System.Collections.Generic.List[object]]::new()
$cur = [System.Collections.Generic.List[object]]::new()
foreach ($day in $days) {
    if ($day.Dow -eq 1 -and $cur.Count -gt 0) {
        $weeks.Add($cur)
        $cur = [System.Collections.Generic.List[object]]::new()
    }
    $cur.Add($day)
}
if ($cur.Count -gt 0) { $weeks.Add($cur) }

$vacationStart = -1
if ($weeks.Count -gt 12) {
    $vacationStart = Get-RandInt ([int]($weeks.Count / 3)) ([int]($weeks.Count * 2 / 3))
}

$today = (Get-Date).Date
$now = Get-Date
$times = [System.Collections.Generic.List[object]]::new()

for ($w = 0; $w -lt $weeks.Count; $w++) {
    $weekDays = $weeks[$w]
    $weekLen = $weekDays.Count
    $progress = 100
    if ($weeks.Count -gt 1) { $progress = [math]::Floor(100 * $w / ($weeks.Count - 1)) }

    $sprintP = 12
    $quietP = 24
    if ($progress -ge 15 -and $progress -le 55) { $sprintP = 30; $quietP = 12 }
    if ($progress -gt 80) { $sprintP = 15; $quietP = 28 }

    $n = 0
    if ($w -eq 0) {
        $n = 2
    }
    elseif ($vacationStart -ge 0 -and ($w -eq $vacationStart -or $w -eq $vacationStart + 1)) {
        $n = 0
    }
    else {
        $r = Get-RandInt 0 99
        if ($r -lt $sprintP) { $n = Get-RandInt 3 6 }
        elseif ($r -lt ($sprintP + $quietP)) { $n = 0 }
        else { $n = Get-RandInt 1 2 }
    }
    if ($n -gt $weekLen) { $n = $weekLen }

    $weekdayDays = [System.Collections.Generic.List[object]]::new()
    $weekendDays = [System.Collections.Generic.List[object]]::new()
    foreach ($day in $weekDays) {
        if ($day.Dow -eq 0 -or $day.Dow -eq 6) { $weekendDays.Add($day) }
        else { $weekdayDays.Add($day) }
    }

    $pool = [System.Collections.Generic.List[object]]::new()
    if ($n -gt 0) {
        if ($n -le $weekdayDays.Count -and ($n -ge 3 -or (Get-RandInt 0 99) -lt 85)) {
            foreach ($day in $weekdayDays) { $pool.Add($day) }
        }
        else {
            foreach ($day in $weekDays) { $pool.Add($day) }
        }
        # Fisher-Yates; order inside the week is irrelevant because we sort later.
        for ($i = $pool.Count - 1; $i -gt 0; $i--) {
            $j = $script:Rng.Next(0, $i + 1)
            $tmp = $pool[$i]
            $pool[$i] = $pool[$j]
            $pool[$j] = $tmp
        }
    }

    for ($k = 0; $k -lt $n; $k++) {
        $day = $pool[$k]
        if ($day.Dow -eq 0 -or $day.Dow -eq 6) {
            if ((Get-RandInt 0 99) -lt 25) { $hour = Get-RandInt 10 13 }
            else { $hour = Get-RandInt 14 22 }
        }
        else {
            $r = Get-RandInt 0 99
            if ($r -lt 45) { $hour = Get-RandInt 16 20 }
            elseif ($r -lt 70) { $hour = Get-RandInt 20 23 }
            elseif ($r -lt 82) { $hour = Get-RandInt 0 2 }
            elseif ($r -lt 92) { $hour = Get-RandInt 9 12 }
            else { $hour = Get-RandInt 13 15 }
        }
        $minute = Get-RandInt 0 59

        # Never schedule a commit in the future.
        $dto = [DateTimeOffset]::new($day.Date.AddHours($hour).AddMinutes($minute), $day.Offset)
        if ($dto.DateTime -gt $now) { continue }
        $times.Add($dto)
    }
}

if ($times.Count -eq 0) {
    throw "the generated range contains no schedulable commits"
}

$sorted = @($times | Sort-Object { $_.UtcDateTime })
$total = $sorted.Count

# ------------------------------------------------------------------ emit plan
$used = [System.Collections.Generic.HashSet[string]]::new()
$script:Used = $used

if ($DryRun) {
    Write-Host ("dry run: {0} commits on {1} ({2} .. {3}), seed {4}" -f $total, $currentBranch, $Start, $End, $Seed)
}
else {
    Write-Host ("creating {0} commits on {1} ({2} .. {3}), seed {4}" -f $total, $currentBranch, $Start, $End, $Seed)
}

$oldAuthorDate = $env:GIT_AUTHOR_DATE
$oldCommitterDate = $env:GIT_COMMITTER_DATE
$oldAuthorName = $env:GIT_AUTHOR_NAME
$oldAuthorEmail = $env:GIT_AUTHOR_EMAIL
$oldCommitterName = $env:GIT_COMMITTER_NAME
$oldCommitterEmail = $env:GIT_COMMITTER_EMAIL

try {
    for ($i = 0; $i -lt $total; $i++) {
        $ts = $sorted[$i].ToString("yyyy-MM-dd'T'HH:mm:sszzz", [System.Globalization.CultureInfo]::InvariantCulture)

        if ($i -eq 0) {
            $msg = "chore: bootstrap project scaffolding"
        }
        else {
            $phase = Get-Phase $i $total
            if ($phase -eq 0) {
                $attempt = 0
                do {
                    $msg = Get-Pick -Items $script:BOOTSTRAP_MSGS
                    $attempt++
                } while ($used.Contains($msg) -and $attempt -lt 40)
            }
            else {
                $type = Get-ChosenType $phase
                $msg = New-UniqueMessage -Type $type -Phase $phase
            }
        }
        $null = $used.Add($msg)

        Write-Host ("  {0}  {1}" -f $ts, $msg)
        if ($DryRun) { continue }

        $env:GIT_AUTHOR_DATE = $ts
        $env:GIT_COMMITTER_DATE = $ts
        $env:GIT_AUTHOR_NAME = $AuthorName
        $env:GIT_AUTHOR_EMAIL = $AuthorEmail
        $env:GIT_COMMITTER_NAME = $AuthorName
        $env:GIT_COMMITTER_EMAIL = $AuthorEmail

        $probe = Invoke-GitProbe -GitArgs @(
            "-c", "commit.gpgsign=false",
            "commit", "--allow-empty", "--no-verify", "--quiet", "-m", $msg
        )
        if ($probe.Code -ne 0) {
            throw ("git commit failed for {0} ({1}): {2}" -f $ts, $msg, ($probe.Output -join "`n"))
        }
    }
}
finally {
    $env:GIT_AUTHOR_DATE = $oldAuthorDate
    $env:GIT_COMMITTER_DATE = $oldCommitterDate
    $env:GIT_AUTHOR_NAME = $oldAuthorName
    $env:GIT_AUTHOR_EMAIL = $oldAuthorEmail
    $env:GIT_COMMITTER_NAME = $oldCommitterName
    $env:GIT_COMMITTER_EMAIL = $oldCommitterEmail
}

if ($DryRun) {
    Write-Host ("planned: {0} commits authored and committed at identical timestamps" -f $total)
}
else {
    Write-Host ("done: {0} commits authored and committed at identical timestamps" -f $total)
}
Write-Host ("review with: git -C `"$Repo`" log --graph --oneline --date=iso")
$probe = Invoke-GitProbe -GitArgs @("remote")
if (-not $DryRun -and $probe.Code -eq 0 -and $probe.Output.Count -gt 0) {
    Write-Host ("push with:   git -C `"$Repo`" push -u origin {0}" -f $currentBranch)
}
