JWS TIMESHEETS — XERO PROJECTS SYNC PATCH

This patch adds:
- Xero Custom Connection authentication
- Sync of active INPROGRESS Xero Projects
- Project upsert/update into PostgreSQL
- Closed/removed Xero projects hidden after the next successful sync
- A manager-only 'Sync from Xero' button
- Xero connection status on Settings

COPY THESE FILES INTO YOUR GITHUB DESKTOP REPOSITORY:
app.py
templates/projects.html
templates/settings.html

Then:
1. GitHub Desktop -> Summary: Add Xero Projects sync
2. Commit to main
3. Push origin
4. Wait for Railway to redeploy and return Online

XERO SETUP
For the simplest single-company setup, use a Xero CUSTOM CONNECTION.
When creating it, request the 'projects' scope (read/write Projects API).
After the Custom Connection is authorised for your Xero organisation:
- Generate/retrieve its Client ID
- Generate its Client Secret
- Put them in Railway web service Variables as:
  XERO_CLIENT_ID
  XERO_CLIENT_SECRET

Do not send the Client Secret in chat.

After Railway redeploys:
JWS Timesheets -> Projects -> Sync from Xero

NOTE
This patch synchronises the project dropdown first.
Writing approved time back to Xero Projects is the next stage because Xero time entries also require a Xero Project Task and Xero user mapping.
