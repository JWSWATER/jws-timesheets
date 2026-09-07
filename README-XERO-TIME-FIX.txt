JWS TIMESHEETS — XERO TIME ENDPOINT + FULL PROJECT HOURS FIX

THIS PATCH FIXES THE 404 ERROR SHOWN:
Xero Projects API failed (404) on POST /Projects/.../Time

1. XERO PROJECTS API URLS
The app now uses Xero's documented lowercase endpoint paths:
- /projects
- /projectsusers
- /projects/{projectId}/tasks
- /projects/{projectId}/time

The documented time-entry endpoint is:
POST /projects/{projectId}/time

2. BREAKS ARE NOT DEDUCTED FROM XERO PROJECTS
Xero Projects now receives the FULL Start-to-Finish duration for every project row.

Example:
Start: 08:00
Finish: 17:00
Break: 30 minutes

JWS paid/payroll worked hours can still use the break separately.
Xero Project time = 9:00 hours.

If several projects are worked during the same day, each project receives its
full recorded Start-to-Finish duration. The daily break is NOT allocated across
the projects.

3. EXISTING FAILED TEST
The failed Shawn/William export shows 0 of 5 time entries sent, so no Xero time
entry IDs were stored by the app.

A task may already have been created in Xero before the time-entry POST failed.
That is safe: on retry the app checks for an existing active task with exactly
the same description/name and reuses it rather than intentionally creating a
duplicate.

FILE TO REPLACE
app.py

DEPLOY
1. Replace app.py in the GitHub Desktop repository.
2. Summary: Fix Xero time endpoint and charge full project hours
3. Commit to main
4. Push origin
5. Wait for Railway to return Online.

RETEST
Manager View -> Shawn's approved timesheet -> View summary
-> Send Pending Time to Xero Projects

Then check the Xero project for:
- task created/reused from the timesheet description
- Staff member = William McKibbin
- full Start-to-Finish duration
