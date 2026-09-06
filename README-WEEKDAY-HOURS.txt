JWS TIMESHEETS — NORMAL WORKING HOURS UPDATE

NORMAL WEEK
Monday    8.5 hours
Tuesday   8.5 hours
Wednesday 8.5 hours
Thursday  8.5 hours
Friday    6.0 hours
Saturday  0 hours
Sunday    0 hours

Total normal week = 40 hours.

ANNUAL LEAVE
- Still entered in hours.
- Partial leave is supported (for example 2 hours).
- The screen shows the normal hours for that day as a guide.
- A full Monday–Thursday leave day would normally be 8.5 hours.
- A full Friday leave day would normally be 6 hours.

PUBLIC HOLIDAYS
- Still a simple full-day tick box.
- Monday–Thursday public holiday = 8.5 paid hours automatically.
- Friday public holiday = 6 paid hours automatically.
- Weekend public holiday = 0 hours.
- The employee cannot alter the public-holiday hours.

EMPLOYEE SETUP
New employees default to:
Mon–Thu normal hours = 8.5
Friday normal hours = 6
Normal weekly hours = 40

FILES TO REPLACE
app.py
templates/dashboard.html
templates/employees.html
templates/summary.html
static/app.js
static/styles.css

This package SUPERSEDES the previous leave/public-holiday package.

DEPLOY
1. Replace the files in the GitHub Desktop repository.
2. Summary: Set weekday normal hours
3. Commit to main
4. Push origin
5. Wait for Railway to redeploy and return Online.
