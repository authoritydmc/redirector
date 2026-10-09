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

# --- Pre-tag gate: VERSION file must match the tag (Docker + tests are
# --- covered by CI on push; this guards tagging the wrong version locally).
Write-Host "Verifying VERSION matches $Version ..." -ForegroundColor Yellow
$want = $Version.TrimStart('v')
$have = (Get-Content VERSION -Raw).Trim()
if ($have -ne $want) {
    Write-Host "VERSION file says '$have' but tag is '$Version'. Fix and re-run." -ForegroundColor Red
    exit 1
}
$dirty = git status --short
if ($dirty) {
    Write-Host "Working tree is not clean. Commit or stash first." -ForegroundColor Red
    exit 1
}

# Create the tag
Write-Host "Creating git tag: $Version" -ForegroundColor Cyan
git tag $Version

# Push the tag to origin
Write-Host "Pushing tag $Version to origin..." -ForegroundColor Cyan
git push origin $Version

Write-Host "Tag $Version created and pushed successfully!" -ForegroundColor Green
Write-Host "GitHub Actions release workflow will now be triggered if configured."
