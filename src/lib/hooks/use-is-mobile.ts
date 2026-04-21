"use client";

import { useSyncExternalStore } from "react";

const MOBILE_MQL = "(max-width: 639px)";

function subscribeMobile(callback: () => void) {
  const mql = window.matchMedia(MOBILE_MQL);
  mql.addEventListener("change", callback);
  return () => mql.removeEventListener("change", callback);
}

function getMobileSnapshot() {
  return window.matchMedia(MOBILE_MQL).matches;
}

export function useIsMobile() {
  return useSyncExternalStore(subscribeMobile, getMobileSnapshot, () => false);
}
