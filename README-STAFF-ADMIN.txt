JWS TIMESHEETS — XERO STAFF MEMBER + MANAGEMENT-ONLY ADMINS

THIS PACKAGE BUILDS ON THE LATEST:
- Xero approved project-time export
- protected Primary Admin
- multiple submission-notification emails
- Xero Payroll ID / employee number

1. XERO STAFF MEMBER
The Employees page now says "Xero Staff Member" rather than "Xero Projects user".

For each employee, choose the person exactly as they appear in Xero when adding
a project time entry under "Staff member".

Example:
JWS employee name: Shawn McKibbin
Xero Staff Member: William McKibbin

The names do NOT need to match.

The app stores Xero's internal userId invisibly after you make the selection.
Automatic name matching has been REMOVED so Shawn cannot accidentally be mapped
to the wrong person.

Technical note:
Xero's product screen calls this "Staff member". The public Projects API still
calls the underlying endpoint /ProjectsUsers and requires its userId when a time
entry is created.

2. XERO PAYROLL ID / EMPLOYEE NUMBER
This remains a separate field.
It will be used for the Xero Payroll integration and does not have to match the
JWS employee's display name.

3. ADDITIONAL ADMIN USERS
The Add Admin User section remains in Settings in its current location.

New additional admins are MANAGEMENT-ONLY:
- Manager View / timesheet approvals
- Employees
- Projects
- Settings
- Change Password

They DO NOT have:
- My Timesheet
- personal Timesheet History
- normal hours
- basic hourly rate
- OT1 or OT2 rates
- weekday working-hour settings

When a management-only admin signs in, /dashboard automatically takes them to
Manager View. The timesheet save endpoint also rejects attempts from these
accounts.

Existing additional admin accounts are automatically changed to management-only
on deployment. The protected Primary Admin is left unchanged.

4. PRIMARY ADMIN SECURITY
Unchanged:
- Primary Admin cannot be disabled or removed.
- Only Primary Admin can add/disable/enable/remove other admins.
- Other admins cannot manage admin accounts.

FILES TO REPLACE
app.py
templates/dashboard.html
templates/history.html
templates/management.html
templates/employees.html
templates/projects.html
templates/settings.html
templates/summary.html
templates/change_password.html
static/styles.css

DEPLOY
1. Copy all files into the matching locations in the GitHub Desktop repository.
2. Summary: Use Xero staff members and management-only admins
3. Commit to main
4. Push origin
5. Wait for Railway to return Online.

FIRST TEST
1. Sign in as Primary Admin.
2. Employees -> Shawn.
3. Under Xero Staff Member choose William McKibbin and click Save.
4. Open Shawn's approved timesheet and retry/send pending project time.
5. Confirm Xero shows William McKibbin in Staff member.
6. Create a temporary additional admin.
7. Sign in as that account.
8. Confirm it opens Manager View and has no My Timesheet or History.
