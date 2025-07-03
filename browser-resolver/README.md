# Browser-IsleMesh-Resolver

This is a simple general browser extension to help browsers with resolving IsleMesh urls,
should be used to help with debugging and certain applications where internal-only special
domains need to be accessible over .local subdomains via the browser.

While you can resolve mdns sub-domains via curl or api calls through other servers,
it won't work through most browsers since their mdns handlers do not anticipate
there being subdomains for mdns websites.  So we need to be able to make a custom
browser add-on in order to help your browser resolve the sites.