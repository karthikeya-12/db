# Connectors

Each connector needs an OAuth app registered with the provider. Nango stores the app's client id and secret
(as an *integration*) and keeps every user's tokens fresh; this project reads the provider's API with those tokens.

## One-time setup for any provider

1. Open the Nango dashboard (`http://localhost:3003`) → **Integrations** → **Set up new integration**.
2. Choose the provider and set the **Unique Key** to the provider key in the table below. The app lists
   integrations by key, so keep the default.
3. Create the OAuth app at the provider (steps below), using the callback URL
   `http://localhost:3003/oauth/callback`. In production use your public Nango URL instead.
4. Paste the client id, client secret and the scopes into the Nango integration and save.
5. In the app open **Connectors**, press **Connect** on the provider card, and authorise.
   The first scan starts straight away; later scans run every `CONNECTOR_POLL_MINUTES`.

| Connector | Nango key | Scopes to request | What is ingested |
|---|---|---|---|
| Gmail | `google-mail` | `https://www.googleapis.com/auth/gmail.readonly` | Messages |
| Google Calendar | `google-calendar` | `https://www.googleapis.com/auth/calendar.readonly` | Events |
| Google Drive | `google-drive` | `https://www.googleapis.com/auth/drive.readonly` | Docs, Sheets, Slides, PDFs, text |
| Slack | `slack` | user scopes `channels:history`, `channels:read`, `users:read` | One record per channel per day |
| Notion | `notion` | (set by the integration's capabilities: read content) | Pages |
| Outlook | `outlook` | `offline_access`, `Mail.Read` | Messages |
| OneDrive for Business | `one-drive` | `offline_access`, `Files.Read.All` | Readable files |
| OneDrive Personal | `one-drive-personal` | `offline_access`, `Files.Read` | Readable files |
| Dropbox | `dropbox` | `files.metadata.read`, `files.content.read` | Readable files |
| GitHub | `github` | `repo`, `read:user` | Issues and pull requests |
| Jira | `jira` | `read:jira-work`, `read:jira-user`, `offline_access` | Issues with comments |
| Confluence | `confluence` | `read:confluence-content.all`, `search:confluence`, `offline_access` | Pages |

Listed as "coming soon" (shown greyed out): SharePoint, Microsoft Teams, HubSpot, Zoom.

## Provider notes

- **Google** (Gmail, Calendar, Drive): one OAuth client in Google Cloud Console (APIs & Services → Credentials →
  OAuth client ID, type *Web application*) can serve all three integrations. Enable the Gmail, Calendar and Drive
  APIs on the project. While the consent screen is in *Testing*, add yourself as a test user.
- **Microsoft** (Outlook, OneDrive): register one app in Microsoft Entra ID → App registrations, with a *Web*
  redirect URI of the Nango callback, and add the delegated Graph permissions from the table. OneDrive Personal
  needs the "personal Microsoft accounts" audience.
- **Slack**: create a Slack app, add the user scopes above, and add the app to each public channel to be read.
- **Notion**: create a *public* integration (OAuth requires it) and share the pages you want ingested with it.
- **Dropbox**: create an app with *Scoped access*, permissions `files.metadata.read` and `files.content.read`.
- **GitHub**: use an OAuth App (key `github`). Only repositories the authorising user can see are read.
- **Jira / Confluence**: one Atlassian OAuth 2.0 (3LO) app serves both; every site the account can reach is read.

## How new data is found

The free self-hosted Nango does not run syncs, so the app polls: every `CONNECTOR_POLL_MINUTES` it asks each enabled
connection for items newer than its stored cursor (first run: the last `CONNECTOR_INITIAL_DAYS` days), at most
`CONNECTOR_MAX_ITEMS_PER_SCAN` per scan, and queues anything new for ingestion. A changed item is re-ingested as a new
version; identical content is skipped. **Sync now** on a connection triggers a scan immediately.

## Adding another provider

1. Add a `ProviderFetcher` in `src/ingestion_pipeline/connectors/providers/` (`list_changes` and `fetch`).
2. Register it in `providers/__init__.py` and add a `CatalogEntry` in `connectors/catalog.py`.
3. Add a mocked-HTTP test in `tests/test_providers.py`.
