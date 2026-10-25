# Finding errors

Restart the running Flask server after updating these files. Run commands from
`level-app` with the same Python environment and Windows account as the server.

## Look up a reference

Error pages and banners display a 16-character reference. Every response also
includes an `X-Request-ID` header. Copy that reference and what the user was doing.

```powershell
python -m flask --app app show-error PASTE_REFERENCE_HERE
```

This displays matching events and a readable traceback. Logs live in
`learning/logs/app.jsonl`, with three rotated backups of approximately 2 MB each. Events
include UTC time, process ID, request ID, method, route, status and duration.
Exceptions include the full traceback and original chained cause: files,
functions and line numbers. Storage errors also record the operation, target
path, errno and Windows error number when provided by the OS.

Request bodies, answers, query strings, cookies and authorization headers are not
automatically logged. Logs still contain internal paths and exception messages;
keep them private. Git ignores the log directory. If file logging fails, the
application terminal receives fallback errors.

## Check the denied progress file

```powershell
python -m flask --app app check-storage
```

This reads each local-user language file and tests writing/replacing disposable
probe files in its directory. It never rewrites real progress. Another account's
successful check does not prove the server account can access the files.

Interpret the logged storage operation:

* `read`: inspect the existing file's ACL, the Python process account, and
  temporary file-sharing conflicts.
* `replace`: a temporary write succeeded, but replacing the destination failed.
  Check destination permissions and conflicting open handles.
* `prepare directory` or `write temporary file`: inspect directory access,
  free disk space, and the original OS exception.
* `decode`: invalid JSON, non-object JSON, or invalid encoding. Preserve and
  inspect the file instead of resetting it to an empty profile.

Permission failures on reads, directory preparation and replacements receive
three bounded retries (50, 150 and 300 ms). Repeated denial returns 503 with a
reference and preserves the original file. Other errors are not blindly retried.
Failed writes are never reported as successful saves. The adapter remains for
single-process local development; multi-worker deployment needs database
transactions, as described in INTEGRATION.md.

The reported October 24 error could not be reproduced during inspection. All
three files were readable, directory-write checks passed, and the inspected
Spanish file/directory ACL granted the user's account full control. The supplied
traceback stopped before the application's failing frame, so the original cause
is not proven. A temporary sharing conflict is possible. Future failures now
retain the exact operation and cause.

## Browser and process diagnostics

All app pages load `ui-diagnostics.js` before activity scripts. Server failures
show their request reference; network failures show a connection message.
Uncaught script failures and promise rejections go to a bounded local endpoint
(five reports per page and twenty per server minute) and receive references.
Script filenames and line/column numbers are recorded when available. Arbitrary
client messages, page contents and stack dumps are not transmitted. Detailed
browser exception messages remain in the browser console. Expected image
fallback errors are not reported as uncaught script failures.

Running `python app.py` also installs process/thread exception hooks. Syntax and
import failures before initialization, process kills, OS crashes, offline clients
and errors already swallowed by application code cannot all be captured by these
hooks. Use the terminal and process supervisor for startup/availability evidence.
Diagnostic-reporting failures do not recursively report themselves.

The default is handled error pages plus full server logging. For interactive
debugging on your own machine, set `$env:APP_DEBUG = '1'` before `python app.py`.
Set it to `0` and restart to return to normal mode.

## How teams operate this

Teams combine request IDs and structured logs with centralized error tracking,
alerts, uptime checks and regression tests. This is the local foundation, not an
external monitoring service or automatic alert delivery. During SaaS integration,
reuse the starter's authentication, CSRF protection, safe references and refresh
client, and send events to one shared sink. Avoid duplicate handlers and local
rotating files across production workers. Use transactional Neon storage.

Verification:

```powershell
python -m unittest discover -s learning/tests -v
node learning/tests/test_ui_diagnostics.cjs
node learning/tests/test_daily_dose_cycle.cjs
node learning/tests/test_shooter_controls.cjs
```


The combined app stores account progress in Neon. Historical local progress and
old logs are not part of the application. Package tests use isolated storage.
