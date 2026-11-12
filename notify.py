"""Send one Pushover message to the operator's phone. Nothing else.

WHY A SEPARATE FILE. The collectors and audits run as workflows and print
to a log nobody reads at 7am. This exists so a scheduled check can push its
finding to a phone instead, and it is deliberately the only thing it does:
it computes nothing, reads no sheet, and reaches no exchange, so a bug here
can at worst fail to send a message.

CREDENTIALS COME FROM THE ENVIRONMENT, NEVER FROM AN ARGUMENT AND NEVER
FROM THIS FILE. PUSHOVER_APP_TOKEN and PUSHOVER_USER_KEY are GitHub
secrets. Passing them on a command line would put them in the process
table and in the workflow log; hardcoding them would put them in a public
repository, which this one is.

THE MESSAGE IS TRUNCATED RATHER THAN DROPPED. Pushover's limit is 1024
characters and it rejects anything longer outright, so a long summary would
mean no notification at all -- the failure mode where the operator learns
nothing and assumes everything is fine. Better a cut message that says it
was cut.

  NOTIFY_TITLE   short title, defaults to a generic one
  NOTIFY_BODY    the message
  NOTIFY_URL     optional link, shown as a tap-through
"""
import os
import sys

LIMIT = 900          # headroom under Pushover's 1024, as botrun.py uses
TITLE_LIMIT = 250


def main():
    token = os.environ.get("PUSHOVER_APP_TOKEN", "").strip()
    user = os.environ.get("PUSHOVER_USER_KEY", "").strip()
    title = (os.environ.get("NOTIFY_TITLE") or "Memecoin bot").strip()
    body = (os.environ.get("NOTIFY_BODY") or "").strip()

    if not body:
        print("  no NOTIFY_BODY; nothing to send")
        return 1
    if not token or not user:
        # SAID OUT LOUD RATHER THAN SKIPPED QUIETLY. A silent skip here
        # looks identical to a delivered message from the caller's side.
        print("  PUSHOVER_APP_TOKEN or PUSHOVER_USER_KEY is not set, so NO")
        print("  MESSAGE WAS SENT. The workflow must pass both secrets.")
        return 1

    if len(title) > TITLE_LIMIT:
        title = title[:TITLE_LIMIT - 3] + "..."
    cut = False
    if len(body) > LIMIT:
        # The suffix's own length is subtracted rather than guessed at. A
        # hardcoded 24 produced 902 characters against a stated limit of
        # 900 -- harmless here, but a limit a file does not keep is not a
        # limit, and the next person to raise LIMIT would inherit the slip.
        suffix = "\n...[cut, see the run log]"
        body = body[:LIMIT - len(suffix)].rstrip() + suffix
        cut = True

    import requests
    data = {"token": token, "user": user, "title": title, "message": body}
    url = (os.environ.get("NOTIFY_URL") or "").strip()
    if url:
        data["url"] = url
        data["url_title"] = "Open the run"
    try:
        r = requests.post("https://api.pushover.net/1/messages.json",
                          data=data, timeout=15)
    except Exception as e:
        print(f"  send failed: {type(e).__name__}: {str(e)[:120]}")
        return 1
    # The body is printed so the run log carries what the phone got, but
    # never the token or the user key.
    print(f"  HTTP {r.status_code} {r.text[:200]}")
    print(f"  title: {title}")
    print(f"  {len(body)} character(s) sent"
          + ("  (TRUNCATED)" if cut else ""))
    if not r.ok:
        print("  Pushover REJECTED the message, so the phone got nothing.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
