"use client";

import { useEffect } from "react";

export function GoogleCallback() {
  useEffect(() => {
    function forwardCallback() {
      const url = new URL(window.location.href);
      const callback = new URLSearchParams(url.hash.slice(1));

      // Google returns to root with a fragment that the server cannot read.
      if (
        callback.has("google_error") ||
        callback.has("google_challenge") ||
        callback.has("google_success")
      ) {
        url.searchParams.set("auth", "login");
        window.location.replace(url);
      }
    }

    forwardCallback();
    window.addEventListener("hashchange", forwardCallback);
    return () => window.removeEventListener("hashchange", forwardCallback);
  }, []);

  return null;
}
