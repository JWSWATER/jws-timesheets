JWS TIMESHEETS — REMOVE WEEKEND HOLIDAY OPTIONS

CHANGE
Saturday and Sunday no longer show:
- Annual Leave (hrs)
- Public Holiday? tick box

Employees can still enter normal project work on Saturday/Sunday when required.

BACKEND PROTECTION
Even if a weekend leave/public-holiday value is submitted manually, the server records 0 hours for it.

FILES TO REPLACE
app.py
templates/dashboard.html

DEPLOY
1. Replace these two files in your GitHub Desktop repository.
2. Summary: Remove weekend holiday options
3. Commit to main
4. Push origin
5. Wait for Railway to redeploy.
