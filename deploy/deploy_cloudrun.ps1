<#
  Deploy Mobiq to Google Cloud Run. Run from the project root:
      .\deploy\deploy_cloudrun.ps1 -Project mobiq-512514
  Re-run the same command to redeploy after code or secret changes.

  Needs: the gcloud CLI (gcloud auth login), billing enabled on the project, and a filled-in
  .streamlit/secrets.toml (Google Sheets service account, [auth], and [smtp] if you use email).
  Nothing here is sent anywhere until you confirm.
#>
param(
    [Parameter(Mandatory = $true)][string]$Project,
    [string]$Region = "us-central1",       # free-tier regions: us-central1, us-east1, us-west1
    [string]$Service = "mobiq",
    [string]$SecretName = "mobiq-secrets",
    [string]$SecretsFile = ".streamlit/secrets.toml"
)

$ErrorActionPreference = "Stop"

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) { Fail "gcloud CLI not found. Install it from https://cloud.google.com/sdk/docs/install and run 'gcloud auth login'." }
if (-not (Test-Path "Dockerfile")) { Fail "Run this from the project root (Dockerfile not found)." }
if (-not (Test-Path $SecretsFile)) { Fail "$SecretsFile not found. Copy .streamlit/secrets.toml.example and fill it in." }

$secrets = Get-Content $SecretsFile -Raw
if ($secrets -match "xxxx xxxx" -or $secrets -match "your\.address@") {
    Write-Host "WARNING: [smtp] still has placeholder values, so Notify and emailed codes will not work." -ForegroundColor Yellow
}
if ($secrets -match "(?m)^\s*require_code\s*=\s*false") {
    Write-Host "WARNING: require_code = false means anyone who knows an active user's email (including an admin's) can sign in as them." -ForegroundColor Yellow
    Write-Host "         The Cloud Run URL will be public. Set require_code = true (and configure [smtp]) first." -ForegroundColor Yellow
}
$answer = Read-Host "Deploy '$Service' to project '$Project' in '$Region' with --allow-unauthenticated? (type yes)"
if ($answer -ne "yes") { Write-Host "Cancelled."; exit 0 }

Write-Host "`n[1/4] Selecting project and enabling APIs..."
gcloud config set project $Project | Out-Null
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
if ($LASTEXITCODE) { Fail "Could not enable APIs (is billing enabled on the project?)." }

Write-Host "`n[2/4] Storing secrets in Secret Manager ($SecretName)..."
gcloud secrets describe $SecretName 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    gcloud secrets versions add $SecretName --data-file=$SecretsFile | Out-Null
    Write-Host "Added a new version to the existing secret."
} else {
    gcloud secrets create $SecretName --data-file=$SecretsFile | Out-Null
    Write-Host "Created the secret."
}
if ($LASTEXITCODE) { Fail "Could not store the secret." }

Write-Host "`n[3/4] Letting Cloud Run read the secret..."
$projectNumber = gcloud projects describe $Project --format="value(projectNumber)"
$runtimeAccount = "$projectNumber-compute@developer.gserviceaccount.com"
gcloud secrets add-iam-policy-binding $SecretName --member="serviceAccount:$runtimeAccount" --role="roles/secretmanager.secretAccessor" | Out-Null
if ($LASTEXITCODE) { Fail "Could not grant secret access to $runtimeAccount." }

Write-Host "`n[4/4] Building and deploying (this takes a few minutes)..."
# max-instances 1: sign-in codes, rate limits and the data cache live in this process's memory.
# session-affinity + long timeout: Streamlit holds a websocket open for each browser session.
gcloud run deploy $Service `
    --source . `
    --region $Region `
    --allow-unauthenticated `
    --port 8080 `
    --memory 512Mi --cpu 1 `
    --max-instances 1 --min-instances 0 `
    --session-affinity `
    --timeout 3600 `
    --set-secrets "/etc/secrets/secrets.toml=${SecretName}:latest"
if ($LASTEXITCODE) { Fail "Deploy failed. Read the build log above." }

$url = gcloud run services describe $Service --region $Region --format="value(status.url)"
Write-Host "`nDone. Open: $url" -ForegroundColor Green
Write-Host "Logs:  gcloud run services logs read $Service --region $Region"
