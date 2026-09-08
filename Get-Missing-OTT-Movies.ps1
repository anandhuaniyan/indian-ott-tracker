$ProjectPath = "C:\Users\anadh\Development\indian-ott-tracker"
$ContainerName = "indian_ott_api"
$OutputFolder = "$env:USERPROFILE\Desktop\OTT_Reports"
$ContainerOutput = "/tmp/missing_ott_movies.csv"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Indian OTT Tracker - Missing OTT Data Report" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $ProjectPath)) {
    Write-Host "ERROR: Project folder not found:" -ForegroundColor Red
    Write-Host $ProjectPath -ForegroundColor Yellow
    Read-Host "Press Enter to close"
    exit 1
}

if (-not (Test-Path $OutputFolder)) {
    New-Item -ItemType Directory -Path $OutputFolder | Out-Null
}

$DockerCheck = docker ps --format "{{.Names}}" 2>$null

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Docker is not running or docker command is unavailable." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

if ($DockerCheck -notcontains $ContainerName) {
    Write-Host "Container '$ContainerName' is not running." -ForegroundColor Yellow
    Write-Host "Trying to start project containers..." -ForegroundColor Cyan

    Set-Location $ProjectPath
    docker compose up -d

    Start-Sleep -Seconds 3

    $DockerCheck = docker ps --format "{{.Names}}"

    if ($DockerCheck -notcontains $ContainerName) {
        Write-Host "ERROR: Could not start '$ContainerName'." -ForegroundColor Red
        Read-Host "Press Enter to close"
        exit 1
    }
}

Write-Host "Using container: $ContainerName" -ForegroundColor Green
Write-Host ""
Write-Host "Scanning movies..." -ForegroundColor Cyan
Write-Host ""

$PythonCode = @'
import csv
from datetime import datetime
from app.database.connection import get_db
from app.models.movie import Movie
from app.models.ott_availability import OttAvailability

db = next(get_db())

try:
    movies = (
        db.query(Movie)
        .order_by(
            Movie.release_date.desc().nullslast(),
            Movie.title.asc()
        )
        .all()
    )

    rows = []

    counts = {
        "NO_OTT_PLATFORM_OR_DATE": 0,
        "PLATFORM_KNOWN_DATE_MISSING": 0,
        "DATE_KNOWN_PLATFORM_MISSING": 0
    }

    for movie in movies:

        ott_rows = (
            db.query(OttAvailability)
            .filter(
                OttAvailability.movie_id == movie.id,
                OttAvailability.country == "IN"
            )
            .all()
        )

        platforms = sorted({
            str(r.provider).strip()
            for r in ott_rows
            if getattr(r, "provider", None)
            and str(r.provider).strip()
        })

        dates = sorted({
            r.ott_release_date.isoformat()
            for r in ott_rows
            if getattr(r, "ott_release_date", None)
        })

        has_platform = bool(platforms)
        has_date = bool(dates)

        if has_platform and has_date:
            continue

        if not has_platform and not has_date:
            issue = "NO_OTT_PLATFORM_OR_DATE"
        elif has_platform and not has_date:
            issue = "PLATFORM_KNOWN_DATE_MISSING"
        else:
            issue = "DATE_KNOWN_PLATFORM_MISSING"

        counts[issue] += 1

        statuses = sorted({
            str(getattr(r, "status", "") or "").strip()
            for r in ott_rows
            if str(getattr(r, "status", "") or "").strip()
        })

        verification = sorted({
            str(getattr(r, "verification_status", "") or "").strip()
            for r in ott_rows
            if str(getattr(r, "verification_status", "") or "").strip()
        })

        rows.append({
            "movie_id": movie.id,
            "tmdb_id": movie.tmdb_id,
            "title": movie.title,
            "original_title": getattr(movie, "original_title", None) or "",
            "language": getattr(movie, "original_language", None) or "",
            "theatrical_release_date":
                movie.release_date.isoformat()
                if getattr(movie, "release_date", None)
                else "",
            "ott_platform": " | ".join(platforms),
            "ott_release_date": " | ".join(dates),
            "ott_status": " | ".join(statuses),
            "verification_status": " | ".join(verification),
            "issue": issue
        })

    output = "/tmp/missing_ott_movies.csv"

    fields = [
        "movie_id",
        "tmdb_id",
        "title",
        "original_title",
        "language",
        "theatrical_release_date",
        "ott_platform",
        "ott_release_date",
        "ott_status",
        "verification_status",
        "issue"
    ]

    with open(output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print("")
    print("========================================")
    print("REPORT COMPLETE")
    print("========================================")
    print("Total movies checked:", len(movies))
    print("Movies needing OTT review:", len(rows))
    print("")
    print("No platform AND no OTT date:",
          counts["NO_OTT_PLATFORM_OR_DATE"])
    print("Platform known, date missing:",
          counts["PLATFORM_KNOWN_DATE_MISSING"])
    print("Date known, platform missing:",
          counts["DATE_KNOWN_PLATFORM_MISSING"])

finally:
    db.close()
'@

$TempPython = "$env:TEMP\missing_ott_report.py"
$PythonCode | Set-Content -Path $TempPython -Encoding UTF8

docker cp $TempPython "${ContainerName}:/tmp/missing_ott_report.py"

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to copy script into Docker container." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

docker exec $ContainerName python /tmp/missing_ott_report.py

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: Report generation failed." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

$Timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"

$FinalFile = Join-Path $OutputFolder "missing_ott_movies_$Timestamp.csv"
$LatestFile = Join-Path $OutputFolder "missing_ott_movies_LATEST.csv"

docker cp "${ContainerName}:${ContainerOutput}" $FinalFile

Copy-Item $FinalFile $LatestFile -Force

Remove-Item $TempPython -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Report created successfully!" -ForegroundColor Green
Write-Host ""
Write-Host $FinalFile -ForegroundColor Yellow
Write-Host ""

Start-Process explorer.exe $OutputFolder

Read-Host "Press Enter to close"