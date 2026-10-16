"use client";
import * as React from "react";

/** True once React has hydrated; used to keep forms from submitting natively before JS is ready. */
export function useHydrated() {
  const [h, setH] = React.useState(false);
  React.useEffect(() => setH(true), []);
  return h;
}
