import * as RadixDialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";

import styles from "./Dialog.module.css";

export interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  /** When false the dialog cannot be dismissed by Escape or a click outside (a decision must be made). */
  dismissable?: boolean;
}

/** A modal dialog: focus moves in, is trapped, and returns to the trigger; Escape closes when dismissable. */
export function Dialog({ open, onOpenChange, title, description, children, dismissable = true }: DialogProps) {
  const prevent = (e: Event) => e.preventDefault();
  const keep = dismissable
    ? {}
    : { onEscapeKeyDown: prevent, onPointerDownOutside: prevent, onInteractOutside: prevent };
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className={styles.overlay} />
        <RadixDialog.Content className={styles.content} {...keep}>
          <RadixDialog.Title className={styles.title}>{title}</RadixDialog.Title>
          {description ? (
            <RadixDialog.Description className={styles.description}>{description}</RadixDialog.Description>
          ) : (
            <RadixDialog.Description className="visually-hidden">{title}</RadixDialog.Description>
          )}
          {children}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

export const DialogClose = RadixDialog.Close;
