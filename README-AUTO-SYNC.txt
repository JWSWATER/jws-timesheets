JWS TIMESHEETS — AUTOMATIC XERO PROJECT SYNC

WHAT THIS UPDATE DOES
- Automatically checks Xero when an employee opens My Timesheet.
- Refreshes active Xero Projects if the cached list is more than 10 minutes old.
- Checks again when a project search is used, so a long-open timesheet also stays current.
- Keeps the existing manual 'Sync from Xero' button as a backup.
- If Xero is temporarily unavailable, employees continue using the last successfully cached project list.
- Failed Xero calls are throttled so a temporary outage does not trigger a request on every keystroke.
- Stores last-sync information in PostgreSQL, so the timing is shared by all app users/workers.

FILES TO REPLACE IN YOUR GITHUB DESKTOP REPOSITORY
app.py
templates/projects.html

THEN
1. GitHub Desktop Summary: Automatic Xero project sync
2. Commit to main
3. Push origin
4. Wait for Railway to redeploy and return Online.

OPTIONAL
The default refresh period is 10 minutes.
You can change it in Railway by adding:
XERO_PROJECT_SYNC_MINUTES = 10
(use another whole-number minute value if preferred)

No scheduled worker or extra Railway service is required.
