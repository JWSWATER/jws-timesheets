JWS TIMESHEETS - FINAL LOCAL BUILD

START ON WINDOWS
1. Extract this ZIP.
2. Make sure Python 3 is installed.
3. Double-click start_windows.bat
4. Open http://127.0.0.1:5050

DEMO LOGINS
Manager: admin / admin123
Employee: john / john123

INCLUDED
- Secure password hashing
- SQLite database
- Individual employee logins
- Manager-only area
- Monday-Sunday timesheets
- Multiple project entries per day
- Start/finish times
- Searchable project field
- Description field
- Daily break deduction
- Weekly totals
- Submit / approve / reject
- Full timesheet summary
- Employee history
- Employee setup with normal and overtime rules
- JWS Water branding
- PWA files for installable app deployment

BEFORE COMPANY-WIDE LIVE USE
- Change the default admin password immediately.
- Set a permanent JWS_SECRET_KEY environment variable.
- Deploy behind HTTPS.
- Back up jws_timesheets.db.
- Connect Xero credentials for live project sync and Xero export.

XERO
The application is structured so the local Projects table can be replaced/updated from Xero Projects.
Live Xero connection is not enabled in this download because it requires your organisation's Xero API credentials and OAuth setup.
