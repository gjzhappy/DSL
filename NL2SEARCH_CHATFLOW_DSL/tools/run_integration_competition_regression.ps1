param(
    [Parameter(Mandatory=$true)]
    [string]$Sha
)

$ErrorActionPreference = 'Stop'
$repo = 'D:\workspace\DSL'
$root = 'E:\work\tmp\20261008'
if($Sha -notmatch '^[0-9a-f]{40}$'){throw 'INVALID_GIT_SHA'}
if(!(Test-Path -LiteralPath $repo)){throw 'DSL_REPOSITORY_NOT_FOUND'}
New-Item -Path $root -ItemType Directory -Force | Out-Null
$run = Join-Path $root ('IC_REGRESSION_' + $Sha.Substring(0,8) + '_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -Path $run -ItemType Directory -Force | Out-Null
Write-Host "RUN_DIR=$run"

$head = (git -C $repo rev-parse HEAD).Trim()
$branch = (git -C $repo branch --show-current).Trim()
$statusBefore = (@(git -C $repo status --porcelain=v1 --untracked-files=all) -join "\n")
$completed = $false

try {
    Write-Host '========== 1. PIN AND BLOBS =========='
    git -C $repo fetch --no-tags origin $Sha
    if($LASTEXITCODE -ne 0 -or (git -C $repo rev-parse FETCH_HEAD).Trim() -ne $Sha){
        throw 'GITHUB_PIN_FAILED'
    }
    $expected = [ordered]@{
        'NL2SEARCH_CHATFLOW_DSL/INTEGRATION_COMPETITION_CHATFLOW/INTEGRATION_COMPETITION_CHATFLOW_00.yml' = 'a414ae4adbe0286e9a1ee5ed3498a2de1c75ce0e'
        'NL2SEARCH_CHATFLOW_DSL/INTEGRATION_COMPETITION_CHATFLOW/INTEGRATION_COMPETITION_CHATFLOW_01.yml' = '588eb24894409cf1ab93276d497c6a8808969a0a'
        'NL2SEARCH_CHATFLOW_DSL/INTEGRATION_COMPETITION_CHATFLOW/INTEGRATION_COMPETITION_CHATFLOW_02.yml' = 'bf9d51d0bf4c09fa31db7dd8620670f07c941a10'
        'NL2SEARCH_CHATFLOW_DSL/INTEGRATION_COMPETITION_CHATFLOW/INTEGRATION_COMPETITION_CHATFLOW_03.yml' = 'e1d2c6baa134edb1cd4748da816e0a843e4d21b3'
        'NL2SEARCH_CHATFLOW_DSL/tools/merge_business_chatflow.py' = 'f73acab735349757e8552497af87bb945fc7399d'
        'NL2SEARCH_CHATFLOW_DSL/tools/test_integration_competition_chatflow.py' = '380a0e3990618ade55645b2407f8bdb60d73e01f'
        'scripts/dify_dsl_schema_check.py' = '8ffc4b35b7dac90d7b9459709a5da44d5fde6bb9'
    }
    foreach($path in $expected.Keys){
        $actual = (git -C $repo rev-parse ('{0}:{1}' -f $Sha,$path)).Trim()
        if($LASTEXITCODE -ne 0 -or $actual -ne $expected[$path]){
            throw ('GITHUB_BLOB_MISMATCH: ' + $path)
        }
    }
    Write-Host 'GITHUB_BLOBS=PASS'

    Write-Host '========== 2. ISOLATED EXPORT =========='
    $zip = Join-Path $run 'source.zip'
    $src = Join-Path $run 'src'
    git -C $repo archive --format=zip "--output=$zip" $Sha
    if($LASTEXITCODE -ne 0){throw 'GIT_ARCHIVE_FAILED'}
    Expand-Archive -LiteralPath $zip -DestinationPath $src -Force
    Write-Host 'ISOLATED_EXPORT=PASS'

    Push-Location $src
    try {
        Write-Host '========== 3. RUN 15 TESTS =========='
        $py = (Get-Command python.exe -ErrorAction Stop).Source
        $process = Start-Process -FilePath $py -ArgumentList 'NL2SEARCH_CHATFLOW_DSL\tools\test_integration_competition_chatflow.py' -WorkingDirectory $src -Wait -PassThru -NoNewWindow -RedirectStandardOutput (Join-Path $run 'test.stdout.log') -RedirectStandardError (Join-Path $run 'test.stderr.log')
        $testExit = $process.ExitCode
        $testLog = (Get-Content -LiteralPath (Join-Path $run 'test.stdout.log') -Raw) + (Get-Content -LiteralPath (Join-Path $run 'test.stderr.log') -Raw)
        Write-Host "TEST_EXIT=$testExit"
        Get-Content -LiteralPath (Join-Path $run 'test.stderr.log') -Tail 18
        if($testExit -ne 0 -or $testLog -notmatch 'Ran 15 tests' -or $testLog -notmatch '(?m)^OK\s*$'){
            throw 'REGRESSION_FAILED'
        }
        Write-Host 'REGRESSION_TEST=PASS'

        Write-Host '========== 4. MERGE AND SCHEMA =========='
        $process = Start-Process -FilePath $py -ArgumentList @('NL2SEARCH_CHATFLOW_DSL\tools\merge_business_chatflow.py','INTEGRATION_COMPETITION','--write') -WorkingDirectory $src -Wait -PassThru -NoNewWindow -RedirectStandardOutput (Join-Path $run 'merge.stdout.log') -RedirectStandardError (Join-Path $run 'merge.stderr.log')
        $mergeExit = $process.ExitCode
        Write-Host "MERGE_EXIT=$mergeExit"
        if($mergeExit -ne 0){
            Get-Content -LiteralPath (Join-Path $run 'merge.stderr.log') -Tail 18
            throw 'MERGE_FAILED'
        }
        $rel = 'NL2SEARCH_CHATFLOW_DSL\INTEGRATION_COMPETITION_CHATFLOW\INTEGRATION_COMPETITION_CHATFLOW.yml'
        $process = Start-Process -FilePath $py -ArgumentList @('scripts\dify_dsl_schema_check.py',$rel) -WorkingDirectory $src -Wait -PassThru -NoNewWindow -RedirectStandardOutput (Join-Path $run 'schema.stdout.log') -RedirectStandardError (Join-Path $run 'schema.stderr.log')
        $schemaExit = $process.ExitCode
        Write-Host "SCHEMA_EXIT=$schemaExit"
        if($schemaExit -ne 0){
            Get-Content -LiteralPath (Join-Path $run 'schema.stderr.log') -Tail 18
            throw 'SCHEMA_FAILED'
        }
        $artifact = Join-Path $src $rel
        if(!(Test-Path -LiteralPath $artifact)){throw 'MERGED_DSL_MISSING'}
        $content = ''
        foreach($number in @('00','01','02','03')){
            $shard = Join-Path $src ("NL2SEARCH_CHATFLOW_DSL\INTEGRATION_COMPETITION_CHATFLOW\INTEGRATION_COMPETITION_CHATFLOW_$number.yml")
            $content += [System.IO.File]::ReadAllText($shard,[System.Text.Encoding]::UTF8)
        }
        if($content -cne [System.IO.File]::ReadAllText($artifact,[System.Text.Encoding]::UTF8)){
            throw 'MERGED_CONTENT_MISMATCH'
        }
        Write-Host 'MERGED_CONTENT_MATCHES_PINNED_SHARDS=PASS'
        Write-Host ('MERGED_SHA256=' + (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash)
        Write-Host 'MERGER_VALIDATION=PASS'
        $completed = $true
    }
    finally {
        Pop-Location
    }
}
finally {
    $headAfter = (git -C $repo rev-parse HEAD).Trim()
    $branchAfter = (git -C $repo branch --show-current).Trim()
    $statusAfter = (@(git -C $repo status --porcelain=v1 --untracked-files=all) -join "\n")
    if($headAfter -eq $head -and $branchAfter -eq $branch -and $statusAfter -ceq $statusBefore){
        Write-Host 'LOCAL_WORKTREE_PRESERVED=PASS'
    }
    else {
        Write-Host 'LOCAL_WORKTREE_PRESERVED=FAIL'
        throw 'LOCAL_BASELINE_CHANGED'
    }
}
if($completed){Write-Host 'INTEGRATION_COMPETITION_FINAL=PASS'}
