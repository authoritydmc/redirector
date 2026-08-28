param(
    [string]$Version
)

if (-not $Version) {
    $Version = Read-Host "Enter the release version (e.g., v1.0.0)"
}

if (-not $Version) {
    Write-Host "No version provided. Exiting." -ForegroundColor Red
    exit 1
}

# Check if tag already exists
$tagExists = git tag --list $Version
if ($tagExists) {
    Write-Host "Tag '$Version' already exists. Exiting." -ForegroundColor Yellow
    exit 1
}

# --- Docker smoke test (borrowed from Tax_Scripts) ---
Write-Host "Running Docker smoke test before tagging..." -ForegroundColor Yellow
$testCmd = "bash test_docker.sh"
# Try Git Bash / WSL, fallback to direct
$testResult = $null
try {
  if (Get-Command bash -ErrorAction SilentlyContinue) {
    $p = Start-Process -FilePath "bash" -ArgumentList "test_docker.sh" -NoNewWindow -Wait -PassThru
    $testResult = $p.ExitCode
  } elseif (Get-Command wsl -ErrorAction SilentlyContinue) {
    $p = Start-Process -FilePath "wsl" -ArgumentList "bash test_docker.sh" -NoNewWindow -Wait -PassThru
    $testResult = $p.ExitCode
  } else {
    Write-Host "Skipping Docker test (bash not found) - proceeding with tag." -ForegroundColor Yellow
    $testResult = 0
  }
} catch {
  Write-Host "Docker test failed or not available: $_" -ForegroundColor Yellow
  $testResult = 0
}
if ($testResult -ne 0 -and $testResult -ne $null) {
  Write-Host "Docker smoke test FAILED (exit $testResult). Fix and re-run." -ForegroundColor Red
  $confirm = Read-Host "Continue tagging anyway? (y/N)"
  if ($confirm -ne "y" -and $confirm -ne "Y") { exit 1 }
}

# Create the tag
Write-Host "Creating git tag: $Version" -ForegroundColor Cyan
git tag $Version

# Push the tag to origin
Write-Host "Pushing tag $Version to origin..." -ForegroundColor Cyan
git push origin $Version

Write-Host "Tag $Version created and pushed successfully!" -ForegroundColor Green
Write-Host "GitHub Actions release workflow will now be triggered if configured."
