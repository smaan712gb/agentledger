/// <reference types="vite/client" />

/** The build the app was compiled from (vite.config.ts `define`): the repository's BUILD_SHA, "dev" in git. */
declare const __BUILD_SHA__: string;

declare module "*.module.css" {
  const classes: Readonly<Record<string, string>>;
  export default classes;
}
