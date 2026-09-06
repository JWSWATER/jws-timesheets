JWS Timesheets CSRF hotfix

Overwrite these files in your GitHub Desktop local repository:
templates/login.html
templates/employees.html
templates/management.html

This fixes:
- Add Employee -> 400 Bad Request / CSRF token missing
- Employee Enable/Disable CSRF
- Manager Approve/Reject CSRF
- Removes demo usernames/passwords from production login screen
- Removes the stray > character on the login page

After copying:
1. Return to GitHub Desktop
2. Summary: Fix form security tokens
3. Commit to main
4. Push origin
5. Railway should redeploy automatically
