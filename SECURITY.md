# Security

These are unofficial Home Assistant apps, built with Claude (Anthropic's AI model) and maintained in
spare time. They hold household data: finances, passwords (Household Vault), chats, family details.
Please report security problems privately so they can be fixed before anyone else learns of them.

## Reporting a problem

Use GitHub's private reporting: on this repository's **Security** tab, choose **Report a
vulnerability**. Please don't open a public issue, discussion or pull request for a security problem.

Helpful to include:

- which app and version (the version is on the app's Information tab in Home Assistant);
- what an attacker could do, and who the attacker is (someone on your network, another person in the
  household with a Home Assistant login, a web page you visit, …);
- the steps to reproduce it, and anything you've found about a fix.

You'll get a reply as soon as possible, on a best-effort basis: there is no bug bounty and no
guaranteed response time. Once a fix is released, the report can be published with credit to you if
you'd like.

## Supported versions

Only the latest version of each app gets fixes. Update from **Settings → Apps** when Home Assistant
offers it; each app's `CHANGELOG.md` says what changed.

## Scope

In scope: the code in this repository — for example a way past Home Assistant's login into an app,
one person reading or changing another person's data, an ordinary user reaching admin-only pages,
cross-site request forgery, stored scripts (XSS), path traversal in uploaded or shared files, the
encryption and key handling in Household Vault, or secrets (such as an AI access key) leaking into
pages, logs or backups.

Out of scope: Home Assistant itself, its Supervisor, ingress and Companion apps (report those to
Home Assistant); the AI providers you connect; and anything that needs someone who already has full
admin access to your Home Assistant or its host.

## How the apps are meant to be run

- Every app is reached only through Home Assistant's own sidebar (ingress). None of them opens a
  network port, and none should be exposed any other way.
- Who someone is comes from Home Assistant's login; the admins are the names in each app's
  `admin_users` option.
- **Household Vault is experimental** and hasn't had an independent security review. Keep your own
  copy of your passwords elsewhere (for example in KeePassXC) and don't rely on it alone.
- An AI provider outside your home network receives what the app sends it (each app's Documentation
  tab says what); a local model such as Ollama keeps it at home.
