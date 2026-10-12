# Security notes

## Secrets

* Credentials and phone numbers are read from the environment (`.env`, which is
  gitignored). Nothing secret belongs in source. `.env.example` lists the names.
* `tests/test_no_secrets.py` fails the build if a Twilio/Groq/ElevenLabs-style
  key or a real-looking phone number appears in the working tree.
* Optional but recommended: `pip install pre-commit && pre-commit install` to run
  gitleaks on every commit.

## A credential was committed in the past

An earlier revision of `config.py` contained a Twilio account SID, auth token and
phone numbers. They are no longer in the working tree, but **they are still in
git history** and, because the repository is public, should be treated as
compromised.

1. **Rotate first.** In the Twilio console, rotate the auth token (and delete any
   API key you do not recognise). Until then, anyone who has cloned the repo can
   send messages on your account. Removing the text from `main` does not change
   this.
2. Put the new values in your local `.env`.
3. Optionally rewrite history so the old values disappear from the repository.
   This is destructive and changes every commit hash, so read it before running:

   ```bash
   pip install git-filter-repo
   # replacements.txt: one "old==>REMOVED" line per secret
   git filter-repo --replace-text replacements.txt
   git push --force --all
   ```

   Anyone with an existing clone, and any fork, still has the old history, which
   is why step 1 matters more than step 3.
4. Scan history afterwards: `gitleaks detect --source .`

## Phone numbers

The emergency contact number is personal data. It is configured via
`EMERGENCY_PHONE` and must not be committed.
