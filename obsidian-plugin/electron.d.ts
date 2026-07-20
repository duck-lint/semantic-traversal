/**
 * Obsidian's desktop runtime supplies Electron's shell module at runtime.
 * The plugin only uses shell.openPath, so keep the local declaration limited
 * to the API actually consumed rather than introducing an unpinned Electron
 * runtime dependency into the community-plugin package.
 */
declare module "electron" {
  export const shell: {
    openPath(path: string): Promise<string>;
  };
}
