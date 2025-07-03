chrome.webRequest.onBeforeRequest.addListener(
  function(details) {
    const redirectUrl = details.url.replace(
      /\.mesh-app\.local/,
      '.mesh-app.localhost'
    );
    return { redirectUrl };
  },
  { urls: ["*://*.mesh-app.local/*"] },
  ["blocking"]
);