JWS TIMESHEETS — CHANGE PASSWORD

WHAT THIS ADDS
- A Change Password link in the app sidebar for both employees and managers.
- Users must enter their current password.
- New password must be at least 8 characters.
- New password must be entered twice and must match.
- New password must be different from the current password.
- Password remains securely stored as a hash — never as plain text.
- After changing the password, the user is logged out and signs back in using the new password.
- CSRF protection remains enabled.

FILES TO REPLACE
app.py
templates/dashboard.html
templates/history.html
templates/management.html
templates/employees.html
templates/projects.html
templates/settings.html
templates/summary.html
static/styles.css

NEW FILE TO ADD
templates/change_password.html

DEPLOY
1. Copy all files from this package into the matching locations in the GitHub Desktop repository.
2. Summary: Add change password
3. Commit to main
4. Push origin
5. Wait for Railway to redeploy and return Online.

TEST
1. Sign in as Shawn.
2. Open Change Password.
3. Enter Shawn's current password.
4. Enter a new password twice.
5. Click Change Password.
6. The app should return to Sign In.
7. Confirm the old password no longer works and the new password does.
