# Connecting Google Workspace to Kulan Fleet

With Google Workspace connected, Kulan Fleet makes and manages people's
mailboxes along with their accounts on the computers (Kulan OS 2.8e):

- Adding someone in People can make their mailbox (`name@yourdomain`).
  You see its first password once; Google asks them for a new one at
  their first sign-in.
- Disabling someone suspends their mailbox; enabling them unsuspends it.
- Changing their name in People changes it in Google too.
- Removing someone: you choose to suspend their mailbox (mail kept, it can
  come back) or delete it (Google keeps it restorable for 20 days).
- On their Kulan OS computer, they're offered their work email in
  Settings › Online Accounts › Google. It brings mail to Geary, their
  calendar and contacts, and Google Drive to Files.

You do this once, as a Google Workspace **super admin**, and then the
Kulan Fleet **Owner** connects it. It takes about 10 minutes.

## What Kulan Fleet gets
A *service account*: a Google account for a program, not a person. You
allow it to do one thing for your domain: manage users
(`https://www.googleapis.com/auth/admin.directory.user`). It can't read
anyone's mail, files or calendar. Kulan Fleet keeps its key encrypted
with a key only Kulan Fleet's server has. Nobody can read it back, not even
the Owner. Kulan Fleet only ever changes the mailboxes of
people in its People list, and a mailbox that belongs to a Google admin
can only be changed by the Owner.

Every mailbox is a Google Workspace licence, billed by Google as usual.

## 1. A Google Cloud project with the Admin SDK
1. Sign in to [console.cloud.google.com](https://console.cloud.google.com)
   with your super admin account.
2. At the top, open the project list › **New project**. Name it, for
   example, `kulan-fleet`, and create it. Make sure it's selected.
3. **APIs & Services › Library**: search for **Admin SDK API**, open it,
   and click **Enable**.

## 2. The service account and its key
1. **IAM & Admin › Service accounts › Create service account**.
2. Name it `kulan-fleet`. Click **Create and continue**, skip the roles
   (it needs none), and click **Done**.
3. Open the new service account. Note its **Unique ID** (a long
   number). This is the *client ID* for step 3.
4. **Keys › Add key › Create new key › JSON › Create**. A `.json` file
   downloads. Keep it private: it's what lets Kulan Fleet act for your
   domain.

**If "Create new key" is refused** with "Service account key creation is
disabled", that's because Google turns key creation off by default for
organisations made since May 2024. To allow it for this project only:
1. **IAM & Admin › IAM**, at your *organisation* (choose it in the project
   list): give your super admin account the role **Organization Policy
   Administrator**.
2. Back in the `kulan-fleet` project: **IAM & Admin › Organization
   policies**, search for **Disable service account key creation**, open
   it › **Manage policy** › **Override parent's policy** › add a rule with
   enforcement **Off** › **Set policy**.
3. Make the key (step 2.4 above). You can then set the policy back to
   **Inherit parent's policy**. The key you made keeps working.

## 3. Allow it in the Google Admin console
1. Sign in to [admin.google.com](https://admin.google.com) as a super admin.
2. **Security › Access and data control › API controls › Manage Domain
   Wide Delegation**, or go to
   admin.google.com/ac/owl/domainwidedelegation.
3. **Add new**: the **Client ID** is the service account's Unique ID from
   step 2.3. In **OAuth scopes**, enter exactly
   `https://www.googleapis.com/auth/admin.directory.user`
4. **Authorize**. It usually works within minutes, though Google says it
   can take up to 24 hours.

## 4. Connect it in Kulan Fleet (the Owner)
1. Kulan Fleet app: your organisation › **Google Workspace › Connect
   Google Workspace**. In the web dashboard, use the **Google Workspace** card.
2. Choose the `.json` key file.
3. Give the email of a **super admin** Kulan Fleet acts as. Google records
   what Kulan Fleet does under that admin's name in its own audit log.
4. **Connect**. Kulan Fleet checks the connection with Google before saving
   anything. If Google refuses, it says why. Usually step 3 isn't done yet,
   or the email isn't a super admin's.
5. Delete the `.json` file from your computer, including from Downloads.

Then, in People, **Add a person** has **Make a Google mailbox**. Each
person's page has **Work email**, where you can make a mailbox, set a new
Google password or stop managing it. Only the Owner can link an existing
mailbox.

## If something goes wrong
- **Google couldn't be reached, or refused a change.** Kulan Fleet always
  makes its own change first, so someone you disable is disabled on the
  computers immediately. The mailbox shows what's left to do, and so does
  an alert. **Retry** finishes it. If you've made the change yourself in the
  Google Admin console, **Done by hand** stops the retries.
- **"Google hasn't allowed this service account to act for your domain".**
  Step 3 isn't done, or the client ID or scope don't match exactly.
- **"check … is a super admin".** The email Kulan Fleet acts as isn't a
  super admin any more. Connect again with another super admin's email.
- **People can't sign in to Google on their computer** ("This app is
  blocked"). If your organisation restricts third-party apps (**Security ›
  Access and data control › API controls › App access control**), mark
  **GNOME** as trusted.
- **To stop it**, choose **Disconnect** in Kulan Fleet. Mailboxes stay as
  they are. To make the key useless too, delete it in Google Cloud (**Service
  accounts › kulan-fleet › Keys**) and remove the client ID from
  domain-wide delegation.

Sources: Google's [domain-wide delegation](https://knowledge.workspace.google.com/admin/apps/control-api-access-with-domain-wide-delegation)
and [secure by default organisations](https://docs.cloud.google.com/resource-manager/docs/secure-by-default-organizations) help pages.
