---
name: test-tunnel
description: End-to-end PublicHost tunnel check — ssh -R and HTTP request through the assigned subdomain
whenToUse: When you need to manually verify that a tunnel from client to their localhost works end-to-end
---

# E2E tunnel check

Steps:

1. Start the server locally (see the `run-dev-server` skill).
2. Start a "local service" on the client side: `python3 -m http.server 3000`
3. Connect with remote forwarding: `ssh -R 0:localhost:3000 -p 2222 localhost`
   (omit `-N` so you see the banner with the assigned subdomain).
4. For local testing, add the assigned subdomain to `/etc/hosts`:
   `127.0.0.1 <label>.publichost.run` (this replaces wildcard DNS).
5. Check: `curl -v http://<label>.publichost.run/` —
   the local `http.server` from step 2 should respond.
6. After testing, remove the line from `/etc/hosts` and stop the processes.

If step 5 fails: check the server log (Assigned/Released/Tunnel), verify the
listener was created for the subdomain, and confirm the `Host` header reaches
the proxy.
