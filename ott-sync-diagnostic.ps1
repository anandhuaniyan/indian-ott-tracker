Write-Host "========================================" -ForegroundColor Cyan
Write-Host " INDIAN OTT TRACKER - SYNC DIAGNOSTIC" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$Output = "ott-sync-diagnostic.txt"

"INDIAN OTT TRACKER - SYNC DIAGNOSTIC" | Out-File $Output
"Generated: $(Get-Date)" | Out-File $Output -Append
"Project: $PWD" | Out-File $Output -Append
"" | Out-File $Output -Append

function Run-Cmd {
    param([string]$Title, [string]$Command)

    "`n========================================" | Out-File $Output -Append
    $Title | Out-File $Output -Append
    "========================================" | Out-File $Output -Append
    "COMMAND: $Command" | Out-File $Output -Append

    try {
        Invoke-Expression $Command 2>&1 | Out-File $Output -Append
    }
    catch {
        $_ | Out-File $Output -Append
    }
}

Run-Cmd "DOCKER COMPOSE STATUS" `
    "docker compose ps"

Run-Cmd "DOCKER CONTAINERS" `
    "docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'"

Run-Cmd "DATABASE TABLES" `
    "docker exec indian_ott_postgres psql -U ott_user -d ott_tracker -c '\dt'"

Run-Cmd "MOVIE COUNT" `
    "docker exec indian_ott_postgres psql -U ott_user -d ott_tracker -c 'SELECT COUNT(*) AS total_movies FROM movies;'"

Run-Cmd "MOVIES BY LANGUAGE" `
    "docker exec indian_ott_postgres psql -U ott_user -d ott_tracker -c 'SELECT original_language, COUNT(*) FROM movies GROUP BY original_language ORDER BY COUNT(*) DESC;'"

Run-Cmd "LATEST MOVIES" `
    "docker exec indian_ott_postgres psql -U ott_user -d ott_tracker -c 'SELECT tmdb_id,title,original_language,release_date,updated_at FROM movies ORDER BY updated_at DESC LIMIT 30;'"

Run-Cmd "OLDEST UPDATED MOVIES" `
    "docker exec indian_ott_postgres psql -U ott_user -d ott_tracker -c 'SELECT tmdb_id,title,original_language,release_date,updated_at FROM movies ORDER BY updated_at ASC LIMIT 20;'"

Run-Cmd "MOVIE TABLE STRUCTURE" `
    "docker exec indian_ott_postgres psql -U ott_user -d ott_tracker -c '\d+ movies'"

Run-Cmd "TMDB FILES" `
    "docker exec indian_ott_api sh -c 'find /app/app/services/tmdb -type f -maxdepth 2 -print | sort'"

Run-Cmd "TMDB INCREMENTAL SYNC" `
    "docker exec indian_ott_api sh -c 'sed -n \"1,320p\" /app/app/services/tmdb/incremental_sync.py'"

Run-Cmd "TMDB SYNC MOVIES" `
    "docker exec indian_ott_api sh -c 'sed -n \"1,320p\" /app/app/services/tmdb/sync_movies.py'"

Run-Cmd "TMDB BULK IMPORTER" `
    "docker exec indian_ott_api sh -c 'sed -n \"1,320p\" /app/app/services/tmdb/bulk_importer.py'"

Run-Cmd "TMDB MOVIE SERVICE" `
    "docker exec indian_ott_api sh -c 'sed -n \"1,320p\" /app/app/services/tmdb/movie_service.py'"

Run-Cmd "TMDB CLIENT" `
    "docker exec indian_ott_api sh -c 'sed -n \"1,260p\" /app/app/services/tmdb/client.py'"

Run-Cmd "TMDB WORKER" `
    "docker exec indian_ott_api sh -c 'sed -n \"1,320p\" /app/app/workers/tmdb_worker.py'"

Run-Cmd "TMDB OTT SERVICE" `
    "docker exec indian_ott_api sh -c 'sed -n \"1,320p\" /app/app/services/tmdb/ott_service.py'"

Run-Cmd "TMDB SYNC OTT" `
    "docker exec indian_ott_api sh -c 'sed -n \"1,320p\" /app/app/services/tmdb/sync_ott.py'"

Run-Cmd "IMPORT CHECKPOINT" `
    "docker exec indian_ott_api sh -c 'cat /app/data/import_checkpoint.json'"

Run-Cmd "BULK IMPORT SCRIPT" `
    "docker exec indian_ott_api sh -c 'sed -n \"1,320p\" /app/scripts/bulk_import.py'"

Run-Cmd "PYTHON ENTRYPOINTS" `
    "docker exec indian_ott_api sh -c 'grep -RniE \"if __name__|argparse|click|typer|asyncio.run|incremental|bulk_import|sync_movies|TMDB\" /app/app /app/scripts --include=\"*.py\" 2>/dev/null'"

Run-Cmd "TMDB ENVIRONMENT VARIABLES (NAMES ONLY)" `
    "docker exec indian_ott_api sh -c 'env | cut -d= -f1 | grep -Ei \"TMDB|DATABASE|POSTGRES|REDIS|OTT\" | sort'"

Run-Cmd "API ROUTES" `
    "docker exec indian_ott_api sh -c 'grep -RniE \"@app\\.|@router\\.|include_router\" /app/app --include=\"*.py\" 2>/dev/null'"

Run-Cmd "RECENT API LOGS" `
    "docker compose logs --tail=300 api"

Run-Cmd "GIT STATUS" `
    "git status --short --branch"

Run-Cmd "GIT LOG" `
    "git log --oneline --decorate -15"

Run-Cmd "GIT BRANCHES" `
    "git branch -a"

Run-Cmd "PROJECT TREE" `
    "Get-ChildItem -Recurse -File app,scripts,tests | Select-Object FullName | Out-String"

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " DIAGNOSTIC COMPLETE" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Saved to:" -ForegroundColor Yellow
Write-Host "$PWD\$Output" -ForegroundColor White
Write-Host ""
