JWS TIMESHEETS — XERO PROJECTS USER VALIDATION

WHAT THE 404 MEANS

The POST endpoint is now correct:
  /projects/{projectId}/time

The remaining issue is the Staff Member supplied in the payload.

Xero's own web screen can show a broader "Staff member" list.
However, Xero's public Projects API documents userId as the Xero user identifier
for the person logging time, and /projectsusers returns active Projects users.

If a selected staff member is not in /projectsusers, the public Projects API
cannot currently create the time entry for that person.

SUPPORTED XERO FIX

For each employee whose project time JWS Timesheets will send:

Xero -> Settings -> Users -> employee -> Projects -> Limited

Projects Limited does not grant ordinary accounting access. It lets the user
work with their own permitted Projects/time.

APP IMPROVEMENT IN THIS PATCH

Before attempting to POST project time, JWS now checks the selected Staff Member
against Xero /projectsusers.

Instead of a generic 404 it will say clearly:

"<Staff member> is visible as a Xero Staff Member but is not currently an
active Xero Projects user. In Xero, give this person Projects -> Limited access,
then retry."

The broader Staff Member dropdown remains in place, so the JWS mapping can still
show:
  Shawn McKibbin -> William McKibbin

Once William is given Projects Limited access, his existing Xero UserID should
become valid for Projects and the same JWS mapping can be retried.

BREAK RULE REMAINS
Xero Projects receives full Start-to-Finish time. Breaks are not deducted.

FILE TO REPLACE
app.py

DEPLOY
1. Replace app.py.
2. Commit: Validate Xero Projects staff access
3. Push origin.
4. Wait for Railway to return Online.
