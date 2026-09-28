# Going public through a Cloudflare Tunnel (2026-09-28)

The user wants Märkmik live on the internet, the same way the Seljakotirobot site is
(`steam.tigetiiger.ee`, a `cloudflared` container, docs in `~/Projects/steam-seljakotirobot`).

## The user's decisions

| Question | Answer |
|---|---|
| Cloudflare Access in front of the owner pages | **No.** The app's own password and rate limits only |
| The Tailscale address `http://100.122.97.7:8082` | **Keep it working.** Secure cookies only on the public HTTPS host |

## What the user does in Cloudflare

1. Zero Trust → Networks → Tunnels → Create a tunnel → Cloudflared → name `markmik` → save.
2. Copy only the token. Nothing is installed on the host: the container runs it.
3. Public Hostname: subdomain (e.g. `markmik`), domain `tigetiiger.ee`, service **HTTP**
   `web:8000`.
4. Put `TUNNEL_TOKEN=…` and `PUBLIC_URL=https://markmik.tigetiiger.ee` into `.env`.

## Lead's calls

**Reuse Küsimustik's wiring** (`~/Projects/kusimustik`, built 2026-09-27; brain
`pages/kusimustik`, "Going public"). There, `cloudflared` runs on its own network with a fixed
address, and the app trusts `CF-Connecting-IP` and `X-Forwarded-Proto` **only from that address**
(`TRUSTED_PROXY_NET`), never `X-Forwarded-For`. Cookies are `Secure` only on tunnel requests. Copy
and trim the same code, so both apps behave alike.

- **`cloudflared` service** in compose, as in Seljakotirobot: `profiles: ["tunnel"]`,
  `TUNNEL_TOKEN` from `.env`. It runs on its own compose network `tunnelnet` with a fixed
  address, and web has an address there too. `COMPOSE_PROFILES=tunnel` in `.env` turns it on.
  Hardened like the other services: `no-new-privileges`, `cap_drop: [ALL]`, read-only root.
- **The real client address (N2).** Through the tunnel every request comes from cloudflared.
  One helper, `client_ip()`, returns `CF-Connecting-IP` **only when `request.remote_addr` is
  cloudflared's fixed address** (`TRUSTED_PROXY_NET`, as in Küsimustik); otherwise `remote_addr`. A header
  sent by anyone else, Tailscale clients included, is ignored. The login lockout, failure
  counting and every rate limit use it. No blanket `ProxyFix`: it would trust the header from
  everyone.
- **Secure cookies only through the tunnel.** The session cookie gets `Secure` when the request
  came through the tunnel, via a custom session interface. The Tailscale http address keeps
  plain cookies and keeps working. Tunnel responses also get
  `Strict-Transport-Security: max-age=31536000`, and the tunnel host redirects http to https at
  Cloudflare. `HTTPS_ONLY` stays for the case where everything is https.
- **Public links.** With `PUBLIC_URL` set, share links (`/j/<token>`) and the private ICS feed
  address are always built on `PUBLIC_URL`, even when the owner is on the Tailscale address.
  Then a copied link works for anyone.
- **The Host header:** only `PUBLIC_URL`'s host and the Tailscale address are served, so a
  forged Host cannot poison generated links.
- **Guest routes** stay as they are, with their rate limits, now keyed on the real address.

## Checklist (package `wp/tunnel`)

- [ ] The compose `cloudflared` service and `tunnelnet` with fixed addresses; `.env.example` with
      `TUNNEL_TOKEN`, `PUBLIC_URL`, `COMPOSE_PROFILES`; README "Going public".
- [ ] `client_ip()`, used by login, lockout and rate limits. Tests: a spoofed `CF-Connecting-IP`
      from a non-proxy address is ignored; from the proxy address it is used; two tunnel clients
      get separate buckets and lockouts.
- [ ] Secure cookie and HSTS only through the tunnel. Tests.
- [ ] Share links and the ICS feed URL built on `PUBLIC_URL`. Tests.
- [ ] Allowed Host names. Test.
- [ ] Whole suite green; review; merge.
- [ ] Deploy once the user has put `TUNNEL_TOKEN` and `PUBLIC_URL` in `.env`. Then a smoke test
      from outside (the public URL over HTTPS): login, a share link opened by a guest, the
      rate-limit key checked in the log.
