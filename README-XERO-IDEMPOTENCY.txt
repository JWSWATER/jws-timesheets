JWS TIMESHEETS — XERO IDEMPOTENCY KEY FIX

ERROR FIXED
Xero returned:
"Idempotency Key ... is used with a different request."

CAUSE
The old app generated a time-entry Idempotency-Key only from the local JWS
entry ID.

We then deliberately changed the Xero payload so project time uses the FULL
Start-to-Finish duration and does not deduct breaks.

That meant:
- same idempotency key
- different duration/payload

Xero correctly rejected that as a different request.

NEW BEHAVIOUR
The Xero idempotency key is now deterministically generated from the exact
request being sent, including:
- local JWS entry
- project
- Xero staff member
- task
- date
- duration
- description

Therefore:
- retrying the exact same entry uses the same key and remains duplicate-safe
- changing a legitimate part of the payload produces a different key
- the previous stale idempotency key will no longer conflict

Task-creation idempotency has also been updated to depend on the exact task
payload for the same reason.

BREAK RULE REMAINS
Breaks are NOT deducted from Xero Projects.

Example:
08:00 to 17:00 with 30-minute break
Xero Projects = 9:00

FILE TO REPLACE
app.py

DEPLOY
1. Replace app.py in GitHub Desktop.
2. Summary: Fix Xero idempotency keys
3. Commit to main
4. Push origin
5. Wait for Railway to return Online.

RETEST
Manager View
-> Shawn
-> View summary
-> Send Pending Time to Xero Projects

The screen currently says 0 of 5 sent, so all five local rows remain pending.
