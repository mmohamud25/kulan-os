# Connecting Microsoft 365 to Kulan Fleet

With Microsoft 365 connected, Kulan Fleet makes and manages people's
mailboxes along with their accounts on the computers (Kulan OS 2.12), as it
does for Google Workspace:

- Adding someone in People can make their mailbox (`name@yourdomain`). Kulan
  Fleet gives it the licence you chose, or your first Microsoft 365 licence
  with a mailbox (Exchange Online) that has seats free. Licences that only
  carry Exchange's basic "foundation" (Power BI, Visio, Project) don't count:
  they give no mailbox. You see its first password once; Microsoft asks them
  for a new one at their first sign-in.
- Disabling someone blocks their sign-in and signs them out everywhere;
  enabling them lets them sign in again.
- Changing their name in People changes it in Microsoft 365 too.
- Removing someone: you choose to block their mailbox (mail kept, it can
  come back, its licence stays in use) or delete it (Microsoft keeps it in
  **Deleted users** for 30 days, where it can be restored).
- On their Kulan OS computer, they're offered their work email at sign-in
  and in Kulan Settings › About Kulan OS. **Open** opens it in Outlook on
  the web, with their address filled in.

You do this once, as a Microsoft 365 **Global Administrator** (or a
Privileged Role Administrator, who can give admin consent), and then the
Kulan Fleet **Owner** connects it. It takes about 10 minutes.

## What Kulan Fleet gets
An *app registration*: an identity for a program, not a person. You give it
these Microsoft Graph **application permissions** and nothing else:

| Permission | What for |
| --- | --- |
| `User.ReadWrite.All` | make, rename, block and delete users, and give them a licence |
| `User-PasswordProfile.ReadWrite.All` | first passwords, and new ones when you ask |
| `Organization.Read.All` | see your licences and how many are free |
| `RoleManagement.Read.Directory` | tell admin accounts apart, including people eligible for a role (only the Owner changes those) |
| `GroupMember.Read.All` | the same for people who get a role through a group |

It can't read anyone's mail, files or calendar. Kulan Fleet keeps the app's
client secret encrypted with a key only Kulan Fleet's server has. Nobody can
read it back, not even the Owner. Kulan Fleet only ever changes the mailboxes
of people in its People list, and a mailbox whose user holds a Microsoft
Entra admin role can only be changed by the Owner.

Every mailbox uses a Microsoft 365 licence, billed by Microsoft as usual.

## 1. Register the app
1. Sign in to [entra.microsoft.com](https://entra.microsoft.com) as a
   Global Administrator.
2. **Identity › Applications › App registrations › New registration**.
3. Name it `Kulan Fleet`. Under **Supported account types**, choose
   **Accounts in this organizational directory only**. Leave **Redirect
   URI** empty. Click **Register**.
4. On the app's **Overview** page, note the **Application (client) ID** and
   the **Directory (tenant) ID**.

## 2. Give it its permissions
1. In the app: **API permissions › Add a permission › Microsoft Graph ›
   Application permissions**.
2. Search for and tick each of the five permissions above, then **Add
   permissions**.
3. **Grant admin consent for** *your organisation* › **Yes**. Each
   permission's status must say **Granted**.
4. You can remove the `User.Read` permission (a delegated one) that the app
   started with. Kulan Fleet doesn't use it.

## 3. Make a client secret
1. In the app: **Certificates & secrets › Client secrets › New client
   secret**.
2. Describe it (`Kulan Fleet`) and choose when it expires (Microsoft
   suggests 180 days; the longest is 24 months).
3. Copy its **Value** straight away. Microsoft shows it only now. Don't copy
   the **Secret ID**: that isn't the secret.

## 4. Connect it in Kulan Fleet (the Owner)
1. Kulan Fleet app: your organisation › **Work email › Connect Microsoft
   365**. In the web dashboard, use the **Work email** card.
2. Enter the **Directory (tenant) ID**, the **Application (client) ID**, the
   secret's **Value** and your email domain (`yourcompany.com`, one of your
   verified domains in Microsoft 365).
3. Choose the country people's mailboxes are used in (two letters, `US`,
   `GB`, `SO`...). Microsoft needs it before it gives anyone a licence.
   If you have more than one licence with a mailbox, you can name the one
   new mailboxes get, as Microsoft names it in **Billing › Licenses**
   (`O365_BUSINESS_ESSENTIALS`, `ENTERPRISEPACK`...).
4. **Connect**. Kulan Fleet checks the app with Microsoft before saving
   anything, and says which licence new mailboxes get and how many are
   free. If Microsoft refuses, it says why.

Then, in People, **Add a person** has **Make a Microsoft 365 mailbox**. Each
person's page has **Work email**, where you can make a mailbox, set a new
Microsoft password or stop managing it. Only the Owner can link an existing
mailbox.

**Before the secret expires**, make a new one (step 3) and choose **Connect
again with a new secret**. Until then, Kulan Fleet's changes to mailboxes
wait, and an alert says the secret has expired.

**Moving from Google Workspace:** Kulan Fleet looks after one service at a
time. Disconnect Google Workspace, unlink people's Google mailboxes in People
(for someone already removed, **Done by hand**), then connect Microsoft 365
and link each person's new mailbox.

## If something goes wrong
- **Microsoft couldn't be reached, or refused a change.** Kulan Fleet always
  makes its own change first, so someone you disable is disabled on the
  computers immediately. The mailbox shows what's left to do, and so does
  an alert. **Retry** finishes it. If you've made the change yourself in the
  Microsoft 365 admin center, **Done by hand** stops the retries.
- **"Microsoft won't let Kulan Fleet…".** A permission is missing, or admin
  consent wasn't given (step 2.3).
- **"Microsoft didn't accept the client secret".** Copy the secret's
  **Value**, not its ID, or make a new secret.
- **"there's no free licence".** Buy or free up a licence with email in the
  Microsoft 365 admin center (**Billing › Licenses**), then try again.
- **"Microsoft only lets Kulan Fleet … of an admin account".** Microsoft
  protects admin accounts from apps that aren't admins themselves. Change
  that account in the Microsoft 365 admin center, or give the Kulan Fleet app
  the **Privileged Authentication Administrator** role (Microsoft Entra ›
  Roles and admins), which lets it change any account.
- **A new mailbox takes a few minutes to work.** Microsoft sets the mailbox
  up after the licence is given; sign-in works at once, mail shortly after.
- **To stop it**, choose **Disconnect** in Kulan Fleet. Mailboxes stay as
  they are. To make the secret useless too, delete it in Microsoft Entra
  (**App registrations › Kulan Fleet › Certificates & secrets**), or delete
  the app.

Sources: Microsoft Learn's [Register an application](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app)
and the [Microsoft Graph permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference).
