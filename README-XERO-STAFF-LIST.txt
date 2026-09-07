JWS TIMESHEETS — FULL XERO STAFF MEMBER LIST

WHAT CHANGED

The Xero Staff Member dropdown no longer relies only on:
  /ProjectsUsers

It now primarily uses:
  GET https://api.xero.com/api.xro/2.0/Users

and merges those results with /ProjectsUsers.

REQUIRED XERO SCOPE
accounting.settings.read

You have already added and re-authorised this scope.

WHY
The Xero Projects-users endpoint was only returning Jonathon in JWS Timesheets.
The Accounting Users endpoint returns Xero organisation users with:
- UserID
- FirstName
- LastName
- EmailAddress

The app normalises these records and stores the selected UserID invisibly for
the Xero Project time entry.

EXAMPLE
JWS employee: Shawn McKibbin
Xero Staff Member: William McKibbin

The display names do not need to match.

TOKEN CHANGE
The Custom Connection access-token request now asks for:
projects accounting.settings.read

Optional Railway override:
XERO_SCOPES = projects accounting.settings.read

Do not add that Railway variable unless you need to override the default.

FILES TO REPLACE
app.py
templates/employees.html
static/styles.css

DEPLOY
1. Replace the three files in your GitHub Desktop repository.
2. Summary: Load full Xero staff member list
3. Commit to main
4. Push origin
5. Wait for Railway to return Online.

TEST
1. Sign in as Primary Admin.
2. Open Employees.
3. Open Shawn's Xero Staff Member dropdown.
4. Look for William McKibbin.
5. Select William and click Save.

If William still does not appear, copy the exact error shown above the Employees
page. That will tell us whether Xero's Accounting /Users endpoint contains the
staff shown in the Xero project-time screen.
