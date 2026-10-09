/** What the boot sequence learnt from the URL fragment, handed to the app once it renders. */
export interface BootNotices {
  toast?: string;
}

let notices: BootNotices = {};

export const bootNotices = {
  set(value: BootNotices): void {
    notices = value;
  },
  take(): BootNotices {
    const v = notices;
    notices = {};
    return v;
  },
};
