JWS TIMESHEETS — PROTECTED PRIMARY ADMIN
This package SUPERSEDES the previous Admin Users / Email Notifications package.

ADMIN SECURITY RULES

PRIMARY ADMIN
- The original JWS Admin account is marked as the protected Primary Admin.
- It cannot be disabled.
- It cannot be removed.
- No additional admin can change that.
- The app automatically protects the account matching Railway variable
  JWS_ADMIN_USERNAME (currently your original Admin login).
- If no match exists on an older database, the oldest manager account is
  protected as the Primary Admin.

ADDITIONAL ADMINS
- Can use Manager View, approvals, Employees, Projects and Settings.
- Cannot add other admin accounts.
- Cannot disable another admin account.
- Cannot remove another admin account.
- Cannot disable or remove the Primary Admin.

PRIMARY ADMIN CONTROLS
The Primary Admin can:
- Add additional admins.
- Disable additional admins.
- Re-enable additional admins.
- Permanently remove additional admins.

AUDIT SAFETY
If an additional admin has historical timesheet records, Remove will disable
the account instead of deleting it, preserving the historical audit trail.

THIS PACKAGE ALSO RETAINS
- Multiple timesheet-submission notification email addresses.
- Test notification email.
- Xero Payroll ID / employee number field.
- Xero Projects user mapping from the previous Xero export update.

FILES TO REPLACE
app.py
templates/settings.html
templates/employees.html
static/styles.css

DEPLOY
1. Use THIS package instead of the previous
   JWS-Timesheets-Admins-Email-Notifications package.
2. Replace the four files in the GitHub Desktop repository.
3. Summary: Protect primary admin account
4. Commit to main
5. Push origin
6. Wait for Railway to redeploy and return Online.

TEST AFTER DEPLOYMENT
1. Sign in using your original Admin account.
2. Settings -> Admin Users.
3. Your account should show:
   Primary Admin · Protected
4. Create a temporary additional admin.
5. Confirm your Primary Admin can disable/re-enable it.
6. Confirm the Remove button is available for the additional admin.
7. Sign in as that additional admin and confirm admin-management buttons are
   not available.
