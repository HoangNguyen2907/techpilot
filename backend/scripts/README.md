# Hacker News ingestion scripts

Run these scripts from any working directory. They resolve the repository and
backend directories from their own location. The downloader needs network access
to Algolia; loading and tagging need the backend database configuration.

```bash
./backend/scripts/initial_ingestion.sh
./backend/scripts/daily_fetch.sh
./backend/scripts/weekly_refresh.sh
```

- `initial_ingestion.sh` downloads stories matching aliases in
  `data/tech_dict.yaml`, from 2025 onward, with at least 50 points. It then
  loads the local payloads and tags the corpus. Run this for the initial corpus
  ingestion.
- `daily_fetch.sh` repeats the dictionary-based fetch and refreshes stories
  from the last day so points and comment counts stay current. It then loads
  the downloaded payloads and tags stories that are new, stale, or need tagging.
- `weekly_refresh.sh` does the same fetch and load/tag cycle, refreshing
  existing stories from the last seven days.

All three commands use `--min-points 50` and `--since 2025`. Change those
arguments in the script if the ingestion window or score threshold should
differ. The downloader's refresh window only refreshes matching stories it
finds; it does not remove stories that disappear from Algolia.

## Background schedule

Install the user's crontab entries from the repository root:

```bash
./backend/scripts/install_cron.sh
```

The installer preserves unrelated crontab entries and replaces its own entries
if run again. It schedules the daily fetch Monday through Saturday at 2:00 AM
and the weekly refresh on Sunday at 2:00 AM, using the computer's local timezone.
Cron runs these jobs without an interactive terminal; output is appended to
`backend/logs/daily.log` and `backend/logs/weekly.log`. A shared lock prevents
the scheduled jobs from overlapping. To remove only these jobs, run
`crontab -e` and delete the lines between `BEGIN techpilot-hn-ingest` and
`END techpilot-hn-ingest`.

You can manually run a background-scheduled job through the same overlap guard:

```bash
./backend/scripts/cron_job.sh daily
./backend/scripts/cron_job.sh weekly
```
