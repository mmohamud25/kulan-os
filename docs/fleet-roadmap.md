# Kulan Fleet roadmap

Agreed with Mohamud, 24 October 2026. The name stays Kulan Fleet. One
headline per release; each stage works on its own.

## Sections of the app
1. People (identity): employees, roles (Owner, admin, employee), setup codes,
   disable and re-enable, their files when they leave. Later two-step
   sign-in and groups.
2. Computers (devices): each computer's health, owner, apps, last report,
   encryption; lock, restart, shut down, Lost mode, erase.
3. Policies: updates, firewall, screen lock, encryption, USB sticks,
   required and blocked apps. Later per group.
4. Apps: what's installed everywhere; install on a group; block.
5. Security and compliance: Needs attention, compliance scores, weekly
   reports, access reviews ("who still has admin rights?").
6. Activity: everything, by whom, exportable for auditors.
7. Organisation: name, Owner, admins, recovery code, email connection
   (Google Workspace now, Kulan's own mail server later), billing later.

Today: most of Computers, Policies, Apps (seeing), Activity and a basic
Security and compliance. People doesn't exist yet.

## Stages
All of Fleet is 2.8, released in steps (Mohamud, 24 October 2026); the
details are in docs/plan-2.8.md.
- 2.8a People: accounts, setup codes, roles with the Owner, disable
  everywhere; Shut down and Lost mode.
- 2.8b Groups and policies: groups of people and computers, a policy per
  group, install apps on a group, block USB sticks or allow approved
  drives only, required and blocked apps.
- 2.8c Security, activity and computers: weekly report, admin-rights
  reviews, activity log export, who uses which computer.
- 2.8d Stronger sign-in: two-step sign-in on the computers (phone code or
  security key); security keys for the Owner and admins in Kulan Fleet.
- 2.8e Organisation: Google Workspace mailboxes from People, email
  connection settings.
- 3.0 Work edition: join Fleet and People during setup; an
  automatic-install file for many computers.
- 3.x Single sign-on: Kulan accounts sign in to other apps (an identity
  provider, like Okta).
- Later: Fleet programs for Windows and Mac; Kulan's own mail server in
  People; Fleet for Kulan Cyber Advisory's clients.
