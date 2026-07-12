# Scrapeboard deploy

HestiaCP production packaging (OpsBoard-style).

**Full guide (all commands):** [hestiacp/README.md](hestiacp/README.md)  
**End-to-end run path (panel + workers):** [../README.md#run-by-default](../README.md#run-by-default)

| | |
|--|--|
| Install | `bash deploy/hestiacp/install.sh` (as **root**) |
| Update | `bash deploy/hestiacp/update.sh` |
| Reset admin password | `bash deploy/hestiacp/reset_admin_password.sh 'NewPass'` |
| Defaults | user `cvmso`, domain `scrape.cvmso.com`, API port **3010** |
| Repo | `https://github.com/x-o-r-r-o/scrapeboard.git` |

Secrets in `panel/backend/.env` are written with **quoted** values so passwords containing `#` work.
