JWS TIMESHEETS — APPROVED TIME TO XERO PROJECTS

WHAT THIS UPDATE DOES

1. MANAGER APPROVAL -> XERO PROJECTS
When a manager approves a submitted timesheet, the app immediately tries to
send the worked project rows to Xero Projects.

2. TASKS ARE CREATED FROM THE TIMESHEET DESCRIPTION
For each project-time row:
- The app uses the Description as the Xero Project task name.
- If an ACTIVE task with exactly the same name already exists on that project,
  it reuses that task.
- Otherwise it creates a new task.
- Xero limits task names to 100 characters, so longer descriptions are
  truncated to 100 characters for the task name.
- The full description is still sent on the Xero time entry.

3. SAFE DEFAULT FOR NEW TASKS
Newly created tasks are:
- NON_CHARGEABLE
- £0 rate
This avoids accidentally changing client invoicing.
Existing matching Xero tasks keep their existing charge/rate settings.

Optional Railway variables if you later want new tasks chargeable:
XERO_TASK_CHARGE_TYPE = TIME
XERO_TASK_RATE = <hourly charge rate>
XERO_TASK_CURRENCY = GBP

4. EMPLOYEE -> XERO PROJECTS USER
The Employees page now has a Xero Projects user dropdown.
The app also tries an exact name match automatically, so Shawn McKibbin should
map automatically if his Xero Projects name is exactly the same.
If not, choose his Xero user once from Employees and click Save.

5. BREAKS
There is one break field per day in JWS Timesheets. When a day contains
multiple project rows, the break is allocated proportionally across those
worked rows so the total minutes sent to Xero equal the approved worked total.

6. DUPLICATE PROTECTION
Each local timesheet row stores the Xero Task ID and Xero Time Entry ID.
Already-sent rows are skipped on retry.
Stable Xero Idempotency-Key values are also used for task/time creation.

7. YOUR ALREADY-APPROVED TEST TIMESHEET
Because Shawn's test timesheet was approved before this update, open:
Manager View -> Shawn's timesheet -> View summary
Then click:
Send Pending Time to Xero Projects

Future approvals will attempt this automatically.

8. VALIDATION
On final submission, each worked row must have:
- Start
- Finish
- Project
- Description
because Description is required for automatic Xero task creation.

FILES TO REPLACE
app.py
templates/employees.html
templates/management.html
templates/summary.html
static/styles.css

DEPLOY
1. Copy these files into your GitHub Desktop jws-timesheets repository.
2. Summary: Export approved time to Xero Projects
3. Commit to main
4. Push origin
5. Wait for Railway to redeploy and return Online.

FIRST TEST
1. Open Employees as Manager.
2. Check Shawn shows against the correct Xero Projects user.
   If not, select him and Save.
3. Open Shawn's already-approved timesheet.
4. Click Send Pending Time to Xero Projects.
5. Check the selected Xero Project:
   - Task created from Description
   - Time entry recorded against Shawn
   - Minutes match the approved JWS time after break deduction.

NOTE
This update is for XERO PROJECTS only.
Xero Payroll / annual leave / public holiday export is the next separate stage.
