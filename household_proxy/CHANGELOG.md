# Changelog

All notable changes to Household Proxy. See the [repository](https://github.com/sameerkotra/ha-apps).

## 0.1.0

- First version: sites opened inside Home Assistant through its sign-in (ingress), at `…/s/<short name>/`.
- Who opens each site: admins only, everyone, or chosen people; admins open every site; people can be
  turned off in **Admin → People**.
- Pages written for their own address work under Home Assistant's: links, forms, images, styles,
  redirects and cookies are rewritten on the way through; big downloads and uploads stream.
- Only the sites an admin lists can be reached; internet addresses only when allowed per site; the
  address is checked when connecting, not only when saved.
- Home Assistant's own sign-in details and cookies never reach a site; a site's extra headers never
  reach the browser and are left out of backups.
- Requests from other web sites are refused, for the sites too.
