# Magpie for Bazaar Server

`server-magpie` is a standalone Bazaar Server contrib. It registers a Magpie
sidebar page and an Overview card using the Server and Overview services.
Install the wheel on the computer running Bazaar Server and add an entry to
that server's existing configuration:

```toml
[[contribs]]
package = "bazaar-contrib-server-magpie==0.3.0rc2"
[[contribs.enabled]]
entry = "server-magpie"
```

Open **Magpie → Add gateway** in a Server account with `magpie.manage` and
enter the gateway's name, base URL, and one Gateway Key. For remote
administration choose a Key with **From any computer** administration in
Magpie. The same Key is used for readings and writes. A read-only Key can be
used for monitoring; a denied admin operation shows the error at its point
of use. `magpie.view` grants access to readings without granting Server-side
management. The integration credential is stored with the Server database
and is used only on the Server, never in a link, page, or event stream.

The API contract targets Magpie `v0.1.4530`. The plugin uses the official
route feed, concurrency, request/usage ledger, cached quotas and management
API. It does not use private GUI endpoints or the local-only Trace API.
Routing and billing views do not show message bodies.

In **Gateway settings → Models**, refresh a provider's list, enter one or
more model IDs, or test models individually or together. Adding to an
automatic list keeps the currently served models as explicit picks.
Connectivity tests are the only actions here that send sample model
requests. They run in Magpie with the provider's existing credentials.

Usage periods follow Magpie's server clock. Request timestamps are shown in
the viewer's time zone.

The Magpie and supported provider marks are from Magpie `v0.1.4530`. Its
upstream MIT license is reproduced in
`src/bazaar_contrib_server_magpie/resources/magpie-license.txt`.
